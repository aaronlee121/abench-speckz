"""Discover per-run HTML reports under raw/ and render the per-run report section."""

import json
from pathlib import Path

from abench_speckz.report._html import (
    escape_for_js_template_literal,
    resolve_metric,
    resolve_var_value,
    safe_id,
)


def discover_run_reports(results_dir: Path) -> dict[str, list[tuple[str, Path]]]:
    """Return ``{run_id: [(report_name, path)]}`` for ``raw/{run_id}_*.html`` files."""
    raw_dir = results_dir / "raw"
    out: dict[str, list[tuple[str, Path]]] = {}
    if not raw_dir.is_dir():
        return out
    for p in sorted(raw_dir.glob("*.html")):
        stem = p.stem  # e.g. "550e8400-e29b-41d4-a716-446655440000_flamegraph"
        if len(stem) > 37 and stem[36] == "_":
            run_id, report_name = stem[:36], stem[37:]
            out.setdefault(run_id, []).append((report_name, p))
    return out


def _var_table_html(vars_: dict, pretty_map: dict) -> str:
    """Render a compact Variable/Value table for a run group heading."""
    if not vars_:
        return ""
    rows = "".join(
        f"<tr><td>{resolve_metric(pretty_map, k)}</td>"
        f"<td>{resolve_var_value(pretty_map, k, str(v))}</td></tr>"
        for k, v in sorted(vars_.items())
    )
    table = (
        f'<div class="var-table-wrap">'
        f'<table class="var-table">'
        f"<thead><tr><th>Variable</th><th>Value</th></tr></thead>"
        f"<tbody>{rows}</tbody>"
        f"</table></div>"
    )
    return f"<details><summary>Variables</summary>{table}</details>\n"


def runs_section(
    run_reports: dict[str, list[tuple[str, Path]]],
    runs: list[dict],
    *,
    embed: bool,
    pretty_map: dict | None = None,
) -> tuple[str, str]:
    """Return ``(section_html, script_js)``. Both empty when nothing to show."""
    eligible = [
        r
        for r in runs
        if (r.get("run_id") in run_reports or r.get("grafana_links"))
        and r.get("exit_code") == 0
        and "warmup" not in (r.get("tags") or [])
    ]
    if not eligible:
        return "", ""

    groups = _group_by_config_hash(eligible)
    sorted_groups = sorted(
        groups.values(),
        key=lambda rlist: sorted((rlist[0].get("vars") or {}).items()),
    )

    body_html = ""
    js_data_lines: list[str] = []

    pm = pretty_map or {}
    for group_runs in sorted_groups:
        for run in group_runs:
            body_html += _render_run_details(
                run=run,
                reports=run_reports.get(run["run_id"]) or [],
                embed=embed,
                js_data_lines=js_data_lines,
                pretty_map=pm,
            )

    has_reports = any(r.get("run_id") in run_reports for r in eligible)
    section_title = "Per-run reports" if has_reports else "Per-run links"
    section_html = f"\n  <section>\n    <h2>{section_title}</h2>\n    {body_html}\n  </section>"
    script_js = _build_loader_script(js_data_lines) if embed and js_data_lines else ""
    return section_html, script_js


def _grafana_links_html(links: list[dict]) -> str:
    """Render a row of Grafana dashboard link buttons, skipping entries with no URL."""
    valid = [lnk for lnk in links if lnk.get("url")]
    if not valid:
        return ""
    anchors = "".join(
        f'<a href="{lnk["url"]}" target="_blank" rel="noopener" class="grafana-link">{lnk["label"]}</a>'
        for lnk in valid
    )
    return f'<div class="grafana-links">{anchors}</div>\n'


def _group_by_config_hash(runs: list[dict]) -> dict[str, list[dict]]:
    """Bucket runs by their config_hash, preserving first-seen order."""
    groups: dict[str, list[dict]] = {}
    for run in runs:
        groups.setdefault(run.get("config_hash", ""), []).append(run)
    return groups


def _render_run_details(
    *,
    run: dict,
    reports: list[tuple[str, Path]],
    embed: bool,
    js_data_lines: list[str],
    pretty_map: dict | None = None,
) -> str:
    """Render one collapsible block for a single run (with nested per-report blocks)."""
    run_id = run.get("run_id", "")
    started_at = run.get("started_at", "")
    short_id = run_id[:8] if run_id else "?"
    names = [name for name, _ in reports]

    inner_html = _var_table_html(run.get("vars") or {}, pretty_map or {})
    inner_html += _grafana_links_html(run.get("grafana_links") or [])
    for name, path in reports:
        iframe_id = "rpt-" + run_id.replace("-", "_") + "_" + safe_id(name)
        if embed:
            content = path.read_text(encoding="utf-8", errors="replace")
            js_data_lines.append(
                f'  _runReports["{run_id}/{name}"] = `{escape_for_js_template_literal(content)}`;'
            )
            inner_html += (
                f"<details><summary>{name}</summary>"
                f'<iframe id="{iframe_id}" class="run-rpt"'
                f' sandbox="allow-scripts allow-same-origin"></iframe>'
                f"</details>\n"
            )
        else:
            href = f"raw/{run_id}_{name}.html"
            inner_html += (
                f'<details><summary>{name}</summary><a href="{href}" target="_blank">{href}</a></details>\n'
            )

    summary = f"Run {short_id}… ({started_at})" if started_at else f"Run {short_id}…"
    if embed:
        toggle_attr = f' ontoggle="loadRunReport(this,{json.dumps(run_id)},{json.dumps(names)})"'
    else:
        toggle_attr = ""
    return f"<details{toggle_attr}><summary>{summary}</summary>{inner_html}</details>\n"


def _build_loader_script(js_data_lines: list[str]) -> str:
    """Lazy iframe loader used by embedded run reports (filled on first <details> open)."""
    data_block = "\n".join(js_data_lines)
    return (
        "\n  const _runReports = {};\n"
        + data_block
        + """
  function loadRunReport(el, runId, names) {
    if (!el.open) return;
    names.forEach(function(name) {
      var iframe = document.getElementById('rpt-' + runId.replace(/-/g,'_') + '_' + name);
      if (iframe && !iframe.srcdoc)
        iframe.srcdoc = _runReports[runId + '/' + name] || '<p>Not available.</p>';
    });
  }"""
    )
