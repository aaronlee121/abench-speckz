"""PlotSpec dataclass and validators for declarative chart definitions."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from abench_speckz.expr import is_expr, validate_expr_syntax
from abench_speckz.model import RunError

ALLOWED_TYPES = {"bar", "stacked-bar", "line", "scatter"}
BLOCK_KINDS = ("text", "plot_id", "html", "include_html")


@dataclass
class PlotSpec:
    id: str
    type: str
    x: str
    y: list[str]
    title: str | None = None
    group_by: list[str] | None = None
    smooth: bool = False
    fixed_vars: bool = True
    series_label: str | None = None


@dataclass
class Block:
    kind: str  # one of BLOCK_KINDS
    value: str  # markdown source (text), plot id (plot_id), or final HTML (html/include_html)


@dataclass
class Section:
    title: str
    description: str | None = None  # markdown source
    blocks: list[Block] = field(default_factory=list)


@dataclass
class PlotsDoc:
    plots: list[PlotSpec] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    report_title: str | None = None
    report_description: str | None = None


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
                f"{source}: plot {plot_id!r}: type must be one of {sorted(ALLOWED_TYPES)}, got {ptype!r}"
            )

        x = item.get("x")
        if not isinstance(x, str) or not x:
            raise RunError(f"{source}: plot {plot_id!r}: x is required (string)")
        if not x.strip().isidentifier():
            validate_expr_syntax(x, f"{source}: plot {plot_id!r}: x")

        y_raw = item.get("y")
        if y_raw is None:
            raise RunError(f"{source}: plot {plot_id!r}: y is required")
        if isinstance(y_raw, str):
            y = [y_raw]
        elif isinstance(y_raw, list) and all(isinstance(m, str) and m for m in y_raw):
            y = list(y_raw)
        else:
            raise RunError(f"{source}: plot {plot_id!r}: y must be a string or list of strings")
        if not y:
            raise RunError(f"{source}: plot {plot_id!r}: y must not be empty")
        for j, yi in enumerate(y):
            if not yi.strip().isidentifier():
                validate_expr_syntax(yi, f"{source}: plot {plot_id!r}: y[{j}]")

        if ptype == "scatter" and len(y) != 1:
            raise RunError(f"{source}: plot {plot_id!r}: scatter requires exactly one y metric")
        if ptype == "stacked-bar" and len(y) < 2:
            raise RunError(f"{source}: plot {plot_id!r}: stacked-bar requires at least two y metrics")

        title = item.get("title")
        if title is not None and not isinstance(title, str):
            raise RunError(f"{source}: plot {plot_id!r}: title must be a string")

        group_by_raw = item.get("group_by")
        if group_by_raw is None:
            group_by = None
        else:
            if isinstance(group_by_raw, str):
                entries = [group_by_raw]
            elif isinstance(group_by_raw, list):
                entries = group_by_raw
            else:
                raise RunError(f"{source}: plot {plot_id!r}: group_by must be a string or list of strings")
            if not entries:
                raise RunError(f"{source}: plot {plot_id!r}: group_by list must not be empty")
            for j, k in enumerate(entries):
                if not isinstance(k, str) or not k:
                    raise RunError(f"{source}: plot {plot_id!r}: group_by[{j}] must be a non-empty string")
                if k.startswith("!") and not k[1:]:
                    raise RunError(
                        f"{source}: plot {plot_id!r}: group_by[{j}] '!' must be followed by a variable name"
                    )
            group_by = list(entries)

        smooth_raw = item.get("smooth", False)
        if not isinstance(smooth_raw, bool):
            raise RunError(f"{source}: plot {plot_id!r}: smooth must be a boolean")
        if smooth_raw and ptype != "line":
            raise RunError(f"{source}: plot {plot_id!r}: smooth is only valid for line plots")
        smooth = smooth_raw

        fixed_vars_raw = item.get("fixed_vars", True)
        if not isinstance(fixed_vars_raw, bool):
            raise RunError(f"{source}: plot {plot_id!r}: fixed_vars must be a boolean")
        fixed_vars = fixed_vars_raw

        series_label_raw = item.get("series_label")
        if series_label_raw is not None and not isinstance(series_label_raw, str):
            raise RunError(f"{source}: plot {plot_id!r}: series_label must be a string")
        series_label = series_label_raw or None

        unknown = set(item) - {"id", "type", "x", "y", "title", "group_by", "smooth", "fixed_vars", "series_label"}
        if unknown:
            raise RunError(f"{source}: plot {plot_id!r}: unknown fields {sorted(unknown)}")

        out.append(PlotSpec(id=plot_id, type=ptype, x=x, y=y, title=title, group_by=group_by, smooth=smooth, fixed_vars=fixed_vars, series_label=series_label))
    return out


def parse_sections(
    raw: Any,
    *,
    source: str,
    plot_ids: set[str],
    base_dir: Path,
) -> list[Section]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RunError(f"{source}: sections must be a list")

    out: list[Section] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RunError(f"{source}: sections[{i}] must be a mapping")

        title = item.get("title")
        if not isinstance(title, str) or not title:
            raise RunError(f"{source}: sections[{i}].title is required (string)")

        description = item.get("description")
        if description is not None and not isinstance(description, str):
            raise RunError(f"{source}: section {title!r}: description must be a string")

        blocks_raw = item.get("blocks")
        if blocks_raw is None:
            blocks: list[Block] = []
        elif not isinstance(blocks_raw, list):
            raise RunError(f"{source}: section {title!r}: blocks must be a list")
        else:
            blocks = [
                _parse_block(b, j, source=source, section_title=title, plot_ids=plot_ids, base_dir=base_dir)
                for j, b in enumerate(blocks_raw)
            ]

        unknown = set(item) - {"title", "description", "blocks"}
        if unknown:
            raise RunError(f"{source}: section {title!r}: unknown fields {sorted(unknown)}")

        out.append(Section(title=title, description=description, blocks=blocks))
    return out


def _parse_block(
    item: Any,
    index: int,
    *,
    source: str,
    section_title: str,
    plot_ids: set[str],
    base_dir: Path,
) -> Block:
    where = f"section {section_title!r} blocks[{index}]"
    if not isinstance(item, dict):
        raise RunError(f"{source}: {where} must be a mapping")

    present = [k for k in BLOCK_KINDS if k in item]
    if not present:
        raise RunError(f"{source}: {where} must have exactly one of {list(BLOCK_KINDS)}")
    if len(present) > 1:
        raise RunError(f"{source}: {where} has multiple kinds {present}; pick exactly one")

    unknown = set(item) - set(BLOCK_KINDS)
    if unknown:
        raise RunError(f"{source}: {where} unknown fields {sorted(unknown)}")

    kind = present[0]
    value = item[kind]
    if not isinstance(value, str) or not value:
        raise RunError(f"{source}: {where} {kind!r} must be a non-empty string")

    if kind == "plot_id":
        if value not in plot_ids:
            raise RunError(f"{source}: {where} plot_id {value!r} not found in top-level plots")
        return Block(kind=kind, value=value)

    if kind == "include_html":
        path = (base_dir / value).resolve()
        if not path.exists():
            raise RunError(f"{source}: {where} include_html: file not found: {path}")
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            raise RunError(f"{source}: {where} include_html: cannot read {path}: {e}") from e
        return Block(kind=kind, value=content)

    return Block(kind=kind, value=value)


def load_plots_file(path: Path) -> PlotsDoc:
    if not path.exists():
        raise RunError(f"{path}: plots file not found")
    raw = yaml.safe_load(path.read_text())
    return _build_doc(raw, source=str(path), base_dir=path.parent)


def _build_doc(raw: Any, *, source: str, base_dir: Path) -> PlotsDoc:
    if raw is None:
        return PlotsDoc()
    if isinstance(raw, list):
        return PlotsDoc(plots=parse_plots(raw, source=source))
    if not isinstance(raw, dict):
        raise RunError(f"{source}: top-level must be a list or mapping")

    report_title = raw.get("report_title") or None
    report_description = raw.get("report_description") or None
    if report_title is not None and not isinstance(report_title, str):
        raise RunError(f"{source}: report_title must be a string")
    if report_description is not None and not isinstance(report_description, str):
        raise RunError(f"{source}: report_description must be a string")

    plots_specs = parse_plots(raw.get("plots"), source=source)
    plot_ids = {p.id for p in plots_specs}
    sections = parse_sections(
        raw.get("sections"),
        source=source,
        plot_ids=plot_ids,
        base_dir=base_dir,
    )

    return PlotsDoc(
        plots=plots_specs,
        sections=sections,
        report_title=report_title,
        report_description=report_description,
    )


def discover_from_tool_snapshots(results_dir: Path) -> PlotsDoc:
    tools_dir = results_dir / "tools"
    if not tools_dir.is_dir():
        return PlotsDoc()

    merged_plots: list[PlotSpec] = []
    seen_ids: set[str] = set()
    merged_sections: list[Section] = []
    report_title: str | None = None
    report_description: str | None = None

    for tool_yaml in sorted(tools_dir.glob("*.yaml")):
        raw = yaml.safe_load(tool_yaml.read_text())
        if not isinstance(raw, dict):
            continue
        if report_title is None:
            report_title = raw.get("report_title") or None
        if report_description is None:
            report_description = raw.get("report_description") or None

        for spec in parse_plots(raw.get("plots"), source=str(tool_yaml)):
            if spec.id in seen_ids:
                continue
            seen_ids.add(spec.id)
            merged_plots.append(spec)

        merged_sections.extend(
            parse_sections(
                raw.get("sections"),
                source=str(tool_yaml),
                plot_ids=seen_ids,
                base_dir=tool_yaml.parent,
            )
        )

    return PlotsDoc(
        plots=merged_plots,
        sections=merged_sections,
        report_title=report_title,
        report_description=report_description,
    )
