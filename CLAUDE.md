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

**Tool YAML fields:** `name`, `command`, `timeout_seconds`, `version_command`, `capture` (JSONPath expressions keyed to metric names), `parser` (`module:func` alternative to capture), `pretty_names`, `units`, `higher_is_better`.

Metric extraction (`extract.py`): parses stdout as JSON, then applies JSONPath expressions from `capture`. A key ending in `[]` collects all matches as a list. Alternatively, `parser: "module:func"` calls an arbitrary Python function.

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
