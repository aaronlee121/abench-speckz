"""HTML shell + small formatting/escaping helpers shared across the report package."""
import re
from datetime import datetime, timezone
from typing import Any

from abench_speckz import prettynames

CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4"
COLORS = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948", "#b07aa1", "#ff9da7"]
STAT_COLS = ("n", "n_failed", "mean", "stddev", "p50", "p95", "p99", "ci95_low", "ci95_high")


# Page-level styles. Extracted to a module constant so the shell template below
# stays readable; doubled braces are required because of the f-string in _html_shell.
_CSS = """
    body { font-family: system-ui, sans-serif; max-width: 1100px; margin: 0 auto; padding: 1rem 2rem; color: #222; }
    h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
    .meta { color: #666; font-size: 0.875rem; margin-bottom: 2rem; }
    section { margin-bottom: 3rem; }
    h2 { font-size: 1.2rem; border-bottom: 1px solid #ddd; padding-bottom: 0.25rem; }
    .chart-wrap { max-width: 720px; margin-bottom: 1rem; }
    .table-wrap { overflow-x: auto; }
    table { border-collapse: collapse; font-size: 0.85rem; }
    th, td { border: 1px solid #ddd; padding: 4px 8px; text-align: right; }
    th { background: #f5f5f5; text-align: center; }
    td:first-child { text-align: left; }
    .legend-wrap { margin-bottom: 0.75rem; }
    .legend-wrap table { font-size: 0.8rem; }
    .legend-wrap th, .legend-wrap td { padding: 3px 8px; }
    .fixed-vars { color: #555; font-size: 0.85rem; margin: 0.25rem 0 0.5rem; }
    details { margin-bottom: 0.5rem; }
    summary { cursor: pointer; font-weight: 500; color: #444; padding: 0.2rem 0; user-select: none; }
    summary:hover { color: #111; }
    iframe.run-rpt { width: 100%; border: none; min-height: 400px; }
    .run-group-heading { font-size: 1rem; margin: 1rem 0 0.25rem; color: #333; }
    .report-intro { margin: 0 0 2rem; color: #333; }
    .report-intro p:first-child { margin-top: 0; }
    .section-description { color: #444; margin-bottom: 1rem; }
    .section-description p:first-child { margin-top: 0; }
    .rotated-slant { --slant-px: 25px; }
    .rotated-slant table { border-collapse: separate; border-spacing: 0; }
    .rotated-slant td { border: 1px solid #ddd; border-left: none; min-width: 60px; padding: 3px 8px; text-align: center; }
    .rotated-slant td:first-child { border-left: 1px solid #ddd; }
    .rotated-slant thead th { border: 1px solid #ddd; border-top: none; border-left: none; border-right: none; background: transparent; padding: 3px 8px 6px; white-space: nowrap; vertical-align: bottom; text-align: center; min-width: 2rem; position: relative; overflow: visible; }
    .rotated-slant thead th .cell-bg { position: absolute; top: 0; left: 0; bottom: 0; width: calc(100% + var(--slant-px)); border-top: 1px solid #bbb; clip-path: polygon(var(--slant-px) 0%, 100% 0%, calc(100% - var(--slant-px)) 100%, 0% 100%); z-index: 0; }
    .rotated-slant thead th:nth-child(odd) .cell-bg { background: #eef1f6; }
    .rotated-slant thead th:nth-child(even) .cell-bg { background: #dde8f4; }
    .rotated-slant thead th span { position: relative; z-index: 2; }
    .rotated-slant thead th:first-child::before { display: none; }
    .rotated-slant thead th::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 1px; background: #bbb; transform: rotate(10deg); transform-origin: bottom center; z-index: 1; }
    .rotated-slant thead th:last-child::after { content: ''; position: absolute; right: 0; top: 0; bottom: 0; width: 1px; background: #bbb; transform: rotate(10deg); transform-origin: bottom center; z-index: 1; }
    .rotated-slant th.rotated-header span { writing-mode: vertical-rl; transform: rotate(190deg); display: inline-block; }
"""


_SLANT_SYNC_JS = """
  (function() {
    var TAN10 = Math.tan(10 * Math.PI / 180);
    var SIN10 = Math.sin(10 * Math.PI / 180);
    function syncSlant() {
      document.querySelectorAll('.rotated-slant').forEach(function(wrap) {
        var thead = wrap.querySelector('thead');
        if (!thead) return;
        thead.querySelectorAll('th.rotated-header span').forEach(function(s) { s.style.marginLeft = ''; });
        var slantPx = Math.round(thead.getBoundingClientRect().height * TAN10);
        wrap.style.setProperty('--slant-px', slantPx + 'px');
        thead.querySelectorAll('th.rotated-header span').forEach(function(span) {
          span.style.marginLeft = Math.round(span.offsetHeight * SIN10) + 'px';
        });
      });
    }
    window.addEventListener('load', syncSlant);
    window.addEventListener('resize', syncSlant);
  })();
"""


def html_shell(sections: list[str], scripts: list[str], title: str = "abench_speckz report") -> str:
    """Render the full HTML document around pre-built section markup and Chart.js init scripts."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body_sections = "".join(sections)
    chart_inits = "".join(scripts)
    return (
        f'<!DOCTYPE html>\n'
        f'<html lang="en">\n'
        f'<head>\n'
        f'  <meta charset="utf-8">\n'
        f'  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'  <title>{title}</title>\n'
        f'  <script src="{CHART_JS_CDN}"></script>\n'
        f'  <style>{_CSS}  </style>\n'
        f'</head>\n'
        f'<body>\n'
        f'  <h1>{title}</h1>\n'
        f'  <p class="meta">Generated: {timestamp}</p>\n'
        f'{body_sections}\n'
        f'  <script>\n'
        f'{chart_inits}\n'
        f'  </script>\n'
        f'  <script>{_SLANT_SYNC_JS}  </script>\n'
        f'</body>\n'
        f'</html>'
    )


def safe_id(s: str) -> str:
    """Strip a string down to characters safe in an HTML id."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", s)


def collapsible(label: str, content: str, *, open: bool = False) -> str:
    open_attr = " open" if open else ""
    return f"<details{open_attr}><summary>{label}</summary>{content}</details>"


def fmt_value(v: Any) -> str:
    """Format scalar values for table cells: trim trailing zeros from floats."""
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".") or "0"
    return str(v)


def color(i: int) -> str:
    return COLORS[i % len(COLORS)]


def resolve_metric(pretty_map: dict, metric: str) -> str:
    return prettynames.resolve(pretty_map, metric)


def try_numeric_sort(values: list[str]) -> list[str]:
    """Sort numerically when every value parses as float, otherwise lexicographically."""
    try:
        return sorted(values, key=lambda v: float(v))
    except (ValueError, TypeError):
        return sorted(values)


def table_html(rows: list[dict], headers: list[str]) -> str:
    if not rows:
        return '<p class="meta">(no data)</p>'
    header_cells = "".join(f"<th>{h}</th>" for h in headers)
    body_rows = "".join(
        "<tr>" + "".join(f"<td>{fmt_value(r.get(h))}</td>" for h in headers) + "</tr>"
        for r in rows
    )
    return (
        f'<div class="table-wrap"><table>'
        f"<thead><tr>{header_cells}</tr></thead>"
        f"<tbody>{body_rows}</tbody>"
        f"</table></div>"
    )


def escape_for_js_template_literal(s: str) -> str:
    return s.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")


def split_group_by_vars(
    rows: list[dict], group_by: list[str],
) -> tuple[dict[str, str], list[str]]:
    """Partition ``group_by`` keys into static (one unique value) and varying (multiple values).

    Returns ``({static_key: value}, [varying_keys_in_order])``.
    """
    if not group_by or not rows:
        return {}, list(group_by)
    unique: dict[str, set] = {k: set() for k in group_by}
    for r in rows:
        for k in group_by:
            unique[k].add(str(r.get(k, "")))
    static_vars = {k: next(iter(v)) for k, v in unique.items() if len(v) == 1}
    varying_keys = [k for k in group_by if k not in static_vars]
    return static_vars, varying_keys


def fixed_vars_html(static_vars: dict[str, str], pretty_map: dict) -> str:
    """Render the ``Fixed: k = v, …`` block shown above plots where some group_by keys collapse."""
    if not static_vars:
        return ""
    parts = ", ".join(
        f"<b>{resolve_metric(pretty_map, k)}</b>&nbsp;=&nbsp;{v}"
        for k, v in static_vars.items()
    )
    return f'<p class="fixed-vars">Fixed: {parts}</p>'


def legend_table_html(
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
        header_cells += '<th class="rotated-header"><div class="cell-bg"></div><span>metric</span></th>'
    for k in varying_keys:
        label = resolve_metric(pretty_map, k)
        header_cells += f'<th class="rotated-header"><div class="cell-bg"></div><span>{label}</span></th>'
    body_rows = ""
    for s in series_meta:
        swatch = (
            f'<span style="display:inline-block;width:13px;height:13px;'
            f'border-radius:3px;background:{s["color"]};vertical-align:middle"></span>'
        )
        cells = f'<td style="text-align:center;border-right:none">{swatch}</td>'
        if multi_metric:
            cells += f"<td>{resolve_metric(pretty_map, s['metric'])}</td>"
        for k in varying_keys:
            cells += f"<td>{s['vals'].get(k, '')}</td>"
        body_rows += f"<tr>{cells}</tr>"
    return (
        f'<div class="legend-wrap table-wrap rotated-slant">'
        f'<table><thead><tr>{header_cells}</tr></thead>'
        f'<tbody>{body_rows}</tbody>'
        f'</table></div>'
    )


def resolve_group_by(group_by: list[str], all_vars: list[str]) -> list[str]:
    """Expand ``!var`` negation entries: replace them with all vars not excluded."""
    if not any(k.startswith("!") for k in group_by):
        return group_by
    excluded = {k[1:] for k in group_by if k.startswith("!")}
    positives = [k for k in group_by if not k.startswith("!")]
    expanded = [v for v in all_vars if v not in excluded and v not in positives]
    return positives + expanded


def render_markdown(src: str) -> str:
    import markdown
    return markdown.markdown(src, extensions=["extra", "sane_lists"])
