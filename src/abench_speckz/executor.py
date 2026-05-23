"""Executor abstraction for running a single benchmark command as a subprocess."""

import os
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

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
