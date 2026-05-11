# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
