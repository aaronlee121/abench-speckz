"""Persist + merge tool-scoped display names for metrics and variable axes."""
import json
from pathlib import Path

from abench_speckz.runspec import ToolSpec


def from_tool(tool: ToolSpec) -> dict[str, dict[str, str]]:
    return {tool.name: dict(tool.pretty_names)}


def merge(existing: dict, incoming: dict) -> dict:
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in existing.items()}
    for k, v in incoming.items():
        if k == "vars":
            out.setdefault("vars", {}).update(v)
        elif isinstance(v, dict):
            out.setdefault(k, {}).update(v)
        else:
            out[k] = v
    return out


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def resolve(mapping: dict, key: str, *, tool: str | None = None) -> str:
    """Fall through current-tool → any tool → vars → raw key.

    When the caller doesn't know which tool produced ``key`` (e.g. the stats
    renderer just has a column name), we still want metric pretty-names to
    work; in that case we search every tool-scoped mapping in turn.
    """
    if tool and tool in mapping and key in mapping[tool]:
        return mapping[tool][key]
    if tool is None:
        for k, v in mapping.items():
            if k == "vars" or not isinstance(v, dict):
                continue
            if key in v:
                return v[key]
    if "vars" in mapping and key in mapping["vars"]:
        return mapping["vars"][key]
    return key
