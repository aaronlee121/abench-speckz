"""Build Chart.js config objects for bar / line / scatter / stacked-bar plots."""

from typing import Any

from abench_speckz.report._html import color, resolve_metric, try_numeric_sort


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
    x_values_raw = sorted({str(r.get(plot.x, "")) for r in rows})
    x_values = try_numeric_sort(x_values_raw) if chart_type == "line" else x_values_raw

    # Label uses only varying keys (omits static ones); falls back to all group_by if none vary.
    label_keys = varying_keys if varying_keys else group_by

    indexed, series_keys = _index_series(rows, plot.x, group_by)

    datasets: list[dict] = []
    series_meta: list[dict] = []
    for i, (metric, gb_tuple) in enumerate(series_keys):
        c = color(i)
        vals = dict(zip(group_by, gb_tuple, strict=True)) if group_by else {}
        varying_parts = [str(vals[k]) for k in label_keys if k in vals]
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
        }
        if chart_type == "line":
            ds["fill"] = False
            ds["tension"] = 0.4 if smooth else 0.1
        datasets.append(ds)
        series_meta.append({"metric": metric, "vals": vals, "color": c})

    options: dict[str, Any] = {
        "responsive": True,
        "plugins": {"legend": {"position": "top"}},
        "scales": {
            "x": {"title": {"display": True, "text": plot.x}},
            "y": {"beginAtZero": False},
        },
    }
    if stacked:
        options["scales"]["x"]["stacked"] = True
        options["scales"]["y"]["stacked"] = True

    config = {
        "type": chart_type,
        "data": {"labels": x_values, "datasets": datasets},
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
        varying_parts = [str(vals[k]) for k in label_keys if k in vals]
        label = " / ".join(varying_parts) if varying_parts else "all"
        datasets.append(
            {
                "label": label,
                "data": [{"x": x_val, "y": y_val}],
                "backgroundColor": c,
                "borderColor": c,
                "pointRadius": 6,
            }
        )
        series_meta.append({"metric": y_metric, "vals": vals, "color": c})

    config = {
        "type": "scatter",
        "data": {"datasets": datasets},
        "options": {
            "responsive": True,
            "plugins": {"legend": {"position": "top"}},
            "scales": {
                "x": {"title": {"display": True, "text": resolve_metric(pretty_map, plot.x)}},
                "y": {"title": {"display": True, "text": resolve_metric(pretty_map, y_metric)}},
            },
        },
    }
    return config, series_meta


def _index_series(
    rows: list[dict],
    x_key: str,
    group_by: list[str],
) -> tuple[dict[tuple, float | None], list[tuple]]:
    """Build (x, metric, gb_tuple) → mean lookup and ordered list of (metric, gb_tuple) keys."""
    indexed: dict[tuple, float | None] = {}
    series_keys: list[tuple] = []
    seen: set[tuple] = set()
    for r in rows:
        x_val = str(r.get(x_key, ""))
        gb_tuple = tuple(str(r.get(k, "")) for k in group_by) if group_by else ()
        metric = r.get("metric")
        key = (metric, gb_tuple)
        if key not in seen:
            seen.add(key)
            series_keys.append(key)
        indexed[(x_val, metric, gb_tuple)] = r.get("mean")
    return indexed, series_keys
