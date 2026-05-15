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
abench-speckz stats results/
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
- Plans work: each matching combo × repeat+warmup reps
- Per run: interpolates the tool `command` template → `executor.execute()` (subprocess) → `extract.apply()` extracts metrics from stdout
- Writes to `runs.jsonl` (one JSON line per run) and updates `aggregates.jsonl` after each run
- Results directory is protected by a `flock` during the sweep

**Tool YAML fields:** `name`, `command`, `timeout_seconds`, `version_command`, `capture` (JSONPath expressions keyed to metric names), `parser` (`module:func` alternative to capture), `output_file` (read extraction input from a file the tool writes instead of stdout), `output_format` (`json` default, or `jsonl` for one object per line), `pretty_names`, `units`, `higher_is_better`, `plots` (declarative Chart.js plot definitions consumed by `stats --report`), `env_probes` (mapping of key → shell command run once at sweep start; stdout captured into `env.snapshot.json` under `"probes"`), `combo_probes` (mapping of key → command template run once per unique config hash; stdout captured into `combo_probes.json` and each run row; see below), `setup` / `teardown` (per-rep shell steps), `post_run` (per-rep shell steps that run after teardown with access to run-result vars; see below), `monitor` (list of shell commands launched as background processes before the benchmark command and terminated after it completes; see below), `setup_timeout_seconds` (per-step timeout, default 120).

**Plot fields:** Each entry in `plots` has: `id` (unique string), `type` (`bar`, `stacked-bar`, `line`, `scatter`), `x` (variable name for the x-axis, or a metric name for `scatter`), `y` (metric name or list of metric names; `scatter` requires exactly one, `stacked-bar` requires at least two), `title` (optional display title), `group_by` (optional variable name or list of variable names — see below).

**Report layout fields (optional, top-level alongside `plots`):**
- `report_title` — string used as the HTML `<title>` and H1.
- `report_description` — Markdown rendered as a `.report-intro` block under the H1 (only when `sections` is present).
- `sections` — list of editorial sections. When present, plots are no longer rendered automatically; instead each section's `blocks` reference plots by id. Plots not referenced by any block are silently omitted (lets you keep a plot library and use a subset).

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

Metric extraction (`extract.py`): reads stdout (or `output_file`) as JSON or JSONL, then applies JSONPath expressions from `capture`. A key ending in `[]` collects all matches as a list. Alternatively, `parser: "module:func"` calls an arbitrary Python function.

### 4. `rebuild-aggregates` — recompute aggregates

`store.rebuild_aggregates()` regenerates `aggregates.jsonl` from `runs.jsonl` (useful after editing/inspecting raw runs).

### 3. `stats` — Aggregate queries

`query.stats()` uses DuckDB to query `aggregates.jsonl` (fast path) or `runs.jsonl` (when `--from-raw` or `--group-by` is used, since percentiles aren't additive across pre-aggregated rows).

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
- `${_exit_code}`, `${_started_at}`, `${_finished_at}`, `${_duration_ms}` — benchmark result fields. Available in `post_run` only (result isn't known earlier).

Variable names starting with `_` are reserved and rejected in spec YAML.

**`post_run`** steps run after `teardown` completes, always — including when the benchmark exits non-zero. The extra synthetic vars let scripts collect time-windowed metrics from external systems (Prometheus, InfluxDB, etc.) keyed to the exact run. `post_run` failure is folded into `failure_reason` but does not suppress the benchmark result. Step records are stored in `raw/{run_id}.json` under `"post_run"`.

**`combo_probes`** runs each command template once per unique config hash (before the first rep for that hash). Commands interpolate combo vars (`${var}`, `${_envfile}`, `${env:VAR}`). Trimmed stdout is the value; non-zero exit, missing command, timeout, or interpolation error stores `null` — probes never abort a sweep. Results are written to `combo_probes.json` (keyed by config hash) and embedded in every `runs.jsonl` row under `"combo_probes"`. Useful for capturing system or service state that varies per combo (e.g. effective DB config, kernel parameters after per-sweep setup).

**`monitor`** is a list of shell commands launched as **background processes** after `setup` completes and before the benchmark command runs. Each process is sent SIGTERM when the benchmark finishes (success or failure); if it does not exit within 5 seconds it receives SIGKILL. Commands interpolate combo vars (`${var}`, `${_envfile}`, `${env:VAR}`). A monitor that fails to start is recorded but never aborts the run. Start records (`monitor_start`) and stop records (`monitor_stop`) are written to `raw/{run_id}.json` under `keep_raw` or when any monitor fails to start. Useful for running side-car collectors (e.g. `python collect_metrics.py`) that run concurrently with each rep.
