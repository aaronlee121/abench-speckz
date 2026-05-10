import math
import statistics
from collections import defaultdict
from typing import Any, Iterable

from benchspec.runspec import AggregateRow


_T_CRIT_95: dict[int, float] = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
    6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
    16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
    26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045,
}


def _t_critical_95(df: int) -> float:
    if df < 1:
        return float("nan")
    return _T_CRIT_95.get(df, 1.96)


def _percentiles(values: list[float], pcts: list[int]) -> list[float]:
    if len(values) < 2:
        return [values[0]] * len(pcts) if values else [float("nan")] * len(pcts)
    qs = statistics.quantiles(values, n=100, method="inclusive")
    return [qs[p - 1] for p in pcts]


def stats_for(values: list[float]) -> dict[str, Any]:
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": None, "stddev": None, "p50": None, "p95": None, "p99": None,
                "ci95_low": None, "ci95_high": None}
    mean = statistics.fmean(values)
    stddev = statistics.stdev(values) if n >= 2 else 0.0
    p50, p95, p99 = _percentiles(values, [50, 95, 99])
    if n >= 2:
        t = _t_critical_95(n - 1)
        half = t * stddev / math.sqrt(n)
        ci_low, ci_high = mean - half, mean + half
    else:
        ci_low = ci_high = None
    return {"n": n, "mean": mean, "stddev": stddev, "p50": p50, "p95": p95, "p99": p99,
            "ci95_low": ci_low, "ci95_high": ci_high}


def compute_aggregate_rows(runs: list[dict[str, Any]]) -> list[AggregateRow]:
    if not runs:
        return []

    successful = [r for r in runs if r.get("exit_code") == 0 and r.get("results")]
    n_failed = sum(1 for r in runs if r is not None and (r.get("exit_code") != 0 or r.get("failure_reason")))

    config_hash = runs[0]["config_hash"]
    vars_ = runs[0].get("vars") or {}
    tags = sorted({t for r in runs for t in (r.get("tags") or [])})

    by_metric: dict[str, list[float]] = defaultdict(list)
    for r in successful:
        if "warmup" in (r.get("tags") or []):
            continue
        for k, v in (r.get("results") or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                by_metric[k].append(float(v))

    rows: list[AggregateRow] = []
    for metric, values in sorted(by_metric.items()):
        s = stats_for(values)
        rows.append(AggregateRow(
            config_hash=config_hash, metric=metric,
            n=s["n"], n_failed=n_failed,
            mean=s["mean"], stddev=s["stddev"],
            p50=s["p50"], p95=s["p95"], p99=s["p99"],
            ci95_low=s["ci95_low"], ci95_high=s["ci95_high"],
            vars=dict(vars_), tags=tags,
        ))
    return rows


def rebuild_table(runs: Iterable[dict[str, Any]]) -> dict[str, list[AggregateRow]]:
    by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in runs:
        by_hash[r["config_hash"]].append(r)
    return {h: compute_aggregate_rows(rs) for h, rs in by_hash.items()}
