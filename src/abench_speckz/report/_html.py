"""HTML shell + small formatting/escaping helpers shared across the report package."""

import json
import re
from datetime import datetime, timezone
from typing import Any

from abench_speckz import prettynames

CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4"

THEMES: dict[str, list[str]] = {
    "tableau":    ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948", "#b07aa1", "#ff9da7"],
    "vivid":      ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00", "#a65628", "#f781bf", "#999999"],
    "pastel":     ["#8dd3c7", "#ffffb3", "#bebada", "#fb8072", "#80b1d3", "#fdb462", "#b3de69", "#fccde5"],
    "paired":     ["#a6cee3", "#1f78b4", "#b2df8a", "#33a02c", "#fb9a99", "#e31a1c", "#fdbf6f", "#ff7f00"],
    "muted":      ["#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02", "#a6761d", "#666666"],
    "accessible": ["#0077bb", "#ee7733", "#009988", "#cc3311", "#33bbee", "#ee3377", "#bbbbbb", "#555555"],
}
COLORS = THEMES["tableau"]
STAT_COLS = ("n", "n_failed", "mean", "stddev", "p50", "p95", "p99", "ci95_low", "ci95_high")

_THEME_LABELS = {
    "tableau": "Tableau", "vivid": "Vivid", "pastel": "Pastel",
    "paired": "Paired", "muted": "Muted", "accessible": "Accessible",
}


# Page-level styles. Uses CSS custom properties for dark-mode support.
_CSS = """
    :root {
      --bg: #f7f8fa; --surface: #fff; --surface-2: #f3f5f8; --surface-3: #f5f5f5;
      --surface-hover: #e8ebf0; --text: #222; --text-2: #444; --text-muted: #666;
      --text-dim: #555; --border: #ddd; --border-2: #bbb; --border-light: #eee;
      --border-mid: #e0e3e8; --cell-odd: #eef1f6; --cell-even: #dde8f4;
    }
    [data-theme="dark"] {
      --bg: #1a1b1e; --surface: #25262b; --surface-2: #2c2d33; --surface-3: #2a2b31;
      --surface-hover: #35363d; --text: #e0e0e0; --text-2: #c0c0c0; --text-muted: #888;
      --text-dim: #aaa; --border: #3a3b40; --border-2: #555; --border-light: #2e2f35;
      --border-mid: #3a3b40; --cell-odd: #2e3038; --cell-even: #2a2e3a;
    }
    body { font-family: system-ui, sans-serif; max-width: 1100px; margin: 0 auto; padding: 1rem 2rem; color: var(--text); background: var(--bg); }
    h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
    .meta { color: var(--text-muted); font-size: 0.875rem; margin-bottom: 0.75rem; }
    section { margin-bottom: 2rem; padding: 1.5rem 1.75rem; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); }
    h2 { font-size: 1.2rem; margin-top: 0; padding-bottom: 0.4rem; border-bottom: 1px solid var(--border-light); color: var(--text); }
    .chart-wrap { max-width: 720px; margin-bottom: 1rem; }
    .table-wrap { overflow-x: auto; }
    table { border-collapse: collapse; font-size: 0.85rem; }
    th, td { border: 1px solid var(--border); padding: 4px 8px; text-align: right; color: var(--text); }
    th { background: var(--surface-3); text-align: center; }
    td:first-child { text-align: left; }
    .legend-wrap { margin-bottom: 0.75rem; }
    .legend-wrap table { font-size: 0.8rem; }
    .legend-wrap th, .legend-wrap td { padding: 3px 8px; }
    .fixed-vars { color: var(--text-dim); font-size: 0.85rem; margin: 0.25rem 0 0.5rem; }
    h3.plot-title { font-size: 1rem; font-weight: 600; color: var(--text-2); margin: 0 0 0.75rem; }
    details { margin-bottom: 0.75rem; border: 1px solid var(--border-mid); border-radius: 8px; overflow: hidden; }
    summary { cursor: pointer; font-weight: 500; color: var(--text-2); padding: 0.4rem 0.75rem; background: var(--surface-2); user-select: none; list-style: none; display: flex; align-items: center; gap: 0.4rem; }
    summary::before { content: '▶'; font-size: 0.65rem; color: var(--text-muted); transition: transform 0.15s; flex-shrink: 0; }
    details[open] > summary::before { transform: rotate(90deg); }
    details[open] > summary { border-bottom: 1px solid var(--border-mid); }
    summary:hover { color: var(--text); background: var(--surface-hover); }
    .details-body { padding: 0.75rem; }
    iframe.run-rpt { width: 100%; border: none; min-height: 400px; }
    .run-group-heading { font-size: 1rem; margin: 1rem 0 0.25rem; color: var(--text-2); }
    .report-intro { margin: 0 0 2rem; color: var(--text-2); }
    .report-intro p:first-child { margin-top: 0; }
    .section-description { color: var(--text-2); margin-bottom: 1rem; }
    .section-description p:first-child { margin-top: 0; }
    .rotated-slant { --slant-px: 25px; }
    .rotated-slant table { border-collapse: separate; border-spacing: 0; }
    .rotated-slant td { border: 1px solid var(--border); border-left: none; min-width: 60px; padding: 3px 8px; text-align: center; }
    .rotated-slant td:first-child { border-left: 1px solid var(--border); }
    .rotated-slant thead th { border: 1px solid var(--border); border-top: none; border-left: none; border-right: none; background: transparent; padding: 3px 8px 6px; white-space: nowrap; vertical-align: bottom; text-align: center; min-width: 2rem; position: relative; overflow: visible; }
    .rotated-slant thead th .cell-bg { position: absolute; top: 0; left: 0; bottom: 0; width: calc(100% + var(--slant-px)); border-top: 1px solid var(--border-2); clip-path: polygon(var(--slant-px) 0%, 100% 0%, calc(100% - var(--slant-px)) 100%, 0% 100%); z-index: 0; }
    .rotated-slant thead th:nth-child(odd) .cell-bg { background: var(--cell-odd); }
    .rotated-slant thead th:nth-child(even) .cell-bg { background: var(--cell-even); }
    .rotated-slant thead th span { position: relative; z-index: 2; }
    .rotated-slant thead th:first-child::before { display: none; }
    .rotated-slant thead th::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 1px; background: var(--border-2); transform: rotate(10deg); transform-origin: bottom center; z-index: 1; }
    .rotated-slant thead th:last-child::after { content: ''; position: absolute; right: 0; top: 0; bottom: 0; width: 1px; background: var(--border-2); transform: rotate(10deg); transform-origin: bottom center; z-index: 1; }
    .rotated-slant th.rotated-header span { writing-mode: vertical-rl; transform: rotate(190deg); display: inline-block; }
    .toolbar { display: flex; align-items: center; gap: 1rem; margin-bottom: 1.5rem; padding: 0.5rem 0.75rem; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; flex-wrap: wrap; }
    .toolbar-label { font-size: 0.8rem; color: var(--text-muted); white-space: nowrap; }
    .swatch-group { display: flex; gap: 0.375rem; align-items: center; flex-wrap: wrap; }
    .swatch-btn { display: flex; gap: 2px; padding: 3px 5px; border: 2px solid transparent; border-radius: 6px; cursor: pointer; background: transparent; }
    .swatch-btn.active { border-color: var(--text); }
    .swatch-btn:hover:not(.active) { border-color: var(--text-muted); }
    .swatch-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
    .dark-toggle { padding: 0.25rem 0.625rem; border: 1px solid var(--border); border-radius: 6px; cursor: pointer; background: var(--surface-2); color: var(--text); font-size: 0.8rem; margin-left: auto; white-space: nowrap; }
    .dark-toggle:hover { background: var(--surface-hover); }
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

def _anti_fouc_js(dark_mode: bool | str, switcher: bool) -> str:
    """Return inline script that sets data-theme before CSS loads to prevent flash."""
    if dark_mode is True:
        return "(function(){ document.documentElement.setAttribute('data-theme', 'dark'); })();"
    if dark_mode is False:
        return "(function(){ document.documentElement.setAttribute('data-theme', 'light'); })();"
    # "auto"
    if switcher:
        return (
            "(function(){"
            "try {"
            "var dm = localStorage.getItem('abench_dark');"
            "var dark = dm === '1' || (dm === null && window.matchMedia('(prefers-color-scheme: dark)').matches);"
            "if (dark) document.documentElement.setAttribute('data-theme', 'dark');"
            "} catch(e) {}"
            "})();"
        )
    return (
        "(function(){"
        "if (window.matchMedia('(prefers-color-scheme: dark)').matches)"
        " document.documentElement.setAttribute('data-theme', 'dark');"
        "})();"
    )


def _theme_js(palette: str, dark_mode: bool | str, switcher: bool) -> str:
    """Return the chart-lifecycle + theme JS block."""
    themes_json = json.dumps(THEMES)

    if dark_mode is True:
        dark_init = "  var _darkMode = true;"
    elif dark_mode is False:
        dark_init = "  var _darkMode = false;"
    elif switcher:
        dark_init = (
            "  var _darkMode = (function() {\n"
            "    try {\n"
            "      var v = localStorage.getItem('abench_dark');\n"
            "      return v === '1' || (v === null && window.matchMedia('(prefers-color-scheme: dark)').matches);\n"
            "    } catch(e) { return false; }\n"
            "  })();"
        )
    else:
        dark_init = "  var _darkMode = window.matchMedia('(prefers-color-scheme: dark)').matches;"

    if switcher:
        theme_init = (
            "  var _currentTheme = (function() {\n"
            f"    try {{ return localStorage.getItem('abench_theme') || '{palette}'; }} catch(e) {{ return '{palette}'; }}\n"
            "  })();"
        )
    else:
        theme_init = f"  var _currentTheme = '{palette}';"

    core = f"""
  var _THEMES = {themes_json};
{theme_init}
{dark_init}
  var _charts = [];
  function _themeColor(idx) {{
    var p = _THEMES[_currentTheme] || _THEMES.tableau;
    return p[idx % p.length];
  }}
  function _applyChartDefaults() {{
    if (_darkMode) {{
      Chart.defaults.color = '#aaa';
      Chart.defaults.borderColor = 'rgba(255,255,255,0.12)';
    }} else {{
      Chart.defaults.color = '#555';
      Chart.defaults.borderColor = 'rgba(0,0,0,0.1)';
    }}
  }}
  function _resolveConfig(config) {{
    var cfg = JSON.parse(JSON.stringify(config));
    if (cfg.data && cfg.data.datasets) {{
      cfg.data.datasets.forEach(function(ds) {{
        if (typeof ds._colorIdx === 'number') {{
          var c = _themeColor(ds._colorIdx);
          ds.backgroundColor = c;
          ds.borderColor = c;
          delete ds._colorIdx;
        }}
      }});
    }}
    return cfg;
  }}
  function _createChart(id, config) {{
    _applyChartDefaults();
    var cfg = _resolveConfig(config);
    var chart = new Chart(document.getElementById(id), cfg);
    _charts.push({{id: id, config: config, chart: chart}});
  }}"""

    if not switcher:
        return core

    interactive = """
  function _updateSwatches() {
    document.querySelectorAll('[data-color-idx]').forEach(function(el) {
      el.style.background = _themeColor(parseInt(el.getAttribute('data-color-idx'), 10));
    });
  }
  function _updateToolbar() {
    document.querySelectorAll('.swatch-btn').forEach(function(btn) {
      btn.classList.toggle('active', btn.getAttribute('data-theme-id') === _currentTheme);
    });
    var dtBtn = document.getElementById('dark-toggle-btn');
    if (dtBtn) dtBtn.textContent = _darkMode ? 'Light mode' : 'Dark mode';
    document.documentElement.setAttribute('data-theme', _darkMode ? 'dark' : 'light');
  }
  function _updateAll() {
    _updateToolbar();
    _updateSwatches();
    _applyChartDefaults();
    _charts.forEach(function(entry) {
      entry.chart.destroy();
      var cfg = _resolveConfig(entry.config);
      entry.chart = new Chart(document.getElementById(entry.id), cfg);
    });
  }
  function _toggleDark() {
    _darkMode = !_darkMode;
    try { localStorage.setItem('abench_dark', _darkMode ? '1' : '0'); } catch(e) {}
    _updateAll();
  }
  function _setTheme(name) {
    _currentTheme = name;
    try { localStorage.setItem('abench_theme', name); } catch(e) {}
    _updateAll();
  }"""
    return core + interactive


def _toolbar_html(active_palette: str) -> str:
    swatch_btns = ""
    for tid, colors in THEMES.items():
        dots = "".join(
            f'<span class="swatch-dot" style="background:{c}"></span>'
            for c in colors[:5]
        )
        label = _THEME_LABELS.get(tid, tid)
        active_cls = ' class="swatch-btn active"' if tid == active_palette else ' class="swatch-btn"'
        swatch_btns += (
            f'<button{active_cls} data-theme-id="{tid}" '
            f'title="{label}" onclick="_setTheme(\'{tid}\')">{dots}</button>'
        )
    return (
        f'<div class="toolbar">'
        f'<span class="toolbar-label">Theme</span>'
        f'<div class="swatch-group">{swatch_btns}</div>'
        f'<button class="dark-toggle" id="dark-toggle-btn" onclick="_toggleDark()">Dark mode</button>'
        f'</div>'
    )


def html_shell(
    sections: list[str],
    scripts: list[str],
    title: str = "abench_speckz report",
    *,
    palette: str = "tableau",
    dark_mode: bool | str = "auto",
    switcher: bool = False,
) -> str:
    """Render the full HTML document around pre-built section markup and Chart.js init scripts."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body_sections = "".join(sections)
    chart_inits = "".join(scripts)
    anti_fouc = _anti_fouc_js(dark_mode, switcher)
    theme_script = _theme_js(palette, dark_mode, switcher)
    toolbar = _toolbar_html(palette) if switcher else ""

    # Apply swatch colors once on load (interactive mode re-applies on theme change).
    # In static mode we still need to paint the legend dot swatches.
    if switcher:
        post_init = "  _updateToolbar();\n  _updateSwatches();\n"
    else:
        post_init = (
            "  document.documentElement.setAttribute('data-theme', _darkMode ? 'dark' : 'light');\n"
            "  document.querySelectorAll('[data-color-idx]').forEach(function(el) {\n"
            "    el.style.background = _themeColor(parseInt(el.getAttribute('data-color-idx'), 10));\n"
            "  });\n"
        )

    toolbar_html = f"  {toolbar}\n" if toolbar else ""
    return (
        f"<!DOCTYPE html>\n"
        f'<html lang="en">\n'
        f"<head>\n"
        f'  <meta charset="utf-8">\n'
        f'  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"  <title>{title}</title>\n"
        f"  <script>{anti_fouc}</script>\n"
        f'  <script src="{CHART_JS_CDN}"></script>\n'
        f"  <style>{_CSS}  </style>\n"
        f"</head>\n"
        f"<body>\n"
        f"  <h1>{title}</h1>\n"
        f'  <p class="meta">Generated: {timestamp}</p>\n'
        f"{toolbar_html}"
        f"{body_sections}\n"
        f"  <script>\n"
        f"{theme_script}\n"
        f"{chart_inits}\n"
        f"{post_init}"
        f"  </script>\n"
        f"  <script>{_SLANT_SYNC_JS}  </script>\n"
        f"</body>\n"
        f"</html>"
    )


def safe_id(s: str) -> str:
    """Strip a string down to characters safe in an HTML id."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", s)


def collapsible(label: str, content: str, *, open: bool = False) -> str:
    open_attr = " open" if open else ""
    return f'<details{open_attr}><summary>{label}</summary><div class="details-body">{content}</div></details>'


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


def resolve_var_value(pretty_map: dict, var: str, value: str) -> str:
    return prettynames.resolve_var_value(pretty_map, var, value)


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
        "<tr>" + "".join(f"<td>{fmt_value(r.get(h))}</td>" for h in headers) + "</tr>" for r in rows
    )
    return (
        f'<div class="table-wrap"><table>'
        f"<thead><tr>{header_cells}</tr></thead>"
        f"<tbody>{body_rows}</tbody>"
        f"</table></div>"
    )


def escape_for_js_template_literal(s: str) -> str:
    s = s.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    # Prevent </script> inside the template literal from closing the outer <script> tag.
    # <\/script> is valid JS (\ before / is a no-op) but the HTML parser won't match it.
    s = re.sub(r"</script", r"<\\/script", s, flags=re.IGNORECASE)
    return s


def split_group_by_vars(
    rows: list[dict],
    group_by: list[str],
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
        f"<b>{resolve_metric(pretty_map, k)}</b>&nbsp;=&nbsp;{resolve_var_value(pretty_map, k, v)}"
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
    for i, s in enumerate(series_meta):
        swatch = (
            f'<span style="display:inline-block;width:13px;height:13px;'
            f'border-radius:3px;background:{s["color"]};vertical-align:middle" '
            f'data-color-idx="{i}"></span>'
        )
        cells = f'<td style="text-align:center;border-right:none">{swatch}</td>'
        if multi_metric:
            cells += f"<td>{resolve_metric(pretty_map, s['metric'])}</td>"
        for k in varying_keys:
            raw_val = s['vals'].get(k, '')
            cells += f"<td>{resolve_var_value(pretty_map, k, raw_val)}</td>"
        body_rows += f"<tr>{cells}</tr>"
    return (
        f'<div class="legend-wrap table-wrap rotated-slant">'
        f"<table><thead><tr>{header_cells}</tr></thead>"
        f"<tbody>{body_rows}</tbody>"
        f"</table></div>"
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
