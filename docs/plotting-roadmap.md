# Plotting roadmap

Design notes for upcoming report/plotting features. The goal is a single report
generator that produces both developer-detail and executive-presentation output
at publication quality.

Two features are specified in implementation-ready detail below: **error bars**
and **baseline diff**. A backlog of further ideas follows.

---

## 1. Error bars

Surface per-run statistical spread on `bar` / `line` plots. The data already
exists — `query.compute_rows` returns `mean`, `stddev`, `ci95_low`, `ci95_high`
per row; today only `mean` is plotted.

### YAML surface

New optional field on each plot entry in `plots:`

```yaml
- id: rps_vs_concurrency
  type: line
  x: concurrency
  y: rps
  error_bars: ci95     # one of: ci95 | stddev   (omit = none)
```

- `ci95` — whiskers span `ci95_low … ci95_high` (asymmetric, taken straight
  from the columns). Recommended default for publication-grade output.
- `stddev` — whiskers span `mean − stddev … mean + stddev`.
- Allowed only on `type: bar` and `type: line`. Reject on `stacked-bar`
  (whiskers on stacked segments mislead) and `scatter` (would need x *and* y
  error — see backlog). Validate at YAML load time, alongside `smooth`.

### Rendering approach

Chart.js 4 has no native error bars. Use a **custom inline plugin** (~40 lines
JS) rather than the `chartjs-chart-error-bars` CDN plugin: no second CDN
dependency, no chart-type swap, dark-mode aware. The report is self-contained
and already owns its JS block.

### Files to change

- **`src/abench_speckz/plots.py`** — add `error_bars: str | None = None` to
  `PlotSpec`; parse + validate against `{"ci95", "stddev"}`; enforce the
  bar/line type restriction; add `error_bars` to the unknown-fields allowlist
  in `parse_plots`.
- **`src/abench_speckz/report/_charts.py`** — `_index_series` currently stores
  only `r.get("mean")` per cell; change it to store the whole row so the
  builder can also read `stddev` / `ci95_low` / `ci95_high`. In
  `build_bar_or_line_config`, when `plot.error_bars` is set, attach two parallel
  per-point arrays to each dataset:

  ```python
  ds["_errLow"]  = [...]   # None where the stat is missing
  ds["_errHigh"] = [...]
  ```

  These survive the `JSON.parse(JSON.stringify(...))` round-trip in
  `_resolveConfig`. Note `_apply_y_exprs` already transforms `stddev` /
  `ci95_*`, so error bars compose with `y` expressions automatically.
- **`src/abench_speckz/report/_html.py`** — add an error-bar plugin to the
  `_theme_js` core block and `Chart.register(...)` it. Sketch:

  ```js
  const _errorBarPlugin = {
    id: 'abenchErrorBars',
    afterDatasetsDraw(chart) {
      const ctx = chart.ctx, yScale = chart.scales.y;
      chart.data.datasets.forEach((ds, di) => {
        if (!ds._errLow) return;
        const meta = chart.getDatasetMeta(di);
        if (meta.hidden) return;
        ctx.save();
        ctx.strokeStyle = _darkMode ? '#ccc' : '#333';
        ctx.lineWidth = 1.5;
        meta.data.forEach((el, i) => {
          const lo = ds._errLow[i], hi = ds._errHigh[i];
          if (lo == null || hi == null) return;
          const yLo = yScale.getPixelForValue(lo);
          const yHi = yScale.getPixelForValue(hi);
          ctx.beginPath();
          ctx.moveTo(el.x, yLo); ctx.lineTo(el.x, yHi);
          ctx.moveTo(el.x - 4, yLo); ctx.lineTo(el.x + 4, yLo);
          ctx.moveTo(el.x - 4, yHi); ctx.lineTo(el.x + 4, yHi);
          ctx.stroke();
        });
        ctx.restore();
      });
    }
  };
  ```

  `el.x` is the rendered bar/point center, so grouped bars get correctly-offset
  whiskers for free.
- **Tests** — `plots.py` parse/validate cases; a `_charts` case asserting
  `_errLow` / `_errHigh` arrays are attached with the right values.
- **CLAUDE.md** — document the `error_bars` plot field.

Effort: small (~half a day). Self-contained and low-risk — build this first.

---

## 2. Baseline diff

Compare a results directory against a baseline directory: overlay baseline
series on every plot, add delta columns to data tables, and auto-inject a
regression-summary section.

### Surface — CLI, not YAML

A baseline is a runtime artifact, not something a report author bakes in.
Add to the `stats` subcommand:

```sh
abench-speckz stats results/ --report report.html \
    --baseline runs/v1.4-baseline/ \
    --regression-threshold 0.05
```

When `--baseline` is present the report enters **comparison mode**.

### Data model

New module `src/abench_speckz/report/_baseline.py`:

```python
@dataclass
class Comparison:
    keys: dict[str, str]      # combo var values
    metric: str
    current: float | None
    baseline: float | None
    delta: float | None       # current - baseline
    delta_pct: float | None   # delta / baseline
    status: str               # regressed | improved | neutral | new | removed
    significant: bool         # CI bands disjoint
```

Join logic: match a current row to a baseline row on
`(group_by tuple, metric)`. Unmatched current row → `new`; unmatched baseline
row → `removed`.

**Regression direction** comes from each metric's `higher_is_better` flag, read
from the `tools/*.yaml` snapshots in the results dir (precedent:
`plots.discover_from_tool_snapshots` already reads those — add a small
`higher_is_better` extractor).

**Significance gate** — a delta is `regressed` only when it (a) exceeds
`--regression-threshold` in the bad direction **and** (b) is statistically
significant:

```
significant = not (cur_ci_low <= base_ci_high and base_ci_low <= cur_ci_high)
```

Within-threshold or CI-overlapping deltas are `neutral`. This gate is what keeps
the feature from flagging noise as regressions — it is the difference between a
dev tool and publication-grade output.

### Presentation

- **Per-plot** (`bar` / `line`): render each current series plus a baseline
  ghost series — same color, `borderDash: [5,5]` for line / ~35% alpha for bar,
  label suffixed ` (baseline)`. Default `baseline_display: overlay`. A
  `baseline_display: delta` mode (bars show `delta_pct`, green/red by status) is
  a fast-follow, not v1.
- **Per-plot data table**: gains `baseline`, `Δ`, `Δ%` columns; regressed rows
  get a red cell class, improved green (CSS custom properties, dark-mode safe).
- **Regression Summary section**: auto-injected at the top of the report. One
  table of every `(combo, metric)`, sorted worst-delta-first, regressions red /
  improvements green / `n.s.` greyed. Serves both the executive and developer
  audiences; natural future home for KPI cards.

### Files to change

- **`src/abench_speckz/cli.py`** — add `--baseline` (path) and
  `--regression-threshold` (float, default 0.05) to the `stats` parser.
- **`src/abench_speckz/query.py`** — thread both args through `stats()` into the
  report call.
- **`src/abench_speckz/report/_baseline.py`** *(new)* — the join, the
  `Comparison` model, and the `higher_is_better` snapshot reader.
- **`src/abench_speckz/report/_plots.py`** — `build_plot_artifacts` takes an
  optional `baseline_dir`; when set, runs a second `query.compute_rows` on it
  with the *same* `group_by` / `metrics`, so per-plot comparison reuses the
  plot's own grouping. Feeds ghost data to the chart builder and extra columns
  to `table_html`.
- **`src/abench_speckz/report/_charts.py`** — emit ghost datasets
  (dashed/translucent). Give them no new `_colorIdx` so they can track the
  parent series color through theme switches — needs a small `_resolveConfig`
  tweak to copy a sibling dataset's resolved color.
- **`src/abench_speckz/report/_render.py`** — `generate_plots_html` accepts
  `baseline_dir` + threshold; runs one full-grain comparison
  (`group_by=all_vars`) and prepends the Regression Summary section.
- **`src/abench_speckz/report/_html.py`** — CSS classes for regressed /
  improved / n.s. table cells.
- **Tests** — the join (matching, new/removed, significance gate, direction from
  `higher_is_better`).
- **CLAUDE.md** — document `--baseline` / `--regression-threshold` and
  comparison mode.

Effort: medium (~2 days). The join module and significance test are the
substantive parts; the rest is plumbing.

---

## Open decisions

1. **Error bars** — custom inline plugin (recommended) vs. the
   `chartjs-chart-error-bars` CDN plugin.
2. **Baseline scope** — CLI flag (recommended; treats baseline as a runtime
   artifact) vs. a YAML field.
3. **Baseline in plots** — ghost overlay default, with `delta` mode as a
   fast-follow — or ship delta bars in v1 too.

---

## Backlog (not yet specified)

Further publication-/presentation-grade ideas, roughly in priority order:

- **Distribution plots** — `box` / `violin` / `cdf` / `histogram` plot types.
  `runs.jsonl` keeps every rep but only aggregates are ever charted; these
  expose variance and tail behavior that a bar-of-means hides.
- **Vector export (SVG/PDF)** — Chart.js renders to canvas, which pixelates in
  print. Main architectural fork: keep Chart.js + add an SVG export path, vs.
  render publication figures server-side (matplotlib/vega). Recommendation:
  keep Chart.js for the interactive report, add opt-in SVG export.
- **Log-scale axes** — `y_scale: log`; benchmark results routinely span orders
  of magnitude.
- **Reference lines / SLO threshold annotations** — e.g. a horizontal latency
  budget line.
- **Figure numbering + captions**, print stylesheet with page-break control.
- **Pareto frontier** highlighting on scatter (throughput vs. latency).
- **Noise/variance badges** — coefficient of variation per series, outlier
  flagging, warmup-vs-measured visibility.
- **Env drill-down** — render captured `env_probes` / `combo_probes` inline so a
  chart is traceable to the machine state that produced it.
- **Scatter error bars** — x and y whiskers; a follow-up to feature 1.

(KPI scorecard blocks are now specified — see "Executive-level features"
below.)

---

## 3. Executive-level features

Three features aimed at presentation-grade output for non-developer audiences:
KPI scorecard blocks, auto-generated headline callouts, and speedup-normalized
charts. Implemented as a 4-phase plan.

### Foundation observations

- The editorial system (`plots.py` → `Section` / `Block` →
  `_render.py::_render_sectioned`) is the natural host for KPI cards and
  headlines — both are new **block kinds**.
- Today `Block` is `kind: str` + `value: str`, and
  `BLOCK_KINDS = ("text", "plot_id", "html", "include_html")`. KPI / headline
  blocks carry *structured* config, not a string — the model needs a small
  extension (add an optional `data: Any = None` field).
- All three features need the same primitive: "resolve a metric value for a
  combo selection." Build it once (Phase 0).
- `compare`-against-a-baseline-directory overlaps with the baseline-diff
  feature above. To keep this work independent, v1 comparisons use a
  within-results-dir `where` selector only; baseline-dir comparison lands with
  baseline-diff.

### Phase 0 — Shared value-selection helper

New module `src/abench_speckz/report/_select.py`:

```python
def resolve_value(results_dir, metric, *, where=None, stat="mean") -> float | None
def rank_by(results_dir, metric, variable, *, where=None, stat="mean") -> list[tuple[str, float]]
```

Thin wrappers over `query.compute_rows` — `resolve_value` picks one metric
value under an optional combo filter; `rank_by` returns `(var_value, metric)`
pairs sorted by the metric. Foundation for all three phases.

- Files: `report/_select.py` *(new)*, tests.
- Effort: ~0.5 day.

### Phase 1 — KPI scorecard blocks

Big-number cards: headline value, label, unit, optional delta arrow.

New `kpi` block holding a list of card definitions:

```yaml
blocks:
  - kpi:
      - label: "Peak throughput"
        metric: rps
        stat: max                 # max|min|mean across matching combos
        where: { workload: heavy } # optional combo filter
        unit: "req/s"
        precision: 0
        compare: { where: { workload: light } }   # optional -> delta arrow
```

- **Model change** — extend `Block` with an optional structured payload
  (`data: Any = None`); add `"kpi"` to `BLOCK_KINDS`; `_parse_block` gets a
  branch validating the card list. `_interpolate_doc` leaves `kpi` blocks
  untouched.
- **Render** — new `report/_kpi.py` resolves each card via `_select`, computes
  the delta, and emits a `.kpi-grid` of `.kpi-card` divs (big number, label,
  unit, up/down arrow colored by `higher_is_better` read from the
  `tools/*.yaml` snapshots). `_render_sectioned` gets a `kpi` branch. CSS in
  `_html.py` (dark-mode-safe custom properties).
- Files: `plots.py`, `report/_kpi.py` *(new)*, `report/_render.py`,
  `report/_html.py`, tests, CLAUDE.md, CHANGELOG.md.
- Effort: ~1.5 days.

### Phase 2 — Headline callout blocks

Deterministic auto-generated comparison sentences (no LLM — the tool runs
offline).

New `headline` block:

```yaml
blocks:
  - headline:
      metric: rps
      compare: backend                 # variable whose values are ranked
      where: { workload: heavy }        # optional narrowing
      template: "{best} delivers {ratio}x the throughput of {worst}"
```

- **Engine** — uses `_select.rank_by`; computes `ratio = best/worst` and
  `delta_pct`; fills tokens `{best}`, `{worst}`, `{best_value}`,
  `{worst_value}`, `{ratio}`, `{delta_pct}` (values mapped through
  `var_values` / `pretty_names`). Renders as a styled `.callout` banner.
- Files: `plots.py` (`"headline"` block kind + parse),
  `report/_headline.py` *(new)*, `report/_render.py`, `report/_html.py` (CSS),
  tests, CLAUDE.md, CHANGELOG.md.
- Effort: ~1 day.

### Phase 3 — Speedup-normalized charts

A new `normalize` field that pins one series to 1.0x and expresses the rest
relative to it.

New optional plot field:

```yaml
- id: speedup
  type: bar
  x: workload
  y: rps
  group_by: backend
  normalize:
    against: backend       # must be a group_by variable
    reference: postgres    # the value pinned to 1.0x
```

- **Render** — post-process the indexed series in
  `_charts.py::build_bar_or_line_config`: for each x value, divide every series
  by the reference series' value at that x. y-axis label becomes "x speedup";
  add a horizontal 1.0 reference line. Validate at YAML load that `against` is
  in `group_by` and `reference` is non-empty.
- Files: `plots.py` (parse/validate `normalize`), `report/_charts.py`,
  `report/_plots.py` (pass-through), `report/_html.py` (reference-line plugin),
  tests, CLAUDE.md, CHANGELOG.md.
- Effort: ~1.5 days.

### Sequencing & total

Phase 0 -> 1 -> 2 -> 3. Phases 1-2 reuse Phase 0; Phase 3 is independent and
can move earlier. Total ~4.5 days. Per phase: unit tests, a `dev/` preview
entry, CLAUDE.md section, CHANGELOG entry.

### Open decisions

1. KPI `compare` — within-dir `where` only for v1 (recommended), or also
   support `baseline: <dir>` now (couples to the baseline-diff feature).
2. Headline `template` — fixed token set (recommended) vs. allowing arbitrary
   `${var}` interpolation too.
3. Normalize reference — within-dir reference combo only (recommended), or also
   normalize against a baseline dir.
