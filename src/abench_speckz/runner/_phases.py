"""Run a list of shell-command steps (setup/teardown/post_run) with structured records."""

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
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            records.append(
                {
                    "command": cmd,
                    "exit_code": -1,
                    "stdout": _to_str(e.stdout),
                    "stderr": _to_str(e.stderr),
                }
            )
            return f"{phase}[{i}]: timeout after {timeout_seconds}s: {cmd}", records
        records.append(
            {
                "command": cmd,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        if result.returncode != 0:
            tail = (result.stderr or result.stdout or "").strip().splitlines()
            hint = tail[-1] if tail else ""
            reason = f"{phase}[{i}]: exit_code={result.returncode}: {cmd}"
            return reason + (f" ({hint})" if hint else ""), records
    return None, records


def _to_str(buf: Any) -> str:
    if buf is None:
        return ""
    if isinstance(buf, bytes):
        return buf.decode(errors="replace")
    return buf
