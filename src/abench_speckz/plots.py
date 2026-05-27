"""PlotSpec dataclass and validators for declarative chart definitions."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from abench_speckz.expr import validate_expr_syntax
from abench_speckz.model import RunError

ALLOWED_TYPES = {"bar", "stacked-bar", "line", "scatter"}
BLOCK_KINDS = ("text", "plot_id", "html", "include_html", "kpi", "headline")
ALLOWED_PALETTES = {"tableau", "vivid", "pastel", "paired", "muted", "accessible"}
ALLOWED_DARK_MODES = {True, False, "auto"}
_KPI_STATS = {"max", "min", "mean"}


@dataclass
class NormalizeSpec:
    against: str  # group_by variable whose reference value pins to 1.0×
    reference: str  # the variable value that becomes the denominator


_KPI_COMPARE_FORMATS = {"pct", "multiplier", "both"}


@dataclass
class KpiCompare:
    where: list[str] = field(default_factory=list)  # "k=v" filter expressions
    format: str = "pct"  # pct | multiplier | both


@dataclass
class KpiCard:
    label: str
    metric: str
    stat: str = "mean"  # max | min | mean across matching combos
    where: list[str] = field(default_factory=list)
    unit: str = ""
    precision: int = 2
    compare: KpiCompare | None = None


@dataclass
class HeadlineDef:
    metric: str
    compare: str  # variable whose values are ranked
    template: str
    where: list[str] = field(default_factory=list)


_ALLOWED_ERROR_BARS = {"ci95", "stddev"}


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
    data_table: bool = True
    x_title: str | None = None
    y_title: str | None = None
    normalize: NormalizeSpec | None = None
    error_bars: str | None = None


@dataclass
class Block:
    kind: str  # one of BLOCK_KINDS
    value: str  # markdown/html/plot_id text; empty for structured blocks (kpi, headline)
    data: Any = None  # structured payload for kpi (list[KpiCard]) / headline (HeadlineDef)


@dataclass
class Section:
    title: str
    description: str | None = None  # markdown source
    blocks: list[Block] = field(default_factory=list)


@dataclass
class ThemeConfig:
    palette: str = "tableau"
    dark_mode: bool | str = "auto"  # True, False, or "auto"
    switcher: bool = False


@dataclass
class PlotsDoc:
    plots: list[PlotSpec] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    report_title: str | None = None
    report_description: str | None = None
    theme: ThemeConfig = field(default_factory=ThemeConfig)


def _req_str(item: dict, key: str, *, ctx: str) -> str:
    """Read a required, non-empty string field (``"X is required (string)"``)."""
    raw = item.get(key)
    if not isinstance(raw, str) or not raw:
        raise RunError(f"{ctx}: {key} is required (string)")
    return raw


def _req_nonempty_str(item: dict, key: str, *, ctx: str, label: str | None = None) -> str:
    """Required non-empty string with ``"X must be a non-empty string"`` wording.

    ``label`` overrides the displayed key (e.g. ``"headline.metric"`` instead
    of plain ``"metric"``) when the error needs a dotted prefix.
    """
    raw = item.get(key)
    if not isinstance(raw, str) or not raw:
        raise RunError(f"{ctx}: {label or key} must be a non-empty string")
    return raw


def _opt_str(item: dict, key: str, *, ctx: str) -> str | None:
    """Read an optional string field; ``None`` when absent or empty."""
    raw = item.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise RunError(f"{ctx}: {key} must be a string")
    return raw or None


def _opt_bool(item: dict, key: str, default: bool, *, ctx: str) -> bool:
    """Read an optional boolean field with a default."""
    raw = item.get(key, default)
    if not isinstance(raw, bool):
        raise RunError(f"{ctx}: {key} must be a boolean")
    return raw


def _enum(item: dict, key: str, allowed: set, *, ctx: str, default: Any = ...) -> Any:
    """Read a field whose value must be in ``allowed``."""
    raw = item.get(key, default) if default is not ... else item.get(key)
    if raw not in allowed:
        raise RunError(f"{ctx}: {key} must be one of {sorted(allowed)}, got {raw!r}")
    return raw


def _check_unknown(item: dict, known: set, *, ctx: str) -> None:
    """Raise when ``item`` carries any keys outside ``known``."""
    unknown = set(item) - known
    if unknown:
        raise RunError(f"{ctx}: unknown fields {sorted(unknown)}")


def parse_theme(raw: Any, source: str) -> ThemeConfig:
    if raw is None:
        return ThemeConfig()
    if not isinstance(raw, dict):
        raise RunError(f"{source}: theme must be a mapping")

    palette = raw.get("palette", "tableau")
    if palette not in ALLOWED_PALETTES:
        raise RunError(f"{source}: theme.palette must be one of {sorted(ALLOWED_PALETTES)}, got {palette!r}")

    dark_mode_raw = raw.get("dark_mode", "auto")
    if dark_mode_raw not in ALLOWED_DARK_MODES:
        raise RunError(f"{source}: theme.dark_mode must be true, false, or 'auto', got {dark_mode_raw!r}")
    dark_mode: bool | str = dark_mode_raw

    switcher = raw.get("switcher", False)
    if not isinstance(switcher, bool):
        raise RunError(f"{source}: theme.switcher must be a boolean")

    unknown = set(raw) - {"palette", "dark_mode", "switcher"}
    if unknown:
        raise RunError(f"{source}: theme: unknown fields {sorted(unknown)}")

    return ThemeConfig(palette=palette, dark_mode=dark_mode, switcher=switcher)


_PLOT_FIELDS = {
    "id",
    "type",
    "x",
    "y",
    "title",
    "group_by",
    "smooth",
    "fixed_vars",
    "series_label",
    "data_table",
    "x_title",
    "y_title",
    "normalize",
    "error_bars",
}


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

        plot_id = _req_str(item, "id", ctx=f"{source}: plots[{i}]")
        if plot_id in seen_ids:
            raise RunError(f"{source}: duplicate plot id {plot_id!r}")
        seen_ids.add(plot_id)

        ctx = f"{source}: plot {plot_id!r}"
        ptype = _enum(item, "type", ALLOWED_TYPES, ctx=ctx)
        x = _req_str(item, "x", ctx=ctx)
        if not x.strip().isidentifier():
            validate_expr_syntax(x, f"{ctx}: x")
        y = _parse_y_field(item, ctx=ctx, plot_type=ptype)
        group_by = _parse_group_by_field(item, ctx=ctx)

        smooth = _opt_bool(item, "smooth", False, ctx=ctx)
        if smooth and ptype != "line":
            raise RunError(f"{ctx}: smooth is only valid for line plots")
        normalize = _parse_normalize_field(item, ctx=ctx, plot_type=ptype)
        error_bars = _parse_error_bars_field(item, ctx=ctx, plot_type=ptype)

        _check_unknown(item, _PLOT_FIELDS, ctx=ctx)

        out.append(
            PlotSpec(
                id=plot_id,
                type=ptype,
                x=x,
                y=y,
                title=_opt_str(item, "title", ctx=ctx),
                group_by=group_by,
                smooth=smooth,
                fixed_vars=_opt_bool(item, "fixed_vars", True, ctx=ctx),
                series_label=_opt_str(item, "series_label", ctx=ctx),
                data_table=_opt_bool(item, "data_table", True, ctx=ctx),
                x_title=_opt_str(item, "x_title", ctx=ctx),
                y_title=_opt_str(item, "y_title", ctx=ctx),
                normalize=normalize,
                error_bars=error_bars,
            )
        )
    return out


def _parse_y_field(item: dict, *, ctx: str, plot_type: str) -> list[str]:
    y_raw = item.get("y")
    if y_raw is None:
        raise RunError(f"{ctx}: y is required")
    if isinstance(y_raw, str):
        y = [y_raw]
    elif isinstance(y_raw, list) and all(isinstance(m, str) and m for m in y_raw):
        y = list(y_raw)
    else:
        raise RunError(f"{ctx}: y must be a string or list of strings")
    if not y:
        raise RunError(f"{ctx}: y must not be empty")
    for j, yi in enumerate(y):
        if not yi.strip().isidentifier():
            validate_expr_syntax(yi, f"{ctx}: y[{j}]")
    if plot_type == "scatter" and len(y) != 1:
        raise RunError(f"{ctx}: scatter requires exactly one y metric")
    if plot_type == "stacked-bar" and len(y) < 2:
        raise RunError(f"{ctx}: stacked-bar requires at least two y metrics")
    return y


def _parse_group_by_field(item: dict, *, ctx: str) -> list[str] | None:
    raw = item.get("group_by")
    if raw is None:
        return None
    if isinstance(raw, str):
        entries = [raw]
    elif isinstance(raw, list):
        entries = raw
    else:
        raise RunError(f"{ctx}: group_by must be a string or list of strings")
    if not entries:
        raise RunError(f"{ctx}: group_by list must not be empty")
    for j, k in enumerate(entries):
        if not isinstance(k, str) or not k:
            raise RunError(f"{ctx}: group_by[{j}] must be a non-empty string")
        if k.startswith("!") and not k[1:]:
            raise RunError(f"{ctx}: group_by[{j}] '!' must be followed by a variable name")
    return list(entries)


def _parse_normalize_field(item: dict, *, ctx: str, plot_type: str) -> NormalizeSpec | None:
    raw = item.get("normalize")
    if raw is None:
        return None
    if plot_type in ("scatter", "stacked-bar"):
        raise RunError(f"{ctx}: normalize is not allowed on {plot_type!r} plots")
    if not isinstance(raw, dict):
        raise RunError(f"{ctx}: normalize must be a mapping")
    against = raw.get("against")
    reference = raw.get("reference")
    if not isinstance(against, str) or not against:
        raise RunError(f"{ctx}: normalize.against must be a non-empty string")
    if reference is None or str(reference) == "":
        raise RunError(f"{ctx}: normalize.reference must be a non-empty value")
    _check_unknown(raw, {"against", "reference"}, ctx=f"{ctx}: normalize")
    return NormalizeSpec(against=str(against), reference=str(reference))


def _parse_error_bars_field(item: dict, *, ctx: str, plot_type: str) -> str | None:
    raw = item.get("error_bars")
    if raw is None:
        return None
    if not isinstance(raw, str) or raw not in _ALLOWED_ERROR_BARS:
        raise RunError(f"{ctx}: error_bars must be one of {sorted(_ALLOWED_ERROR_BARS)}, got {raw!r}")
    if plot_type in ("scatter", "stacked-bar"):
        raise RunError(f"{ctx}: error_bars is not allowed on {plot_type!r} plots")
    return raw


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

        ctx = f"{source}: section {title!r}"
        description = _opt_str(item, "description", ctx=ctx)

        blocks_raw = item.get("blocks")
        if blocks_raw is None:
            blocks: list[Block] = []
        elif not isinstance(blocks_raw, list):
            raise RunError(f"{ctx}: blocks must be a list")
        else:
            blocks = [
                _parse_block(b, j, source=source, section_title=title, plot_ids=plot_ids, base_dir=base_dir)
                for j, b in enumerate(blocks_raw)
            ]

        _check_unknown(item, {"title", "description", "blocks"}, ctx=ctx)
        out.append(Section(title=title, description=description, blocks=blocks))
    return out


def _where_dict_to_list(raw: Any, *, source: str, ctx: str) -> list[str]:
    """Convert a YAML where-dict {k: v, ...} to ["k=v", ...] filter strings."""
    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise RunError(f"{source}: {ctx}: where must be a mapping")
    return [f"{k}={v}" for k, v in raw.items()]


def _parse_kpi_card(item: Any, index: int, *, source: str, ctx: str) -> KpiCard:
    loc = f"{ctx} kpi[{index}]"
    full_ctx = f"{source}: {loc}"
    if not isinstance(item, dict):
        raise RunError(f"{full_ctx} must be a mapping")

    precision_raw = item.get("precision", 2)
    if not isinstance(precision_raw, int) or isinstance(precision_raw, bool) or precision_raw < 0:
        raise RunError(f"{full_ctx}: precision must be a non-negative integer")

    compare_raw = item.get("compare")
    if compare_raw is None:
        compare = None
    else:
        if not isinstance(compare_raw, dict):
            raise RunError(f"{full_ctx}: compare must be a mapping")
        cmp_where = _where_dict_to_list(compare_raw.get("where"), source=source, ctx=f"{loc} compare")
        cmp_format = _enum(
            compare_raw, "format", _KPI_COMPARE_FORMATS, ctx=f"{full_ctx}: compare", default="pct"
        )
        _check_unknown(compare_raw, {"where", "format"}, ctx=f"{full_ctx}: compare")
        compare = KpiCompare(where=cmp_where, format=cmp_format)

    _check_unknown(
        item,
        {"label", "metric", "stat", "where", "unit", "precision", "compare"},
        ctx=full_ctx,
    )

    return KpiCard(
        label=_req_nonempty_str(item, "label", ctx=full_ctx),
        metric=_req_nonempty_str(item, "metric", ctx=full_ctx),
        stat=_enum(item, "stat", _KPI_STATS, ctx=full_ctx, default="mean"),
        where=_where_dict_to_list(item.get("where"), source=source, ctx=loc),
        unit=_opt_str(item, "unit", ctx=full_ctx) or "",
        precision=precision_raw,
        compare=compare,
    )


def _parse_headline(item: Any, *, source: str, ctx: str) -> HeadlineDef:
    full_ctx = f"{source}: {ctx}"
    if not isinstance(item, dict):
        raise RunError(f"{full_ctx}: headline must be a mapping")

    metric = _req_nonempty_str(item, "metric", ctx=full_ctx, label="headline.metric")
    compare = item.get("compare")
    if not isinstance(compare, str) or not compare:
        raise RunError(f"{full_ctx}: headline.compare must be a non-empty string (variable name)")

    template = item.get("template", "{best} is {ratio} ahead of {worst}")
    if not isinstance(template, str):
        raise RunError(f"{full_ctx}: headline.template must be a string")

    _check_unknown(item, {"metric", "compare", "template", "where"}, ctx=f"{full_ctx}: headline")

    return HeadlineDef(
        metric=metric,
        compare=compare,
        template=template,
        where=_where_dict_to_list(item.get("where"), source=source, ctx=ctx),
    )


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

    if kind == "kpi":
        cards_raw = item[kind]
        if not isinstance(cards_raw, list):
            raise RunError(f"{source}: {where}: kpi must be a list of card definitions")
        cards = [_parse_kpi_card(c, j, source=source, ctx=where) for j, c in enumerate(cards_raw)]
        return Block(kind=kind, value="", data=cards)

    if kind == "headline":
        headline_raw = item[kind]
        headline = _parse_headline(headline_raw, source=source, ctx=where)
        return Block(kind=kind, value="", data=headline)

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

    theme = parse_theme(raw.get("theme"), source=source)
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
        theme=theme,
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
    theme: ThemeConfig | None = None

    for tool_yaml in sorted(tools_dir.glob("*.yaml")):
        raw = yaml.safe_load(tool_yaml.read_text())
        if not isinstance(raw, dict):
            continue
        if report_title is None:
            report_title = raw.get("report_title") or None
        if report_description is None:
            report_description = raw.get("report_description") or None
        if theme is None and "theme" in raw:
            theme = parse_theme(raw.get("theme"), source=str(tool_yaml))

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
        theme=theme or ThemeConfig(),
    )
