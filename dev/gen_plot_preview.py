"""Seed a temporary results dir and render a report showing all plot types and features.

Run with:  python dev/gen_plot_preview.py
Opens dev/plot_preview/report.html in the browser when done.
"""

import io
import json
import random
import shutil
import sys
import uuid
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from abench_speckz import query, store  # noqa: E402

OUT = Path(__file__).parent / "plot_preview"
PLOTS_YAML = Path(__file__).parent / "plot_preview_plots.yaml"
REPORT = OUT / "report.html"

# ---------------------------------------------------------------------------
# Synthetic data parameters
# ---------------------------------------------------------------------------

WORKLOADS = ["read", "write", "mixed"]
BACKENDS = ["postgres", "mysql", "sqlite"]
CONCURRENCIES = [1, 4, 8, 16, 32]
REPS = 5
RNG = random.Random(42)


def _rps(workload: str, backend: str, concurrency: int) -> float:
    base = {"read": 5000, "write": 3000, "mixed": 4000}[workload]
    scale = {"postgres": 1.0, "mysql": 0.85, "sqlite": 0.55}[backend]
    conc_factor = concurrency**0.65
    return base * scale * conc_factor * RNG.uniform(0.95, 1.05)


def _latencies(rps_val: float) -> dict[str, float]:
    base_ms = 1000 / max(rps_val, 1) * 100
    return {
        "mean_ms": base_ms * RNG.uniform(0.9, 1.1),
        "p50_ms": base_ms * RNG.uniform(0.85, 1.0),
        "p95_ms": base_ms * RNG.uniform(1.5, 2.5),
        "p99_ms": base_ms * RNG.uniform(2.5, 4.0),
    }


# ---------------------------------------------------------------------------
# Seed results dir
# ---------------------------------------------------------------------------

shutil.rmtree(OUT, ignore_errors=True)
store.init_results_dir(OUT)

# Write pretty_names so the report shows human-friendly metric and variable labels
(OUT / "pretty_names.json").write_text(json.dumps({
    "preview": {
        "rps": "Requests/s",
        "mean_ms": "Mean latency (ms)",
        "p50_ms": "P50 latency (ms)",
        "p95_ms": "P95 latency (ms)",
        "p99_ms": "P99 latency (ms)",
    },
    "vars": {
        "workload": "Workload",
        "backend": "Backend",
        "concurrency": "Concurrency",
    },
    "var_values": {
        "workload": {"read": "Read-heavy", "write": "Write-heavy", "mixed": "Mixed"},
        "backend": {"postgres": "PostgreSQL 16", "mysql": "MySQL 8.0", "sqlite": "SQLite 3"},
    },
}))

for workload in WORKLOADS:
    for backend in BACKENDS:
        for concurrency in CONCURRENCIES:
            config_hash = f"{workload}-{backend}-{concurrency}"
            for i in range(REPS):
                rps_val = _rps(workload, backend, concurrency)
                results = {"rps": rps_val, **_latencies(rps_val)}
                store.append_run(
                    OUT,
                    {
                        "run_id": str(uuid.uuid4()),
                        "config_hash": config_hash,
                        "vars": {
                            "workload": workload,
                            "backend": backend,
                            "concurrency": str(concurrency),
                        },
                        "tags": ["bench"],
                        "exit_code": 0,
                        "results": results,
                        "failure_reason": None,
                    },
                )
            store.update_aggregates_for_hash(OUT, config_hash)

# ---------------------------------------------------------------------------
# Plots definition — all four types + extra features
# ---------------------------------------------------------------------------

PLOTS_YAML.write_text(
    """\
report_title: "Plot format gallery"
report_description: |
  All supported plot types and YAML features rendered against a synthetic
  benchmark dataset: **3 workloads × 3 backends × 5 concurrency levels**.
  Pretty names, `text` blocks, multi-key `group_by`, and chart titles are
  all demonstrated below.

sections:
  - title: "Bar"
    description: |
      `type: bar` — each x-axis value gets one bar per series.
      Use `group_by` to split a single metric across variable values.
    blocks:
      - text: "**No `group_by`** — all data collapses into a single series per metric."
      - plot_id: bar_simple
      - text: "**`group_by: backend`** — one bar cluster per workload, one bar per backend."
      - plot_id: bar_grouped
      - text: "**`group_by: [workload, backend]`** — multiple keys joined in the legend label."
      - plot_id: bar_multi_group

  - title: "Stacked bar"
    description: |
      `type: stacked-bar` — bars subdivided by metric. Requires at least two `y` values.
      Useful for showing composition (e.g. how latency percentiles stack up).
    blocks:
      - plot_id: stacked

  - title: "Line"
    description: |
      `type: line` — x-axis values are connected with a line. Works best when
      x is a numeric variable (concurrency, threads, payload size …).
      Add `smooth: true` to use a spline curve instead of straight segments.
    blocks:
      - text: "**No `group_by`** — single line across all data."
      - plot_id: line_single
      - text: "**`group_by: backend`** — one line per backend."
      - plot_id: line_grouped
      - text: "**`smooth: true`** — same chart with spline interpolation."
      - plot_id: line_smooth

  - title: "Scatter"
    description: |
      `type: scatter` — **both axes are metrics**, not variables.
      Each point is one combo's mean values. `group_by` names each point.
    blocks:
      - text: "**No `group_by`** — all combos collapse to a single unlabelled point."
      - plot_id: scatter_plain
      - text: "**`group_by: workload`** — one labelled point per workload."
      - plot_id: scatter_grouped

  - title: "var_values — friendly value labels"
    description: |
      `var_values` in `pretty_names.json` maps raw combo variable values to
      display strings. Labels appear in chart x-axis ticks, dataset legend
      labels, the colour-coded legend table, and the fixed-vars annotation.
    blocks:
      - text: "x-axis ticks and dataset labels use the mapped names."
      - plot_id: var_values_bar
      - text: "Legend table cells also show the mapped names."
      - plot_id: var_values_line

  - title: "series_label — compact one-line legend"
    description: |
      `series_label` replaces the multi-column table legend with Chart.js's
      built-in one-line legend. The value is a template string rendered per
      series using each series's variable values; `var_values` mapping is
      applied automatically.
    blocks:
      - text: "Without `series_label` — full table legend (default)."
      - plot_id: no_series_label
      - text: "With `series_label` set to a single-variable template — compact inline legend."
      - plot_id: with_series_label
      - text: "Multi-variable template joining two group_by keys."
      - plot_id: multi_series_label

plots:
  # ── Bar ──────────────────────────────────────────────────────────────────
  - id: bar_simple
    type: bar
    title: "Throughput by workload"
    x: workload
    y: rps

  - id: bar_grouped
    type: bar
    title: "Throughput by workload × backend"
    x: workload
    y: rps
    group_by: backend

  - id: bar_multi_group
    type: bar
    title: "Throughput — multi-key group_by"
    x: concurrency
    y: rps
    group_by: [workload, backend]

  # ── Stacked bar ──────────────────────────────────────────────────────────
  - id: stacked
    type: stacked-bar
    title: "Latency percentile breakdown by workload"
    x: workload
    y: [p50_ms, p95_ms, p99_ms]

  # ── Line ─────────────────────────────────────────────────────────────────
  - id: line_single
    type: line
    title: "Throughput vs concurrency"
    x: concurrency
    y: rps

  - id: line_grouped
    type: line
    title: "Throughput vs concurrency, per backend"
    x: concurrency
    y: rps
    group_by: backend

  - id: line_smooth
    type: line
    title: "Throughput vs concurrency, per backend (smooth)"
    x: concurrency
    y: rps
    group_by: backend
    smooth: true

  # ── Scatter ──────────────────────────────────────────────────────────────
  - id: scatter_plain
    type: scatter
    title: "Throughput / latency tradeoff"
    x: rps
    y: [p95_ms]

  - id: scatter_grouped
    type: scatter
    title: "Throughput / latency tradeoff, per workload"
    x: rps
    y: [p95_ms]
    group_by: workload

  # ── var_values demo ───────────────────────────────────────────────────────
  - id: var_values_bar
    type: bar
    title: "Throughput by workload (var_values on x-axis)"
    x: workload
    y: rps

  - id: var_values_line
    type: line
    title: "Throughput vs concurrency, per backend (var_values in legend)"
    x: concurrency
    y: rps
    group_by: backend

  # ── series_label demo ─────────────────────────────────────────────────────
  - id: no_series_label
    type: line
    title: "Per-backend throughput — table legend (default)"
    x: concurrency
    y: rps
    group_by: backend

  - id: with_series_label
    type: line
    title: "Per-backend throughput (compact legend)"
    x: concurrency
    y: rps
    group_by: backend
    series_label: "${backend}"

  - id: multi_series_label
    type: line
    title: "Per workload × backend (multi-variable template label)"
    x: concurrency
    y: rps
    group_by: [workload, backend]
    series_label: "${workload} / ${backend}"
"""
)

# ---------------------------------------------------------------------------
# Render (pretty=True so pretty_names.json is picked up)
# ---------------------------------------------------------------------------

query.stats(OUT, report=REPORT, plots=PLOTS_YAML, pretty=True, out=io.StringIO())
print(f"Report written to {REPORT}")
webbrowser.open(REPORT.as_uri())
