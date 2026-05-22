---
name: run-abench-speckz
description: run, build, test, smoke-test, or screenshot abench-speckz; launch the CLI; verify a change works end-to-end
---

abench-speckz is a Python CLI tool with three subcommands: `gen` (generates env-file combos from a YAML spec), `run` (executes a benchmark tool per combo), and `stats` (queries results + generates an HTML report). There is no GUI. The driver is a bash smoke script at `.claude/skills/run-abench-speckz/smoke.sh` that runs the full pipeline end-to-end.

## Prerequisites

Python 3.10+ with a virtualenv:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

No system packages beyond Python are needed.

## Build

```bash
pip install -e '.[dev]'
```

That's the only build step. The `abench-speckz` command is registered as an entry point and available immediately after install.

## Run (agent path)

The smoke script exercises the full pipeline: `gen` → `run` → `--skip-existing` check → `stats` (table + HTML report) → `rebuild-aggregates`. It uses `tests/fixtures/fake_tool.py` (no real benchmark needed) and exits non-zero on any failure.

```bash
source .venv/bin/activate
bash .claude/skills/run-abench-speckz/smoke.sh          # workdir: /tmp/abench-smoke-$$
bash .claude/skills/run-abench-speckz/smoke.sh /tmp/my  # specify workdir
```

Expected output ends with:

```
=== all checks passed ===
report: /tmp/.../report.html
```

To test a specific feature without the full pipeline, call the CLI directly:

```bash
# Gen: preview combos
abench-speckz gen examples/full.yaml --dry-run
abench-speckz gen examples/full.yaml --dry-run --profile full

# Run: sweep with fake tool (fake.tool.yaml in this skill dir)
abench-speckz gen examples/full.yaml --out /tmp/out
abench-speckz run /tmp/out/ \
  --tool .claude/skills/run-abench-speckz/fake.tool.yaml \
  --repeat 3 --results /tmp/results

# Stats: table
abench-speckz stats /tmp/results/
abench-speckz stats /tmp/results/ --group-by workload --group-by backend

# Stats: HTML report
abench-speckz stats /tmp/results/ --report /tmp/report.html
```

Note: `--group-by` takes one variable per flag; `--group-by workload,backend` is rejected.

## Run (human path)

Same as above — this is a CLI tool, no separate "human" launch mode.

## Test

```bash
pytest                                  # full suite (~556 tests, ~12s)
pytest tests/test_runner.py             # runner only
pytest tests/test_runner.py::test_skip_existing  # single test
```

## Gotchas

- **`--group-by` syntax**: pass it once per variable (`--group-by workload --group-by backend`), not comma-separated. The CLI rejects `workload,backend` with a validation error.
- **`fake.tool.yaml` uses a relative path** for `command: "python tests/fixtures/fake_tool.py"`. Run from the repo root, or the command will not resolve.
- **`--skip-existing` skips warmup reps too** (fixed in commit 415ca92). If you see warmups re-running on a second pass, check that you're on a version after that commit.
- **Default profile is `smoke`** (1 combo). Use `--profile full` with `gen` to get the full 15-combo matrix.
- **Results dir is append-only**: `runs.jsonl` is never truncated. Use a fresh `--results` dir when testing from scratch, or use `--skip-existing` to avoid re-running completed combos.
- **Stats reads `aggregates.jsonl` by default** (fast path). If you edit `runs.jsonl` manually, run `rebuild-aggregates` to resync; or pass `--from-raw` to query raw runs directly.

## Troubleshooting

**`abench-speckz: command not found`** — activate the venv first (`source .venv/bin/activate`) or reinstall (`pip install -e '.[dev]'`).

**`ERROR: No module named abench_speckz`** — you're running `python` outside the venv. Activate it.

**`error: invalid group-by key: 'workload,backend'`** — use `--group-by workload --group-by backend` (separate flags).

**`# 0 rows` in stats output** — the results dir is empty or missing `runs.jsonl`. Run `abench-speckz run ...` first.
