"""Launch and terminate side-car monitor processes that run alongside the benchmark."""

import subprocess
from typing import Any

from abench_speckz import interpolate

_MONITOR_STOP_GRACE = 5  # seconds before escalating to SIGKILL


def run_monitors(
    steps: list[str],
    vars_: dict[str, Any],
    env: dict[str, str],
) -> tuple[list[subprocess.Popen], list[dict[str, Any]]]:
    """Launch each monitor command as a background process.

    Returns ``(running_procs, start_records)``. Commands that fail to start are
    recorded but never abort the run.
    """
    procs: list[subprocess.Popen] = []
    records: list[dict[str, Any]] = []
    for template in steps:
        try:
            cmd = interpolate.resolve_string(template, vars_, env=env)
        except Exception as e:
            records.append({"command": template, "pid": None, "error": f"interpolation: {e}"})
            continue
        try:
            proc = subprocess.Popen(
                cmd,
                shell=True,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            procs.append(proc)
            records.append({"command": cmd, "pid": proc.pid})
        except Exception as e:
            records.append({"command": cmd, "pid": None, "error": str(e)})
    return procs, records


def stop_monitors(procs: list[subprocess.Popen]) -> list[dict[str, Any]]:
    """SIGTERM each monitor; SIGKILL after the grace window. Collect final output."""
    records: list[dict[str, Any]] = []
    for proc in procs:
        try:
            proc.terminate()
            try:
                stdout, stderr = proc.communicate(timeout=_MONITOR_STOP_GRACE)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
            records.append(
                {
                    "pid": proc.pid,
                    "exit_code": proc.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                }
            )
        except Exception as e:
            records.append({"pid": proc.pid, "error": str(e)})
    return records
