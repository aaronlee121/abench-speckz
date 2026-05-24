"""Top-level report entry points: render aggregate rows or a PlotsDoc to a self-contained HTML file."""

import dataclasses
import json
import os
from pathlib import Path
from typing import Any

from abench_speckz import interpolate, store
from abench_speckz.model import RunError
from abench_speckz.report._html import (
    COLORS,
    STAT_COLS,
    collapsible,
    comparison_table_html,
    fixed_vars_html,
    html_shell,
    render_markdown,
    resolve_metric,
    safe_id,
    split_group_by_vars,
    table_html,
)
from abench_speckz.report._plots import build_plot_artifacts, render_plot_block
from abench_speckz.report._runs import discover_run_reports, runs_section


def _gather_text_vars(results_dir: Path) -> dict[str, str]:
    """Return combo vars with exactly one unique value across all results (safe to embed in text)."""
    unique: dict[str, set] = {}
    for fname in ("aggregates.jsonl", "runs.jsonl"):
        path = results_dir / fname
        if not path.exists() or path.stat().st_size == 0:
            continue
        with path.open() as f:
            for line in f:
                try:
                    row = json.loads(line)
                    vars_ = row.get("vars", {})
                    if isinstance(vars_, dict):
                        for k, v in vars_.items():
                            unique.setdefault(k, set()).add(str(v))
                except json.JSONDecodeError:
                    pass
        break
    return {k: next(iter(v)) for k, v in unique.items() if len(v) == 1}


def _interp(s: str | None, vars_: dict[str, str], env: dict[str, str]) -> str | None:
    if s is None:
        return None
    try:
        return interpolate.resolve_string(s, vars_, env=env)
    except Exception as e:
        raise RunError(f"text interpolation failed: {e}") from e


def _interpolate_doc(doc, vars_: dict[str, str], env: dict[str, str]):
    """Return a new PlotsDoc with ${var}/${env:VAR} substituted in all text fields."""
    new_plots = [
        dataclasses.replace(
            p,
            title=_interp(p.title, vars_, env),
            x_title=_interp(p.x_title, vars_, env),
            y_title=_interp(p.y_title, vars_, env),
        )
        for p in doc.plots
    ]
    new_sections = []
    for sec in doc.sections:
        new_blocks = [
            dataclasses.replace(blk, value=_interp(blk.value, vars_, env))
            if blk.kind in ("text", "html")
            else blk
            for blk in sec.blocks
        ]
        new_sections.append(
            dataclasses.replace(
                sec,
                title=_interp(sec.title, vars_, env),
                description=_interp(sec.description, vars_, env),
                blocks=new_blocks,
            )
        )
    return dataclasses.replace(
        doc,
        plots=new_plots,
        sections=new_sections,
        report_title=_interp(doc.report_title, vars_, env),
        report_description=_interp(doc.report_description, vars_, env),
    )


def generate_html(
    rows: list[dict[str, Any]],
    path: Path,
    group_by: list[str],
    pretty_map: dict,
    *,
    results_dir: Path | None = None,
    run_reports_mode: str = "embedded",
) -> None:
    """Legacy entry point: one section per metric, with a stat-comparison bar chart."""
    sections: list[str] = []
    chart_scripts: list[str] = []

    for metric, metric_rows in _group_rows_by_metric(rows).items():
        section, script = _render_metric_section(
            metric,
            metric_rows,
            group_by,
            pretty_map,
        )
        sections.append(section)
        chart_scripts.append(script)

    _append_run_reports_section(
        sections,
        chart_scripts,
        results_dir=results_dir,
        run_reports_mode=run_reports_mode,
    )

    path.write_text(html_shell(sections, chart_scripts))


def generate_plots_html(
    results_dir: Path,
    doc,
    output_path: Path,
    pretty_map: dict,
    *,
    run_reports_mode: str = "embedded",
    where: list[str] | None = None,
    require_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    baseline_dir: Path | None = None,
    regression_threshold: float = 0.05,
) -> None:
    """Render a PlotsDoc (declarative plots ± editorial sections) to HTML."""
    from abench_speckz import query

    all_vars = query.var_names(results_dir)
    text_vars = _gather_text_vars(results_dir)
    doc = _interpolate_doc(doc, text_vars, dict(os.environ))

    sections: list[str] = []
    scripts: list[str] = []

    artifacts_by_id: dict[str, dict] = {}
    for plot in doc.plots:
        art = build_plot_artifacts(
            plot,
            all_vars,
            results_dir,
            pretty_map,
            where=where,
            require_tags=require_tags,
            exclude_tags=exclude_tags,
            baseline_dir=baseline_dir,
            regression_threshold=regression_threshold,
        )
        if art is not None:
            artifacts_by_id[plot.id] = art

    if baseline_dir is not None:
        _prepend_regression_summary(
            sections, scripts, results_dir, baseline_dir, all_vars, pretty_map, regression_threshold
        )

    if doc.sections:
        _render_sectioned(doc, artifacts_by_id, sections, scripts, results_dir, pretty_map)
    else:
        _render_plots_only(doc, artifacts_by_id, sections, scripts)

    _append_run_reports_section(
        sections,
        scripts,
        results_dir=results_dir,
        run_reports_mode=run_reports_mode,
    )

    title = doc.report_title or "abench_speckz report"
    output_path.write_text(
        html_shell(
            sections,
            scripts,
            title=title,
            palette=doc.theme.palette,
            dark_mode=doc.theme.dark_mode,
            switcher=doc.theme.switcher,
        )
    )


def _prepend_regression_summary(
    sections: list[str],
    scripts: list[str],
    results_dir: Path,
    baseline_dir: Path,
    all_vars: list[str],
    pretty_map: dict,
    threshold: float,
) -> None:
    """Build and prepend the Regression Summary section to the report."""
    from abench_speckz import query
    from abench_speckz.report._baseline import compare_rows, load_higher_is_better, sort_summary

    current_rows = query.compute_rows(results_dir, group_by=all_vars)
    baseline_rows = query.compute_rows(baseline_dir, group_by=all_vars)
    hib = load_higher_is_better(results_dir)

    comparisons = compare_rows(
        current_rows, baseline_rows, all_vars, higher_is_better=hib, threshold=threshold
    )
    comparisons = sort_summary(comparisons, hib)

    if not comparisons:
        return

    n_reg = sum(1 for c in comparisons if c.status == "regressed")
    n_imp = sum(1 for c in comparisons if c.status == "improved")
    summary_line = (
        f"{n_reg} regression{'s' if n_reg != 1 else ''}, {n_imp} improvement{'s' if n_imp != 1 else ''}"
    )
    table = comparison_table_html(comparisons, all_vars, pretty_map)
    section_html = (
        f'\n  <section class="regression-summary">\n'
        f"    <h2>Regression Summary</h2>\n"
        f'    <p class="meta">{summary_line}</p>\n'
        f"    {table}\n"
        f"  </section>"
    )
    sections.insert(0, section_html)


def _group_rows_by_metric(rows: list[dict[str, Any]]) -> dict[str, list[dict]]:
    by_metric: dict[str, list[dict]] = {}
    for row in rows:
        by_metric.setdefault(row["metric"], []).append(row)
    return by_metric


def _render_metric_section(
    metric: str,
    metric_rows: list[dict],
    group_by: list[str],
    pretty_map: dict,
) -> tuple[str, str]:
    """Render one section for ``generate_html``'s per-metric layout."""
    display_name = resolve_metric(pretty_map, metric)
    safe = safe_id(metric)

    static_vars, varying_keys = split_group_by_vars(metric_rows, group_by)
    label_keys = varying_keys if varying_keys else group_by
    labels = (
        [" / ".join(str(r.get(g, "")) for g in label_keys) for r in metric_rows]
        if label_keys
        else ["all"] * len(metric_rows)
    )

    datasets = [
        {
            "label": stat_key,
            "data": [r.get(stat_key) for r in metric_rows],
            "backgroundColor": COLORS[i % len(COLORS)],
            "borderColor": COLORS[i % len(COLORS)],
            "borderWidth": 1,
            "_colorIdx": i,
        }
        for i, stat_key in enumerate(["mean", "p50", "p95", "p99"])
    ]

    config = {
        "type": "bar",
        "data": {"labels": labels, "datasets": datasets},
        "options": {
            "responsive": True,
            "plugins": {"legend": {"position": "top"}},
            "scales": {"y": {"beginAtZero": False}},
        },
    }
    all_headers = list(group_by) + list(STAT_COLS)
    chart_html = collapsible(
        "Chart",
        f'<div class="chart-wrap"><canvas id="chart-{safe}"></canvas></div>',
        open=True,
    )
    data_html = collapsible("Data", table_html(metric_rows, all_headers))
    section = (
        f"\n  <section>\n"
        f"    <h2>{display_name}</h2>\n"
        f"    {fixed_vars_html(static_vars, pretty_map)}\n"
        f"    {chart_html}\n"
        f"    {data_html}\n"
        f"  </section>"
    )
    script = f"\n  _createChart('chart-{safe}', {json.dumps(config)});"
    return section, script


def _render_sectioned(doc, artifacts_by_id, sections, scripts, results_dir, pretty_map) -> None:
    """Editorial-section layout: blocks reference plots by id; text/html blocks interleave."""
    from abench_speckz.report._headline import render_headline_block
    from abench_speckz.report._kpi import render_kpi_block

    if doc.report_description:
        sections.append(f'\n  <div class="report-intro">{render_markdown(doc.report_description)}</div>')

    plot_occurrences: dict[str, int] = {}
    for sec in doc.sections:
        body = ""
        if sec.description:
            body += f'<div class="section-description">{render_markdown(sec.description)}</div>\n    '
        for block in sec.blocks:
            if block.kind == "text":
                body += render_markdown(block.value) + "\n    "
            elif block.kind in ("html", "include_html"):
                body += block.value + "\n    "
            elif block.kind == "kpi":
                body += render_kpi_block(block.data, results_dir, pretty_map) + "\n    "
            elif block.kind == "headline":
                body += render_headline_block(block.data, results_dir, pretty_map) + "\n    "
            elif block.kind == "plot_id":
                art = artifacts_by_id.get(block.value)
                if art is None:
                    body += f'<p class="meta">(plot {block.value!r} unavailable)</p>\n    '
                    continue
                occ = plot_occurrences.get(block.value, 0)
                inner, script = render_plot_block(art, occ)
                plot_occurrences[block.value] = occ + 1
                body += inner + "\n    "
                scripts.append(script)
        sections.append(f"\n  <section>\n    <h2>{sec.title}</h2>\n    {body}\n  </section>")


def _render_plots_only(doc, artifacts_by_id, sections, scripts) -> None:
    """No editorial sections: emit one section per plot in declaration order."""
    for plot in doc.plots:
        art = artifacts_by_id.get(plot.id)
        if art is None:
            continue
        inner, script = render_plot_block(art, 0)
        scripts.append(script)
        sections.append(
            f"\n  <section>\n"
            f"    <h2>{art['title']}</h2>\n"
            f'    <p class="meta">type: {plot.type}</p>\n'
            f"    {inner}\n"
            f"  </section>"
        )


def _append_run_reports_section(
    sections: list[str],
    scripts: list[str],
    *,
    results_dir: Path | None,
    run_reports_mode: str,
) -> None:
    """Add a 'Per-run reports/links' section if there are run-level HTML reports or grafana_links."""
    if results_dir is None or run_reports_mode == "none":
        return
    run_reports = discover_run_reports(results_dir)
    runs = store.read_runs(results_dir)
    rpt_html, rpt_js = runs_section(run_reports, runs, embed=(run_reports_mode == "embedded"))
    if rpt_html:
        sections.append(rpt_html)
        scripts.append(rpt_js)
