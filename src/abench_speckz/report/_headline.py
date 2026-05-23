"""Render headline callout blocks (Phase 2 of executive-level features)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from abench_speckz.report._html import resolve_var_value
from abench_speckz.report._select import higher_is_better_map, rank_by

if TYPE_CHECKING:
    from abench_speckz.plots import HeadlineDef

_FALLBACK = '<div class="callout"><em>(insufficient data for headline)</em></div>'


def render_headline_block(headline: HeadlineDef, results_dir: Path, pretty_map: dict) -> str:
    """Render a HeadlineDef as a styled .callout banner."""
    hib = higher_is_better_map(results_dir)
    pairs = rank_by(results_dir, headline.metric, headline.compare, where=headline.where)
    if len(pairs) < 2:
        return _FALLBACK

    # pairs is sorted descending (highest first); interpret based on higher_is_better
    higher_good = hib.get(headline.metric, True)
    if higher_good:
        best_var, best_val = pairs[0]
        worst_var, worst_val = pairs[-1]
    else:
        best_var, best_val = pairs[-1]
        worst_var, worst_val = pairs[0]

    ratio: str
    delta_pct: str
    if worst_val and worst_val != 0.0:
        ratio = f"{best_val / worst_val:.2f}x"
        dp = abs((best_val - worst_val) / worst_val * 100.0)
        delta_pct = f"{dp:.1f}%"
    else:
        ratio = "N/A"
        delta_pct = "N/A"

    best_display = resolve_var_value(pretty_map, headline.compare, best_var)
    worst_display = resolve_var_value(pretty_map, headline.compare, worst_var)

    tokens = {
        "best": best_display,
        "worst": worst_display,
        "best_value": f"{best_val:.2f}",
        "worst_value": f"{worst_val:.2f}",
        "ratio": ratio,
        "delta_pct": delta_pct,
    }
    text = headline.template
    for key, val in tokens.items():
        text = text.replace("{" + key + "}", val)

    return f'<div class="callout">{text}</div>'
