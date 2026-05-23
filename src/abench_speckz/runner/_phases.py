"""Run a list of shell-command steps (setup/teardown/post_run) with structured records."""

import os
import signal
import subprocess
from typing import Any

from abench_speckz import interpolate


def run_phase(
    phase: str,
    steps: list[str],
    vars_: dict[str, Any],
    env: dict[str, str],
    timeout_seconds: int,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Run a list of shell-command steps.

    Returns ``(failure_reason, step_records)``. ``failure_reason`` is ``None`` on
    success; otherwise ``'<phase>[i]: <reason>'``. Records is a list of dicts
    with ``command``, ``exit_code``, ``stdout``, ``stderr`` for each step that
    was attempted (including the failing one when the failure came from the
    subprocess). Interpolation failures produce no record for that step.
    """
    records: list[dict[str, Any]] = []
    for i, template in enumerate(steps):
        try:
            cmd = interpolate.resolve_string(template, vars_, env=env)
        except Exception as e:
            return f"{phase}[{i}]: interpolation: {e}", records
        with subprocess.Popen(
            cmd,
            shell=True,
            start_new_session=True,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as proc:
            try:
                stdout, stderr = proc.communicate(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = proc.communicate()
                records.append(
                    {
                        "command": cmd,
                        "exit_code": -1,
                        "stdout": stdout,
                        "stderr": stderr,
                    }
                )
                return f"{phase}[{i}]: timeout after {timeout_seconds}s: {cmd}", records
        records.append(
            {
                "command": cmd,
                "exit_code": proc.returncode,
                "stdout": stdout,
                "stderr": stderr,
            }
        )
        if proc.returncode != 0:
            tail = (stderr or stdout or "").strip().splitlines()
            hint = tail[-1] if tail else ""
            reason = f"{phase}[{i}]: exit_code={proc.returncode}: {cmd}"
            return reason + (f" ({hint})" if hint else ""), records
    return None, records
