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
    """Owns the stdout/stderr log files and their relay threads for one monitor."""

    __slots__ = ("_out_file", "_err_file", "_out_thread", "_err_thread", "_master_fd")

    def __init__(
        self,
        out_file: IO,
        err_file: IO,
        out_thread: threading.Thread,
        err_thread: threading.Thread,
        master_fd: int,
    ) -> None:
        self._out_file = out_file
        self._err_file = err_file
        self._out_thread: threading.Thread | None = out_thread
        self._err_thread: threading.Thread | None = err_thread
        self._master_fd: int | None = master_fd

    @property
    def closed(self) -> bool:
        return self._out_file.closed and self._err_file.closed

    def close(self) -> None:
        """Drain relay threads then close log files."""
        # stdout relay reads from the PTY master; force-close master_fd if stuck.
        if self._out_thread is not None:
            self._out_thread.join(timeout=5)
            if self._out_thread.is_alive() and self._master_fd is not None:
                try:
                    os.close(self._master_fd)
                except OSError:
                    pass
                self._master_fd = None
                self._out_thread.join(timeout=2)
            self._out_thread = None
        # stderr relay reads from a PIPE; it exits naturally when the child closes
        # its write end, which happens as soon as the child exits.
        if self._err_thread is not None:
            self._err_thread.join(timeout=5)
            self._err_thread = None
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None
        for f in (self._out_file, self._err_file):
            if f is not None:
                try:
                    f.close()
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


def _relay_pipe(stderr_pipe: IO, log_file: IO) -> None:
    """Drain a stderr PIPE and stream bytes to log_file in real time."""
    try:
        while True:
            chunk = stderr_pipe.read(4096)
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

    When *log_dir* is given, stdout and stderr are captured separately:
    stdout goes through a PTY (so programs use line buffering) into
    ``<log_prefix>-<n>.out.log``; stderr goes through a PIPE into
    ``<log_prefix>-<n>.err.log``. Both files are readable with ``tail -f``
    in real time and surfaced by ``abench-speckz tail`` with distinct labels.

    Without *log_dir*, both streams are captured via pipes and returned by
    ``stop_monitors``.

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
        log_path_out: Path | None = None
        log_path_err: Path | None = None
        master_fd: int | None = None
        slave_fd: int | None = None
        out_file: IO | None = None
        err_file: IO | None = None

        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path_out = log_dir / f"{log_prefix}-{i}.out.log"
            log_path_err = log_dir / f"{log_prefix}-{i}.err.log"
            master_fd, slave_fd = os.openpty()
            tty.setraw(slave_fd)
            out_file = open(log_path_out, "wb")  # noqa: SIM115
            err_file = open(log_path_err, "wb")  # noqa: SIM115
            stdout_arg: Any = slave_fd
            stderr_arg: Any = subprocess.PIPE
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

            if log_dir is not None:
                out_thread = threading.Thread(
                    target=_relay_pty,
                    args=(master_fd, out_file),
                    daemon=True,
                    name=f"monitor-pty-relay-{i}",
                )
                err_thread = threading.Thread(
                    target=_relay_pipe,
                    args=(proc.stderr, err_file),
                    daemon=True,
                    name=f"monitor-pipe-relay-{i}",
                )
                out_thread.start()
                err_thread.start()
                assert out_file is not None
                assert err_file is not None
                assert master_fd is not None
                handle = _LogHandle(out_file, err_file, out_thread, err_thread, master_fd)

            procs.append(proc)
            log_handles.append(handle)
            record: dict[str, Any] = {"command": cmd, "pid": proc.pid}
            if log_path_out is not None:
                record["log_path_out"] = str(log_path_out)
                record["log_path_err"] = str(log_path_err)
            records.append(record)
        except Exception as e:
            if slave_fd is not None:
                try:
                    os.close(slave_fd)
                except OSError:
                    pass
            if master_fd is not None and handle is None:
                try:
                    os.close(master_fd)
                except OSError:
                    pass
            for f in (out_file, err_file):
                if f is not None:
                    try:
                        f.close()
                    except Exception:
                        pass
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
            stdout: str | None = None
            stderr: str | None = None
            try:
                if log_file is None:
                    stdout, stderr = proc.communicate(timeout=_MONITOR_STOP_GRACE)
                else:
                    proc.wait(timeout=_MONITOR_STOP_GRACE)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                if log_file is None:
                    stdout, stderr = proc.communicate()
                else:
                    proc.wait()
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
