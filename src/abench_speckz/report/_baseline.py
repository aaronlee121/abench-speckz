"""Baseline comparison: join current vs baseline rows, compute deltas, classify status."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Comparison:
    keys: dict[str, str]  # combo var values
    metric: str
    current: float | None
    baseline: float | None
    delta: float | None  # current - baseline
    delta_pct: float | None  # delta / baseline
    status: str  # regressed | improved | neutral | new | removed
    significant: bool  # CI bands disjoint


def load_higher_is_better(results_dir: Path) -> dict[str, bool]:
    """Read metric→bool from all tools/*.yaml snapshots in results_dir."""
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
        hib = raw.get("higher_is_better")
        if isinstance(hib, dict):
            for k, v in hib.items():
                if isinstance(v, bool):
                    out[k] = v
    return out


def compare_rows(
    current_rows: list[dict[str, Any]],
    baseline_rows: list[dict[str, Any]],
    group_by: list[str],
    *,
    higher_is_better: dict[str, bool],
    threshold: float = 0.05,
) -> list[Comparison]:
    """Join current and baseline on (group_by vars, metric) and classify each pair."""
    # Index baseline by (gb_key_tuple, metric)
    base_idx: dict[tuple, dict[str, Any]] = {}
    for r in baseline_rows:
        key = tuple(str(r.get(k, "")) for k in group_by)
        base_idx[(key, r.get("metric"))] = r

    seen: set[tuple] = set()
    results: list[Comparison] = []

    for r in current_rows:
        key = tuple(str(r.get(k, "")) for k in group_by)
        metric: str = r.get("metric") or ""
        lookup = (key, metric)
        seen.add(lookup)

        keys_dict = {k: str(r.get(k, "")) for k in group_by}
        cur_mean = r.get("mean")
        base_row = base_idx.get(lookup)

        if base_row is None:
            results.append(
                Comparison(
                    keys=keys_dict,
                    metric=metric,
                    current=cur_mean,
                    baseline=None,
                    delta=None,
                    delta_pct=None,
                    status="new",
                    significant=False,
                )
            )
            continue

        base_mean = base_row.get("mean")
        delta = (cur_mean - base_mean) if (cur_mean is not None and base_mean is not None) else None
        delta_pct = (delta / base_mean) if (delta is not None and base_mean not in (None, 0.0)) else None

        sig = _is_significant(r, base_row)
        status = _classify(delta_pct, metric, sig, higher_is_better, threshold)

        results.append(
            Comparison(
                keys=keys_dict,
                metric=metric,
                current=cur_mean,
                baseline=base_mean,
                delta=delta,
                delta_pct=delta_pct,
                status=status,
                significant=sig,
            )
        )

    # Baseline rows with no matching current row → "removed"
    for (key, base_metric), base_row in base_idx.items():
        if (key, base_metric) not in seen:
            keys_dict = {k: v for k, v in zip(group_by, key, strict=True)}
            results.append(
                Comparison(
                    keys=keys_dict,
                    metric=base_metric or "",
                    current=None,
                    baseline=base_row.get("mean"),
                    delta=None,
                    delta_pct=None,
                    status="removed",
                    significant=False,
                )
            )

    return results


def _is_significant(cur: dict, base: dict) -> bool:
    """Return True when CI bands are disjoint (no overlap)."""
    c_lo = cur.get("ci95_low")
    c_hi = cur.get("ci95_high")
    b_lo = base.get("ci95_low")
    b_hi = base.get("ci95_high")
    if c_lo is None or c_hi is None or b_lo is None or b_hi is None:
        return False
    # Overlap when c_lo <= b_hi AND b_lo <= c_hi; disjoint otherwise
    return not (c_lo <= b_hi and b_lo <= c_hi)


def _classify(
    delta_pct: float | None,
    metric: str,
    significant: bool,
    higher_is_better: dict[str, bool],
    threshold: float,
) -> str:
    if delta_pct is None:
        return "neutral"
    hib = higher_is_better.get(metric, True)  # default: higher is better
    # "bad direction" means lower when higher_is_better, or higher when not
    bad = -delta_pct if hib else delta_pct
    if bad > threshold and significant:
        return "regressed"
    # "good direction"
    good = delta_pct if hib else -delta_pct
    if good > threshold and significant:
        return "improved"
    return "neutral"


def sort_summary(comparisons: list[Comparison], higher_is_better: dict[str, bool]) -> list[Comparison]:
    """Sort comparisons worst-first: regressions before improvements before neutral."""
    _order = {"regressed": 0, "removed": 1, "improved": 2, "new": 3, "neutral": 4}

    def _sort_key(c: Comparison):
        hib = higher_is_better.get(c.metric, True)
        # Magnitude of regression (positive = bad)
        if c.delta_pct is not None:
            bad_mag = -c.delta_pct if hib else c.delta_pct
        else:
            bad_mag = 0.0
        return (_order.get(c.status, 99), -bad_mag)

    return sorted(comparisons, key=_sort_key)
