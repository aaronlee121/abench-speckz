import os
import time

from abench_speckz.runner._phases import run_phase


def test_phase_success_with_pipe_command():
    reason, records = run_phase("setup", ["echo hello | cat"], {}, os.environ.copy(), timeout_seconds=10)
    assert reason is None
    assert records[0]["exit_code"] == 0
    assert "hello" in records[0]["stdout"]


def test_phase_timeout_with_pipe_does_not_hang():
    # A pipeline leaves grandchild processes alive after the shell is killed.
    # Without start_new_session+killpg, communicate() hangs indefinitely.
    t0 = time.monotonic()
    reason, records = run_phase("setup", ["sleep 30 | cat"], {}, os.environ.copy(), timeout_seconds=1)
    elapsed = time.monotonic() - t0

    assert reason is not None and "timeout" in reason
    assert records[0]["exit_code"] == -1
    assert elapsed < 10, f"run_phase took {elapsed:.1f}s — likely hung on pipe grandchild"
