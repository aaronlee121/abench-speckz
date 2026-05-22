"""Render KPI scorecard blocks (Phase 1 of executive-level features)."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from abench_speckz.report._html import resolve_metric
from abench_speckz.report._select import higher_is_better_map, resolve_value

if TYPE_CHECKING:
    from abench_speckz.plots import KpiCard


def _fmt_val(val: float, precision: int) -> str:
    if precision == 0:
        return str(int(round(val)))
    return f"{val:.{precision}f}"


def render_kpi_block(cards: list[KpiCard], results_dir: Path, pretty_map: dict) -> str:
    """Render a list of KpiCard definitions as a .kpi-grid of .kpi-card divs."""
    hib = higher_is_better_map(results_dir)
    card_htmls: list[str] = []

    for card in cards:
        val = resolve_value(results_dir, card.metric, where=card.where, stat=card.stat)
        val_str = _fmt_val(val, card.precision) if val is not None else "—"
        unit_html = f'<span class="kpi-unit">{card.unit}</span>' if card.unit else ""

        delta_html = ""
        if card.compare is not None and val is not None:
            cmp_val = resolve_value(
                results_dir, card.metric, where=card.compare.where, stat=card.stat
            )
            if cmp_val is not None and cmp_val != 0.0:
                delta = val - cmp_val
                delta_pct = delta / abs(cmp_val) * 100.0
                higher_good = hib.get(card.metric, True)
                positive_is_good = (delta > 0) == higher_good
                cls = "kpi-delta-good" if positive_is_good else "kpi-delta-bad"
                arrow = "↑" if delta > 0 else "↓"
                delta_html = f'<div class="kpi-delta {cls}">{arrow} {abs(delta_pct):.1f}%</div>'

        label = resolve_metric(pretty_map, card.label)
        card_htmls.append(
            f'<div class="kpi-card">'
            f'<div class="kpi-value">{val_str}{unit_html}</div>'
            f'{delta_html}'
            f'<div class="kpi-label">{label}</div>'
            f'</div>'
        )

    return f'<div class="kpi-grid">{"".join(card_htmls)}</div>'
