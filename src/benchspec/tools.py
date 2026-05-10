import shlex
import subprocess
from pathlib import Path

import yaml

from benchspec.model import RunError
from benchspec.runspec import ToolSpec


_FIELDS = {
    "name", "command", "timeout_seconds", "version_command",
    "capture", "parser", "pretty_names", "units", "higher_is_better",
}


def load_tool(path: str | Path) -> ToolSpec:
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise RunError(f"{path}: tool YAML root must be a mapping")

    unknown = set(raw) - _FIELDS
    if unknown:
        raise RunError(f"{path}: unknown tool fields: {sorted(unknown)}")

    if "name" not in raw or not isinstance(raw["name"], str):
        raise RunError(f"{path}: tool.name (string) is required")
    if "command" not in raw or not isinstance(raw["command"], str):
        raise RunError(f"{path}: tool.command (string) is required")

    parser = raw.get("parser")
    capture = raw.get("capture") or {}
    if parser is not None and not isinstance(parser, str):
        raise RunError(f"{path}: tool.parser must be a string like 'module:func'")
    if not isinstance(capture, dict):
        raise RunError(f"{path}: tool.capture must be a mapping")

    return ToolSpec(
        name=raw["name"],
        command=raw["command"],
        timeout_seconds=int(raw.get("timeout_seconds", 600)),
        version_command=raw.get("version_command"),
        capture=dict(capture),
        parser=parser,
        pretty_names=dict(raw.get("pretty_names") or {}),
        units=dict(raw.get("units") or {}),
        higher_is_better=dict(raw.get("higher_is_better") or {}),
    )


def resolve_tool_version(tool: ToolSpec) -> str | None:
    if not tool.version_command:
        return None
    try:
        result = subprocess.run(
            shlex.split(tool.version_command),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"unknown ({type(e).__name__})"
    out = (result.stdout or result.stderr or "").strip().splitlines()
    return out[0] if out else None
