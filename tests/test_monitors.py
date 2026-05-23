"""Unit tests for run_monitors / stop_monitors."""

import os
import time

from abench_speckz.runner._monitors import run_monitors, stop_monitors


def _env():
    return os.environ.copy()


def _wait_for(path, timeout: float = 5.0) -> bool:
    """Poll until *path* exists or *timeout* seconds elapse."""
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


# ---------------------------------------------------------------------------
# pipe mode (no log_dir) — baseline behaviour
# ---------------------------------------------------------------------------


def test_pipe_mode_captures_stdout(tmp_path):
    # Use a marker so we know echo ran before we call stop_monitors.
    marker = tmp_path / "started"
    procs, records, log_files = run_monitors(
        [f"echo hello && touch {marker} && sleep 30"],
        {},
        _env(),
    )
    assert _wait_for(marker), "monitor did not start in time"
    stop_records = stop_monitors(procs, log_files)

    assert "hello" in stop_records[0]["stdout"]
    assert log_files == [None]


def test_pipe_mode_captures_stderr(tmp_path):
    marker = tmp_path / "started"
    procs, records, log_files = run_monitors(
        [f"echo err >&2 && touch {marker} && sleep 30"],
        {},
        _env(),
    )
    assert _wait_for(marker), "monitor did not start in time"
    stop_records = stop_monitors(procs, log_files)

    assert "err" in stop_records[0]["stderr"]


# ---------------------------------------------------------------------------
# log file mode
# ---------------------------------------------------------------------------


def test_log_file_created(tmp_path):
    # The file is created at Popen time, no race with process output.
    log_dir = tmp_path / "logs"
    procs, records, log_files = run_monitors(
        ["sleep 0"],
        {},
        _env(),
        log_dir=log_dir,
        log_prefix="mon",
    )
    stop_monitors(procs, log_files)

    assert (log_dir / "mon-0.log").exists()


def test_log_file_contains_output(tmp_path):
    marker = tmp_path / "started"
    log_dir = tmp_path / "logs"
    procs, records, log_files = run_monitors(
        [f"echo hello from monitor && touch {marker} && sleep 30"],
        {},
        _env(),
        log_dir=log_dir,
    )
    assert _wait_for(marker), "monitor did not start in time"
    stop_monitors(procs, log_files)

    content = (log_dir / "monitor-0.log").read_text()
    assert "hello from monitor" in content


def test_log_file_receives_stderr_too(tmp_path):
    # stderr=STDOUT merges stderr into the log file.
    marker = tmp_path / "started"
    log_dir = tmp_path / "logs"
    procs, records, log_files = run_monitors(
        [f"echo out && echo err >&2 && touch {marker} && sleep 30"],
        {},
        _env(),
        log_dir=log_dir,
    )
    assert _wait_for(marker), "monitor did not start in time"
    stop_monitors(procs, log_files)

    content = (log_dir / "monitor-0.log").read_text()
    assert "out" in content
    assert "err" in content


def test_log_path_in_start_record(tmp_path):
    log_dir = tmp_path / "logs"
    procs, records, log_files = run_monitors(
        ["sleep 0"],
        {},
        _env(),
        log_dir=log_dir,
        log_prefix="test",
    )
    stop_monitors(procs, log_files)

    assert records[0]["log_path"] == str(log_dir / "test-0.log")


def test_stop_record_has_no_stdout_with_log_file(tmp_path):
    # With file logging, communicate() returns (None, None) — stop record omits those keys.
    marker = tmp_path / "started"
    log_dir = tmp_path / "logs"
    procs, records, log_files = run_monitors(
        [f"echo hi && touch {marker} && sleep 30"],
        {},
        _env(),
        log_dir=log_dir,
    )
    assert _wait_for(marker), "monitor did not start in time"
    stop_records = stop_monitors(procs, log_files)

    assert "stdout" not in stop_records[0]
    assert "stderr" not in stop_records[0]


def test_stop_monitors_closes_log_file(tmp_path):
    log_dir = tmp_path / "logs"
    procs, records, log_files = run_monitors(["sleep 30"], {}, _env(), log_dir=log_dir)
    lf = log_files[0]
    assert not lf.closed
    stop_monitors(procs, log_files)
    assert lf.closed


def test_long_running_monitor_is_killed_and_log_file_closed(tmp_path):
    log_dir = tmp_path / "logs"
    procs, records, log_files = run_monitors(["sleep 30"], {}, _env(), log_dir=log_dir)
    lf = log_files[0]
    stop_monitors(procs, log_files)
    assert lf.closed


# ---------------------------------------------------------------------------
# log_files is parallel to procs (not to records)
# ---------------------------------------------------------------------------


def test_log_files_parallel_to_procs_with_interpolation_failure(tmp_path):
    # If command 0 fails interpolation and command 1 succeeds, procs has one
    # entry and log_files must also have exactly one entry (for command 1's file).
    log_dir = tmp_path / "logs"
    procs, records, log_files = run_monitors(
        ["${__undefined_var_xyz}", "sleep 0"],
        {},
        _env(),
        log_dir=log_dir,
        log_prefix="p",
    )
    assert len(procs) == 1
    assert len(log_files) == 1
    stop_monitors(procs, log_files)
    # The log file belongs to command 1 (index 1 → p-1.log)
    assert (log_dir / "p-1.log").exists()


def test_multiple_monitors_get_separate_log_files(tmp_path):
    marker0 = tmp_path / "m0"
    marker1 = tmp_path / "m1"
    log_dir = tmp_path / "logs"
    procs, records, log_files = run_monitors(
        [
            f"echo first && touch {marker0} && sleep 30",
            f"echo second && touch {marker1} && sleep 30",
        ],
        {},
        _env(),
        log_prefix="m",
        log_dir=log_dir,
    )
    assert _wait_for(marker0) and _wait_for(marker1), "monitors did not start in time"
    stop_monitors(procs, log_files)

    assert "first" in (log_dir / "m-0.log").read_text()
    assert "second" in (log_dir / "m-1.log").read_text()


# ---------------------------------------------------------------------------
# real-time streaming — log file is written before process exits
# ---------------------------------------------------------------------------


def test_log_file_written_before_process_exits(tmp_path):
    # A long-running monitor that writes immediately; the log should be readable
    # while the process is still alive.
    log_dir = tmp_path / "logs"
    marker = tmp_path / "started"
    procs, records, log_files = run_monitors(
        [f"echo live && touch {marker} && sleep 30"],
        {},
        _env(),
        log_dir=log_dir,
    )

    assert _wait_for(marker), "monitor did not start in time"
    content = (log_dir / "monitor-0.log").read_text()
    assert "live" in content

    stop_monitors(procs, log_files)
