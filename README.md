# abench-speckz

Generate Docker env-file combinations from a YAML benchmark spec, execute a benchmark tool across every combination, and query the results.

## Install

Requires Python 3.10+.

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
abench-speckz run out/ --tool oha.tool.yaml --filter-exclude-tag slow
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
abench-speckz stats results/ --filter-exclude-tag slow
abench-speckz stats results/ --format json
abench-speckz stats results/ --format tsv
abench-speckz stats results/ --pretty            # use display names from tool YAML
abench-speckz stats results/ --from-raw          # recompute from runs.jsonl
abench-speckz stats results/ --report report.html              # self-contained Chart.js HTML
abench-speckz stats results/ --report report.html --plots plots.yaml  # override tool YAML plots
```

`--report` writes a self-contained HTML file with Chart.js plots. Plot definitions come from the tool YAML's `plots:` list (see below), or from a separate YAML file via `--plots`. When no plots are defined, a default per-metric bar chart is rendered.

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

Variable names starting with `_` are reserved and will be rejected at load time.

Profiles overlay the base spec — variables, static, when, and exclude lists are merged. The `default_profile` is used when `--profile` is not specified.

## Tool YAML format

```yaml
name: oha
command: "oha ${URL} -n ${REQUESTS} -c ${concurrency} --json"
# ${_envfile} is a built-in variable: absolute path to the current combo's env file.
# Example: docker run --env-file ${_envfile} myimage
timeout_seconds: 300
version_command: "oha --version"

# extract metrics from JSON stdout via JSONPath
capture:
  requests_per_sec: "$.summary.requestsPerSec"
  p50_ms: "$.latencyPercentiles.p50"
  errors[]: "$.errors[*].message"   # trailing [] collects all matches as a list

# alternative: a custom Python parser function
# parser: "mymodule:parse_fn"       # fn(stdout: str) -> dict

# read extraction input from a file the tool writes, instead of stdout
# output_file: "results.json"       # interpolates ${var} / ${env:VAR}
# output_format: jsonl              # "json" (default) or "jsonl" for one JSON object per line

pretty_names:
  requests_per_sec: "Requests/s"
  p50_ms: "p50 latency"
units:
  p50_ms: ms
higher_is_better:
  requests_per_sec: true
  p50_ms: false

# optional: run once at the start of the sweep; output captured into env.snapshot.json
# under a "probes" key. Non-zero exit or missing command stores null for that key.
env_probes:
  kernel:       "uname -r"
  cpu:          "sysctl -n machdep.cpu.brand_string"
  redis_version: "redis-cli --version"

# optional: shell steps run around every rep (warmup and measured)
setup:
  - "docker compose up -d redis"
  - "sleep 1"
teardown:
  - "docker compose down -v"
setup_timeout_seconds: 120   # per-step timeout for setup/teardown (default 120)

# optional: declarative plots used by `stats --report`
plots:
  - id: rps_by_workload
    type: bar                        # bar | stacked-bar | line | scatter
    title: "Throughput by workload"
    x: workload
    y: requests_per_sec
  - id: latency_breakdown
    type: stacked-bar
    title: "Latency percentiles"
    x: workload
    y: [p50_ms, p95_ms, p99_ms]
  - id: rps_vs_concurrency
    type: line
    title: "Throughput scaling"
    x: concurrency                        # combo variable on x-axis
    y: requests_per_sec
    group_by: workload                    # one line per workload value
  - id: rps_vs_concurrency_multi
    type: line
    title: "Throughput scaling by workload + backend"
    x: concurrency
    y: requests_per_sec
    group_by: [workload, backend]         # one line per workload+backend combo
  - id: throughput_vs_latency
    type: scatter
    title: "Throughput / latency tradeoff"
    x: requests_per_sec                  # metric on x-axis (not a variable)
    y: p95_ms                            # metric on y-axis
    group_by: workload                   # one labeled point per workload value
```

**`group_by` in plots.** Splits data into multiple series based on combo variable values. Accepts a single variable name or a list of variable names; multiple keys are joined with ` / ` in the legend label.

- `bar` / `stacked-bar` / `line`: without `group_by`, each `y` metric becomes one series. With `group_by`, you get one series per (metric, group-value) pair. A single string and a one-element list are equivalent.
- `scatter`: `x` and `y` are both metric names (not variables). Each unique combination of `group_by` values becomes its own labeled point. Without `group_by`, all points collapse into a single `"all"` series.

**`env_probes`.** A mapping of key → shell command run **once** at the very start of the sweep (before any rep). The trimmed stdout of each command is stored in `env.snapshot.json` under `"probes"`. A non-zero exit code or missing command stores `null` for that key — probes never abort a sweep.

```json
// env.snapshot.json (excerpt)
{
  "host": "...",
  "probes": {
    "kernel": "24.2.0",
    "redis_version": "Redis server v=7.2.3 sha=...",
    "cpu": null
  }
}
```

**Setup / teardown.** Each rep is wrapped `setup → command → teardown`. Teardown runs in a `finally` block, so it also fires on benchmark failure or `Ctrl-C`. Combo vars (`${var}`), `${_envfile}`, and `${env:VAR}` interpolate in setup/teardown commands. Steps are split with `shlex.split` and executed without a shell, so chain via multiple list entries rather than `&&`.

- Setup failure → the command is skipped, teardown still runs best-effort, and `failure_reason` is recorded as `setup[i]: …`.
- Teardown failure → the benchmark's `exit_code` and metrics are preserved, but `teardown[i]: …` is appended to `failure_reason` (so the run is counted as failed).

**Sweep-scoped setup / teardown.** `setup_per_sweep` and `teardown_per_sweep` run *outside* the per-rep loop, useful for expensive prep like seeding a database. By default each fires exactly once for the whole sweep. Set `per_sweep_var: <name>` to scope each fire to a single variable: combos are stably grouped by that variable's value, and the phases fire once per distinct value (around the reps for that group).

```yaml
setup_per_sweep:    ["seed-db.sh"]              # fires once before any rep
teardown_per_sweep: ["drop-db.sh"]
per_sweep_var:      workload                    # optional; one var only
```

- Without `per_sweep_var`: only `${env:VAR}` can be referenced; any `${combo_var}` rejected at sweep start.
- With `per_sweep_var: X`: only `${X}` and `${env:VAR}` can be referenced; the current group's value of `X` is substituted.
- `--skip-existing`: if every rep in a group is already recorded, both phases are skipped for that group.
- Setup failure: all planned reps in that group get a failure row with `failure_reason="per_sweep_setup[i]: …"`; teardown still runs best-effort. Next group proceeds.
- Teardown failure: appended to the *last* rep row in the group's `failure_reason`.
- Raw record: `raw/sweep.json` (Mode A) or `raw/sweep-{slug(value)}.json` (Mode B) — same shape as per-rep raw files.

**Raw output records.** When a raw record is written, `raw/{run_id}.json` is a JSON object with:

- `stdout`, `stderr` — the tool's own streams (always present).
- `output_file` — `{path, content}` when `output_file` is configured in the tool YAML, so the tool's stdout/stderr stay separate from the file content used for extraction.
- `setup`, `teardown` — one entry per step that ran, each with `command`, `exit_code`, `stdout`, `stderr`.

## Results directory layout

```
results/
  runs.jsonl              # append-only log, one JSON object per run
  aggregates.jsonl        # per-combo stats (n, mean, stddev, p50/95/99, CI95)
  manifest.snapshot.json  # copy of the manifest used
  tools/{name}.yaml       # copy of the tool YAML used
  env.snapshot.json       # host info (OS, CPU, git SHA)
  pretty_names.json       # merged metric display names
  raw/{run_id}.json       # structured raw record (see below); written with
                          # --keep-raw, on extract failure, on tool failure,
                          # or when setup/teardown failed
  raw/sweep[-{slug}].json # per_sweep setup/teardown records; written on
                          # --keep-raw or any per_sweep phase failure
```
