"""Launch and terminate side-car monitor processes that run alongside the benchmark."""

import os
import signal
import subprocess
import threading
import tty
from pathlib import Path
from typing import IO, Any

from abench_speckz import interpolate

_MONITOR_STOP_GRACE = 5  # seconds before escalating to SIGKILL


class _LogHandle:
    """Owns a monitor log file and its PTY relay thread."""

    __slots__ = ("_file", "_master_fd", "_relay_thread")

    def __init__(
        self,
        file: IO,
        master_fd: int | None = None,
        relay_thread: threading.Thread | None = None,
    ) -> None:
        self._file = file
        self._master_fd = master_fd
        self._relay_thread = relay_thread

    @property
    def closed(self) -> bool:
        return self._file is None or self._file.closed

    def close(self) -> None:
        """Drain the relay thread then close the log file."""
        if self._relay_thread is not None:
            # Child has already exited; relay will drain remaining PTY data and stop.
            self._relay_thread.join(timeout=5)
            if self._relay_thread.is_alive() and self._master_fd is not None:
                # Stuck — force-close master_fd to unblock the relay read.
                try:
                    os.close(self._master_fd)
                except OSError:
                    pass
                self._master_fd = None
                self._relay_thread.join(timeout=2)
            self._relay_thread = None
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass


def _relay_pty(master_fd: int, log_file: IO) -> None:
    """Drain the PTY master and stream bytes to log_file in real time."""
    try:
        while True:
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            log_file.write(chunk)
            log_file.flush()
    except Exception:
        pass


def run_monitors(
    steps: list[str],
    vars_: dict[str, Any],
    env: dict[str, str],
    log_dir: Path | None = None,
    log_prefix: str = "monitor",
) -> tuple[list[subprocess.Popen], list[dict[str, Any]], list[_LogHandle | None]]:
    """Launch each monitor command as a background process.

    When *log_dir* is given each monitor's stdout+stderr are streamed in real
    time to ``<log_dir>/<log_prefix>-<n>.log`` via a PTY, so ``tail -f`` works
    even for commands that buffer their stdout. Otherwise output is captured via
    pipes and returned by ``stop_monitors``.

    Returns ``(running_procs, start_records, log_handles)``. Commands that fail
    to start are recorded but never abort the run.
    """
    procs: list[subprocess.Popen] = []
    records: list[dict[str, Any]] = []
    log_handles: list[_LogHandle | None] = []
    for i, template in enumerate(steps):
        try:
            cmd = interpolate.resolve_string(template, vars_, env=env)
        except Exception as e:
            records.append({"command": template, "pid": None, "error": f"interpolation: {e}"})
            continue

        handle: _LogHandle | None = None
        log_path: Path | None = None
        master_fd: int | None = None
        slave_fd: int | None = None

        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{log_prefix}-{i}.log"
            master_fd, slave_fd = os.openpty()
            tty.setraw(slave_fd)
            log_file: IO = open(log_path, "wb")  # noqa: SIM115
            relay = threading.Thread(
                target=_relay_pty,
                args=(master_fd, log_file),
                daemon=True,
                name=f"monitor-pty-relay-{i}",
            )
            relay.start()
            stdout_arg: Any = slave_fd
            stderr_arg: Any = subprocess.STDOUT
            handle = _LogHandle(log_file, master_fd, relay)
        else:
            stdout_arg = subprocess.PIPE
            stderr_arg = subprocess.PIPE

        try:
            proc = subprocess.Popen(
                cmd,
                shell=True,
                start_new_session=True,
                env=env,
                stdout=stdout_arg,
                stderr=stderr_arg,
                text=(log_dir is None),
            )
            if slave_fd is not None:
                os.close(slave_fd)
                slave_fd = None
            procs.append(proc)
            log_handles.append(handle)
            record: dict[str, Any] = {"command": cmd, "pid": proc.pid}
            if log_path is not None:
                record["log_path"] = str(log_path)
            records.append(record)
        except Exception as e:
            if slave_fd is not None:
                try:
                    os.close(slave_fd)
                except OSError:
                    pass
            if handle is not None:
                handle.close()
            records.append({"command": cmd, "pid": None, "error": str(e)})
    return procs, records, log_handles


def stop_monitors(
    procs: list[subprocess.Popen],
    log_files: list[_LogHandle | None] | None = None,
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
