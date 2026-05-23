import os
import sys

import pytest

from abench_speckz.executor import LocalSubprocessExecutor
from abench_speckz.runspec import RunRequest


def _request(command, env=None, timeout=10):
    return RunRequest(
        config_hash="abc",
        vars={},
        tags=[],
        tool="t",
        tool_version=None,
        command=command,
        env=env or os.environ.copy(),
        timeout_seconds=timeout,
    )


def test_success():
    ex = LocalSubprocessExecutor()
    r = ex.execute(_request(f"{sys.executable} -c 'print(\"ok\")'"))
    assert r.exit_code == 0
    assert "ok" in r.stdout
    assert r.failure_reason is None
    assert r.duration_ms >= 0


def test_nonzero_exit():
    ex = LocalSubprocessExecutor()
    r = ex.execute(_request(f"{sys.executable} -c 'import sys; sys.exit(7)'"))
    assert r.exit_code == 7
    assert r.failure_reason == "exit_code=7"


def test_timeout():
    ex = LocalSubprocessExecutor()
    r = ex.execute(_request(f"{sys.executable} -c 'import time; time.sleep(5)'", timeout=1))
    assert r.exit_code == -1
    assert r.failure_reason == "timeout"


def test_command_not_found():
    ex = LocalSubprocessExecutor()
    r = ex.execute(_request("definitely-not-a-real-command-xyz"))
    assert r.exit_code == 127
    assert r.failure_reason and "exit_code=127" in r.failure_reason


def test_env_is_passed():
    ex = LocalSubprocessExecutor()
    env = {**os.environ, "MY_VAR": "hello"}
    r = ex.execute(_request(f"{sys.executable} -c 'import os; print(os.environ[\"MY_VAR\"])'", env=env))
    assert r.exit_code == 0
    assert "hello" in r.stdout


def test_pipe_command_success():
    ex = LocalSubprocessExecutor()
    r = ex.execute(_request("echo hello | cat"))
    assert r.exit_code == 0
    assert "hello" in r.stdout


@pytest.mark.timeout(15)
def test_timeout_with_pipe_does_not_hang():
    # A pipeline leaves grandchild processes alive after the shell is killed.
    # Without start_new_session+killpg, communicate() hangs indefinitely.
    import time

    ex = LocalSubprocessExecutor()
    t0 = time.monotonic()
    r = ex.execute(_request("sleep 30 | cat", timeout=1))
    elapsed = time.monotonic() - t0

    assert r.failure_reason == "timeout"
    assert elapsed < 10, f"execute took {elapsed:.1f}s — likely hung on pipe grandchild"


def test_to_str_handles_bytes():
    from abench_speckz.executor import _to_str

    assert _to_str(b"hello") == "hello"
    # Invalid utf-8 falls back to "replace" errors mode (returns a string).
    assert _to_str(b"\xff\xfe") == "��"


def test_to_str_handles_none_and_str():
    from abench_speckz.executor import _to_str

    assert _to_str(None) == ""
    assert _to_str("already-a-string") == "already-a-string"
