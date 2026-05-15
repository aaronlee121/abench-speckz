"""Read manifest.json and combo env-files; capture host/git metadata at sweep start."""
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any

from abench_speckz.model import RunError


def load_manifest(path: Path) -> tuple[Path, list[dict[str, Any]]]:
    """Resolve *path* (file or dir) to a manifest.json and return (path, entries)."""
    manifest_path = path / "manifest.json" if path.is_dir() else path
    if not manifest_path.exists():
        raise RunError(f"manifest not found: {manifest_path}")
    data = json.loads(manifest_path.read_text())
    if not isinstance(data, list):
        raise RunError(f"{manifest_path}: expected an array")
    return manifest_path, data


def load_envfile(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE env-file, skipping blank lines and ``#`` comments."""
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v
    return out


def collect_env_metadata() -> dict[str, Any]:
    """Snapshot host/python info plus the current git HEAD sha (best effort)."""
    info: dict[str, Any] = {
        "host": platform.node(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
    }
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        if result.returncode == 0:
            info["git_sha"] = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return info
