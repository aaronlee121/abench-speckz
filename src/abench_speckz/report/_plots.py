"""Build the per-plot artifacts that section rendering and legacy generate_html consume."""
import json
from pathlib import Path

from abench_speckz.report._charts import build_bar_or_line_config, build_scatter_config
from abench_speckz.report._html import (
    STAT_COLS,
    collapsible,
    fixed_vars_html,
    legend_table_html,
    resolve_group_by,
    safe_id,
    split_group_by_vars,
    table_html,
)


def build_plot_artifacts(plot, all_vars: list[str], results_dir: Path, pretty_map: dict):
    """Compute everything needed to render a plot, independent of where it lands in the page.

    Returns ``None`` for unknown plot types so the caller can skip them.
    """
    from abench_speckz import query

    gb_keys = resolve_group_by(list(plot.group_by or []), all_vars)

    if plot.type == "scatter":
        group_by = gb_keys
        metrics = [plot.x] + plot.y
    else:
        group_by = [plot.x] + gb_keys
        metrics = plot.y

    rows = query.compute_rows(results_dir, group_by=group_by, metrics=metrics)
    static_vars, varying_keys = split_group_by_vars(rows, gb_keys)
    multi_metric = len(plot.y) > 1

    if plot.type == "scatter":
        chart_config, series_meta = build_scatter_config(
            plot, rows, pretty_map, group_by=gb_keys, varying_keys=varying_keys,
        )
    elif plot.type in ("bar", "stacked-bar"):
        chart_config, series_meta = build_bar_or_line_config(
            plot, rows, pretty_map,
            chart_type="bar", stacked=(plot.type == "stacked-bar"),
            group_by=gb_keys, varying_keys=varying_keys,
        )
    elif plot.type == "line":
        chart_config, series_meta = build_bar_or_line_config(
            plot, rows, pretty_map, chart_type="line", stacked=False,
            group_by=gb_keys, varying_keys=varying_keys,
        )
    else:
        return None

    if varying_keys:
        chart_config["options"]["plugins"]["legend"] = {"display": False}

    return {
        "plot": plot,
        "fixed_html": fixed_vars_html(static_vars, pretty_map),
        "legend_html": legend_table_html(series_meta, varying_keys, pretty_map, multi_metric),
        "chart_config": chart_config,
        "table_html_inner": table_html(rows, list(group_by) + ["metric"] + list(STAT_COLS) if rows else []),
        "title": plot.title or plot.id,
    }


def render_plot_block(art: dict, occurrence: int) -> tuple[str, str]:
    """Return (inner_html, init_script) for one occurrence of a plot."""
    canvas_id = f"chart-{safe_id(art['plot'].id)}-{occurrence}"
    chart_inner = (
        f"{art['legend_html']}"
        f'<div class="chart-wrap"><canvas id="{canvas_id}"></canvas></div>'
    )
    chart_html = collapsible("Chart", chart_inner, open=True)
    data_html = collapsible("Data", art["table_html_inner"])
    inner = f"{art['fixed_html']}\n    {chart_html}\n    {data_html}"
    script = (
        f"\n  new Chart(document.getElementById('{canvas_id}'), "
        f"{json.dumps(art['chart_config'])});"
    )
    return inner, script
