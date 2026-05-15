"""Render aggregate rows as table / TSV / JSON text — used by ``abench-speckz stats``."""

import json
from typing import Any

from abench_speckz import prettynames


def render(rows: list[dict[str, Any]], fmt: str, out, *, pretty_map: dict) -> None:
    """Write *rows* to *out* in the chosen format.

    Headers come from the first row's keys. When *pretty_map* is non-empty, the
    table format swaps header labels for their pretty-name equivalents.
    """
    headers = list(rows[0].keys())

    if fmt == "json":
        out.write(json.dumps(rows, indent=2, default=_json_default) + "\n")
        return
    if fmt == "tsv":
        out.write("\t".join(headers) + "\n")
        for r in rows:
            out.write("\t".join(_fmt(r[h]) for h in headers) + "\n")
        return

    label = (lambda h: prettynames.resolve(pretty_map, h)) if pretty_map else (lambda h: h)
    pretty_headers = [label(h) for h in headers]
    rendered = [[_fmt(r[h]) for h in headers] for r in rows]
    widths = [max(len(h), *(len(row[i]) for row in rendered)) for i, h in enumerate(pretty_headers)]
    fmt_line = "  ".join(f"{{:<{w}}}" for w in widths)
    out.write(fmt_line.format(*pretty_headers) + "\n")
    out.write(fmt_line.format(*("-" * w for w in widths)) + "\n")
    for row in rendered:
        out.write(fmt_line.format(*row) + "\n")


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".") or "0"
    return str(v)


def _json_default(v: Any):
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)
