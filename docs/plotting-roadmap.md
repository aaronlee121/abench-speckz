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
- **KPI scorecard blocks** — big-number cards with delta arrows; a new block
  kind alongside `text` / `plot_id`.
- **Scatter error bars** — x and y whiskers; a follow-up to feature 1.
