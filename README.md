# benchspec

Generate Docker env-file combinations from a YAML benchmark spec, execute a benchmark tool across every combination, and query the results.

## Install

```sh
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

## Workflow

```
spec.yaml  →  benchspec gen  →  out/ (env-files + manifest.json)
                                    ↓
              benchspec run  →  results/ (runs.jsonl + aggregates.jsonl)
                                    ↓
              benchspec stats →  table / JSON / TSV
```

## Commands

### `gen` — generate env-file combinations

```sh
benchspec gen spec.yaml --out out/           # write env-files
benchspec gen spec.yaml --dry-run            # print summary table
benchspec gen spec.yaml --list               # print TSV
benchspec gen spec.yaml --profile smoke --out out/
benchspec gen spec.yaml --tag stress --out out/
benchspec gen spec.yaml --exclude-tag slow --out out/
```

Each combination is written as a Docker env-file (`KEY=value` per line). A `manifest.json` in the output directory maps each filename back to its full variable assignment and tags.

### `run` — execute a tool across every combination

```sh
benchspec run out/ --tool oha.tool.yaml
benchspec run out/ --tool oha.tool.yaml --repeat 5 --warmup 1
benchspec run out/ --tool oha.tool.yaml --filter workload=read
benchspec run out/ --tool oha.tool.yaml --filter-tag stress
benchspec run out/ --tool oha.tool.yaml --skip-existing --keep-raw
benchspec run out/ --tool oha.tool.yaml --dry-run   # print planned commands
```

Results are written to `results/` (configurable with `--results`).

### `stats` — aggregate and display results

```sh
benchspec stats results/
benchspec stats results/ --group-by workload --group-by concurrency
benchspec stats results/ --metric requests_per_sec --metric p50_ms
benchspec stats results/ --where workload=read
benchspec stats results/ --filter-tag stress
benchspec stats results/ --format json
benchspec stats results/ --format tsv
benchspec stats results/ --pretty            # use display names from tool YAML
benchspec stats results/ --from-raw          # recompute from runs.jsonl
```

### `rebuild-aggregates` — regenerate aggregates from raw runs

```sh
benchspec rebuild-aggregates results/
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
```

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
