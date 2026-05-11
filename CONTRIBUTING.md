# Contributing

Thanks for your interest in benchspec. This file covers what you need to make a change locally and open a pull request.

## Setup

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

This installs the package in editable mode along with `pytest`, `pytest-timeout`, `ruff`, and `mypy`.

## Before opening a PR

Run the same checks CI runs:

```sh
ruff check .
mypy
pytest -q
```

All three must pass. Auto-fix common lint issues with `ruff check . --fix`.

## Tests

- Test files live in `tests/` and follow `test_<module>.py` naming.
- Shared YAML/tool fixtures live in `tests/fixtures/`; use the `fixtures_dir` fixture from `conftest.py` to access them.
- Single test file: `pytest tests/test_generate.py`
- Single test: `pytest tests/test_generate.py::test_expand_basic`
- Keyword match: `pytest -k interpolate`

## Style

- Code is type-annotated; new code should be too.
- Default to no comments; add one only when *why* is non-obvious.
- One short line is enough for most module/function docstrings.
- Don't add backwards-compatibility shims for unreleased code; just change it.

## Commit messages

- Use the imperative ("Add X", not "Added X").
- Short subject line (under ~70 chars), then a body explaining *why* if it's not obvious from the diff.

## Pull requests

- Keep PRs focused. One topic per PR makes review faster.
- The CI matrix runs ruff + mypy + pytest on Python 3.10, 3.11, and 3.12 — make sure your change works on the lowest supported version.
- Link related issues in the description.

## Project layout

See [developer.md](developer.md) for the module map and a tour of the combo-generation pipeline.
