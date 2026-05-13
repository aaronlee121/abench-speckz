# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-05-13

### Added

- `post_run` tool YAML field — a list of shell steps run after `teardown` completes, always (including on benchmark failure). Receives result-scoped synthetic variables: `${_exit_code}`, `${_started_at}`, `${_finished_at}`, `${_duration_ms}`. Intended for collecting time-windowed metrics from external systems (Prometheus, InfluxDB, etc.) keyed to the exact run. Failure is folded into `failure_reason` but does not suppress the benchmark result or its metrics. Step records are stored in `raw/{run_id}.json` under `"post_run"`.
- `combo_probes` tool YAML field — a mapping of key → command template run **once per unique config hash** (before the first rep for that combo, after per-sweep setup). Commands interpolate combo vars (`${var}`, `${_envfile}`, `${env:VAR}`). Trimmed stdout is the value; non-zero exit, missing command, timeout, or interpolation error stores `null`. Results are written to `combo_probes.json` (keyed by config hash) and embedded in every `runs.jsonl` row under `"combo_probes"`. Useful for capturing system or service state that varies per combo (e.g. effective DB config, dataset row counts after per-sweep seeding).
- `${_run_id}` built-in synthetic variable — the UUID for the current rep, matching the `run_id` field written to `runs.jsonl`. Available in `command`, `setup`, `teardown`, and `post_run`. Run ID is now pre-generated before setup, so it is available throughout the full rep lifecycle.

### Fixed

- `raw/{run_id}.json` now correctly spills large `post_run` step stdout/stderr to sidecar files (the `_spill_phases` helper was not covering the `post_run` key).

## [0.3.0] - 2026-05-12

### Added

- `env_probes` tool YAML field — a mapping of key → shell command run **once** at sweep start (before any rep). Trimmed stdout is stored in `env.snapshot.json` under `"probes"`. Non-zero exit or missing command stores `null`; probes never abort a sweep.
- `${_envfile}` built-in variable — resolves to the absolute path of the current combo's env file. Available in tool `command`, `setup`, `teardown`, and `output_file` fields.
- `group_by` in plot definitions now accepts a **list** of variable names in addition to a single string. Multiple keys are joined with ` / ` in the chart legend label (e.g. `group_by: [workload, backend]`).
- Negation syntax for `group_by`: prefix a variable name with `!` to mean "all variables except this one" (e.g. `group_by: "!concurrency"`). Multiple negations are supported. Resolved at report time against the variable names present in `aggregates.jsonl`.

### Changed

- Variable names starting with `_` are now rejected at spec load time (`static`, `variables`, and `when[].set` keys). The `_` namespace is reserved for built-in synthetic variables such as `${_envfile}`.

## [0.2.1] - 2026-05-11

### Changed

- Raw output fields (`stdout`, `stderr`, `output_file.content`, per-step stdout/stderr) exceeding 8 KiB are now spilled to adjacent sidecar files (`raw/{run_id}.{field}`) and replaced with a `{"$ref": "raw/…"}` pointer in the raw JSON. This keeps `raw/*.json` small for metadata queries. Use the new `store.load_raw_run()` helper to read a record with all pointers resolved.

## [0.2.0] - 2026-05-11

### Added

- `per_sweep_var`, `setup_per_sweep`, and `teardown_per_sweep` tool YAML fields — expensive setup/teardown can now run outside the per-rep loop. Without `per_sweep_var` the phases fire once for the whole sweep; with it, combos are grouped by that variable's value and phases fire once per distinct value. Setup failures record failure rows for the affected group; teardown failures fold into the group's last rep row.
- Raw run records (`raw/{run_id}.json`) are now structured JSON capturing the tool's own `stdout`/`stderr`, the `output_file` path and content separately when configured, and per-step `command`/`exit_code`/`stdout`/`stderr` for `setup` and `teardown`. Raw records are also written when setup or teardown fail. Per-sweep phase output lands in `raw/sweep.json` or `raw/sweep-{slug}.json`.

## [0.1.1] - 2026-05-10

### Changed

- Expanded README and `CLAUDE.md` to document `output_file`, `output_format`, `plots`, `setup`/`teardown`, `--exclude-tag`, `--report`/`--plots`, and `rebuild-aggregates`.

## [0.1.0] - 2026-05-10

Initial public release.

### Added

- `abench-speckz gen` — expand a YAML spec into Docker env-file combinations with a `manifest.json` index.
- `abench-speckz run` — execute a tool YAML's command across every combination, with `--repeat` / `--warmup` / `--filter` / `--skip-existing` / `--keep-raw`.
- Per-rep `setup` / `teardown` shell steps in the tool YAML, with `setup_timeout_seconds` (default 120). Teardown always runs, including on benchmark failure and `KeyboardInterrupt`.
- `abench-speckz stats` — DuckDB-backed aggregate queries with `--group-by`, `--where`, `--filter-tag`, `--metric`, `--format`, `--pretty`, `--from-raw`.
- `abench-speckz stats --report` — self-contained Chart.js HTML report with declarative plot definitions (`bar`, `stacked-bar`, `line`, `scatter`).
- `abench-speckz rebuild-aggregates` — rebuild `aggregates.jsonl` from `runs.jsonl`.
- YAML spec features: variables (cartesian product), `static`, `when` overlays with tag accumulation, `exclude` rules, `${var}` / `${env:VAR}` interpolation with `$$` literal escape, profiles, universal `tags`.
- Metric extraction via JSONPath `capture` (with `[]` list-collection suffix), JSONL output via `output_format: jsonl`, file-based output via `output_file`, or pluggable Python parsers via `parser: "module:func"`.
- Atomic, file-locked results directory with `runs.jsonl`, `aggregates.jsonl`, env/manifest/tool snapshots, and optional `raw/` stdout retention.

### Security

- All user-supplied identifier inputs (`--group-by`, `--where` keys) are allowlist-validated before reaching DuckDB SQL builders to prevent JSON-key literal injection.
