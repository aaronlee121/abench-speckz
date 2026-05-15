"""Render aggregate rows as a self-contained Chart.js-powered HTML report."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from abench_speckz import prettynames

_CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4"
_COLORS = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948", "#b07aa1", "#ff9da7"]
_STAT_COLS = ("n", "n_failed", "mean", "stddev", "p50", "p95", "p99", "ci95_low", "ci95_high")


def _safe_id(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", s)


def _collapsible(label: str, content: str, *, open: bool = False) -> str:
    open_attr = " open" if open else ""
    return f"<details{open_attr}><summary>{label}</summary>{content}</details>"


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".") or "0"
    return str(v)


def _resolve_metric(pretty_map: dict, metric: str) -> str:
    return prettynames.resolve(pretty_map, metric)


def _color(i: int) -> str:
    return _COLORS[i % len(_COLORS)]


def _try_numeric_sort(values: list[str]) -> list[str]:
    try:
        return sorted(values, key=lambda v: float(v))
    except (ValueError, TypeError):
        return sorted(values)


def _html_shell(sections: list[str], scripts: list[str]) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>abench_speckz report</title>
  <script src="{_CHART_JS_CDN}"></script>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 1100px; margin: 0 auto; padding: 1rem 2rem; color: #222; }}
    h1 {{ font-size: 1.5rem; margin-bottom: 0.25rem; }}
    .meta {{ color: #666; font-size: 0.875rem; margin-bottom: 2rem; }}
    section {{ margin-bottom: 3rem; }}
    h2 {{ font-size: 1.2rem; border-bottom: 1px solid #ddd; padding-bottom: 0.25rem; }}
    .chart-wrap {{ max-width: 720px; margin-bottom: 1rem; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ border-collapse: collapse; font-size: 0.85rem; }}
    th, td {{ border: 1px solid #ddd; padding: 4px 8px; text-align: right; }}
    th {{ background: #f5f5f5; text-align: center; }}
    td:first-child {{ text-align: left; }}
    .legend-wrap {{ margin-bottom: 0.75rem; }}
    .legend-wrap table {{ font-size: 0.8rem; }}
    .legend-wrap th, .legend-wrap td {{ padding: 3px 8px; }}
    .fixed-vars {{ color: #555; font-size: 0.85rem; margin: 0.25rem 0 0.5rem; }}
    details {{ margin-bottom: 0.5rem; }}
    summary {{ cursor: pointer; font-weight: 500; color: #444; padding: 0.2rem 0; user-select: none; }}
    summary:hover {{ color: #111; }}
  </style>
</head>
<body>
  <h1>abench_speckz report</h1>
  <p class="meta">Generated: {timestamp}</p>
{"".join(sections)}
  <script>
{"".join(scripts)}
  </script>
</body>
</html>"""


def _table_html(rows: list[dict], headers: list[str]) -> str:
    if not rows:
        return '<p class="meta">(no data)</p>'
    header_cells = "".join(f"<th>{h}</th>" for h in headers)
    body_rows = ""
    for r in rows:
        cells = "".join(f"<td>{_fmt(r.get(h))}</td>" for h in headers)
        body_rows += f"<tr>{cells}</tr>"
    return (
        f'<div class="table-wrap"><table>'
        f"<thead><tr>{header_cells}</tr></thead>"
        f"<tbody>{body_rows}</tbody>"
        f"</table></div>"
    )


def _split_group_by_vars(
    rows: list[dict], group_by: list[str],
) -> tuple[dict[str, str], list[str]]:
    """Partition group_by keys into static (one unique value) and varying (multiple values)."""
    if not group_by or not rows:
        return {}, list(group_by)
    unique: dict[str, set] = {k: set() for k in group_by}
    for r in rows:
        for k in group_by:
            unique[k].add(str(r.get(k, "")))
    static_vars = {k: next(iter(v)) for k, v in unique.items() if len(v) == 1}
    varying_keys = [k for k in group_by if k not in static_vars]
    return static_vars, varying_keys


def _fixed_vars_html(static_vars: dict[str, str], pretty_map: dict) -> str:
    if not static_vars:
        return ""
    parts = ", ".join(
        f"<b>{_resolve_metric(pretty_map, k)}</b>&nbsp;=&nbsp;{v}"
        for k, v in static_vars.items()
    )
    return f'<p class="fixed-vars">Fixed: {parts}</p>'


def _legend_table_html(
    series_meta: list[dict],
    varying_keys: list[str],
    pretty_map: dict,
    multi_metric: bool,
) -> str:
    """Color-coded table legend replacing Chart.js's built-in legend for multi-key plots."""
    if not series_meta or not varying_keys:
        return ""
    header_cells = "<th></th>"
    if multi_metric:
        header_cells += "<th>metric</th>"
    for k in varying_keys:
        header_cells += f"<th>{_resolve_metric(pretty_map, k)}</th>"
    body_rows = ""
    for s in series_meta:
        color = s["color"]
        swatch = (
            f'<span style="display:inline-block;width:13px;height:13px;'
            f'border-radius:3px;background:{color};vertical-align:middle"></span>'
        )
        cells = f'<td style="text-align:center;border-right:none">{swatch}</td>'
        if multi_metric:
            cells += f"<td>{_resolve_metric(pretty_map, s['metric'])}</td>"
        for k in varying_keys:
            cells += f"<td>{s['vals'].get(k, '')}</td>"
        body_rows += f"<tr>{cells}</tr>"
    return (
        f'<div class="legend-wrap table-wrap">'
        f'<table><thead><tr>{header_cells}</tr></thead>'
        f'<tbody>{body_rows}</tbody>'
        f'</table></div>'
    )


def generate_html(
    rows: list[dict[str, Any]],
    path: Path,
    group_by: list[str],
    pretty_map: dict,
) -> None:
    by_metric: dict[str, list[dict]] = {}
    for row in rows:
        by_metric.setdefault(row["metric"], []).append(row)

    sections: list[str] = []
    chart_scripts: list[str] = []

    for metric, metric_rows in by_metric.items():
        display_name = _resolve_metric(pretty_map, metric)
        safe = _safe_id(metric)

        static_vars, varying_keys = _split_group_by_vars(metric_rows, group_by)
        label_keys = varying_keys if varying_keys else group_by

        if label_keys:
            labels = [" / ".join(str(r.get(g, "")) for g in label_keys) for r in metric_rows]
        else:
            labels = ["all"] * len(metric_rows)

        fixed_html = _fixed_vars_html(static_vars, pretty_map)

        datasets = [
            {
                "label": stat_key,
                "data": [r.get(stat_key) for r in metric_rows],
                "backgroundColor": color,
                "borderColor": color,
                "borderWidth": 1,
            }
            for stat_key, color in zip(["mean", "p50", "p95", "p99"], _COLORS, strict=False)
        ]

        all_headers = list(group_by) + list(_STAT_COLS)
        chart_html = _collapsible("Chart", f'<div class="chart-wrap"><canvas id="chart-{safe}"></canvas></div>', open=True)
        table_html = _collapsible("Data", _table_html(metric_rows, all_headers))
        sections.append(f"""
  <section>
    <h2>{display_name}</h2>
    {fixed_html}
    {chart_html}
    {table_html}
  </section>""")

        chart_scripts.append(f"""
  new Chart(document.getElementById('chart-{safe}'), {{
    type: 'bar',
    data: {{
      labels: {json.dumps(labels)},
      datasets: {json.dumps(datasets)}
    }},
    options: {{
      responsive: true,
      plugins: {{ legend: {{ position: 'top' }} }},
      scales: {{ y: {{ beginAtZero: false }} }}
    }}
  }});""")

    path.write_text(_html_shell(sections, chart_scripts))


def _resolve_group_by(group_by: list[str], all_vars: list[str]) -> list[str]:
    """Expand !var negation entries: replace them with all vars not excluded."""
    if not any(k.startswith("!") for k in group_by):
        return group_by
    excluded = {k[1:] for k in group_by if k.startswith("!")}
    positives = [k for k in group_by if not k.startswith("!")]
    expanded = [v for v in all_vars if v not in excluded and v not in positives]
    return positives + expanded


def generate_plots_html(
    results_dir: Path,
    plot_specs: list,
    output_path: Path,
    pretty_map: dict,
) -> None:
    from abench_speckz import query

    all_vars = query.var_names(results_dir)
    sections: list[str] = []
    scripts: list[str] = []

    for plot in plot_specs:
        gb_keys = _resolve_group_by(list(plot.group_by or []), all_vars)

        if plot.type == "scatter":
            group_by = gb_keys
            metrics = [plot.x] + plot.y
        else:
            group_by = [plot.x] + gb_keys
            metrics = plot.y

        rows = query.compute_rows(results_dir, group_by=group_by, metrics=metrics)
        static_vars, varying_keys = _split_group_by_vars(rows, gb_keys)
        multi_metric = len(plot.y) > 1

        if plot.type == "scatter":
            chart_config, series_meta = _build_scatter_config(
                plot, rows, pretty_map, group_by=gb_keys, varying_keys=varying_keys,
            )
        elif plot.type in ("bar", "stacked-bar"):
            chart_config, series_meta = _build_bar_or_line_config(
                plot, rows, pretty_map,
                chart_type="bar",
                stacked=(plot.type == "stacked-bar"),
                group_by=gb_keys,
                varying_keys=varying_keys,
            )
        elif plot.type == "line":
            chart_config, series_meta = _build_bar_or_line_config(
                plot, rows, pretty_map, chart_type="line", stacked=False,
                group_by=gb_keys,
                varying_keys=varying_keys,
            )
        else:
            continue

        use_table_legend = bool(varying_keys)
        if use_table_legend:
            chart_config["options"]["plugins"]["legend"] = {"display": False}

        fixed_html = _fixed_vars_html(static_vars, pretty_map)
        legend_html = _legend_table_html(series_meta, varying_keys, pretty_map, multi_metric)

        safe = _safe_id(plot.id)
        title = plot.title or plot.id
        table_headers = list(group_by) + ["metric"] + list(_STAT_COLS) if rows else []
        chart_inner = f"{legend_html}<div class=\"chart-wrap\"><canvas id=\"chart-{safe}\"></canvas></div>"
        chart_html = _collapsible("Chart", chart_inner, open=True)
        table_html = _collapsible("Data", _table_html(rows, table_headers))
        sections.append(f"""
  <section>
    <h2>{title}</h2>
    <p class="meta">type: {plot.type}</p>
    {fixed_html}
    {chart_html}
    {table_html}
  </section>""")

        scripts.append(
            f"\n  new Chart(document.getElementById('chart-{safe}'), "
            f"{json.dumps(chart_config)});"
        )

    output_path.write_text(_html_shell(sections, scripts))


def _build_bar_or_line_config(
    plot, rows: list[dict], pretty_map: dict, *, chart_type: str, stacked: bool,
    group_by: list[str], varying_keys: list[str],
) -> tuple[dict, list[dict]]:
    x_values_raw = sorted({str(r.get(plot.x, "")) for r in rows})
    if chart_type == "line":
        x_values = _try_numeric_sort(x_values_raw)
    else:
        x_values = x_values_raw

    # Label uses only varying keys (omits static ones); falls back to all group_by if none vary.
    label_keys = varying_keys if varying_keys else group_by

    indexed: dict[tuple, float | None] = {}
    series_keys: list[tuple] = []  # (metric, gb_tuple)
    seen: set[tuple] = set()
    for r in rows:
        x_val = str(r.get(plot.x, ""))
        gb_tuple = tuple(str(r.get(k, "")) for k in group_by) if group_by else ()
        metric = r.get("metric")
        key = (metric, gb_tuple)
        if key not in seen:
            seen.add(key)
            series_keys.append(key)
        indexed[(x_val, metric, gb_tuple)] = r.get("mean")

    datasets = []
    series_meta = []
    for i, (metric, gb_tuple) in enumerate(series_keys):
        color = _color(i)
        vals = dict(zip(group_by, gb_tuple)) if group_by else {}
        varying_parts = [str(vals[k]) for k in label_keys if k in vals]
        label_parts = [_resolve_metric(pretty_map, metric)]
        if varying_parts:
            label_parts.append(" / ".join(varying_parts))
        label = " / ".join(label_parts)

        data = [indexed.get((x, metric, gb_tuple)) for x in x_values]
        ds = {
            "label": label,
            "data": data,
            "backgroundColor": color,
            "borderColor": color,
            "borderWidth": 1,
        }
        if chart_type == "line":
            ds["fill"] = False
            ds["tension"] = 0.1
        datasets.append(ds)
        series_meta.append({"metric": metric, "vals": vals, "color": color})

    options: dict = {
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

    return {
        "type": chart_type,
        "data": {"labels": x_values, "datasets": datasets},
        "options": options,
    }, series_meta


def _build_scatter_config(
    plot, rows: list[dict], pretty_map: dict, *, group_by: list[str], varying_keys: list[str],
) -> tuple[dict, list[dict]]:
    label_keys = varying_keys if varying_keys else group_by

    groups: dict[tuple, dict[str, float | None]] = {}
    group_vals: dict[tuple, dict[str, str]] = {}
    for r in rows:
        gb_tuple = tuple(str(r.get(k, "")) for k in group_by) if group_by else ("all",)
        vals = dict(zip(group_by, gb_tuple)) if group_by else {}
        groups.setdefault(gb_tuple, {})[str(r.get("metric"))] = r.get("mean")
        group_vals[gb_tuple] = vals

    datasets = []
    series_meta = []
    y_metric = plot.y[0]
    for i, (gb_tuple, by_metric) in enumerate(groups.items()):
        x_val = by_metric.get(plot.x)
        y_val = by_metric.get(y_metric)
        if x_val is None or y_val is None:
            continue
        color = _color(i)
        vals = group_vals.get(gb_tuple, {})
        varying_parts = [str(vals[k]) for k in label_keys if k in vals]
        label = " / ".join(varying_parts) if varying_parts else "all"
        datasets.append({
            "label": label,
            "data": [{"x": x_val, "y": y_val}],
            "backgroundColor": color,
            "borderColor": color,
            "pointRadius": 6,
        })
        series_meta.append({"metric": y_metric, "vals": vals, "color": color})

    return {
        "type": "scatter",
        "data": {"datasets": datasets},
        "options": {
            "responsive": True,
            "plugins": {"legend": {"position": "top"}},
            "scales": {
                "x": {"title": {"display": True, "text": _resolve_metric(pretty_map, plot.x)}},
                "y": {"title": {"display": True, "text": _resolve_metric(pretty_map, y_metric)}},
            },
        },
    }, series_meta
