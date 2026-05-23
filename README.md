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
abench-speckz run out/ --tool oha.tool.yaml --ci-target rps=0.05 --min-repeat 3 --max-repeat 20
abench-speckz run out/ --tool oha.tool.yaml --filter workload=read
abench-speckz run out/ --tool oha.tool.yaml --filter-tag stress
abench-speckz run out/ --tool oha.tool.yaml --filter-exclude-tag slow
abench-speckz run out/ --tool oha.tool.yaml --skip-existing --keep-raw
abench-speckz run out/ --tool oha.tool.yaml --dry-run   # print planned commands
```

Results are written to `results/` (configurable with `--results`).

**Adaptive stopping.** `--ci-target METRIC=THRESHOLD` (repeatable) stops a combo once every named metric's 95% CI half-width is within `THRESHOLD * |mean|`, bounded by `--min-repeat` (default 3) and `--max-repeat` (default `max(--repeat, 30)`). Combos with stable metrics finish in `--min-repeat` reps; noisy combos use more, up to `--max-repeat`. Stopping decisions are scoped to one per_sweep group at a time.

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

**Comparison mode.** Pass `--baseline <dir>` to compare the results directory against a baseline directory. When active the HTML report gains:
- A **Regression Summary** section at the top — every combo×metric sorted worst-delta-first, regressed rows in red, improved in green, statistically-insignificant changes greyed out.
- **Ghost overlay series** on every bar/line chart (dashed for line, ~35% alpha for bar, labeled ` (baseline)`).
- **Δ / Δ%** columns in every per-plot data table.

A delta is flagged as `regressed` or `improved` only when it exceeds `--regression-threshold` (default `0.05` = 5%) **and** is statistically significant (CI bands disjoint). Direction follows `higher_is_better` in the tool YAML (defaults to higher-is-better when absent).

`--report` writes a self-contained HTML file with Chart.js plots. Plot definitions come from the tool YAML's `plots:` list (see below), or from a separate YAML file via `--plots`. When no plots are defined, a default per-metric bar chart is rendered.

The report includes a toolbar with a **dark mode toggle** and **six built-in color themes** (Tableau, Vivid, Pastel, Paired, Muted, Accessible). The selected theme and dark/light preference are persisted in `localStorage`, so the report remembers your choice across sessions. Switching a theme or toggling dark mode instantly updates all charts and legend swatches without a page reload.

### `browse` — interactive TUI browser

```sh
abench-speckz browse results/
```

Launches a terminal UI for exploring a results directory. The left pane shows a tree of combo configurations grouped by variable; the right pane shows per-metric statistics and individual run rows for the selected combo. Requires the optional `tui` extra:

```sh
pip install 'abench-speckz[tui]'
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

Variable names starting with `_` are reserved and will be rejected at load time. Built-in synthetic variables:

| Variable | Available in | Description |
|---|---|---|
| `${_envfile}` | `command`, `setup`, `teardown`, `post_run`, `monitor`, `output_file`, `setup_per_sweep`, `teardown_per_sweep` | Absolute path to the current combo's env file. In per_sweep phases, resolves to the first entry's env file in the group. |
| `${_run_id}` | `command`, `setup`, `teardown`, `post_run`, `monitor`, `setup_per_sweep`, `teardown_per_sweep` | UUID for this rep — same value written to `runs.jsonl`. In per_sweep phases, one UUID is generated per group and shared between `setup_per_sweep` and `teardown_per_sweep`. |
| `${_results_dir}` | `command`, `setup`, `teardown`, `post_run`, `output_file`, `setup_per_sweep`, `teardown_per_sweep`, `combo_probes`, `monitor` | Absolute path to the results directory. |
| `${_exit_code}` | `post_run` | Benchmark exit code |
| `${_started_at}` | `post_run` | ISO timestamp when the benchmark started |
| `${_finished_at}` | `post_run` | ISO timestamp when the benchmark finished |
| `${_duration_ms}` | `post_run` | Wall-clock duration in milliseconds |
| `${_started_at_ms}` | `grafana_links` | Run start as Unix milliseconds (Grafana time-range format) |
| `${_finished_at_ms}` | `grafana_links` | Run end as Unix milliseconds (Grafana time-range format) |

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
var_values:                     # friendly labels for combo variable values in legends / charts
  backend:
    postgres: "PostgreSQL 16"
    mysql:    "MySQL 8.0"
  workload:
    read:  "Read-heavy"
    write: "Write-heavy"
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

# optional: run once per unique config hash (before the first rep for that combo);
# commands may reference combo vars. Results stored in combo_probes.json and
# embedded in every runs.jsonl row under "combo_probes".
combo_probes:
  effective_maxmemory: "redis-cli -p ${PORT} CONFIG GET maxmemory"
  row_count:           "psql ${DSN} -tAc 'SELECT count(*) FROM events'"

# optional: shell steps run around every rep (warmup and measured)
setup:
  - "docker compose up -d redis"
  - "sleep 1"
teardown:
  - "docker compose down -v"
setup_timeout_seconds: 120   # per-step timeout for setup/teardown/post_run (default 120)

# optional: shell steps run after teardown, always (even on benchmark failure)
# receives run-result vars: ${_run_id}, ${_exit_code}, ${_started_at}, ${_finished_at}, ${_duration_ms}
post_run:
  - "prom-query.sh ${_started_at} ${_finished_at} ${_run_id}"

# optional: Grafana dashboard links rendered in stats --report.
# URL templates support combo vars, ${env:VAR}, ${_run_id}, ${_started_at},
# ${_finished_at}, ${_started_at_ms}, ${_finished_at_ms}.
grafana_links:
  - label: "Overview"
    url: "https://grafana.example.com/d/abc?from=${_started_at_ms}&to=${_finished_at_ms}&var-workload=${workload}"
  - label: "CPU"
    url: "https://grafana.example.com/d/cpu?from=${_started_at_ms}&to=${_finished_at_ms}"

# optional: background processes. Each entry is a plain string (command template)
# or an object with 'command' and an optional 'span' field.
# span: per_rep (default) — live from after setup until benchmark finishes
# span: per_group         — live from after setup_per_sweep until before teardown_per_sweep
# span: per_sweep         — live from sweep init until all groups finish
# Each process receives SIGTERM when its span ends; SIGKILL follows after 5 s.
monitor:
  - "python collect-metrics.py --run-id ${_run_id}"   # span: per_rep (default, string form)
  - command: "group_monitor.sh ${workload}"
    span: per_group    # spans all reps in the group; can reference per_sweep_var variables
  - command: "top -b > sweep.log"
    span: per_sweep    # spans the entire sweep; only ${_run_id} and ${env:VAR} available

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
    smooth: true                          # spline interpolation instead of straight segments
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

**`group_by` in plots.** Splits data into multiple series based on combo variable values. Accepts a single variable name or a list; multiple keys are joined with ` / ` in the legend label.

- `bar` / `stacked-bar` / `line`: without `group_by`, each `y` metric becomes one series. With `group_by`, you get one series per (metric, group-value) pair.
- `scatter`: `x` and `y` are both metric names (not variables). Each unique combination of `group_by` values becomes its own labeled point. Without `group_by`, all points collapse into a single `"all"` series.

Variable values in chart labels, the legend table, the fixed-vars annotation, and x-axis tick labels are mapped through `var_values` (defined in the tool YAML — see above) when a matching entry exists.

**Negation in `group_by`.** Prefix a variable name with `!` to mean "all variables *except* this one". Useful when you have many variables and don't want to list them all:

```yaml
- id: rps_all_configs
  type: line
  x: concurrency
  y: rps
  group_by: "!concurrency"       # one line per every other variable combination

- id: rps_except_region
  type: line
  x: concurrency
  y: rps
  group_by: ["!concurrency", "!region"]   # exclude multiple vars; keep the rest
```

Negation is resolved at report time against the actual variable names in `aggregates.jsonl`. Unknown excluded names are silently ignored.

**Axis expressions.** The `x` and `y` fields accept arithmetic expressions as well as plain names. Supported operators are `+`, `-`, `*`, `/`, `**`, `//`, and `%`; parentheses are allowed; function calls are not.

Use an `x` expression to derive an axis from multiple variables:

```yaml
- id: total_conns
  type: line
  x: "workers * connections_per_worker"   # computed x-axis label
  y: requests_per_sec
  group_by: backend
```

Use a `y` expression for unit conversion or to normalize a metric by a variable:

```yaml
- id: latency_ms
  type: bar
  x: concurrency
  y: "latency_us / 1000"          # convert µs → ms; expression becomes the series label

- id: rps_per_worker
  type: line
  x: concurrency
  y: "rps / workers"              # normalize throughput by worker count
  group_by: backend
```

For `y` expressions every identifier is classified at report time: names that match a combo variable are treated as variable references (their per-row value is substituted); all other names are expected to be metric names (their aggregated `mean`/`p50`/… values are substituted). Exactly one metric name per expression is required. Multiple metrics in one expression (e.g. `rps / p95_ms`) are not supported.

Expression syntax is validated when the YAML is loaded, so malformed expressions are caught early.

**Plot titles.** The optional `title` field is used as an `<h3>` heading inside each plot block when sections are present, so multiple plots within one section are individually labelled. Without sections it becomes the section heading instead.

**`normalize` — relative speedup charts.** Pins one series to 1.0× and expresses all others relative to it. Requires `group_by`; `against` must be one of the `group_by` variables; `reference` is the value pinned to 1.0×. The y-axis becomes "× speedup" and a dashed reference line is drawn at 1.0.

```yaml
- id: speedup
  type: bar
  x: workload
  y: rps
  group_by: backend
  normalize:
    against: backend       # must be a group_by variable
    reference: postgres    # this series is pinned to 1.0×
```

**`error_bars` — whiskers on bar/line charts.** Set to `ci95` to draw 95%-CI whiskers or `stddev` to draw ±stddev whiskers on each data point. Only supported on `bar` and `line` plot types.

```yaml
- id: rps_with_ci
  type: bar
  x: workload
  y: rps
  error_bars: ci95     # or: stddev
```

**`data_table` — suppress the collapsible data table.** Each plot renders a "Data" table below the chart by default. Set `data_table: false` to hide it.

```yaml
- id: rps_by_workload
  type: bar
  x: workload
  y: rps
  data_table: false
```

**Static-variable block.** When a `group_by` variable has the same value across every series in a plot, abench-speckz collapses it out of the legend and shows it in a `Fixed: key = value` line above the chart. Set `fixed_vars: false` on a plot to suppress this block entirely:

```yaml
- id: rps_by_workload
  type: bar
  x: workload
  y: rps
  group_by: [backend, region]
  fixed_vars: false   # hide the "Fixed: region = us-east-1" annotation
```

**`series_label` — compact one-line legend.** By default, plots with `group_by` render a multi-column colour-coded table (one row per series, one column per variable). Set `series_label` to a `${var}` template to replace the table with Chart.js's compact built-in legend — one coloured entry per series, formatted by the template. `var_values` mapping is applied automatically to substituted values.

```yaml
# Default: table legend with columns for each group_by variable
- id: rps_by_backend_table
  type: line
  x: concurrency
  y: rps
  group_by: backend

# Compact: one-line legend using a template
- id: rps_by_backend_compact
  type: line
  x: concurrency
  y: rps
  group_by: backend
  series_label: "${backend}"          # e.g. "PostgreSQL 16" with var_values

# Multi-variable template
- id: rps_workload_backend
  type: line
  x: concurrency
  y: rps
  group_by: [workload, backend]
  series_label: "${workload} / ${backend}"
```

**Report layout.** By default, every plot in `plots:` is rendered automatically in the order it is defined. To control layout, interleave prose, and publish only a subset of your plots, add a `sections:` list alongside `plots:`.

```yaml
report_title: "Database benchmark — Q3 2025"
report_description: |
  Throughput and latency sweeps across three workload types and four concurrency
  levels. All runs used PostgreSQL 16 on a c5.4xlarge instance.

plots:
  - id: rps_by_workload
    type: bar
    title: "Throughput by workload"
    x: workload
    y: requests_per_sec

  - id: latency_breakdown
    type: stacked-bar
    title: "Latency percentile breakdown"
    x: workload
    y: [p50_ms, p95_ms, p99_ms]

  - id: rps_vs_concurrency
    type: line
    title: "Throughput scaling"
    x: concurrency
    y: requests_per_sec
    group_by: workload

  - id: throughput_vs_latency   # defined but not referenced below → omitted from report
    type: scatter
    x: requests_per_sec
    y: p95_ms
    group_by: workload

sections:
  - title: "Throughput"
    description: |
      Requests per second across all three workload types.
    blocks:
      - plot_id: rps_by_workload
      - plot_id: rps_vs_concurrency
      - text: |
          **Key finding:** write throughput degrades sharply above 16 connections
          due to lock contention in the storage layer.

  - title: "Latency"
    description: Percentile breakdown of response time by workload.
    blocks:
      - plot_id: latency_breakdown
      - html: <p class="note">Numbers above exclude connection setup time.</p>
      - include_html: methodology-table.html   # path relative to the YAML file
```

**`theme` — report visual theme.** An optional top-level mapping controlling the visual theme of the HTML report. All sub-fields are optional.

| Sub-field | Values | Default | Description |
|---|---|---|---|
| `palette` | `tableau`, `vivid`, `pastel`, `paired`, `muted`, `accessible` | `tableau` | Color palette for all chart series. |
| `dark_mode` | `true`, `false`, `auto` | `auto` | `auto` follows OS preference; `true` always dark; `false` always light. |
| `switcher` | `true`, `false` | `false` | Show an interactive palette picker and dark-mode toggle in the report header. When shown, `palette` and `dark_mode` serve as initial defaults, overridable by the reader via `localStorage`. |

```yaml
theme:
  palette: vivid
  dark_mode: auto
  switcher: true
```

When `sections:` is present:
- Plots are **not** auto-rendered — only plots referenced by a `plot_id` block appear in the report.
- `report_description` is rendered as Markdown below the `report_title` heading.
- The same `plot_id` can appear in multiple blocks; each reference renders an independent canvas.
- Plots not referenced by any block are silently omitted, so you can maintain a plot library and selectively publish a subset.

Each block has exactly one key:

| Key | Value | Renders as |
|---|---|---|
| `plot_id` | id of a plot in the top-level `plots:` list | Chart.js chart |
| `text` | Markdown string | Formatted prose |
| `html` | Raw HTML string | Inlined verbatim |
| `include_html` | File path relative to the YAML file | Contents of that file, inlined verbatim |
| `kpi` | List of scorecard card definitions (see below) | Grid of big-number cards |
| `headline` | Headline definition (see below) | Auto-generated comparison sentence |

**`kpi` blocks — big-number scorecards.** Each card specifies a metric value to highlight with optional delta comparison. `stat` controls how the value is reduced across matching combos: `max`, `min`, or `mean` (default). `where` is a dict of variable filters. `compare.where` targets a second combo to show Δ%.

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
    - label: "p95 latency"
      metric: p95_ms
      stat: min
      where: {workload: read}
      unit: ms
```

**`headline` blocks — auto-generated comparison sentences.** Produces a deterministic sentence comparing the best and worst values of a metric across a variable. Sort direction follows `higher_is_better` from the tool YAML. Template tokens: `{best}`, `{worst}`, `{best_value}`, `{worst_value}`, `{ratio}`, `{delta_pct}`.

```yaml
- headline:
    metric: rps
    compare: backend
    where: {workload: heavy}
    template: "{best} delivers {ratio} the throughput of {worst}"
```

**Variable interpolation in text fields.** `report_title`, `report_description`, section `title`, section `description`, `text` blocks, `html` blocks, and plot `title` all support `${env:VAR}` (current environment variable) and `${var}` (combo variable that has exactly one unique value across all results). Multi-valued variables are not available — referencing one raises an error. Use `$$` to write a literal `$` when you need `${...}` to appear verbatim in display text (e.g. `$$${backend}` renders as `${backend}`). Note: `series_label` is a per-series render-time template and is intentionally excluded from this doc-level interpolation.

```yaml
report_title: "Results for ${env:BUILD_VERSION}"
report_description: "Backend: **${backend}**, commit ${env:GIT_SHA}"

sections:
  - title: "Throughput on ${backend}"
    blocks:
      - text: "Suite run: ${env:SUITE_NAME}"
      - plot_id: rps_chart

plots:
  - id: rps_chart
    type: bar
    x: concurrency
    y: rps
    title: "RPS (${env:BACKEND_LABEL})"
```

**Using a separate plots file.** Pass `--plots plots.yaml` to supply plot definitions from a standalone file instead of the tool YAML. The standalone file uses the same format — a mapping with `plots:`, `sections:`, `report_title:`, `report_description:` — or just a bare list of plot entries for the minimal case:

```sh
# Plots come from the tool YAML (default)
abench-speckz stats results/ --report report.html

# Override with a standalone plots file
abench-speckz stats results/ --report report.html --plots editorial.yaml
```

A bare-list standalone file (no sections, no title):

```yaml
# editorial.yaml — just a list
- id: rps_by_workload
  type: bar
  x: workload
  y: requests_per_sec
- id: rps_vs_concurrency
  type: line
  x: concurrency
  y: requests_per_sec
  group_by: workload
```

**`env_probes`.** A mapping of key → shell command run **once** at the very start of the sweep (before any rep). The trimmed stdout of each command is stored in `env.snapshot.json` under `"probes"`. A non-zero exit code or missing command stores `null` for that key — probes never abort a sweep.

**`combo_probes`.** A mapping of key → command template run **once per unique config hash**, before the first rep for that combo (after per-sweep setup). Commands interpolate combo vars (`${var}`, `${_envfile}`, `${env:VAR}`). Results are stored in two places: `combo_probes.json` (keyed by config hash) and embedded in every `runs.jsonl` row under `"combo_probes"`. Non-zero exit, missing command, timeout, or interpolation error stores `null` — probes never abort a sweep. Useful for capturing system or service state that varies per combo (e.g. effective DB config after per-sweep setup seeded a different dataset, kernel tuning parameters set per workload).

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

**`grafana_links`.** A list of `{label, url}` entries. After each benchmark run completes, the URL templates are resolved using combo vars, environment variables, and run-result synthetic vars. The resolved links are stored in `runs.jsonl` under `"grafana_links"` and rendered as styled clickable buttons in `stats --report` inside each run's collapsible block.

Use `${_started_at_ms}` and `${_finished_at_ms}` to embed Unix-millisecond timestamps directly into Grafana's `from=` / `to=` query parameters:

```yaml
grafana_links:
  - label: "Overview"
    url: "https://grafana.example.com/d/abc123?from=${_started_at_ms}&to=${_finished_at_ms}&var-workload=${workload}"
  - label: "CPU"
    url: "https://grafana.example.com/d/def456?from=${_started_at_ms}&to=${_finished_at_ms}"
```

All combo vars, `${env:VAR}`, `${_run_id}`, `${_started_at}` (ISO), and `${_finished_at}` (ISO) are also available. An unresolvable URL (referencing an unknown variable) is stored as `null` and silently omitted from the report — it never aborts a run. When runs have grafana_links but no per-run HTML report files, the report section is titled "Per-run links"; when both are present the title is "Per-run reports".

**Setup / teardown / post_run / monitor.** The full per-rep lifecycle is:

```
setup  →  [monitor start]  →  command  →  [monitor stop]  →  teardown  →  post_run
```

Teardown runs in a `finally` block, so it fires even on benchmark failure or `Ctrl-C`. Combo vars (`${var}`), `${_envfile}`, `${_run_id}`, and `${env:VAR}` interpolate in all phases. Steps are split with `shlex.split` and executed without a shell, so chain via multiple list entries rather than `&&`.

- Setup failure → the command is skipped, monitor is not started, teardown still runs best-effort, `post_run` is skipped, and `failure_reason` is recorded as `setup[i]: …`.
- Teardown failure → the benchmark's `exit_code` and metrics are preserved, but `teardown[i]: …` is appended to `failure_reason`.
- `post_run` → runs after teardown completes, always — including when the benchmark exits non-zero. In addition to combo vars, it receives `${_exit_code}`, `${_started_at}`, `${_finished_at}`, and `${_duration_ms}`. Useful for collecting time-windowed metrics from external systems (Prometheus, InfluxDB, etc.) keyed to the exact run via `${_run_id}`. `post_run` failure is appended to `failure_reason` but does not suppress the benchmark result or its metrics.
- `monitor` → each entry is a plain string or an object with `command` and an optional `span` field. The `span` controls the process lifetime and which variables are available:

  | `span` | Lifetime | Variables available |
  |---|---|---|
  | `per_rep` (default) | After `setup`, until benchmark finishes | All combo vars + `${_run_id}`, `${_envfile}`, `${env:VAR}` |
  | `per_group` | After `setup_per_sweep`, until before `teardown_per_sweep` | `per_sweep_var` values + `${_run_id}`, `${_envfile}`, `${env:VAR}` |
  | `per_sweep` | After sweep init, until all groups finish | `${_run_id}` (sweep-level UUID) + `${env:VAR}` |

  Each process receives SIGTERM when its span ends; SIGKILL follows after 5 seconds. A monitor that fails to start is recorded but never aborts the run. Raw records (`monitor_start`, `monitor_stop`) are written to different files by span:
  - `per_rep` → `raw/{run_id}.json` (under `--keep-raw` or when any monitor fails to start)
  - `per_group` → `raw/sweep.json` / `raw/sweep-{slug}.json` alongside per_sweep setup/teardown records
  - `per_sweep` → `raw/sweep_monitors.json`

**Sweep-scoped setup / teardown.** `setup_per_sweep` and `teardown_per_sweep` run *outside* the per-rep loop, useful for expensive prep like seeding a database. By default each fires exactly once for the whole sweep. Set `per_sweep_var` to group combos and fire the phases once per distinct group.

`per_sweep_var` accepts three forms:

```yaml
# Single variable — one group per distinct value of 'backend'
per_sweep_var: backend

# List of variables — one group per unique combination of (backend, workload);
# 'concurrency' varies freely within each group
per_sweep_var: [backend, workload]

# Negation — group by all variables EXCEPT 'concurrency'
per_sweep_var: "!concurrency"

# Negation in a list — explicitly include 'backend', exclude 'concurrency'
per_sweep_var: [backend, "!concurrency"]
```

Negation entries (`!var`) are expanded against the `variables` keys in the manifest (static values are excluded), mirroring the `group_by` negation syntax used in plots. The group slug in raw file names joins all variable values (e.g. `postgres-read_heavy`).

```yaml
setup_per_sweep:
  - "seed-db.sh ${backend} --mode ${workload}"
teardown_per_sweep:
  - "drop-db.sh ${backend}"
per_sweep_var: [backend, workload]   # concurrency varies freely within each group
```

- Without `per_sweep_var`: only `${_envfile}`, `${_run_id}`, and `${env:VAR}` can be referenced; any other `${combo_var}` is rejected at sweep start.
- With `per_sweep_var`: only the listed variables, `${_envfile}`, `${_run_id}`, and `${env:VAR}` can be referenced in per_sweep steps; the current group's values are substituted.
- `--skip-existing`: if every rep in a group is already recorded, both phases are skipped for that group.
- Setup failure: all planned reps in that group get a failure row with `failure_reason="per_sweep_setup[i]: …"`; teardown still runs best-effort. Next group proceeds.
- Teardown failure: appended to the *last* rep row in the group's `failure_reason`.
- Raw record: `raw/sweep.json` (no grouping) or `raw/sweep-{slug}.json` (grouped, slug joins all group variable values) — same shape as per-rep raw files.

**`scopes` — nested multi-level per-sweep grouping.** `scopes` provides nested multi-level per-sweep setup/teardown. Each entry has `vars` (required — same string/list/negation syntax as `per_sweep_var`) plus optional `setup` and `teardown` step lists. Scopes are ordered outermost to innermost; setup runs top-down before reps begin, teardown runs bottom-up after reps finish. `scopes` cannot be combined with `per_sweep_var`, `setup_per_sweep`, or `teardown_per_sweep`.

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
      - "load_dataset.sh ${backend} ${workload}"
    teardown:
      - "cleanup_dataset.sh ${workload}"
```

Execution order (e.g. backends `pg`/`mysql`, workloads `read`/`write`):
1. `deploy.sh pg` → `load_dataset.sh pg read` → all reps → `cleanup_dataset.sh read`
2. `load_dataset.sh pg write` → all reps → `cleanup_dataset.sh write`
3. `stop.sh pg`
4. `deploy.sh mysql` → … → `stop.sh mysql`

Innermost teardown failures are folded into the last rep's `failure_reason`. Outer teardown failures are written to `raw/scope{i}-{slug}.json`. A scope entry with no `setup` or `teardown` is a pure grouping scope (useful as a bracket for `per_group` monitors).

**Raw output records.** When a raw record is written, `raw/{run_id}.json` is a JSON object with:

- `stdout`, `stderr` — the tool's own streams (always present).
- `output_file` — `{path, content}` when `output_file` is configured in the tool YAML, so the tool's stdout/stderr stay separate from the file content used for extraction.
- `setup`, `teardown`, `post_run` — one entry per step that ran, each with `command`, `exit_code`, `stdout`, `stderr`.
- `monitor_start` — one entry per `per_rep` monitor command with `command`, `pid` (or `error` if it failed to start).
- `monitor_stop` — one entry per `per_rep` monitor process with `pid`, `exit_code`, `stdout`, `stderr`.

`per_group` monitor records appear in `raw/sweep[-{slug}].json`; `per_sweep` monitor records appear in `raw/sweep_monitors.json`.

## Results directory layout

```
results/
  runs.jsonl              # append-only log, one JSON object per run
  aggregates.jsonl        # per-combo stats (n, mean, stddev, p50/95/99, CI95)
  manifest.snapshot.json  # copy of the manifest used
  tools/{name}.yaml       # copy of the tool YAML used
  env.snapshot.json       # host info (OS, CPU, git SHA) + env_probes results under "probes"
  combo_probes.json       # combo_probes results keyed by config hash
  pretty_names.json       # merged metric display names
  raw/{run_id}.json       # structured raw record (see below); written with
                          # --keep-raw, on extract failure, on tool failure,
                          # or when setup/teardown/post_run failed
  raw/sweep[-{slug}].json # per_sweep setup/teardown records; written on
                          # --keep-raw or any per_sweep phase failure
```
