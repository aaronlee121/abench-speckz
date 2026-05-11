import shlex
import subprocess
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from benchspec.runspec import RunRequest, RunResult


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _to_str(buf) -> str:
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
        try:
            result = subprocess.run(
                shlex.split(request.command),
                env=request.env,
                capture_output=True,
                text=True,
                timeout=request.timeout_seconds,
                check=False,
            )
            return RunResult(
                exit_code=result.returncode,
                duration_ms=int((time.monotonic() - t0) * 1000),
                started_at=started_at,
                finished_at=_now_iso(),
                stdout=result.stdout,
                stderr=result.stderr,
                failure_reason=f"exit_code={result.returncode}" if result.returncode != 0 else None,
            )
        except subprocess.TimeoutExpired as e:
            return RunResult(
                exit_code=-1,
                duration_ms=int((time.monotonic() - t0) * 1000),
                started_at=started_at,
                finished_at=_now_iso(),
                stdout=_to_str(e.stdout),
                stderr=_to_str(e.stderr),
                failure_reason="timeout",
            )
        except FileNotFoundError as e:
            return RunResult(
                exit_code=-1,
                duration_ms=int((time.monotonic() - t0) * 1000),
                started_at=started_at,
                finished_at=_now_iso(),
                stdout="",
                stderr=str(e),
                failure_reason=f"command_not_found: {e}",
            )
