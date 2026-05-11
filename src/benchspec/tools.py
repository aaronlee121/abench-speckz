"""Parse a tool YAML into a ToolSpec and resolve the tool's reported version."""
import shlex
import subprocess
from pathlib import Path

import yaml

from benchspec.model import RunError
from benchspec.runspec import ToolSpec

_FIELDS = {
    "name", "command", "timeout_seconds", "version_command",
    "capture", "parser", "output_file", "output_format", "pretty_names", "units", "higher_is_better",
    "plots",
    "setup", "teardown", "setup_timeout_seconds",
}


def _load_phase(raw: dict, path, key: str) -> list[str]:
    value = raw.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise RunError(f"{path}: tool.{key} must be a list of shell-command strings")
    steps: list[str] = []
    for i, step in enumerate(value):
        if not isinstance(step, str):
            raise RunError(f"{path}: tool.{key}[{i}] must be a string, got {type(step).__name__}")
        steps.append(step)
    return steps


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

    output_file = raw.get("output_file")
    if output_file is not None and not isinstance(output_file, str):
        raise RunError(f"{path}: tool.output_file must be a string path")

    output_format = raw.get("output_format", "json")
    if output_format not in ("json", "jsonl"):
        raise RunError(f"{path}: tool.output_format must be 'json' or 'jsonl', got {output_format!r}")

    plots = raw.get("plots")
    if plots is not None and not isinstance(plots, list):
        raise RunError(f"{path}: tool.plots must be a list")

    setup = _load_phase(raw, path, "setup")
    teardown = _load_phase(raw, path, "teardown")

    setup_timeout = raw.get("setup_timeout_seconds", 120)
    if not isinstance(setup_timeout, int) or isinstance(setup_timeout, bool) or setup_timeout <= 0:
        raise RunError(f"{path}: tool.setup_timeout_seconds must be a positive integer")

    return ToolSpec(
        name=raw["name"],
        command=raw["command"],
        timeout_seconds=int(raw.get("timeout_seconds", 600)),
        version_command=raw.get("version_command"),
        capture=dict(capture),
        parser=parser,
        output_file=output_file,
        output_format=output_format,
        pretty_names=dict(raw.get("pretty_names") or {}),
        units=dict(raw.get("units") or {}),
        higher_is_better=dict(raw.get("higher_is_better") or {}),
        plots=list(plots or []),
        setup=setup,
        teardown=teardown,
        setup_timeout_seconds=setup_timeout,
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
