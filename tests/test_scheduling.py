"""Tests for the Scheduler protocol and built-in schedulers."""

from __future__ import annotations

import pytest

from abench_speckz.runner._probes import config_hash
from abench_speckz.runspec import ToolSpec
from abench_speckz.scheduling import (
    CITarget,
    CITargetScheduler,
    FixedScheduler,
    SweepState,
    make_scheduler,
    parse_ci_target,
)


def _entry(**vars_):
    return {"vars": vars_, "filename": "x.env", "tags": []}


def _row(ch: str, *, metric: str = "rps", value: float, is_warmup: bool = False, exit_code: int = 0):
    return {
        "config_hash": ch,
        "exit_code": exit_code,
        "results": {metric: value},
        "tags": ["warmup"] if is_warmup else [],
        "vars": {},
    }


# ----- FixedScheduler -----


def test_fixed_scheduler_yields_warmup_then_measured_per_entry():
    e1 = _entry(workload="read")
    e2 = _entry(workload="write")
    sched = FixedScheduler([e1, e2], warmup=2, repeat=3)
    state = SweepState()

    seq = []
    while (item := sched.next(state)) is not None:
        seq.append((item.entry["vars"]["workload"], item.is_warmup))

    # entry1: 2 warmup + 3 measured, then entry2: 2 warmup + 3 measured
    assert seq == [
        ("read", True),
        ("read", True),
        ("read", False),
        ("read", False),
        ("read", False),
        ("write", True),
        ("write", True),
        ("write", False),
        ("write", False),
        ("write", False),
    ]


def test_fixed_scheduler_honors_skip_existing():
    e1 = _entry(workload="read")
    e2 = _entry(workload="write")
    ch1 = config_hash(e1["vars"])
    skip_counts = {ch1: 5}
    sched = FixedScheduler([e1, e2], warmup=0, repeat=3, skip_counts=skip_counts, skip_existing=True)
    state = SweepState()

    seen = [item.entry["vars"]["workload"] for item in iter(lambda: sched.next(state), None)]
    # e1 is fully skipped (count >= repeat); e2 runs 3 reps.
    assert seen == ["write", "write", "write"]


def test_fixed_scheduler_skip_existing_off_ignores_skip_counts():
    e1 = _entry(workload="read")
    ch1 = config_hash(e1["vars"])
    sched = FixedScheduler([e1], warmup=0, repeat=2, skip_counts={ch1: 10}, skip_existing=False)
    state = SweepState()
    _items = list(iter(lambda: sched.next(SweepState()), None) if False else [])
    # Easier: just count what next() returns
    state = SweepState()
    seen = []
    while (item := sched.next(state)) is not None:
        seen.append(item)
    assert len(seen) == 2


def test_fixed_scheduler_expected_max_reps():
    e1 = _entry(workload="read")
    e2 = _entry(workload="write")
    sched = FixedScheduler([e1, e2], warmup=1, repeat=4)
    assert sched.expected_max_reps() == 2 * (1 + 4)


def test_fixed_scheduler_planned_counts_per_entry():
    e1 = _entry(workload="read")
    sched = FixedScheduler([e1], warmup=2, repeat=5)
    assert sched.planned_warmup_for(e1) == 2
    assert sched.planned_measured_for(e1) == 5


# ----- CITargetScheduler -----


def test_ci_target_stops_at_min_repeat_when_already_tight():
    e = _entry(workload="read")
    ch = config_hash(e["vars"])
    sched = CITargetScheduler([e], [CITarget("rps", 0.05)], min_repeat=3, max_repeat=20, warmup=0)
    state = SweepState()

    issued = 0
    while (_ := sched.next(state)) is not None:
        issued += 1
        # Nearly identical values → very tight CI
        state.record(ch, _row(ch, value=100.0 + 0.001 * issued))
        if issued > 50:
            pytest.fail("scheduler should have stopped well before 50 reps")

    # Should stop at exactly min_repeat — once we have 3 samples with tight CI.
    assert issued == 3


def test_ci_target_runs_to_max_repeat_when_never_converges():
    e = _entry(workload="read")
    ch = config_hash(e["vars"])
    sched = CITargetScheduler([e], [CITarget("rps", 0.001)], min_repeat=3, max_repeat=10, warmup=0)
    state = SweepState()

    issued = 0
    import random

    rng = random.Random(42)
    while (_ := sched.next(state)) is not None:
        issued += 1
        # Highly variable values → CI never converges to 0.1%.
        state.record(ch, _row(ch, value=rng.uniform(10, 1000)))
    assert issued == 10


def test_ci_target_requires_all_metrics_to_converge():
    e = _entry(workload="read")
    ch = config_hash(e["vars"])
    sched = CITargetScheduler(
        [e],
        [CITarget("rps", 0.05), CITarget("latency_ms", 0.05)],
        min_repeat=3,
        max_repeat=8,
        warmup=0,
    )
    state = SweepState()

    issued = 0
    while (_ := sched.next(state)) is not None:
        issued += 1
        # rps is tight, latency_ms varies wildly.
        state.record(
            ch,
            {
                "config_hash": ch,
                "exit_code": 0,
                "results": {"rps": 100.0 + 0.001 * issued, "latency_ms": 5.0 * issued},
                "tags": [],
                "vars": {},
            },
        )
    # latency_ms never converges → run to max_repeat
    assert issued == 8


def test_ci_target_warmup_runs_first():
    e = _entry(workload="read")
    ch = config_hash(e["vars"])
    sched = CITargetScheduler([e], [CITarget("rps", 0.05)], min_repeat=3, max_repeat=10, warmup=2)
    state = SweepState()

    seq = []
    while (item := sched.next(state)) is not None:
        seq.append(item.is_warmup)
        state.record(ch, _row(ch, value=100.0, is_warmup=item.is_warmup))
        if len(seq) > 20:
            pytest.fail("did not stop")
    # 2 warmup + 3 measured (converges at min_repeat)
    assert seq == [True, True, False, False, False]


def test_ci_target_validates_against_tool_capture():
    e = _entry(workload="read")
    sched = CITargetScheduler([e], [CITarget("missing", 0.05)], min_repeat=3, max_repeat=10)
    tool = ToolSpec(name="t", command="x", capture={"rps": "$.rps"})
    with pytest.raises(ValueError, match="missing"):
        sched.validate(tool)


def test_ci_target_validate_accepts_known_metric_with_list_suffix():
    e = _entry(workload="read")
    sched = CITargetScheduler([e], [CITarget("rps", 0.05)], min_repeat=3, max_repeat=10)
    # The "rps[]" form means "collect all matches as a list"; the metric name is still "rps".
    tool = ToolSpec(name="t", command="x", capture={"rps[]": "$.items[*].rps"})
    sched.validate(tool)  # no raise


def test_ci_target_validate_skips_check_when_parser_is_set():
    e = _entry(workload="read")
    sched = CITargetScheduler([e], [CITarget("anything", 0.05)], min_repeat=3, max_repeat=10)
    tool = ToolSpec(name="t", command="x", parser="mymod:myfunc")
    sched.validate(tool)  # no raise


def test_ci_target_rejects_bad_args():
    e = _entry(workload="read")
    with pytest.raises(ValueError):
        CITargetScheduler([e], [], min_repeat=3, max_repeat=10)
    with pytest.raises(ValueError):
        CITargetScheduler([e], [CITarget("rps", 0.05)], min_repeat=1, max_repeat=10)
    with pytest.raises(ValueError):
        CITargetScheduler([e], [CITarget("rps", 0.05)], min_repeat=5, max_repeat=3)


def test_ci_target_planned_measured_is_min_repeat():
    e = _entry(workload="read")
    sched = CITargetScheduler([e], [CITarget("rps", 0.05)], min_repeat=4, max_repeat=20, warmup=1)
    assert sched.planned_warmup_for(e) == 1
    assert sched.planned_measured_for(e) == 4


# ----- SweepState -----


def test_sweep_state_record_and_aggregate_cache():
    ch = "abc"
    state = SweepState()
    for v in [10.0, 11.0, 12.0]:
        state.record(ch, _row(ch, value=v))
    rows = state.aggregates_for(ch)
    rps = next(r for r in rows if r.metric == "rps")
    assert rps.n == 3
    assert rps.mean == pytest.approx(11.0)
    # Cached on second call (no re-record between).
    assert state.aggregates_for(ch) is rows


def test_sweep_state_invalidates_cache_on_record():
    ch = "abc"
    state = SweepState()
    state.record(ch, _row(ch, value=10.0))
    state.record(ch, _row(ch, value=20.0))
    rows1 = state.aggregates_for(ch)
    state.record(ch, _row(ch, value=30.0))
    rows2 = state.aggregates_for(ch)
    assert rows1 is not rows2
    assert next(r for r in rows2 if r.metric == "rps").n == 3


def test_sweep_state_counts_exclude_warmup():
    ch = "abc"
    state = SweepState()
    state.record(ch, _row(ch, value=10.0, is_warmup=True))
    state.record(ch, _row(ch, value=10.0))
    state.record(ch, _row(ch, value=10.0, exit_code=1))
    assert state.attempt_count(ch) == 2  # excludes warmup
    assert state.success_count(ch) == 1  # excludes warmup AND failure


def test_sweep_state_seeded_from_prior_rows():
    ch = "abc"
    seed = [_row(ch, value=v) for v in [1.0, 2.0, 3.0]]
    state = SweepState(seed_rows=seed)
    assert state.attempt_count(ch) == 3
    assert state.aggregates_for(ch)[0].n == 3


# ----- make_scheduler / parse_ci_target -----


def test_make_scheduler_picks_fixed_by_default():
    s = make_scheduler(
        entries=[_entry(workload="read")],
        warmup=0,
        repeat=1,
        ci_targets=None,
        min_repeat=3,
        max_repeat=10,
    )
    assert isinstance(s, FixedScheduler)


def test_make_scheduler_picks_ci_target_when_targets_present():
    s = make_scheduler(
        entries=[_entry(workload="read")],
        warmup=0,
        repeat=1,
        ci_targets=[CITarget("rps", 0.05)],
        min_repeat=3,
        max_repeat=10,
    )
    assert isinstance(s, CITargetScheduler)


def test_parse_ci_target_round_trip():
    t = parse_ci_target("rps=0.05")
    assert t == CITarget(metric="rps", threshold=0.05)


@pytest.mark.parametrize("bad", ["", "rps", "=0.05", "rps=", "rps=abc", "rps=-0.1", "rps=0"])
def test_parse_ci_target_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_ci_target(bad)
