import os
import sys

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
    assert r.exit_code == -1
    assert r.failure_reason and "command_not_found" in r.failure_reason


def test_env_is_passed():
    ex = LocalSubprocessExecutor()
    env = {**os.environ, "MY_VAR": "hello"}
    r = ex.execute(_request(
        f"{sys.executable} -c 'import os; print(os.environ[\"MY_VAR\"])'", env=env
    ))
    assert r.exit_code == 0
    assert "hello" in r.stdout


def test_to_str_handles_bytes():
    from abench_speckz.executor import _to_str
    assert _to_str(b"hello") == "hello"
    # Invalid utf-8 falls back to "replace" errors mode (returns a string).
    assert _to_str(b"\xff\xfe") == "��"


def test_to_str_handles_none_and_str():
    from abench_speckz.executor import _to_str
    assert _to_str(None) == ""
    assert _to_str("already-a-string") == "already-a-string"
