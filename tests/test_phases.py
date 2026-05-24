import os
import time

import pytest

from abench_speckz.runner._phases import run_phase
from abench_speckz.runspec import Step


def _steps(*cmds: str) -> list[Step]:
    return [Step(run=cmd) for cmd in cmds]


def test_phase_success_with_pipe_command():
    reason, records = run_phase(
        "setup", _steps("echo hello | cat"), {}, os.environ.copy(), timeout_seconds=10
    )
    assert reason is None
    assert records[0]["exit_code"] == 0
    assert "hello" in records[0]["stdout"]


@pytest.mark.timeout(15)
def test_phase_timeout_with_pipe_does_not_hang():
    # A pipeline leaves grandchild processes alive after the shell is killed.
    # Without start_new_session+killpg, communicate() hangs indefinitely.
    t0 = time.monotonic()
    reason, records = run_phase("setup", _steps("sleep 30 | cat"), {}, os.environ.copy(), timeout_seconds=1)
    elapsed = time.monotonic() - t0

    assert reason is not None and "timeout" in reason
    assert records[0]["exit_code"] == -1
    assert elapsed < 10, f"run_phase took {elapsed:.1f}s — likely hung on pipe grandchild"


def test_phase_when_matching_step_runs():
    steps = [Step(run="echo ran", when={"workload": "read"})]
    reason, records = run_phase("setup", steps, {"workload": "read"}, os.environ.copy(), timeout_seconds=10)
    assert reason is None
    assert len(records) == 1
    assert "ran" in records[0]["stdout"]


def test_phase_when_non_matching_step_skipped():
    steps = [Step(run="echo ran", when={"workload": "read"})]
    reason, records = run_phase("setup", steps, {"workload": "write"}, os.environ.copy(), timeout_seconds=10)
    assert reason is None
    assert records == []


def test_phase_when_missing_var_does_not_match():
    steps = [Step(run="echo ran", when={"workload": "read"})]
    reason, records = run_phase("setup", steps, {}, os.environ.copy(), timeout_seconds=10)
    assert reason is None
    assert records == []


def test_phase_when_multiple_conditions_all_must_match():
    steps = [Step(run="echo ran", when={"workload": "read", "backend": "pg"})]
    reason, records = run_phase(
        "setup", steps, {"workload": "read", "backend": "mysql"}, os.environ.copy(), timeout_seconds=10
    )
    assert reason is None
    assert records == []


def test_phase_when_empty_runs_unconditionally():
    steps = [Step(run="echo always", when={})]
    reason, records = run_phase(
        "setup", steps, {"workload": "anything"}, os.environ.copy(), timeout_seconds=10
    )
    assert reason is None
    assert len(records) == 1
