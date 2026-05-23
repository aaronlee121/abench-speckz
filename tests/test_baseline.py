"""Tests for report/_baseline.py — join logic, classification, significance gate."""

import pytest

from abench_speckz.report._baseline import (
    Comparison,
    _classify,
    _is_significant,
    compare_rows,
    sort_summary,
)


def _row(metric, mean, *, workload="read", backend="pg", ci95_low=None, ci95_high=None):
    return {
        "workload": workload,
        "backend": backend,
        "metric": metric,
        "mean": mean,
        "ci95_low": ci95_low,
        "ci95_high": ci95_high,
    }


# ---------------------------------------------------------------------------
# _is_significant
# ---------------------------------------------------------------------------


def test_significant_disjoint_bands():
    cur = {"ci95_low": 110.0, "ci95_high": 120.0}
    base = {"ci95_low": 80.0, "ci95_high": 100.0}
    assert _is_significant(cur, base) is True


def test_not_significant_overlapping_bands():
    cur = {"ci95_low": 90.0, "ci95_high": 110.0}
    base = {"ci95_low": 85.0, "ci95_high": 105.0}
    assert _is_significant(cur, base) is False


def test_not_significant_missing_ci():
    cur = {"ci95_low": None, "ci95_high": 110.0}
    base = {"ci95_low": 80.0, "ci95_high": 100.0}
    assert _is_significant(cur, base) is False


# ---------------------------------------------------------------------------
# _classify
# ---------------------------------------------------------------------------


def test_classify_regressed_higher_is_better():
    # rps dropped 10% — bad when higher_is_better=True
    assert _classify(-0.10, "rps", True, {"rps": True}, 0.05) == "regressed"


def test_classify_regressed_lower_is_better():
    # latency increased 10% — bad when higher_is_better=False
    assert _classify(0.10, "latency", True, {"latency": False}, 0.05) == "regressed"


def test_classify_improved_higher_is_better():
    assert _classify(0.10, "rps", True, {"rps": True}, 0.05) == "improved"


def test_classify_neutral_below_threshold():
    # delta is 3% — below default 5% threshold
    assert _classify(-0.03, "rps", True, {"rps": True}, 0.05) == "neutral"


def test_classify_neutral_not_significant():
    # delta is large but CI bands overlap
    assert _classify(-0.20, "rps", False, {"rps": True}, 0.05) == "neutral"


def test_classify_neutral_no_delta():
    assert _classify(None, "rps", True, {"rps": True}, 0.05) == "neutral"


def test_classify_defaults_higher_is_better_when_unknown():
    # metric not in hib dict → defaults to True (higher is better)
    assert _classify(-0.10, "unknown_metric", True, {}, 0.05) == "regressed"


# ---------------------------------------------------------------------------
# compare_rows
# ---------------------------------------------------------------------------

GROUP_BY = ["workload", "backend"]
HIB = {"rps": True, "latency": False}


def test_compare_matched_neutral():
    cur = [_row("rps", 1000.0, ci95_low=980.0, ci95_high=1020.0)]
    base = [_row("rps", 1000.0, ci95_low=980.0, ci95_high=1020.0)]
    results = compare_rows(cur, base, GROUP_BY, higher_is_better=HIB)
    assert len(results) == 1
    c = results[0]
    assert c.status == "neutral"
    assert c.delta == pytest.approx(0.0)
    assert c.delta_pct == pytest.approx(0.0)


def test_compare_regression_detected():
    cur = [_row("rps", 850.0, ci95_low=840.0, ci95_high=860.0)]
    base = [_row("rps", 1000.0, ci95_low=990.0, ci95_high=1010.0)]
    results = compare_rows(cur, base, GROUP_BY, higher_is_better=HIB, threshold=0.05)
    assert results[0].status == "regressed"
    assert results[0].significant is True
    assert results[0].delta == pytest.approx(-150.0)
    assert results[0].delta_pct == pytest.approx(-0.15)


def test_compare_improvement_detected():
    cur = [_row("rps", 1200.0, ci95_low=1190.0, ci95_high=1210.0)]
    base = [_row("rps", 1000.0, ci95_low=990.0, ci95_high=1010.0)]
    results = compare_rows(cur, base, GROUP_BY, higher_is_better=HIB, threshold=0.05)
    assert results[0].status == "improved"


def test_compare_new_row():
    cur = [_row("rps", 1000.0)]
    base = []  # no baseline for this combo
    results = compare_rows(cur, base, GROUP_BY, higher_is_better=HIB)
    assert results[0].status == "new"
    assert results[0].baseline is None


def test_compare_removed_row():
    cur = []
    base = [_row("rps", 1000.0)]
    results = compare_rows(cur, base, GROUP_BY, higher_is_better=HIB)
    assert results[0].status == "removed"
    assert results[0].current is None


def test_compare_multiple_metrics():
    cur = [
        _row("rps", 900.0, ci95_low=890.0, ci95_high=910.0),
        _row("latency", 55.0, ci95_low=54.0, ci95_high=56.0),
    ]
    base = [
        _row("rps", 1000.0, ci95_low=990.0, ci95_high=1010.0),
        _row("latency", 50.0, ci95_low=49.0, ci95_high=51.0),
    ]
    results = compare_rows(cur, base, GROUP_BY, higher_is_better=HIB, threshold=0.05)
    by_metric = {c.metric: c for c in results}
    assert by_metric["rps"].status == "regressed"
    assert by_metric["latency"].status == "regressed"  # latency went up, lower_is_better


def test_compare_group_key_isolation():
    """Two groups with the same metric; only one regresses."""
    cur = [
        _row("rps", 850.0, workload="read", backend="pg", ci95_low=840.0, ci95_high=860.0),
        _row("rps", 1000.0, workload="write", backend="pg", ci95_low=990.0, ci95_high=1010.0),
    ]
    base = [
        _row("rps", 1000.0, workload="read", backend="pg", ci95_low=990.0, ci95_high=1010.0),
        _row("rps", 1000.0, workload="write", backend="pg", ci95_low=990.0, ci95_high=1010.0),
    ]
    results = compare_rows(cur, base, GROUP_BY, higher_is_better=HIB, threshold=0.05)
    by_workload = {c.keys["workload"]: c for c in results}
    assert by_workload["read"].status == "regressed"
    assert by_workload["write"].status == "neutral"


# ---------------------------------------------------------------------------
# sort_summary
# ---------------------------------------------------------------------------


def test_sort_summary_regressions_first():
    comps = [
        Comparison({}, "rps", 900.0, 1000.0, -100.0, -0.10, "neutral", False),
        Comparison({}, "rps", 800.0, 1000.0, -200.0, -0.20, "regressed", True),
        Comparison({}, "rps", 1100.0, 1000.0, 100.0, 0.10, "improved", True),
    ]
    sorted_ = sort_summary(comps, {"rps": True})
    assert sorted_[0].status == "regressed"
    assert sorted_[-1].status == "neutral"


def test_sort_summary_worst_regression_first():
    comps = [
        Comparison({}, "rps", 900.0, 1000.0, -100.0, -0.10, "regressed", True),
        Comparison({}, "rps", 700.0, 1000.0, -300.0, -0.30, "regressed", True),
    ]
    sorted_ = sort_summary(comps, {"rps": True})
    assert sorted_[0].delta_pct == pytest.approx(-0.30)


# ---------------------------------------------------------------------------
# higher_is_better reader (filesystem)
# ---------------------------------------------------------------------------


def test_load_higher_is_better(tmp_path):
    from abench_speckz.report._baseline import load_higher_is_better

    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "a.yaml").write_text(
        "name: a\ncommand: x\nhigher_is_better:\n  rps: true\n  latency: false\n"
    )
    hib = load_higher_is_better(tmp_path)
    assert hib["rps"] is True
    assert hib["latency"] is False


def test_load_higher_is_better_no_tools_dir(tmp_path):
    from abench_speckz.report._baseline import load_higher_is_better

    assert load_higher_is_better(tmp_path) == {}
