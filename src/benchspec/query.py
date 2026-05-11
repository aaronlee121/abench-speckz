"""DuckDB-backed queries over aggregates.jsonl (fast path) or runs.jsonl (when grouping)."""
import json
import sys
from pathlib import Path
from typing import Any

import duckdb

from benchspec import aggregate, prettynames
from benchspec.filters import parse_kv
from benchspec.model import RunError


_AGGREGATE_COLUMNS = ("n", "n_failed", "mean", "stddev", "p50", "p95", "p99", "ci95_low", "ci95_high")


def compute_rows(
    results_dir: Path,
    *,
    group_by: list[str] | None = None,
    where: list[str] | None = None,
    require_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    metrics: list[str] | None = None,
    from_raw: bool = False,
) -> list[dict[str, Any]]:
    """Return aggregate rows for the results dir.

    Reads pre-aggregated stats from ``aggregates.jsonl`` by default, but
    routes through ``runs.jsonl`` whenever the caller groups across
    config_hashes — stddev and percentiles aren't additive across
    pre-aggregated rows, so they have to be recomputed from raw values.
    """
    group_by = group_by or []
    where = where or []
    require_tags = require_tags or []
    exclude_tags = exclude_tags or []
    metrics = metrics or []

    must_recompute = bool(group_by)
    if from_raw or must_recompute:
        return _from_raw(results_dir, group_by, where, require_tags, exclude_tags, metrics)
    return _from_aggregates(results_dir, group_by, where, require_tags, exclude_tags, metrics)


def stats(
    results_dir: Path,
    *,
    group_by: list[str] | None = None,
    where: list[str] | None = None,
    require_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    metrics: list[str] | None = None,
    from_raw: bool = False,
    fmt: str = "table",
    pretty: bool = False,
    report: Path | None = None,
    plots: Path | None = None,
    out=sys.stdout,
) -> None:
    group_by = group_by or []
    rows = compute_rows(
        results_dir,
        group_by=group_by,
        where=where,
        require_tags=require_tags,
        exclude_tags=exclude_tags,
        metrics=metrics,
        from_raw=from_raw,
    )

    if not rows:
        print("# 0 rows", file=out)
        return

    pretty_map = prettynames.load(results_dir / "pretty_names.json") if pretty else {}
    _render(rows, fmt, out, pretty_map=pretty_map)

    if report is not None:
        from benchspec import plots as _plots
        from benchspec import report as _report
        pm = prettynames.load(results_dir / "pretty_names.json")
        if plots is not None:
            specs = _plots.load_plots_file(plots)
        else:
            specs = _plots.discover_from_tool_snapshots(results_dir)
        if specs:
            _report.generate_plots_html(results_dir, specs, report, pm)
        else:
            _report.generate_html(rows, report, group_by, pm)
        print(f"report written to {report}", file=out)


def _from_aggregates(
    results_dir: Path,
    group_by: list[str],
    where_exprs: list[str],
    require_tags: list[str],
    exclude_tags: list[str],
    metrics: list[str],
) -> list[dict[str, Any]]:
    path = results_dir / "aggregates.jsonl"
    if not path.exists() or path.stat().st_size == 0:
        return []

    con = duckdb.connect(":memory:")
    src = f"read_json_auto('{path}', format='newline_delimited')"
    where_clause, params = _build_where(where_exprs, require_tags, exclude_tags, metrics)
    group_cols = [f"(vars->>'{g}')" for g in group_by]

    if group_by:
        select_keys = ", ".join(f"{c} AS {g}" for c, g in zip(group_cols, group_by))
        agg_picks = ", ".join(f"any_value({c}) AS {c}" for c in _AGGREGATE_COLUMNS)
        sql = (
            f"SELECT {select_keys}, metric, {agg_picks} "
            f"FROM {src} "
            f"WHERE {where_clause} "
            f"GROUP BY {', '.join(group_cols)}, metric "
            f"ORDER BY {', '.join(group_cols)}, metric"
        )
    else:
        cols = ", ".join(_AGGREGATE_COLUMNS)
        sql = (
            f"SELECT metric, {cols} FROM {src} WHERE {where_clause} ORDER BY metric"
        )

    res = con.execute(sql, params).fetchall()
    headers = [d[0] for d in con.description]
    return [dict(zip(headers, r)) for r in res]


def _from_raw(
    results_dir: Path,
    group_by: list[str],
    where_exprs: list[str],
    require_tags: list[str],
    exclude_tags: list[str],
    metrics: list[str],
) -> list[dict[str, Any]]:
    from collections import defaultdict

    path = results_dir / "runs.jsonl"
    if not path.exists() or path.stat().st_size == 0:
        return []

    con = duckdb.connect(":memory:")
    src = f"read_json_auto('{path}', format='newline_delimited')"

    base_clauses = ["exit_code = 0", "results IS NOT NULL"]
    base_clauses.append("NOT list_contains(tags, 'warmup')")
    params: list[Any] = []
    for pred in parse_kv(where_exprs):
        base_clauses.append(f"(vars->>'{pred.key}') = ?")
        params.append(pred.value)
    for t in require_tags:
        base_clauses.append("list_contains(tags, ?)")
        params.append(t)
    for t in exclude_tags:
        base_clauses.append("NOT list_contains(tags, ?)")
        params.append(t)

    where_clause = " AND ".join(base_clauses)
    rows_sql = f"SELECT * FROM {src} WHERE {where_clause}"
    raw_rows = con.execute(rows_sql, params).fetchall()
    headers = [d[0] for d in con.description]
    runs = [dict(zip(headers, r)) for r in raw_rows]

    n_failed_by_key: dict[tuple, int] = defaultdict(int)
    failed_sql = (
        f"SELECT vars, tags FROM {src} WHERE exit_code <> 0 "
        f"AND NOT list_contains(tags, 'warmup')"
    )
    for r in con.execute(failed_sql).fetchall():
        d = dict(zip([h[0] for h in con.description], r))
        key = tuple((d.get("vars") or {}).get(g, "") for g in group_by)
        n_failed_by_key[key] += 1

    by_group: dict[tuple, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    by_group_meta: dict[tuple, dict[str, str]] = {}
    for run in runs:
        vars_ = run.get("vars") or {}
        key = tuple(vars_.get(g, "") for g in group_by)
        if key not in by_group_meta:
            by_group_meta[key] = {g: vars_.get(g, "") for g in group_by}
        for metric, value in (run.get("results") or {}).items():
            if metrics and metric not in metrics:
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                by_group[key][metric].append(float(value))

    output_rows: list[dict[str, Any]] = []
    for key, metric_values in by_group.items():
        meta = by_group_meta[key]
        for metric, values in sorted(metric_values.items()):
            if not values:
                continue
            s = aggregate.stats_for(values)
            row: dict[str, Any] = dict(meta)
            row["metric"] = metric
            row["n"] = s["n"]
            row["n_failed"] = n_failed_by_key.get(key, 0)
            for c in ("mean", "stddev", "p50", "p95", "p99", "ci95_low", "ci95_high"):
                row[c] = s[c]
            output_rows.append(row)

    output_rows.sort(key=lambda r: tuple(r.get(g, "") for g in group_by) + (r["metric"],))
    return output_rows


def _build_where(
    where_exprs: list[str],
    require_tags: list[str],
    exclude_tags: list[str],
    metrics: list[str],
) -> tuple[str, list[Any]]:
    clauses: list[str] = ["1=1"]
    params: list[Any] = []
    for pred in parse_kv(where_exprs):
        clauses.append(f"(vars->>'{pred.key}') = ?")
        params.append(pred.value)
    for t in require_tags:
        clauses.append("list_contains(tags, ?)")
        params.append(t)
    for t in exclude_tags:
        clauses.append("NOT list_contains(tags, ?)")
        params.append(t)
    if metrics:
        placeholders = ", ".join("?" for _ in metrics)
        clauses.append(f"metric IN ({placeholders})")
        params.extend(metrics)
    return " AND ".join(clauses), params


def _render(rows: list[dict[str, Any]], fmt: str, out, *, pretty_map: dict) -> None:
    headers = list(rows[0].keys())

    def header_label(h: str) -> str:
        if not pretty_map:
            return h
        return prettynames.resolve(pretty_map, h)

    if fmt == "json":
        out.write(json.dumps(rows, indent=2, default=_json_default) + "\n")
        return
    if fmt == "tsv":
        out.write("\t".join(headers) + "\n")
        for r in rows:
            out.write("\t".join(_fmt(r[h]) for h in headers) + "\n")
        return
    pretty_headers = [header_label(h) for h in headers]
    rendered = [[_fmt(r[h]) for h in headers] for r in rows]
    widths = [max(len(h), *(len(row[i]) for row in rendered)) for i, h in enumerate(pretty_headers)]
    fmt_line = "  ".join(f"{{:<{w}}}" for w in widths)
    out.write(fmt_line.format(*pretty_headers) + "\n")
    out.write(fmt_line.format(*("-" * w for w in widths)) + "\n")
    for row in rendered:
        out.write(fmt_line.format(*row) + "\n")


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".") or "0"
    return str(v)


def _json_default(v: Any):
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)
