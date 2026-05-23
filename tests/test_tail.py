"""Tests for the tail module: formatting helpers and the main follow loop."""

import json

import pytest

from abench_speckz.tail import _fmt_val, _print_row, _report_new_logs, tail


# ---------------------------------------------------------------------------
# _fmt_val
# ---------------------------------------------------------------------------


def test_fmt_val_large_float():
    assert _fmt_val(12345.6) == "12,346"


def test_fmt_val_medium_float():
    assert _fmt_val(2.345) == "2.35"


def test_fmt_val_very_small_float():
    assert _fmt_val(0.00123) == "0.00123"


def test_fmt_val_int():
    assert _fmt_val(42) == "42"


def test_fmt_val_large_int():
    assert _fmt_val(1_000_000) == "1,000,000"


def test_fmt_val_string():
    assert _fmt_val("hello") == "hello"


def test_fmt_val_negative_float():
    assert _fmt_val(-12345.0) == "-12,345"


# ---------------------------------------------------------------------------
# _print_row
# ---------------------------------------------------------------------------


def _ok_row(**kwargs):
    base = {
        "started_at": "2026-05-22T14:00:00Z",
        "vars": {"backend": "pg", "workload": "read"},
        "tags": [],
        "results": {"rps": 12345.0, "p95_ms": 2.3},
        "duration_ms": 1234,
        "failure_reason": None,
    }
    base.update(kwargs)
    return base


def test_print_row_ok_shows_vars_and_metrics(capsys):
    _print_row(_ok_row())
    out = capsys.readouterr().out
    assert "ok" in out
    assert "backend=pg" in out
    assert "workload=read" in out
    assert "12,345" in out  # rps formatted
    assert "2.3" in out     # p95_ms
    assert "1.2s" in out    # duration


def test_print_row_ok_shows_timestamp(capsys):
    import re
    _print_row(_ok_row())
    out = capsys.readouterr().out
    # A bracketed HH:MM:SS timestamp derived from started_at (displayed in local TZ).
    assert re.search(r"\[\d{2}:\d{2}:\d{2}\]", out), f"no timestamp in: {out!r}"


def test_print_row_failure_shows_reason(capsys):
    row = _ok_row(failure_reason="exit_code=7: bench crashed", results={})
    _print_row(row)
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "exit_code=7" in out
    assert "bench crashed" in out


def test_print_row_warmup_label(capsys):
    row = _ok_row(tags=["warmup"])
    _print_row(row)
    out = capsys.readouterr().out
    assert "warmup" in out


def test_print_row_no_metrics(capsys):
    row = _ok_row(results={})
    _print_row(row)
    out = capsys.readouterr().out
    assert "ok" in out
    assert "→" not in out  # no arrow when no metrics


def test_print_row_missing_started_at_uses_fallback(capsys):
    row = _ok_row(started_at=None)
    _print_row(row)  # should not raise
    out = capsys.readouterr().out
    assert "ok" in out


# ---------------------------------------------------------------------------
# _report_new_logs
# ---------------------------------------------------------------------------


def test_report_new_logs_shows_new_file(tmp_path, capsys):
    log_file = tmp_path / "monitor-abc-0.log"
    log_file.write_text("")
    seen: set[str] = set()

    _report_new_logs(tmp_path, seen)

    out = capsys.readouterr().out
    assert "monitor-abc-0.log" in out
    assert "tail -f" in out
    assert str(log_file) in seen


def test_report_new_logs_skips_already_seen(tmp_path, capsys):
    log_file = tmp_path / "monitor-abc-0.log"
    log_file.write_text("")
    seen: set[str] = {str(log_file)}

    _report_new_logs(tmp_path, seen)

    out = capsys.readouterr().out
    assert out == ""


def test_report_new_logs_no_op_when_dir_missing(tmp_path, capsys):
    _report_new_logs(tmp_path / "nonexistent", set())
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# tail() integration — monkeypatch time.sleep to control the loop
# ---------------------------------------------------------------------------


def _make_sleep_bomb(n_before_interrupt):
    """Return a mock sleep that raises KeyboardInterrupt after n calls."""
    count = 0

    def mock_sleep(_):
        nonlocal count
        count += 1
        if count >= n_before_interrupt:
            raise KeyboardInterrupt

    return mock_sleep


def test_tail_prints_existing_rows(tmp_path, capsys, monkeypatch):
    rows = [
        {
            "started_at": "2026-05-22T12:00:00Z",
            "vars": {"k": "v"},
            "tags": [],
            "results": {"rps": 100.0},
            "duration_ms": 1000,
            "failure_reason": None,
        },
        {
            "started_at": "2026-05-22T12:00:01Z",
            "vars": {"k": "v2"},
            "tags": [],
            "results": {},
            "duration_ms": 500,
            "failure_reason": "exit_code=1",
        },
    ]
    (tmp_path / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    monkeypatch.setattr("abench_speckz.tail.time.sleep", _make_sleep_bomb(2))

    with pytest.raises(KeyboardInterrupt):
        tail(tmp_path)

    out = capsys.readouterr().out
    assert "k=v" in out
    assert "100" in out
    assert "FAIL" in out
    assert "exit_code=1" in out


def test_tail_waits_for_runs_jsonl(tmp_path, capsys, monkeypatch):
    runs_path = tmp_path / "runs.jsonl"
    call_count = 0

    def mock_sleep(_):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            runs_path.write_text("")  # create file on first sleep
        if call_count >= 3:
            raise KeyboardInterrupt

    monkeypatch.setattr("abench_speckz.tail.time.sleep", mock_sleep)

    with pytest.raises(KeyboardInterrupt):
        tail(tmp_path)

    out = capsys.readouterr().out
    assert "Waiting for" in out
    assert "Watching" in out


def test_tail_surfaces_new_monitor_log_files(tmp_path, capsys, monkeypatch):
    runs_path = tmp_path / "runs.jsonl"
    runs_path.write_text("")
    logs_dir = tmp_path / "logs"
    call_count = 0

    def mock_sleep(_):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Drop a new log file on first sleep
            logs_dir.mkdir()
            (logs_dir / "monitor-abc-0.log").write_text("")
        if call_count >= 3:
            raise KeyboardInterrupt

    monkeypatch.setattr("abench_speckz.tail.time.sleep", mock_sleep)

    with pytest.raises(KeyboardInterrupt):
        tail(tmp_path)

    out = capsys.readouterr().out
    assert "monitor-abc-0.log" in out
    assert "tail -f" in out


def test_tail_does_not_replay_existing_log_files(tmp_path, capsys, monkeypatch):
    runs_path = tmp_path / "runs.jsonl"
    runs_path.write_text("")
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "monitor-old-0.log").write_text("")  # pre-existing file

    monkeypatch.setattr("abench_speckz.tail.time.sleep", _make_sleep_bomb(2))

    with pytest.raises(KeyboardInterrupt):
        tail(tmp_path)

    out = capsys.readouterr().out
    assert "monitor-old-0.log" not in out


def test_tail_skips_malformed_json_lines(tmp_path, capsys, monkeypatch):
    (tmp_path / "runs.jsonl").write_text("not json\n")

    monkeypatch.setattr("abench_speckz.tail.time.sleep", _make_sleep_bomb(2))

    with pytest.raises(KeyboardInterrupt):
        tail(tmp_path)  # should not raise ValueError/JSONDecodeError
