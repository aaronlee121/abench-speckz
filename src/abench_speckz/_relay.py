"""Low-level byte-relay helpers shared by the executor, phases, and monitors."""

from typing import IO


def relay_tee(pipe: IO[bytes], log_file: IO[bytes], buf: list[bytes]) -> None:
    """Drain *pipe*, write to *log_file* in real time, and collect chunks in *buf*."""
    try:
        while True:
            chunk = pipe.read(4096)
            if not chunk:
                break
            log_file.write(chunk)
            log_file.flush()
            buf.append(chunk)
    except Exception:
        pass
