"""Live progress follower for an active sweep: ``abench-speckz tail``."""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import IO


def tail(results_dir: Path) -> None:
    """Follow runs.jsonl and print each completed run as it lands.

    Blocks until Ctrl+C. Also streams content from monitor log files as they
    appear so monitor output is visible inline without a separate terminal.
    """
    runs_path = results_dir / "runs.jsonl"
    logs_dir = results_dir / "logs"

    if not runs_path.exists():
        print(f"Waiting for {runs_path} ...", flush=True)
        while not runs_path.exists():
            time.sleep(0.5)

    print(f"Watching {runs_path}  (Ctrl+C to stop)\n", flush=True)

    seen_logs: set[str] = set()
    # path_str → (file_handle, short_label, partial_line_buffer)
    open_logs: dict[str, tuple[IO, str, bytes]] = {}
    # base_stem (without .out/.err) → assigned monitor index N
    base_to_index: dict[str, int] = {}
    log_index = 0

    # Open pre-existing log files seeked to the end so we follow future output
    # without replaying history.
    if logs_dir.exists():
        for p in sorted(logs_dir.glob("*.log")):
            key = str(p)
            seen_logs.add(key)
            stem = p.stem
            if stem.endswith(".out"):
                base, stream = stem[:-4], "out"
            elif stem.endswith(".err"):
                base, stream = stem[:-4], "err"
            else:
                base, stream = stem, "out"
            if base not in base_to_index:
                base_to_index[base] = log_index
                log_index += 1
            n = base_to_index[base]
            label = f"mon-{n} {stream}"
            print(f"  → [{label}] {p.name}", flush=True)
            try:
                fh = open(p, "rb")  # noqa: SIM115
                fh.seek(0, 2)  # seek to end
                open_logs[key] = (fh, label, b"")
            except OSError:
                pass

    with runs_path.open() as f:
        while True:
            line = f.readline()
            if not line:
                log_index = _open_new_logs(logs_dir, seen_logs, open_logs, base_to_index, log_index)
                _drain_logs(open_logs)
                time.sleep(0.5)
                continue
            log_index = _open_new_logs(logs_dir, seen_logs, open_logs, base_to_index, log_index)
            _drain_logs(open_logs)
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            _print_row(row)


def _open_new_logs(
    logs_dir: Path,
    seen: set[str],
    open_logs: dict[str, tuple],
    base_to_index: dict[str, int],
    log_index: int,
) -> int:
    if not logs_dir.exists():
        return log_index
    for p in sorted(logs_dir.glob("*.log")):
        key = str(p)
        if key not in seen:
            seen.add(key)
            stem = p.stem  # e.g. "monitor-uuid-0.out" or "monitor-uuid-0.err"
            if stem.endswith(".out"):
                base, stream = stem[:-4], "out"
            elif stem.endswith(".err"):
                base, stream = stem[:-4], "err"
            else:
                base, stream = stem, "out"
            if base not in base_to_index:
                base_to_index[base] = log_index
                log_index += 1
            n = base_to_index[base]
            label = f"mon-{n} {stream}"
            print(f"  → [{label}] {p.name}", flush=True)
            try:
                open_logs[key] = (open(p, "rb"), label, b"")  # noqa: SIM115
            except OSError:
                pass
    return log_index


def _drain_logs(open_logs: dict[str, tuple]) -> None:
    for key, (fh, label, buf) in list(open_logs.items()):
        try:
            chunk = fh.read()
        except OSError:
            open_logs.pop(key, None)
            continue
        if not chunk:
            continue
        buf += chunk
        while b"\n" in buf:
            raw_line, buf = buf.split(b"\n", 1)
            text = raw_line.decode("utf-8", errors="replace").rstrip("\r")
            print(f"  [{label}] {text}", flush=True)
        open_logs[key] = (fh, label, buf)


def _print_row(row: dict) -> None:
    started = row.get("started_at")
    if started:
        try:
            dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            ts = dt.astimezone().strftime("%H:%M:%S")
        except (ValueError, AttributeError):
            ts = "--:--:--"
    else:
        ts = datetime.now().strftime("%H:%M:%S")

    failure = row.get("failure_reason")
    status = "FAIL" if failure else "ok  "

    vars_ = row.get("vars") or {}
    warmup = "tags" in row and "warmup" in (row.get("tags") or [])
    vars_str = "  ".join(f"{k}={v}" for k, v in vars_.items())
    if warmup:
        vars_str = f"[warmup] {vars_str}"

    duration_s = (row.get("duration_ms") or 0) / 1000

    results = row.get("results") or {}
    metrics_parts = [f"{k}={_fmt_val(v)}" for k, v in results.items()]

    parts: list[str] = [f"[{ts}]", status]
    if vars_str:
        parts.append(vars_str)
    if metrics_parts:
        parts.append("→  " + "  ".join(metrics_parts))
    if failure:
        parts.append(f"({failure})")
    parts.append(f"{duration_s:.1f}s")

    print("  ".join(parts), flush=True)


def _fmt_val(v: object) -> str:
    if isinstance(v, float):
        if abs(v) >= 10_000:
            return f"{v:,.0f}"
        if abs(v) >= 10:
            return f"{v:.1f}"
        return f"{v:.3g}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)
