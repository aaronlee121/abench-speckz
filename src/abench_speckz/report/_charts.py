"""Build Chart.js config objects for bar / line / scatter / stacked-bar plots."""

from typing import Any

from abench_speckz import interpolate
from abench_speckz.expr import eval_expr, extract_names, is_expr
from abench_speckz.model import RunError
from abench_speckz.report._html import color, resolve_metric, resolve_var_value, try_numeric_sort


def _apply_series_label(template: str, mapped_vals: dict[str, str]) -> str:
    """Substitute ${var} in a series_label template using pre-mapped variable values."""
    try:
        return interpolate.resolve_string(template, mapped_vals)
    except Exception:
        return template


def _make_x_getter(x_spec: str):
    """Return a callable that extracts or computes the x-axis value from a row."""
    if not is_expr(x_spec):
        return lambda r: str(r.get(x_spec, ""))
    x_vars = sorted(extract_names(x_spec))

    def getter(r: dict) -> str:
        ns: dict[str, float] = {}
        for v in x_vars:
            raw = r.get(v)
            try:
                ns[v] = float(raw) if raw is not None else 0.0
            except (TypeError, ValueError):
                ns[v] = 0.0
        try:
            result = eval_expr(x_spec, ns)
            return str(int(result)) if result == int(result) else str(result)
        except (ValueError, ZeroDivisionError, OverflowError):
            return ""

    return getter


def build_bar_or_line_config(
    plot,
    rows: list[dict],
    pretty_map: dict,
    *,
    chart_type: str,
    stacked: bool,
    group_by: list[str],
    varying_keys: list[str],
    smooth: bool = False,
) -> tuple[dict, list[dict]]:
    """Return (chart_config, series_meta) for a bar/line/stacked-bar plot."""
    get_x = _make_x_getter(plot.x)
    x_values_raw = sorted({get_x(r) for r in rows})
    x_values = try_numeric_sort(x_values_raw) if chart_type == "line" else x_values_raw
    if not is_expr(plot.x):
        x_labels = [resolve_var_value(pretty_map, plot.x, v) for v in x_values]
    else:
        x_labels = list(x_values)

    # Label uses only varying keys (omits static ones); falls back to all group_by if none vary.
    label_keys = varying_keys if varying_keys else group_by
    series_label_tpl = getattr(plot, "series_label", None)

    indexed, series_keys = _index_series(rows, get_x, group_by)

    datasets: list[dict] = []
    series_meta: list[dict] = []
    for i, (metric, gb_tuple) in enumerate(series_keys):
        c = color(i)
        vals = dict(zip(group_by, gb_tuple, strict=True)) if group_by else {}
        if series_label_tpl:
            mapped_vals = {k: resolve_var_value(pretty_map, k, str(v)) for k, v in vals.items()}
            group_label = _apply_series_label(series_label_tpl, mapped_vals)
            metric_display = resolve_metric(pretty_map, metric)
            label = f"{metric_display} / {group_label}" if len(set(m for m, _ in series_keys)) > 1 else group_label
        else:
            varying_parts = [resolve_var_value(pretty_map, k, str(vals[k])) for k in label_keys if k in vals]
            label_parts = [resolve_metric(pretty_map, metric)]
            if varying_parts:
                label_parts.append(" / ".join(varying_parts))
            label = " / ".join(label_parts)

        data = [indexed.get((x, metric, gb_tuple)) for x in x_values]
        ds: dict[str, Any] = {
            "label": label,
            "data": data,
            "backgroundColor": c,
            "borderColor": c,
            "borderWidth": 1,
            "_colorIdx": i,
        }
        if chart_type == "line":
            ds["fill"] = False
            ds["tension"] = 0.4 if smooth else 0.1
        datasets.append(ds)
        series_meta.append({"metric": metric, "vals": vals, "color": c})

    x_scale: dict[str, Any] = {"title": {"display": True, "text": plot.x_title if plot.x_title else plot.x}}
    y_scale: dict[str, Any] = {"beginAtZero": True}
    if plot.y_title:
        y_scale["title"] = {"display": True, "text": plot.y_title}
    if stacked:
        x_scale["stacked"] = True
        y_scale["stacked"] = True
    options: dict[str, Any] = {
        "responsive": True,
        "plugins": {"legend": {"position": "top"}},
        "scales": {"x": x_scale, "y": y_scale},
    }

    normalize = getattr(plot, "normalize", None)
    if normalize is not None and group_by:
        if normalize.against not in group_by:
            raise RunError(
                f"plot {plot.id!r}: normalize.against {normalize.against!r} "
                f"is not in the resolved group_by {group_by}"
            )
        against_idx = group_by.index(normalize.against)
        ref_val = normalize.reference

        # Build per-(metric, x) reference values
        ref_lookup: dict[tuple[str, str], float] = {}
        for metric, gb_tuple in series_keys:
            if len(gb_tuple) > against_idx and gb_tuple[against_idx] == ref_val:
                for x in x_values:
                    v = indexed.get((x, metric, gb_tuple))
                    if v is not None:
                        ref_lookup[(metric, x)] = v

        # Divide each dataset's data by its reference series' value
        for ds, (metric, gb_tuple) in zip(datasets, series_keys):
            new_data: list[float | None] = []
            for j, x in enumerate(x_values):
                orig = ds["data"][j]
                ref = ref_lookup.get((metric, x))
                if orig is not None and ref is not None and ref != 0.0:
                    new_data.append(orig / ref)
                else:
                    new_data.append(None)
            ds["data"] = new_data

        # Update y-axis to show "× speedup" unless caller already set a custom title
        if not plot.y_title:
            y_scale["title"] = {"display": True, "text": "× speedup"}
        y_scale["beginAtZero"] = False
        # Horizontal reference line at 1.0 (rendered by abenchRefLine plugin in _html.py)
        options["plugins"]["abenchRefLine"] = {"y": 1.0}

    config = {
        "type": chart_type,
        "data": {"labels": x_labels, "datasets": datasets},
        "options": options,
    }
    return config, series_meta


def build_scatter_config(
    plot,
    rows: list[dict],
    pretty_map: dict,
    *,
    group_by: list[str],
    varying_keys: list[str],
) -> tuple[dict, list[dict]]:
    """Return (chart_config, series_meta) for a scatter plot.

    For scatter, plot.x and plot.y[0] are *metrics* (not variables); each unique
    combination of group_by values becomes its own named point.
    """
    label_keys = varying_keys if varying_keys else group_by
    series_label_tpl = getattr(plot, "series_label", None)

    groups: dict[tuple, dict[str, float | None]] = {}
    group_vals: dict[tuple, dict[str, str]] = {}
    for r in rows:
        gb_tuple = tuple(str(r.get(k, "")) for k in group_by) if group_by else ("all",)
        vals = dict(zip(group_by, gb_tuple, strict=True)) if group_by else {}
        groups.setdefault(gb_tuple, {})[str(r.get("metric"))] = r.get("mean")
        group_vals[gb_tuple] = vals

    datasets: list[dict] = []
    series_meta: list[dict] = []
    y_metric = plot.y[0]
    for i, (gb_tuple, by_metric) in enumerate(groups.items()):
        x_val = by_metric.get(plot.x)
        y_val = by_metric.get(y_metric)
        if x_val is None or y_val is None:
            continue
        c = color(i)
        vals = group_vals.get(gb_tuple, {})
        if series_label_tpl:
            mapped_vals = {k: resolve_var_value(pretty_map, k, str(v)) for k, v in vals.items()}
            label = _apply_series_label(series_label_tpl, mapped_vals)
        else:
            varying_parts = [resolve_var_value(pretty_map, k, str(vals[k])) for k in label_keys if k in vals]
            label = " / ".join(varying_parts) if varying_parts else "all"
        datasets.append(
            {
                "label": label,
                "data": [{"x": x_val, "y": y_val}],
                "backgroundColor": c,
                "borderColor": c,
                "pointRadius": 6,
                "_colorIdx": i,
            }
        )
        series_meta.append({"metric": y_metric, "vals": vals, "color": c})

    x_title_text = plot.x_title if plot.x_title else resolve_metric(pretty_map, plot.x)
    y_title_text = plot.y_title if plot.y_title else resolve_metric(pretty_map, y_metric)
    config = {
        "type": "scatter",
        "data": {"datasets": datasets},
        "options": {
            "responsive": True,
            "plugins": {"legend": {"position": "top"}},
            "scales": {
                "x": {"title": {"display": True, "text": x_title_text}},
                "y": {"beginAtZero": True, "title": {"display": True, "text": y_title_text}},
            },
        },
    }
    return config, series_meta


def _index_series(
    rows: list[dict],
    get_x,
    group_by: list[str],
) -> tuple[dict[tuple, float | None], list[tuple]]:
    """Build (x, metric, gb_tuple) → mean lookup and ordered list of (metric, gb_tuple) keys."""
    indexed: dict[tuple, float | None] = {}
    series_keys: list[tuple] = []
    seen: set[tuple] = set()
    for r in rows:
        x_val = get_x(r)
        gb_tuple = tuple(str(r.get(k, "")) for k in group_by) if group_by else ()
        metric = r.get("metric")
        key = (metric, gb_tuple)
        if key not in seen:
            seen.add(key)
            series_keys.append(key)
        indexed[(x_val, metric, gb_tuple)] = r.get("mean")
    return indexed, series_keys
