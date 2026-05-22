"""Parse a tool YAML into a ToolSpec and resolve the tool's reported version."""

import shlex
import subprocess
from pathlib import Path

import yaml

from abench_speckz.model import RunError
from abench_speckz.runspec import MonitorEntry, ToolSpec, _MONITOR_SPANS

_FIELDS = {
    "name",
    "command",
    "timeout_seconds",
    "version_command",
    "capture",
    "parser",
    "output_file",
    "output_format",
    "pretty_names",
    "var_values",
    "units",
    "higher_is_better",
    "plots",
    "env_probes",
    "combo_probes",
    "setup",
    "teardown",
    "post_run",
    "monitor",
    "setup_timeout_seconds",
    "setup_per_sweep",
    "teardown_per_sweep",
    "per_sweep_var",
    "grafana_links",
}


def _load_monitors(raw: dict, path) -> list[MonitorEntry]:
    value = raw.get("monitor")
    if value is None:
        return []
    if not isinstance(value, list):
        raise RunError(f"{path}: tool.monitor must be a list")
    entries: list[MonitorEntry] = []
    for i, item in enumerate(value):
        if isinstance(item, str):
            entries.append(MonitorEntry(command=item))
        elif isinstance(item, dict):
            cmd = item.get("command")
            if not isinstance(cmd, str) or not cmd:
                raise RunError(f"{path}: tool.monitor[{i}].command must be a non-empty string")
            span = item.get("span", "per_rep")
            if span not in _MONITOR_SPANS:
                raise RunError(
                    f"{path}: tool.monitor[{i}].span must be one of {list(_MONITOR_SPANS)}, got {span!r}"
                )
            unknown = set(item) - {"command", "span"}
            if unknown:
                raise RunError(f"{path}: tool.monitor[{i}] has unknown keys: {sorted(unknown)}")
            entries.append(MonitorEntry(command=cmd, span=span))
        else:
            raise RunError(
                f"{path}: tool.monitor[{i}] must be a string or mapping, got {type(item).__name__}"
            )
    return entries


def _load_grafana_links(raw: dict, path) -> list[dict]:
    value = raw.get("grafana_links")
    if value is None:
        return []
    if not isinstance(value, list):
        raise RunError(f"{path}: tool.grafana_links must be a list")
    entries: list[dict] = []
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise RunError(
                f"{path}: tool.grafana_links[{i}] must be a mapping, got {type(item).__name__}"
            )
        label = item.get("label")
        url = item.get("url")
        if not isinstance(label, str) or not label:
            raise RunError(f"{path}: tool.grafana_links[{i}].label must be a non-empty string")
        if not isinstance(url, str) or not url:
            raise RunError(f"{path}: tool.grafana_links[{i}].url must be a non-empty string")
        unknown = set(item) - {"label", "url"}
        if unknown:
            raise RunError(
                f"{path}: tool.grafana_links[{i}] has unknown keys: {sorted(unknown)}"
            )
        entries.append({"label": label, "url": url})
    return entries


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


def _parse_var_values(raw, path) -> dict[str, dict[str, str]]:
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise RunError(f"{path}: tool.var_values must be a mapping")
    out: dict[str, dict[str, str]] = {}
    for var, val_map in raw.items():
        if not isinstance(val_map, dict):
            raise RunError(f"{path}: tool.var_values.{var} must be a mapping")
        out[var] = {str(k): str(v) for k, v in val_map.items()}
    return out


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

    env_probes = raw.get("env_probes") or {}
    if not isinstance(env_probes, dict):
        raise RunError(f"{path}: tool.env_probes must be a mapping")
    for k, v in env_probes.items():
        if not isinstance(k, str) or not k:
            raise RunError(f"{path}: tool.env_probes keys must be non-empty strings")
        if not isinstance(v, str) or not v:
            raise RunError(f"{path}: tool.env_probes[{k!r}] value must be a non-empty command string")

    combo_probes = raw.get("combo_probes") or {}
    if not isinstance(combo_probes, dict):
        raise RunError(f"{path}: tool.combo_probes must be a mapping")
    for k, v in combo_probes.items():
        if not isinstance(k, str) or not k:
            raise RunError(f"{path}: tool.combo_probes keys must be non-empty strings")
        if not isinstance(v, str) or not v:
            raise RunError(f"{path}: tool.combo_probes[{k!r}] value must be a non-empty command string")

    setup = _load_phase(raw, path, "setup")
    teardown = _load_phase(raw, path, "teardown")
    post_run = _load_phase(raw, path, "post_run")
    monitor = _load_monitors(raw, path)
    setup_per_sweep = _load_phase(raw, path, "setup_per_sweep")
    teardown_per_sweep = _load_phase(raw, path, "teardown_per_sweep")
    grafana_links = _load_grafana_links(raw, path)

    per_sweep_var = raw.get("per_sweep_var")
    if per_sweep_var is not None:
        entries = [per_sweep_var] if isinstance(per_sweep_var, str) else per_sweep_var
        if not isinstance(entries, list) or not entries:
            raise RunError(f"{path}: tool.per_sweep_var must be a non-empty string or list of strings")
        for i, entry in enumerate(entries):
            if not isinstance(entry, str) or not entry:
                raise RunError(
                    f"{path}: tool.per_sweep_var[{i}] must be a non-empty string"
                )
            bare = entry[1:] if entry.startswith("!") else entry
            if not bare:
                raise RunError(
                    f"{path}: tool.per_sweep_var[{i}] '!' must be followed by a variable name"
                )

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
        var_values=_parse_var_values(raw.get("var_values"), path),
        units=dict(raw.get("units") or {}),
        higher_is_better=dict(raw.get("higher_is_better") or {}),
        plots=list(plots or []),
        env_probes=dict(env_probes),
        combo_probes=dict(combo_probes),
        setup=setup,
        teardown=teardown,
        post_run=post_run,
        monitor=monitor,
        setup_timeout_seconds=setup_timeout,
        setup_per_sweep=setup_per_sweep,
        teardown_per_sweep=teardown_per_sweep,
        per_sweep_var=per_sweep_var,
        grafana_links=grafana_links,
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
