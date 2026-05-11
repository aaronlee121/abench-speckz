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
```

## Key Modules

| Module | Purpose |
|--------|---------|
| [cli.py](src/abench_speckz/cli.py) | Entry point and argument parsing |
| [generate.py](src/abench_speckz/generate.py) | Combo generation pipeline |
| [spec.py](src/abench_speckz/spec.py) | YAML spec loading |
| [model.py](src/abench_speckz/model.py) | `Spec` and `Combo` data classes |
| [runner.py](src/abench_speckz/runner.py) | Benchmark sweep orchestration |
| [executor.py](src/abench_speckz/executor.py) | Subprocess execution |
| [extract.py](src/abench_speckz/extract.py) | Metric extraction from stdout |
| [query.py](src/abench_speckz/query.py) | DuckDB-backed stats queries |
| [tools.py](src/abench_speckz/tools.py) | Tool YAML loading (`ToolSpec`) |

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
