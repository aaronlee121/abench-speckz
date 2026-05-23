"""Shared value-selection helpers: Phase 0 of executive-level features."""

from __future__ import annotations

from pathlib import Path

import yaml


def higher_is_better_map(results_dir: Path) -> dict[str, bool]:
    """Read higher_is_better flags from tools/*.yaml snapshots in results_dir."""
    tools_dir = results_dir / "tools"
    out: dict[str, bool] = {}
    if not tools_dir.is_dir():
        return out
    for tool_yaml in sorted(tools_dir.glob("*.yaml")):
        try:
            raw = yaml.safe_load(tool_yaml.read_text())
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        for metric, val in (raw.get("higher_is_better") or {}).items():
            if metric not in out:
                out[metric] = bool(val)
    return out


def resolve_value(
    results_dir: Path,
    metric: str,
    *,
    where: list[str] | None = None,
    stat: str = "mean",
) -> float | None:
    """Return a single scalar for metric aggregated (max/min/mean) across matching combos.

    The per-combo value is always the combo's mean; ``stat`` controls how those
    means are reduced across the matching set.
    """
    from abench_speckz import query

    rows = query.compute_rows(results_dir, metrics=[metric], where=where or [])
    vals: list[float] = [r["mean"] for r in rows if r.get("metric") == metric and r.get("mean") is not None]
    if not vals:
        return None
    if stat == "max":
        return max(vals)
    if stat == "min":
        return min(vals)
    return sum(vals) / len(vals)


def rank_by(
    results_dir: Path,
    metric: str,
    variable: str,
    *,
    where: list[str] | None = None,
    stat: str = "mean",
) -> list[tuple[str, float]]:
    """Return (var_value, metric_value) pairs sorted descending by the per-group mean.

    Callers that want ascending order (lower-is-better) should reverse the result.
    """
    from abench_speckz import query

    rows = query.compute_rows(
        results_dir,
        group_by=[variable],
        metrics=[metric],
        where=where or [],
    )
    pairs: list[tuple[str, float]] = []
    for r in rows:
        if r.get("metric") != metric:
            continue
        var_val = r.get(variable)
        val = r.get("mean")
        if var_val is not None and val is not None:
            pairs.append((str(var_val), float(val)))
    pairs.sort(key=lambda p: p[1], reverse=True)
    return pairs
