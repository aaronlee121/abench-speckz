# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

# Run all tests
pytest

# Run a single test file
pytest tests/test_generate.py

# Run a single test
pytest tests/test_generate.py::test_expand_basic

# CLI (after install)
abench-speckz gen examples/full.yaml --dry-run
abench-speckz gen examples/full.yaml --out out
abench-speckz run out/ --tool mytool.yaml --repeat 3
abench-speckz run out/ --tool mytool.yaml --ci-target rps=0.05 --min-repeat 3 --max-repeat 20
abench-speckz stats results/
abench-speckz stats results/ --baseline runs/v1.4-baseline/ --report report.html
```

## Architecture

**abench-speckz** is a CLI tool with three main workflows:

### 1. `gen` — Combo generation pipeline

`spec.load()` → `Spec` → `generate.run_pipeline()` → env-files + `manifest.json`

The pipeline in `generate.run_pipeline()` is a strict sequence:
1. **expand** — cartesian product of all `variables` values (includes `static` in every combo)
2. **apply_excludes** — drop combos matching any `exclude` rule
3. **apply_when** — for matching combos: merge `set` values into vars, accumulate tags; raises `SpecError` on conflicting pins
4. **dedupe** — merge combos with identical vars, unioning tags
5. **interpolate_combos** — resolve `${var}` and `${env:VAR}` templates in all values
6. **assign_universal_tags** — add the spec-level `tags` list to all combos
7. **filter_tags** — apply `--tag` / `--exclude-tag` CLI filters

Profiles overlay the base spec (variables, static, when, exclude are merged); `default_profile` is applied when no `--profile` is given.

### 2. `run` — Benchmark execution

`runner.execute_sweep()` orchestrates the sweep:
- Loads `manifest.json` and a **tool YAML** (`ToolSpec` via `tools.load_tool()`)
- Filters and sorts combos, then walks them by per_sweep group; a **Scheduler** (`src/abench_speckz/scheduling.py`) decides which rep to issue next within each group
- Per run: interpolates the tool `command` template → `executor.execute()` (subprocess) → `extract.apply()` extracts metrics from stdout
- Writes to `runs.jsonl` (one JSON line per run) and updates `aggregates.jsonl` after each run
- Results directory is protected by a `flock` during the sweep

**Schedulers:** `FixedScheduler` (default) yields `warmup + --repeat` reps per combo in input order. `CITargetScheduler` (enabled by `--ci-target METRIC=THRESHOLD`) stops a combo early once every named metric's CI half-width is within `THRESHOLD * |mean|`, bounded by `[--min-repeat, --max-repeat]`. Scheduling is **scoped to one per_sweep / scope group at a time** — schedulers can ask for more reps of any combo in the current group but cannot jump back to a combo in a previously-completed group (teardown would have to re-run). The `Scheduler` protocol (`next(state) -> WorkItem | None`) is the seam future benchmark-specific planner plugins will hang off.

**Tool YAML fields:** `name`, `command`, `timeout_seconds`, `version_command`, `capture` (JSONPath expressions keyed to metric names), `parser` (`module:func` alternative to capture), `output_file` (read extraction input from a file the tool writes instead of stdout), `output_format` (`json` default, or `jsonl` for one object per line), `pretty_names`, `var_values` (mapping of variable name → {raw_value: display_label} for friendly legend labels), `units`, `higher_is_better`, `plots` (declarative Chart.js plot definitions consumed by `stats --report`), `env_probes` (mapping of key → shell command run once at sweep start; stdout captured into `env.snapshot.json` under `"probes"`), `combo_probes` (mapping of key → command template run once per unique config hash; stdout captured into `combo_probes.json` and each run row; see below), `setup` / `teardown` (per-rep shell steps), `post_run` (per-rep shell steps that run after teardown with access to run-result vars; see below), `monitor` (list of background-process definitions, each a string or `{command, span}` object; `span` is `per_rep` (default), `per_group`, or `per_sweep`; see below), `setup_timeout_seconds` (per-step timeout, default 120), `per_sweep_var` (string, list of strings, or negation entries like `"!var"` — see below), `scopes` (list of scope entries for nested per-sweep grouping — see below; mutually exclusive with `per_sweep_var`/`setup_per_sweep`/`teardown_per_sweep`), `grafana_links` (list of `{label, url}` entries; URL templates resolved per-run using combo vars and run-result vars including `${_started_at_ms}` / `${_finished_at_ms}`; stored in `runs.jsonl` and rendered as clickable buttons in `stats --report`).

**Plot fields:** Each entry in `plots` has: `id` (unique string), `type` (`bar`, `stacked-bar`, `line`, `scatter`), `x` (variable name or arithmetic expression of variable names for the x-axis; for `scatter`, a metric name or expression), `y` (metric name, arithmetic expression, or list thereof; `scatter` requires exactly one, `stacked-bar` requires at least two), `title` (optional display title; supports `${var}` and `${env:VAR}` interpolation), `group_by` (optional variable name or list of variable names — see below), `smooth` (boolean, line only — spline curve instead of straight segments), `fixed_vars` (boolean, default `true` — set to `false` to suppress the "Fixed: k = v" annotation), `series_label` (optional `${var}` template string — replaces the multi-column legend table with Chart.js's compact one-line legend; values are mapped through `var_values` automatically), `data_table` (boolean, default `true` — set to `false` to suppress the collapsible "Data" table below the chart), `error_bars` (optional; `ci95` or `stddev`; only on `bar` and `line` — draws whiskers on each data point using CI or ±stddev from the aggregated stats). Axis expressions support `+`, `-`, `*`, `/`, `**`, `//`, `%` over variable or metric names and numeric literals; for `y` expressions, exactly one identifier must be a metric name (the rest are treated as combo variables). Syntax is validated at YAML load time.

**Report layout fields (optional, top-level alongside `plots`):**
- `report_title` — string used as the HTML `<title>` and H1.
- `report_description` — Markdown rendered as a `.report-intro` block under the H1 (only when `sections` is present).
- `sections` — list of editorial sections. When present, plots are no longer rendered automatically; instead each section's `blocks` reference plots by id. Plots not referenced by any block are silently omitted (lets you keep a plot library and use a subset).
- `theme` — optional mapping controlling the visual theme of the report. All sub-fields are optional:
  - `palette` — color palette name: `tableau` (default), `vivid`, `pastel`, `paired`, `muted`, `accessible`.
  - `dark_mode` — `true` (always dark), `false` (always light), or `auto` (default — follows OS preference).
  - `switcher` — `false` (default) hides the interactive theme toolbar; `true` shows a palette picker and dark-mode toggle in the report header. When the switcher is shown, `palette` and `dark_mode` serve as the initial defaults (overridable by the reader via localStorage).

Each section entry has:
- `title` (required) — H2 heading.
- `description` (optional) — Markdown rendered above the section's blocks.
- `blocks` (optional list) — interleaved content. Each block has exactly one of:
  - `text: <markdown>` — Markdown rendered to HTML.
  - `plot_id: <id>` — references an entry in top-level `plots`. The same id may appear in multiple blocks; each occurrence renders an independent canvas.
  - `html: <raw html>` — inlined verbatim.
  - `include_html: <path>` — file path resolved relative to the plots YAML (or relative to each tool YAML when sections are discovered from `tools/`); contents inlined verbatim. Missing file is a parse error.

**`group_by` in plots:** Splits chart data into multiple series. Accepts a single variable name (`group_by: workload`) or a list (`group_by: [workload, backend]`); multiple keys are joined with ` / ` in the legend label.

- `bar` / `stacked-bar` / `line`: without `group_by`, each `y` metric is a single series across x-axis values. With `group_by`, each unique combination of variable values produces a separate series per metric.
- `scatter`: `x` and `y` are both metrics (not variables). Each unique combination of `group_by` values becomes its own named point. Without `group_by`, all data collapses into a single `"all"` series.

Example — line chart of throughput vs. concurrency, one line per workload type:
```yaml
- id: rps_vs_concurrency
  type: line
  x: concurrency       # combo variable swept across x-axis
  y: rps               # metric on y-axis
  group_by: workload   # one line per workload value
```

Example — scatter plot of throughput vs. latency tradeoff, one point per workload:
```yaml
- id: throughput_vs_latency
  type: scatter
  x: rps               # metric on x-axis
  y: p95_ms            # metric on y-axis
  group_by: workload   # one labeled point per workload value
```

**`normalize` plot field (bar/line only):** Pins one series to 1.0× and expresses all others relative to it. Requires `group_by` with the `against` variable. `reference` is the value pinned to 1.0×. Y-axis becomes "× speedup"; a dashed reference line at 1.0 is drawn by the `abenchRefLine` Chart.js plugin.

```yaml
- id: speedup
  type: bar
  x: workload
  y: rps
  group_by: backend
  normalize:
    against: backend       # must be in group_by
    reference: postgres    # value pinned to 1.0×
```

**Executive-level block kinds (in `sections`):**

`kpi` — Grid of big-number scorecard cards. Each card specifies a metric value to highlight, with optional delta comparison arrow. `stat` controls how the value is reduced across matching combos: `max`, `min`, or `mean` (default). `where` is a dict of variable filters. `compare.where` targets a second combo to show Δ%.

```yaml
- kpi:
    - label: "Peak throughput"
      metric: rps
      stat: max
      where: {workload: heavy}
      unit: "req/s"
      precision: 0
      compare:
        where: {workload: light}
```

`headline` — A deterministic auto-generated sentence comparing the best and worst values of a metric across a variable. Template tokens: `{best}`, `{worst}`, `{best_value}`, `{worst_value}`, `{ratio}`, `{delta_pct}`. Sort direction follows `higher_is_better` from `tools/*.yaml`.

```yaml
- headline:
    metric: rps
    compare: backend
    where: {workload: heavy}
    template: "{best} delivers {ratio} the throughput of {worst}"
```

Metric extraction (`extract.py`): reads stdout (or `output_file`) as JSON or JSONL, then applies JSONPath expressions from `capture`. A key ending in `[]` collects all matches as a list. Alternatively, `parser: "module:func"` calls an arbitrary Python function.

### 4. `rebuild-aggregates` — recompute aggregates

`store.rebuild_aggregates()` regenerates `aggregates.jsonl` from `runs.jsonl` (useful after editing/inspecting raw runs).

### 3. `stats` — Aggregate queries

`query.stats()` uses DuckDB to query `aggregates.jsonl` (fast path) or `runs.jsonl` (when `--from-raw` or `--group-by` is used, since percentiles aren't additive across pre-aggregated rows).

**Comparison mode** — pass `--baseline <dir>` to compare the results directory against a baseline directory. When active the HTML report gains: a **Regression Summary** section (auto-injected at the top, every combo×metric sorted worst-delta-first, regressed rows red / improved green / `n.s.` greyed); **ghost overlay series** on every bar/line chart (dashed for line, ~35% alpha for bar, labeled ` (baseline)`); and **Δ / Δ%** columns in every per-plot data table. A delta is only flagged as `regressed` or `improved` when it (a) exceeds `--regression-threshold` (default `0.05`, i.e. 5%) in the bad/good direction **and** (b) is statistically significant (CI bands disjoint). Direction is read from `higher_is_better` in the `tools/*.yaml` snapshots; defaults to higher-is-better when absent.

### Key data structures

| Class | Module | Purpose |
|-------|--------|---------|
| `Spec` | `model.py` | Parsed YAML spec (static, variables, when/exclude rules) |
| `Combo` | `model.py` | One variable assignment + accumulated tags + varying_keys |
| `ToolSpec` | `runspec.py` | Tool definition (command template, capture rules, metadata) |
| `RunRequest/RunResult` | `runspec.py` | Per-run I/O passed to/from `Executor` |
| `AggregateRow` | `runspec.py` | Statistical summary (n, mean, stddev, p50/95/99, CI) stored in `aggregates.jsonl` |

### Results directory layout

```
results/
  runs.jsonl            # append-only, one row per run
  aggregates.jsonl      # rewritten atomically after each run
  manifest.snapshot.json
  tools/{name}.yaml
  env.snapshot.json
  pretty_names.json
  raw/{run_id}.json     # raw stdout, only if --keep-raw or on extract failure
```

### Interpolation

`${var}` references other combo vars; `${env:VAR}` reads from the process environment. Resolved iteratively up to 16 times to support chained references. Use `$$` to escape a literal `$`.

Built-in synthetic variables (injected per-rep, not user-definable):
- `${_envfile}` — absolute path to the current combo's env file. Available in `command`, `setup`, `teardown`, `post_run`, `output_file`, `setup_per_sweep`, and `teardown_per_sweep`. For per_sweep phases, resolves to the first entry's env file in the group.
- `${_run_id}` — UUID for this rep (same value written to `runs.jsonl`). Available in `command`, `setup`, `teardown`, `post_run`, `setup_per_sweep`, and `teardown_per_sweep`. For per_sweep phases, a single UUID is generated per group and shared between `setup_per_sweep` and `teardown_per_sweep`.
- `${_results_dir}` — absolute path to the results directory. Available in `command`, `setup`, `teardown`, `post_run`, `output_file`, `setup_per_sweep`, `teardown_per_sweep`, `combo_probes`, and all `monitor` spans.
- `${_exit_code}`, `${_started_at}`, `${_finished_at}`, `${_duration_ms}` — benchmark result fields. Available in `post_run` only (result isn't known earlier).
- `${_started_at_ms}`, `${_finished_at_ms}` — run start/end as Unix milliseconds. Available in `grafana_links` URL templates only.

Variable names starting with `_` are reserved and rejected in spec YAML.

**`post_run`** steps run after `teardown` completes, always — including when the benchmark exits non-zero. The extra synthetic vars let scripts collect time-windowed metrics from external systems (Prometheus, InfluxDB, etc.) keyed to the exact run. `post_run` failure is folded into `failure_reason` but does not suppress the benchmark result. Step records are stored in `raw/{run_id}.json` under `"post_run"`.

**`combo_probes`** runs each command template once per unique config hash (before the first rep for that hash). Commands interpolate combo vars (`${var}`, `${_envfile}`, `${_results_dir}`, `${env:VAR}`). Trimmed stdout is the value; non-zero exit, missing command, timeout, or interpolation error stores `null` — probes never abort a sweep. Results are written to `combo_probes.json` (keyed by config hash) and embedded in every `runs.jsonl` row under `"combo_probes"`. Useful for capturing system or service state that varies per combo (e.g. effective DB config, kernel parameters after per-sweep setup).

**`monitor`** is a list of background-process definitions. Each entry is either a plain string (command template) or an object with `command` and an optional `span` field. The `span` controls how long the monitor lives:

| `span` | Lifetime | Variables available |
|---|---|---|
| `per_rep` (default) | After `setup`, until benchmark finishes | Full combo vars + `${_run_id}`, `${_envfile}`, `${_results_dir}`, `${env:VAR}` |
| `per_group` | After `setup_per_sweep`, until before `teardown_per_sweep` (spans all reps of a group) | Group phase vars: all per_sweep_var values + `${_run_id}`, `${_envfile}`, `${_results_dir}`, `${env:VAR}` |
| `per_sweep` | After sweep init, until all groups finish | `${_run_id}` (sweep-level UUID) + `${_results_dir}`, `${env:VAR}` |

Each process receives SIGTERM when its span ends; SIGKILL follows after 5 seconds if it has not exited. A monitor that fails to start is recorded but never aborts the run.

Records (`monitor_start`, `monitor_stop`) are written under `keep_raw` or when any monitor fails to start:
- `per_rep`: in `raw/{run_id}.json`
- `per_group`: in `raw/sweep.json` / `raw/sweep-{slug}.json` alongside setup/teardown records
- `per_sweep`: in `raw/sweep_monitors.json`

```yaml
monitor:
  - "python collect_metrics.py --rep ${_run_id}"    # span: per_rep (default, string form)
  - command: "group_monitor.sh ${workload}"
    span: per_group
  - command: "top -b > sweep.log"
    span: per_sweep
```

**`grafana_links`** is a list of `{label, url}` entries. URL templates are resolved per-run after each benchmark completes and the resolved list is stored in `runs.jsonl` under `"grafana_links"`. In `stats --report`, the links appear as styled buttons inside each run's collapsible block (section title is "Per-run links" when there are no HTML report files, "Per-run reports" when both are present). Template variables available: all combo vars, `${env:VAR}`, `${_run_id}`, `${_started_at}` (ISO), `${_finished_at}` (ISO), `${_started_at_ms}` (Unix ms), `${_finished_at_ms}` (Unix ms). An unresolvable URL is stored as `null` and omitted from the report.

```yaml
grafana_links:
  - label: "Overview"
    url: "https://grafana.example.com/d/abc?from=${_started_at_ms}&to=${_finished_at_ms}&var-workload=${workload}"
  - label: "CPU"
    url: "https://grafana.example.com/d/cpu?from=${_started_at_ms}&to=${_finished_at_ms}"
```

**`per_sweep_var`** controls how combos are grouped for `setup_per_sweep` / `teardown_per_sweep`. Accepts a string, a list of strings, or negation entries (prefix `!` to exclude a variable from the auto-expanded set). Negation is resolved against the `variables` keys in the manifest (static values are excluded), mirroring the `group_by` negation in plots.

```yaml
per_sweep_var: backend                  # one group per backend value
per_sweep_var: [backend, workload]      # one group per (backend, workload) combo
per_sweep_var: "!concurrency"           # one group per unique combo of all vars except concurrency
per_sweep_var: [backend, "!concurrency"] # include backend explicitly, exclude concurrency
```

`setup_per_sweep` / `teardown_per_sweep` steps may only reference variables listed in `per_sweep_var` (after negation expansion), plus `${_envfile}`, `${_run_id}`, `${_results_dir}`, and `${env:VAR}`. The group slug in raw file names joins all group variable values with `-`.

**`scopes`** provides nested multi-level per-sweep grouping. Each entry has `vars` (required — same string/list/negation syntax as `per_sweep_var`) plus optional `setup` and `teardown` step lists. Scopes are ordered outermost to innermost; setup runs top-down before reps begin, teardown runs bottom-up after reps finish. `scopes` cannot be combined with `per_sweep_var`, `setup_per_sweep`, or `teardown_per_sweep`.

Step variable access follows scope depth: scope `i` steps may reference vars from scopes `0..i` (outer scopes are constant within an inner group), plus `${_envfile}`, `${_run_id}`, `${_results_dir}`, and `${env:VAR}`.

```yaml
scopes:
  - vars: backend                        # outer: one group per backend
    setup:
      - "deploy.sh ${backend}"
    teardown:
      - "stop.sh ${backend}"
  - vars: [backend, workload]            # inner: one group per (backend, workload)
    setup:
      - "load_dataset.sh ${backend} ${workload}"   # may reference outer var too
    teardown:
      - "cleanup_dataset.sh ${workload}"
```

Execution order (for backends `pg`, `mysql` each with workloads `read`, `write`):
1. `deploy.sh pg` → `load_dataset.sh pg read` → all reps → `cleanup_dataset.sh read`
2. `load_dataset.sh pg write` → all reps → `cleanup_dataset.sh write`
3. `stop.sh pg`
4. `deploy.sh mysql` → … → `stop.sh mysql`

Innermost teardown failures are folded into the last rep's `failure_reason` (same as flat mode). Outer teardown failures are written to `raw/scope{i}-{slug}.json`. A scope entry with no `setup` or `teardown` is a pure grouping scope (useful as a bracket for per_group monitors at that level).
