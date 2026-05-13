"""PlotSpec dataclass and validators for declarative chart definitions."""
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from abench_speckz.model import RunError

ALLOWED_TYPES = {"bar", "stacked-bar", "line", "scatter"}


@dataclass
class PlotSpec:
    id: str
    type: str
    x: str
    y: list[str]
    title: str | None = None
    group_by: list[str] | None = None


def parse_plots(raw: Any, source: str) -> list[PlotSpec]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RunError(f"{source}: plots must be a list")

    out: list[PlotSpec] = []
    seen_ids: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RunError(f"{source}: plots[{i}] must be a mapping")

        plot_id = item.get("id")
        if not isinstance(plot_id, str) or not plot_id:
            raise RunError(f"{source}: plots[{i}].id is required (string)")
        if plot_id in seen_ids:
            raise RunError(f"{source}: duplicate plot id {plot_id!r}")
        seen_ids.add(plot_id)

        ptype = item.get("type")
        if ptype not in ALLOWED_TYPES:
            raise RunError(
                f"{source}: plot {plot_id!r}: type must be one of "
                f"{sorted(ALLOWED_TYPES)}, got {ptype!r}"
            )

        x = item.get("x")
        if not isinstance(x, str) or not x:
            raise RunError(f"{source}: plot {plot_id!r}: x is required (string)")

        y_raw = item.get("y")
        if y_raw is None:
            raise RunError(f"{source}: plot {plot_id!r}: y is required")
        if isinstance(y_raw, str):
            y = [y_raw]
        elif isinstance(y_raw, list) and all(isinstance(m, str) and m for m in y_raw):
            y = list(y_raw)
        else:
            raise RunError(
                f"{source}: plot {plot_id!r}: y must be a string or list of strings"
            )
        if not y:
            raise RunError(f"{source}: plot {plot_id!r}: y must not be empty")

        if ptype == "scatter" and len(y) != 1:
            raise RunError(
                f"{source}: plot {plot_id!r}: scatter requires exactly one y metric"
            )
        if ptype == "stacked-bar" and len(y) < 2:
            raise RunError(
                f"{source}: plot {plot_id!r}: stacked-bar requires at least two y metrics"
            )

        title = item.get("title")
        if title is not None and not isinstance(title, str):
            raise RunError(f"{source}: plot {plot_id!r}: title must be a string")

        group_by_raw = item.get("group_by")
        if group_by_raw is None:
            group_by = None
        elif isinstance(group_by_raw, str):
            if not group_by_raw:
                raise RunError(f"{source}: plot {plot_id!r}: group_by must be a non-empty string or list")
            group_by = [group_by_raw]
        elif isinstance(group_by_raw, list):
            if not group_by_raw:
                raise RunError(f"{source}: plot {plot_id!r}: group_by list must not be empty")
            for j, k in enumerate(group_by_raw):
                if not isinstance(k, str) or not k:
                    raise RunError(f"{source}: plot {plot_id!r}: group_by[{j}] must be a non-empty string")
            group_by = list(group_by_raw)
        else:
            raise RunError(f"{source}: plot {plot_id!r}: group_by must be a string or list of strings")

        unknown = set(item) - {"id", "type", "x", "y", "title", "group_by"}
        if unknown:
            raise RunError(
                f"{source}: plot {plot_id!r}: unknown fields {sorted(unknown)}"
            )

        out.append(PlotSpec(id=plot_id, type=ptype, x=x, y=y, title=title, group_by=group_by))
    return out


def load_plots_file(path: Path) -> list[PlotSpec]:
    if not path.exists():
        raise RunError(f"{path}: plots file not found")
    raw = yaml.safe_load(path.read_text())
    if isinstance(raw, dict) and "plots" in raw:
        raw = raw["plots"]
    return parse_plots(raw, source=str(path))


def discover_from_tool_snapshots(results_dir: Path) -> list[PlotSpec]:
    tools_dir = results_dir / "tools"
    if not tools_dir.is_dir():
        return []
    out: list[PlotSpec] = []
    seen_ids: set[str] = set()
    for tool_yaml in sorted(tools_dir.glob("*.yaml")):
        raw = yaml.safe_load(tool_yaml.read_text())
        if not isinstance(raw, dict):
            continue
        for spec in parse_plots(raw.get("plots"), source=str(tool_yaml)):
            if spec.id in seen_ids:
                continue
            seen_ids.add(spec.id)
            out.append(spec)
    return out
