import shlex
import subprocess
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from benchspec.runspec import RunRequest, RunResult


class Executor(ABC):
    @abstractmethod
    def execute(self, request: RunRequest) -> RunResult: ...


class LocalSubprocessExecutor(Executor):
    def execute(self, request: RunRequest) -> RunResult:
        started = datetime.now(timezone.utc)
        t0 = time.monotonic()
        try:
            result = subprocess.run(
                shlex.split(request.command),
                env=request.env,
                capture_output=True,
                text=True,
                timeout=request.timeout_seconds,
                check=False,
            )
            duration_ms = int((time.monotonic() - t0) * 1000)
            finished = datetime.now(timezone.utc)
            failure_reason = f"exit_code={result.returncode}" if result.returncode != 0 else None
            return RunResult(
                exit_code=result.returncode,
                duration_ms=duration_ms,
                started_at=started.isoformat().replace("+00:00", "Z"),
                finished_at=finished.isoformat().replace("+00:00", "Z"),
                stdout=result.stdout,
                stderr=result.stderr,
                failure_reason=failure_reason,
            )
        except subprocess.TimeoutExpired as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            finished = datetime.now(timezone.utc)
            return RunResult(
                exit_code=-1,
                duration_ms=duration_ms,
                started_at=started.isoformat().replace("+00:00", "Z"),
                finished_at=finished.isoformat().replace("+00:00", "Z"),
                stdout=e.stdout or "" if isinstance(e.stdout, str) else (e.stdout.decode() if e.stdout else ""),
                stderr=e.stderr or "" if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else ""),
                failure_reason="timeout",
            )
        except FileNotFoundError as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            finished = datetime.now(timezone.utc)
            return RunResult(
                exit_code=-1,
                duration_ms=duration_ms,
                started_at=started.isoformat().replace("+00:00", "Z"),
                finished_at=finished.isoformat().replace("+00:00", "Z"),
                stdout="",
                stderr=str(e),
                failure_reason=f"command_not_found: {e}",
            )
