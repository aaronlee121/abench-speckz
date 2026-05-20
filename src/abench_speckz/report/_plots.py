"""Build the per-plot artifacts that section rendering and legacy generate_html consume."""

import json
from pathlib import Path

from abench_speckz.expr import eval_expr, extract_names, is_expr
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

_EXPR_STAT_COLS = ("mean", "stddev", "p50", "p95", "p99", "ci95_low", "ci95_high")


def _resolve_y_axes(
    y_list: list[str], all_vars_set: set[str]
) -> tuple[list[str], dict[str, str]]:
    """Return (query_metrics, expr_map) separating plain metric names from expressions.

    expr_map maps base_metric_name → expression string for any y entry that is an
    arithmetic expression.  Plain entries pass through unchanged.
    """
    from abench_speckz.model import RunError

    query_metrics: list[str] = []
    expr_map: dict[str, str] = {}
    for yi in y_list:
        if not is_expr(yi):
            query_metrics.append(yi)
            continue
        names = extract_names(yi)
        metric_names = names - all_vars_set
        if len(metric_names) != 1:
            raise RunError(
                f"y expression {yi!r} must reference exactly one metric name "
                f"(found: {sorted(metric_names) if metric_names else 'none'})"
            )
        base = next(iter(metric_names))
        query_metrics.append(base)
        expr_map[base] = yi
    return query_metrics, expr_map


def _apply_y_exprs(
    rows: list[dict], expr_map: dict[str, str], all_vars_set: set[str]
) -> list[dict]:
    """Rename expression metrics and apply the expression to every stat column."""
    result: list[dict] = []
    for r in rows:
        metric = r.get("metric")
        if metric not in expr_map:
            result.append(r)
            continue
        expr = expr_map[metric]
        var_ns: dict[str, float] = {}
        for k, v in r.items():
            if k in all_vars_set and v is not None:
                try:
                    var_ns[k] = float(v)
                except (TypeError, ValueError):
                    pass
        new_row = dict(r)
        new_row["metric"] = expr
        for col in _EXPR_STAT_COLS:
            raw = r.get(col)
            if raw is None:
                continue
            ns = {metric: float(raw), **var_ns}
            try:
                new_row[col] = eval_expr(expr, ns)
            except (ValueError, ZeroDivisionError, OverflowError):
                new_row[col] = None
        result.append(new_row)
    return result


def build_plot_artifacts(plot, all_vars: list[str], results_dir: Path, pretty_map: dict):
    """Compute everything needed to render a plot, independent of where it lands in the page.

    Returns ``None`` for unknown plot types so the caller can skip them.
    """
    from abench_speckz import query

    gb_keys = resolve_group_by(list(plot.group_by or []), all_vars)
    all_vars_set = set(all_vars)

    if plot.type == "scatter":
        group_by = gb_keys
        metrics, y_expr_map = _resolve_y_axes([plot.x] + plot.y, all_vars_set)
    else:
        if is_expr(plot.x):
            x_vars = sorted(extract_names(plot.x))
            group_by = x_vars + gb_keys
        else:
            group_by = [plot.x] + gb_keys
        metrics, y_expr_map = _resolve_y_axes(plot.y, all_vars_set)

    rows = query.compute_rows(results_dir, group_by=group_by, metrics=metrics)
    if y_expr_map:
        rows = _apply_y_exprs(rows, y_expr_map, all_vars_set)
    static_vars, varying_keys = split_group_by_vars(rows, gb_keys)
    multi_metric = len(plot.y) > 1

    if plot.type == "scatter":
        chart_config, series_meta = build_scatter_config(
            plot,
            rows,
            pretty_map,
            group_by=gb_keys,
            varying_keys=varying_keys,
        )
    elif plot.type in ("bar", "stacked-bar"):
        chart_config, series_meta = build_bar_or_line_config(
            plot,
            rows,
            pretty_map,
            chart_type="bar",
            stacked=(plot.type == "stacked-bar"),
            group_by=gb_keys,
            varying_keys=varying_keys,
            smooth=False,
        )
    elif plot.type == "line":
        chart_config, series_meta = build_bar_or_line_config(
            plot,
            rows,
            pretty_map,
            chart_type="line",
            stacked=False,
            group_by=gb_keys,
            varying_keys=varying_keys,
            smooth=getattr(plot, "smooth", False),
        )
    else:
        return None

    show_fixed = getattr(plot, "fixed_vars", True)
    use_series_label = bool(getattr(plot, "series_label", None))
    if varying_keys and len(series_meta) == 1:
        single = series_meta[0]
        extra = {k: single["vals"][k] for k in varying_keys if k in single["vals"]}
        if multi_metric:
            extra["metric"] = single["metric"]
        merged_static = {**static_vars, **extra}
        fixed_html = fixed_vars_html(merged_static, pretty_map) if show_fixed else ""
        legend_html = ""
        chart_config["options"]["plugins"]["legend"] = {"display": False}
    elif varying_keys:
        fixed_html = fixed_vars_html(static_vars, pretty_map) if show_fixed else ""
        if use_series_label:
            legend_html = ""
            # Chart.js built-in legend shows the template-formatted labels inline
        else:
            legend_html = legend_table_html(series_meta, varying_keys, pretty_map, multi_metric)
            chart_config["options"]["plugins"]["legend"] = {"display": False}
    else:
        fixed_html = fixed_vars_html(static_vars, pretty_map) if show_fixed else ""
        legend_html = legend_table_html(series_meta, varying_keys, pretty_map, multi_metric)

    return {
        "plot": plot,
        "fixed_html": fixed_html,
        "legend_html": legend_html,
        "chart_config": chart_config,
        "table_html_inner": table_html(rows, list(group_by) + ["metric"] + list(STAT_COLS) if rows else []),
        "title": plot.title or plot.id,
    }


def render_plot_block(art: dict, occurrence: int) -> tuple[str, str]:
    """Return (inner_html, init_script) for one occurrence of a plot."""
    canvas_id = f"chart-{safe_id(art['plot'].id)}-{occurrence}"
    title_html = f'<h3 class="plot-title">{art["plot"].title}</h3>\n    ' if art["plot"].title else ""
    chart_inner = f'{art["legend_html"]}<div class="chart-wrap"><canvas id="{canvas_id}"></canvas></div>'
    chart_html = collapsible("Chart", chart_inner, open=True)
    data_html = collapsible("Data", art["table_html_inner"])
    inner = f"{title_html}{art['fixed_html']}\n    {chart_html}\n    {data_html}"
    script = f"\n  new Chart(document.getElementById('{canvas_id}'), {json.dumps(art['chart_config'])});"
    return inner, script
