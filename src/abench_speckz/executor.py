"""Executor abstraction for running a single benchmark command as a subprocess."""

import os
import signal
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from abench_speckz._relay import relay_tee
from abench_speckz.runspec import RunRequest, RunResult


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _to_str(buf: Any) -> str:
    if buf is None:
        return ""
    if isinstance(buf, bytes):
        return buf.decode(errors="replace")
    return buf


class Executor(ABC):
    @abstractmethod
    def execute(self, request: RunRequest) -> RunResult: ...


class LocalSubprocessExecutor(Executor):
    def execute(self, request: RunRequest) -> RunResult:
        started_at = _now_iso()
        t0 = time.monotonic()

        if request.log_dir is not None:
            return self._execute_streaming(request, started_at, t0)

        with subprocess.Popen(
            request.command,
            shell=True,
            start_new_session=True,
            env=request.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as proc:
            try:
                stdout, stderr = proc.communicate(timeout=request.timeout_seconds)
                return RunResult(
                    exit_code=proc.returncode,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    started_at=started_at,
                    finished_at=_now_iso(),
                    stdout=stdout,
                    stderr=stderr,
                    failure_reason=f"exit_code={proc.returncode}" if proc.returncode != 0 else None,
                )
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = proc.communicate()
                return RunResult(
                    exit_code=-1,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    started_at=started_at,
                    finished_at=_now_iso(),
                    stdout=stdout,
                    stderr=stderr,
                    failure_reason="timeout",
                )
            except KeyboardInterrupt:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.communicate()
                raise

    def _execute_streaming(self, request: RunRequest, started_at: str, t0: float) -> RunResult:
        log_dir = request.log_dir
        assert log_dir is not None
        log_dir.mkdir(parents=True, exist_ok=True)
        out_path = log_dir / f"{request.log_prefix}.out.log"
        err_path = log_dir / f"{request.log_prefix}.err.log"
        stdout_buf: list[bytes] = []
        stderr_buf: list[bytes] = []
        with open(out_path, "wb") as out_f, open(err_path, "wb") as err_f:  # noqa: SIM115
            with subprocess.Popen(
                request.command,
                shell=True,
                start_new_session=True,
                env=request.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ) as proc:
                out_t = threading.Thread(target=relay_tee, args=(proc.stdout, out_f, stdout_buf), daemon=True)
                err_t = threading.Thread(target=relay_tee, args=(proc.stderr, err_f, stderr_buf), daemon=True)
                out_t.start()
                err_t.start()
                timed_out = False
                try:
                    proc.wait(timeout=request.timeout_seconds)
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
        if timed_out:
            return RunResult(
                exit_code=-1,
                duration_ms=int((time.monotonic() - t0) * 1000),
                started_at=started_at,
                finished_at=_now_iso(),
                stdout=stdout,
                stderr=stderr,
                failure_reason="timeout",
            )
        return RunResult(
            exit_code=exit_code,
            duration_ms=int((time.monotonic() - t0) * 1000),
            started_at=started_at,
            finished_at=_now_iso(),
            stdout=stdout,
            stderr=stderr,
            failure_reason=f"exit_code={exit_code}" if exit_code != 0 else None,
        )
