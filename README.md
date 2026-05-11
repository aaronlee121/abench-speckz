# abench-speckz

Generate Docker env-file combinations from a YAML benchmark spec, execute a benchmark tool across every combination, and query the results.

## Install

```sh
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

> **Note on examples:** files under [`examples/`](examples/) reference paths like `python examples/sample_bench.py`. Those paths are relative to the repo root, so the examples run only from a checkout — not from an arbitrary working directory after `pip install`. Clone the repo and `cd` into it to follow the examples verbatim.

## Workflow

```
spec.yaml  →  abench-speckz gen  →  out/ (env-files + manifest.json)
                                    ↓
              abench-speckz run  →  results/ (runs.jsonl + aggregates.jsonl)
                                    ↓
              abench-speckz stats →  table / JSON / TSV
```

## Commands

### `gen` — generate env-file combinations

```sh
abench-speckz gen spec.yaml --out out/           # write env-files
abench-speckz gen spec.yaml --dry-run            # print summary table
abench-speckz gen spec.yaml --list               # print TSV
abench-speckz gen spec.yaml --profile smoke --out out/
abench-speckz gen spec.yaml --tag stress --out out/
abench-speckz gen spec.yaml --exclude-tag slow --out out/
```

Each combination is written as a Docker env-file (`KEY=value` per line). A `manifest.json` in the output directory maps each filename back to its full variable assignment and tags.

### `run` — execute a tool across every combination

```sh
abench-speckz run out/ --tool oha.tool.yaml
abench-speckz run out/ --tool oha.tool.yaml --repeat 5 --warmup 1
abench-speckz run out/ --tool oha.tool.yaml --filter workload=read
abench-speckz run out/ --tool oha.tool.yaml --filter-tag stress
abench-speckz run out/ --tool oha.tool.yaml --skip-existing --keep-raw
abench-speckz run out/ --tool oha.tool.yaml --dry-run   # print planned commands
```

Results are written to `results/` (configurable with `--results`).

### `stats` — aggregate and display results

```sh
abench-speckz stats results/
abench-speckz stats results/ --group-by workload --group-by concurrency
abench-speckz stats results/ --metric requests_per_sec --metric p50_ms
abench-speckz stats results/ --where workload=read
abench-speckz stats results/ --filter-tag stress
abench-speckz stats results/ --format json
abench-speckz stats results/ --format tsv
abench-speckz stats results/ --pretty            # use display names from tool YAML
abench-speckz stats results/ --from-raw          # recompute from runs.jsonl
```

### `rebuild-aggregates` — regenerate aggregates from raw runs

```sh
abench-speckz rebuild-aggregates results/
```

## Spec format

```yaml
static:
  IMAGE: myapp:latest
  REGION: us-east-1

variables:
  workload:    [read, write, mixed]
  concurrency: [1, 8, 64]
  backend:     [postgres, mysql]

# conditional overrides and tagging
when:
  - if:  { workload: write, backend: mysql }
    set: { LOCK_TIMEOUT: "30s" }
    tag: [slow, write-heavy]
  - if:  { concurrency: 64 }
    set: { THREAD_POOL: "${concurrency}" }
    tag: [stress]

# combos to drop entirely
exclude:
  - { backend: mysql, concurrency: 1 }

# tags applied to every combo
tags: [bench]

profiles:
  smoke:
    variables:
      concurrency: [1]
      workload: [read]
  full: {}

default_profile: smoke
```

**Interpolation:** use `${var}` to reference other variables and `${env:VAR}` to read from the process environment. Use `$$` for a literal `$`.

Profiles overlay the base spec — variables, static, when, and exclude lists are merged. The `default_profile` is used when `--profile` is not specified.

## Tool YAML format

```yaml
name: oha
command: "oha ${URL} -n ${REQUESTS} -c ${concurrency} --json"
timeout_seconds: 300
version_command: "oha --version"

# extract metrics from JSON stdout via JSONPath
capture:
  requests_per_sec: "$.summary.requestsPerSec"
  p50_ms: "$.latencyPercentiles.p50"
  errors[]: "$.errors[*].message"   # trailing [] collects all matches as a list

# alternative: a custom Python parser function
# parser: "mymodule:parse_fn"       # fn(stdout: str) -> dict

pretty_names:
  requests_per_sec: "Requests/s"
  p50_ms: "p50 latency"
units:
  p50_ms: ms
higher_is_better:
  requests_per_sec: true
  p50_ms: false

# optional: shell steps run around every rep (warmup and measured)
setup:
  - "docker compose up -d redis"
  - "sleep 1"
teardown:
  - "docker compose down -v"
setup_timeout_seconds: 120   # per-step timeout for setup/teardown (default 120)
```

**Setup / teardown.** Each rep is wrapped `setup → command → teardown`. Teardown runs in a `finally` block, so it also fires on benchmark failure or `Ctrl-C`. Combo vars (`${var}`) and `${env:VAR}` interpolate in setup/teardown commands. Steps are split with `shlex.split` and executed without a shell, so chain via multiple list entries rather than `&&`.

- Setup failure → the command is skipped, teardown still runs best-effort, and `failure_reason` is recorded as `setup[i]: …`.
- Teardown failure → the benchmark's `exit_code` and metrics are preserved, but `teardown[i]: …` is appended to `failure_reason` (so the run is counted as failed).

## Results directory layout

```
results/
  runs.jsonl              # append-only log, one JSON object per run
  aggregates.jsonl        # per-combo stats (n, mean, stddev, p50/95/99, CI95)
  manifest.snapshot.json  # copy of the manifest used
  tools/{name}.yaml       # copy of the tool YAML used
  env.snapshot.json       # host info (OS, CPU, git SHA)
  pretty_names.json       # merged metric display names
  raw/{run_id}.json       # raw stdout (only with --keep-raw or on extract failure)
```
