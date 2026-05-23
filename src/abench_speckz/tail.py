"""Live progress follower for an active sweep: ``abench-speckz tail``."""

import json
import time
from datetime import datetime
from pathlib import Path


def tail(results_dir: Path) -> None:
    """Follow runs.jsonl and print each completed run as it lands.

    Blocks until Ctrl+C. Also surfaces new monitor log files as they appear
    so the caller can ``tail -f`` them in a second terminal.
    """
    runs_path = results_dir / "runs.jsonl"
    logs_dir = results_dir / "logs"

    if not runs_path.exists():
        print(f"Waiting for {runs_path} ...", flush=True)
        while not runs_path.exists():
            time.sleep(0.5)

    print(f"Watching {runs_path}  (Ctrl+C to stop)\n", flush=True)

    # Seed seen_logs with any pre-existing files so we don't replay old runs.
    seen_logs: set[str] = set(str(p) for p in logs_dir.glob("*.log")) if logs_dir.exists() else set()

    with runs_path.open() as f:
        while True:
            line = f.readline()
            if not line:
                _report_new_logs(logs_dir, seen_logs)
                time.sleep(0.5)
                continue
            _report_new_logs(logs_dir, seen_logs)
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            _print_row(row)


def _report_new_logs(logs_dir: Path, seen: set[str]) -> None:
    if not logs_dir.exists():
        return
    for p in sorted(logs_dir.glob("*.log")):
        key = str(p)
        if key not in seen:
            seen.add(key)
            print(f"  → monitor log: {p}  (tail -f to follow)", flush=True)


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
