import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchspec import prettynames

_CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4"
_COLORS = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948", "#b07aa1", "#ff9da7"]
_STAT_COLS = ("n", "n_failed", "mean", "stddev", "p50", "p95", "p99", "ci95_low", "ci95_high")


def _safe_id(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", s)


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
  <title>benchspec report</title>
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
  </style>
</head>
<body>
  <h1>benchspec report</h1>
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

        if group_by:
            labels = [" / ".join(str(r.get(g, "")) for g in group_by) for r in metric_rows]
        else:
            labels = ["all"] * len(metric_rows)

        datasets = [
            {
                "label": stat_key,
                "data": [r.get(stat_key) for r in metric_rows],
                "backgroundColor": color,
                "borderColor": color,
                "borderWidth": 1,
            }
            for stat_key, color in zip(["mean", "p50", "p95", "p99"], _COLORS)
        ]

        all_headers = list(group_by) + list(_STAT_COLS)
        sections.append(f"""
  <section>
    <h2>{display_name}</h2>
    <div class="chart-wrap"><canvas id="chart-{safe}"></canvas></div>
    {_table_html(metric_rows, all_headers)}
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


def generate_plots_html(
    results_dir: Path,
    plot_specs: list,
    output_path: Path,
    pretty_map: dict,
) -> None:
    from benchspec import query

    sections: list[str] = []
    scripts: list[str] = []

    for plot in plot_specs:
        if plot.type == "scatter":
            group_by = [plot.group_by] if plot.group_by else []
            metrics = [plot.x] + plot.y
        else:
            group_by = [plot.x]
            if plot.group_by:
                group_by.append(plot.group_by)
            metrics = plot.y

        rows = query.compute_rows(results_dir, group_by=group_by, metrics=metrics)

        if plot.type == "scatter":
            chart_config = _build_scatter_config(plot, rows, pretty_map)
        elif plot.type in ("bar", "stacked-bar"):
            chart_config = _build_bar_or_line_config(
                plot, rows, pretty_map,
                chart_type="bar",
                stacked=(plot.type == "stacked-bar"),
            )
        elif plot.type == "line":
            chart_config = _build_bar_or_line_config(
                plot, rows, pretty_map, chart_type="line", stacked=False,
            )
        else:
            continue

        safe = _safe_id(plot.id)
        title = plot.title or plot.id
        table_headers = list(group_by) + ["metric"] + list(_STAT_COLS) if rows else []
        sections.append(f"""
  <section>
    <h2>{title}</h2>
    <p class="meta">type: {plot.type}</p>
    <div class="chart-wrap"><canvas id="chart-{safe}"></canvas></div>
    {_table_html(rows, table_headers)}
  </section>""")

        scripts.append(
            f"\n  new Chart(document.getElementById('chart-{safe}'), "
            f"{json.dumps(chart_config)});"
        )

    output_path.write_text(_html_shell(sections, scripts))


def _build_bar_or_line_config(
    plot, rows: list[dict], pretty_map: dict, *, chart_type: str, stacked: bool,
) -> dict:
    x_values_raw = sorted({str(r.get(plot.x, "")) for r in rows})
    if chart_type == "line":
        x_values = _try_numeric_sort(x_values_raw)
    else:
        x_values = x_values_raw

    indexed: dict[tuple, float] = {}
    series_keys: list[tuple] = []
    seen: set[tuple] = set()
    for r in rows:
        x_val = str(r.get(plot.x, ""))
        gb_val = str(r.get(plot.group_by, "")) if plot.group_by else None
        metric = r.get("metric")
        key = (metric, gb_val)
        if key not in seen:
            seen.add(key)
            series_keys.append(key)
        indexed[(x_val, metric, gb_val)] = r.get("mean")

    datasets = []
    for i, (metric, gb_val) in enumerate(series_keys):
        label_parts = [_resolve_metric(pretty_map, metric)]
        if gb_val is not None:
            label_parts.append(gb_val)
        label = " / ".join(label_parts)
        data = [indexed.get((x, metric, gb_val)) for x in x_values]
        ds = {
            "label": label,
            "data": data,
            "backgroundColor": _color(i),
            "borderColor": _color(i),
            "borderWidth": 1,
        }
        if chart_type == "line":
            ds["fill"] = False
            ds["tension"] = 0.1
        datasets.append(ds)

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
    }


def _build_scatter_config(plot, rows: list[dict], pretty_map: dict) -> dict:
    groups: dict[str, dict[str, float]] = {}
    for r in rows:
        gb = str(r.get(plot.group_by, "")) if plot.group_by else "all"
        groups.setdefault(gb, {})[r.get("metric")] = r.get("mean")

    datasets = []
    y_metric = plot.y[0]
    for i, (gb_val, by_metric) in enumerate(groups.items()):
        x_val = by_metric.get(plot.x)
        y_val = by_metric.get(y_metric)
        if x_val is None or y_val is None:
            continue
        datasets.append({
            "label": gb_val,
            "data": [{"x": x_val, "y": y_val}],
            "backgroundColor": _color(i),
            "borderColor": _color(i),
            "pointRadius": 6,
        })

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
    }
