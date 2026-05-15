"""Render aggregate rows as a self-contained Chart.js-powered HTML report.

The package is split for readability:

- ``_html``    — shell template, CSS, and small formatting helpers (tables, legends, markdown).
- ``_charts``  — Chart.js config builders for bar / line / stacked-bar / scatter.
- ``_runs``    — discovery + rendering of per-run HTML reports under ``raw/``.
- ``_plots``   — per-plot artifact assembly (data + chart + table) shared by both layouts.
- ``_render``  — top-level ``generate_html`` / ``generate_plots_html`` entry points.

The public entry points used by ``query.stats`` are re-exported below. A handful
of internal helpers are also re-exported (with their original leading-underscore
names) so existing tests can keep importing them via ``abench_speckz.report``.
"""

# Backwards-compat re-exports for tests that import these via abench_speckz.report.
from abench_speckz.report._html import fixed_vars_html as _fixed_vars_html  # noqa: F401
from abench_speckz.report._html import html_shell as _html_shell  # noqa: F401
from abench_speckz.report._html import legend_table_html as _legend_table_html  # noqa: F401
from abench_speckz.report._html import resolve_group_by as _resolve_group_by  # noqa: F401
from abench_speckz.report._html import safe_id as _safe_id  # noqa: F401
from abench_speckz.report._html import split_group_by_vars as _split_group_by_vars  # noqa: F401
from abench_speckz.report._html import table_html as _table_html  # noqa: F401
from abench_speckz.report._render import generate_html, generate_plots_html
from abench_speckz.report._runs import discover_run_reports as _discover_run_reports  # noqa: F401
from abench_speckz.report._runs import runs_section as _runs_section  # noqa: F401

__all__ = [
    "generate_html",
    "generate_plots_html",
]
