# Developer Guide

## Prerequisites

- Python 3.10+

## Setup

```sh
# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install the package with dev dependencies
pip install -e '.[dev]'
```

## Running Tests

```sh
# Run all tests
pytest

# Run a single test file
pytest tests/test_generate.py

# Run a single test by name
pytest tests/test_generate.py::test_expand_basic

# Run tests matching a keyword
pytest -k "interpolate"

# Run with verbose output
pytest -v

# Run with stdout capture disabled (see print output)
pytest -s

# Run with timeout enforcement (already configured via pytest-timeout)
pytest --timeout=30
```

Test files live in [tests/](tests/) and fixtures in [tests/fixtures/](tests/fixtures/).

## Project Layout

```
src/abench_speckz/       # Main package
tests/               # Pytest tests
tests/fixtures/      # YAML specs and tool definitions used by tests
examples/            # Example benchmark specs
```

## CLI Usage

After `pip install -e '.[dev]'`, the `abench-speckz` command is available:

```sh
# Generate env-file combos (dry run)
abench-speckz gen examples/full.yaml --dry-run

# Generate env-file combos to a directory
abench-speckz gen examples/full.yaml --out out/

# Apply a named profile
abench-speckz gen examples/full.yaml --profile smoke --out out/

# Run a benchmark sweep
abench-speckz run out/ --tool mytool.yaml --repeat 3

# Show aggregated stats
abench-speckz stats results/

# Generate HTML report with Chart.js plots (from tool YAML plots: definition)
abench-speckz stats results/ --report report.html

# Regenerate aggregates.jsonl from runs.jsonl (e.g. after editing raw runs)
abench-speckz rebuild-aggregates results/
```

## Key Modules

| Module | Purpose |
|--------|---------|
| [cli.py](src/abench_speckz/cli.py) | Entry point and argument parsing |
| [generate.py](src/abench_speckz/generate.py) | Combo generation pipeline |
| [spec.py](src/abench_speckz/spec.py) | YAML spec loading |
| [model.py](src/abench_speckz/model.py) | `Spec` and `Combo` data classes |
| [runspec.py](src/abench_speckz/runspec.py) | `ToolSpec`, `RunRequest/Result`, `AggregateRow` |
| [runner/](src/abench_speckz/runner/) | Benchmark sweep orchestration (see submodules below) |
| [executor.py](src/abench_speckz/executor.py) | Subprocess execution |
| [extract.py](src/abench_speckz/extract.py) | Metric extraction from stdout |
| [store.py](src/abench_speckz/store.py) | `runs.jsonl` / `aggregates.jsonl` persistence |
| [query.py](src/abench_speckz/query.py) | DuckDB-backed stats queries |
| [render.py](src/abench_speckz/render.py) | Table / TSV / JSON output for `stats` |
| [tools.py](src/abench_speckz/tools.py) | Tool YAML loading (`ToolSpec`) |
| [plots.py](src/abench_speckz/plots.py) | `PlotSpec` parsing and validation |
| [report/](src/abench_speckz/report/) | Chart.js HTML report rendering (see submodules below) |
| [prettynames.py](src/abench_speckz/prettynames.py) | Metric display-name resolution |

### `runner/` submodules

| Submodule | Responsibility |
|-----------|---------------|
| [_manifest.py](src/abench_speckz/runner/_manifest.py) | Load `manifest.json` and env-files; capture host/git metadata |
| [_probes.py](src/abench_speckz/runner/_probes.py) | `config_hash`; run env- and combo-scoped probes |
| [_phases.py](src/abench_speckz/runner/_phases.py) | `run_phase`: sequential shell steps with structured per-step records |
| [_monitors.py](src/abench_speckz/runner/_monitors.py) | Launch / terminate background side-car processes |
| [_planning.py](src/abench_speckz/runner/_planning.py) | Filter manifest entries, expand into reps, validate per_sweep config |
| [_rep.py](src/abench_speckz/runner/_rep.py) | Execute one rep end-to-end; build the `runs.jsonl` row |
| [_sweep.py](src/abench_speckz/runner/_sweep.py) | Top-level `execute_sweep` orchestrator |

### `report/` submodules

| Submodule | Responsibility |
|-----------|---------------|
| [_html.py](src/abench_speckz/report/_html.py) | HTML shell, CSS, and table/legend/markdown helpers |
| [_charts.py](src/abench_speckz/report/_charts.py) | Chart.js config builders (bar / line / scatter / stacked-bar) |
| [_runs.py](src/abench_speckz/report/_runs.py) | Discover and render per-run HTML reports under `raw/` |
| [_plots.py](src/abench_speckz/report/_plots.py) | Per-plot artifact assembly (data + chart + table) |
| [_render.py](src/abench_speckz/report/_render.py) | Top-level `generate_html` / `generate_plots_html` |

## Platform Support

abench-speckz is developed and tested on **macOS and Linux**. Windows is not currently supported, but the framework code could be ported with the following targeted changes:

### Unix-only APIs and their Windows equivalents

| Location | Unix API | Why it's used | Windows replacement |
|---|---|---|---|
| `store.py` | `fcntl.flock` | Exclusive lock on the results dir during a sweep | Replace `lock_results_dir` with the [`filelock`](https://pypi.org/project/filelock/) package — cross-platform drop-in |
| `executor.py`, `runner/_phases.py`, `runner/_monitors.py` | `os.killpg(os.getpgid(pid), SIGKILL)` | Kill the entire process group on timeout (catches child processes the benchmark spawned) | `subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)])` — `/T` kills the whole process tree |
| `executor.py`, `runner/_phases.py`, `runner/_monitors.py` | `signal.SIGTERM` | Graceful-stop signal to monitor processes | `proc.terminate()` (calls `TerminateProcess` on Windows; no grace period) |
| `executor.py`, `runner/_phases.py`, `runner/_monitors.py` | `start_new_session=True` | Isolate the subprocess in its own session/group so `killpg` works | Works on Windows too (maps to `CREATE_NEW_PROCESS_GROUP`), but combine with the `taskkill` approach above for teardown |

A small `_kill_tree(proc)` helper gated on `sys.platform == "win32"` would centralise all three of the signal/killpg call sites.

### What code changes would NOT fix

The `command` fields in tool YAML files are shell commands intended for bash/zsh. Those would need to be rewritten as PowerShell or cmd equivalents by the user — that is a configuration concern, not a framework concern.

## Adding a New Test

1. Create or open a file in `tests/` matching the module you're testing (e.g., `test_generate.py`).
2. Use the `fixtures_dir` fixture from `conftest.py` to access YAML files in `tests/fixtures/`.
3. Run `pytest tests/test_your_module.py -v` to verify.

## Combo Generation Pipeline

The `gen` command runs this fixed sequence inside `generate.run_pipeline()`:

1. **expand** — cartesian product of all `variables`
2. **apply_excludes** — drop combos matching any `exclude` rule
3. **apply_when** — merge `set` values and accumulate tags for matching combos
4. **dedupe** — merge identical-var combos, unioning tags
5. **interpolate_combos** — resolve `${var}` and `${env:VAR}` templates
6. **assign_universal_tags** — attach spec-level `tags` to all combos
7. **filter_tags** — apply `--tag` / `--exclude-tag` CLI filters
