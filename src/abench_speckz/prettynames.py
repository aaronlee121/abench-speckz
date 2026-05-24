"""Persist + merge tool-scoped display names for metrics and variable axes."""

import json
from pathlib import Path

from abench_speckz.runspec import ToolSpec


def from_tool(tool: ToolSpec) -> dict:
    out: dict = {tool.name: dict(tool.pretty_names)}
    if tool.var_values:
        out["var_values"] = {k: dict(v) for k, v in tool.var_values.items()}
    if tool.units:
        out["units"] = dict(tool.units)
    return out


def merge(existing: dict, incoming: dict) -> dict:
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in existing.items()}
    for k, v in incoming.items():
        if k == "vars":
            out.setdefault("vars", {}).update(v)
        elif k == "var_values":
            existing_vv = out.setdefault("var_values", {})
            if isinstance(v, dict):
                for var_name, val_map in v.items():
                    existing_vv.setdefault(var_name, {}).update(val_map if isinstance(val_map, dict) else {})
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


def resolve_unit(mapping: dict, key: str) -> str:
    """Return the unit string for a metric key, or empty string if none."""
    units = mapping.get("units", {})
    if isinstance(units, dict):
        return units.get(key, "")
    return ""


def resolve_var_value(mapping: dict, var: str, value: str) -> str:
    """Return the display label for a combo variable value, or the raw value if no mapping exists."""
    var_values = mapping.get("var_values", {})
    if isinstance(var_values, dict):
        val_map = var_values.get(var, {})
        if isinstance(val_map, dict) and value in val_map:
            return val_map[value]
    return value


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
            if k in ("vars", "var_values", "units") or not isinstance(v, dict):
                continue
            if key in v:
                return v[key]
    if "vars" in mapping and key in mapping["vars"]:
        return mapping["vars"][key]
    return key
