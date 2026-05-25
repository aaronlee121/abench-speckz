"""Run a list of shell-command steps (setup/teardown/post_run) with structured records."""

import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import Any

from abench_speckz import interpolate
from abench_speckz._relay import relay_tee
from abench_speckz.runspec import Step


def run_phase(
    phase: str,
    steps: list[Step],
    vars_: dict[str, Any],
    env: dict[str, str],
    timeout_seconds: int,
    log_dir: Path | None = None,
    log_prefix: str = "",
) -> tuple[str | None, list[dict[str, Any]]]:
    """Run a list of shell-command steps, skipping those whose ``when`` filter doesn't match.

    Returns ``(failure_reason, step_records)``. ``failure_reason`` is ``None`` on
    success; otherwise ``'<phase>[i]: <reason>'``. Records is a list of dicts
    with ``command``, ``exit_code``, ``stdout``, ``stderr`` for each step that
    was attempted (including the failing one when the failure came from the
    subprocess). Interpolation failures produce no record for that step.
    Skipped steps (``when`` filter not satisfied) produce no record.

    When *log_dir* is provided, stdout and stderr are streamed to
    ``<log_dir>/<log_prefix>--<i>.out.log`` / ``.err.log`` in real time so that
    ``abench-speckz tail`` can follow them, while still being captured for the
    step records.
    """
    records: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        if not step.matches(vars_):
            continue
        try:
            cmd = interpolate.resolve_string(step.run, vars_, env=env)
        except Exception as e:
            return f"{phase}[{i}]: interpolation: {e}", records

        if log_dir is not None:
            stdout, stderr, exit_code, timed_out = _run_step_streaming(
                cmd, env, timeout_seconds, log_dir, f"{log_prefix}--{i}"
            )
        else:
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
                    exit_code, timed_out = proc.returncode, False
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    stdout, stderr = proc.communicate()
                    exit_code, timed_out = -1, True
                except KeyboardInterrupt:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.communicate()
                    raise

        if timed_out:
            records.append({"command": cmd, "exit_code": exit_code, "stdout": stdout, "stderr": stderr})
            return f"{phase}[{i}]: timeout after {timeout_seconds}s: {cmd}", records

        records.append({"command": cmd, "exit_code": exit_code, "stdout": stdout, "stderr": stderr})
        if exit_code != 0:
            tail = (stderr or stdout or "").strip().splitlines()
            hint = tail[-1] if tail else ""
            reason = f"{phase}[{i}]: exit_code={exit_code}: {cmd}"
            return reason + (f" ({hint})" if hint else ""), records
    return None, records


def _run_step_streaming(
    cmd: str,
    env: dict[str, str],
    timeout_seconds: int,
    log_dir: Path,
    log_prefix: str,
) -> tuple[str, str, int, bool]:
    """Run *cmd* streaming stdout/stderr to log files; return (stdout, stderr, exit_code, timed_out)."""
    log_dir.mkdir(parents=True, exist_ok=True)
    out_path = log_dir / f"{log_prefix}.out.log"
    err_path = log_dir / f"{log_prefix}.err.log"
    stdout_buf: list[bytes] = []
    stderr_buf: list[bytes] = []
    timed_out = False
    with open(out_path, "wb") as out_f, open(err_path, "wb") as err_f:  # noqa: SIM115
        with subprocess.Popen(
            cmd,
            shell=True,
            start_new_session=True,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as proc:
            out_t = threading.Thread(target=relay_tee, args=(proc.stdout, out_f, stdout_buf), daemon=True)
            err_t = threading.Thread(target=relay_tee, args=(proc.stderr, err_f, stderr_buf), daemon=True)
            out_t.start()
            err_t.start()
            try:
                proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                timed_out = True
            except KeyboardInterrupt:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                raise
            out_t.join()
            err_t.join()
        exit_code = proc.returncode
    stdout = b"".join(stdout_buf).decode(errors="replace")
    stderr = b"".join(stderr_buf).decode(errors="replace")
    return stdout, stderr, exit_code, timed_out
