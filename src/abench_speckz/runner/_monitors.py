"""Launch and terminate side-car monitor processes that run alongside the benchmark."""

import os
import signal
import subprocess
from pathlib import Path
from typing import IO, Any

from abench_speckz import interpolate

_MONITOR_STOP_GRACE = 5  # seconds before escalating to SIGKILL


def run_monitors(
    steps: list[str],
    vars_: dict[str, Any],
    env: dict[str, str],
    log_dir: Path | None = None,
    log_prefix: str = "monitor",
) -> tuple[list[subprocess.Popen], list[dict[str, Any]], list[IO | None]]:
    """Launch each monitor command as a background process.

    When *log_dir* is given, each monitor's stdout+stderr are streamed to
    ``<log_dir>/<log_prefix>-<n>.log`` in real time so callers can ``tail -f``
    them. Otherwise output is captured via pipes and returned by
    ``stop_monitors``.

    Returns ``(running_procs, start_records, log_files)``. Commands that fail
    to start are recorded but never abort the run.
    """
    procs: list[subprocess.Popen] = []
    records: list[dict[str, Any]] = []
    log_files: list[IO | None] = []
    for i, template in enumerate(steps):
        try:
            cmd = interpolate.resolve_string(template, vars_, env=env)
        except Exception as e:
            records.append({"command": template, "pid": None, "error": f"interpolation: {e}"})
            log_files.append(None)
            continue

        log_file: IO | None = None
        log_path: Path | None = None
        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{log_prefix}-{i}.log"
            log_file = open(log_path, "w")  # noqa: SIM115

        stdout_arg = log_file if log_file is not None else subprocess.PIPE
        stderr_arg = subprocess.STDOUT if log_file is not None else subprocess.PIPE

        try:
            proc = subprocess.Popen(
                cmd,
                shell=True,
                start_new_session=True,
                env=env,
                stdout=stdout_arg,
                stderr=stderr_arg,
                text=True,
            )
            procs.append(proc)
            log_files.append(log_file)
            record: dict[str, Any] = {"command": cmd, "pid": proc.pid}
            if log_path is not None:
                record["log_path"] = str(log_path)
            records.append(record)
        except Exception as e:
            if log_file is not None:
                log_file.close()
            records.append({"command": cmd, "pid": None, "error": str(e)})
            log_files.append(None)
    return procs, records, log_files


def stop_monitors(
    procs: list[subprocess.Popen],
    log_files: list[IO | None] | None = None,
) -> list[dict[str, Any]]:
    """SIGTERM each monitor; SIGKILL after the grace window. Collect final output."""
    records: list[dict[str, Any]] = []
    for i, proc in enumerate(procs):
        log_file = log_files[i] if log_files else None
        try:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = proc.communicate(timeout=_MONITOR_STOP_GRACE)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = proc.communicate()
            if log_file is not None:
                try:
                    log_file.close()
                except Exception:
                    pass
            record: dict[str, Any] = {"pid": proc.pid, "exit_code": proc.returncode}
            if stdout is not None:
                record["stdout"] = stdout
            if stderr is not None:
                record["stderr"] = stderr
            records.append(record)
        except Exception as e:
            if log_file is not None:
                try:
                    log_file.close()
                except Exception:
                    pass
            records.append({"pid": proc.pid, "error": str(e)})
    return records
