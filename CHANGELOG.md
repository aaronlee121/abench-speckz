# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.14.0] - 2026-05-26

### Added

- **`${_started_at_s}` / `${_finished_at_s}` synthetic variables** — Unix-seconds variants of the timestamp vars, compatible with the Prometheus `query_range` API (`start=` / `end=` parameters). Available in `post_run` steps and `grafana_links` URL templates alongside the existing millisecond (`_ms`) and ISO (`_started_at`) variants.

### Fixed

- **Normalized chart tooltips now show the original metric value.** Hovering a bar or line point on a `normalize:`-enabled chart previously only showed the `×` multiplier (e.g. `"Backend A: 2.50×"`). The tooltip now includes the pre-division value: `"Backend A: 2.50× (orig: 1235)"`.
- **`_started_at_ms` / `_finished_at_ms` wired into `post_run`** — these were previously only available in `grafana_links` URL templates; they are now also injected into the `post_run` variable context.

### Changed

- README: documented `compare.format` option for KPI cards and corrected the synthetic variables availability table to reflect `post_run` access for the `_ms` and `_s` timestamp variants.

## [0.13.0] - 2026-05-26

### Added

- **`compare.format` on KPI cards** — new optional field on the `compare` block controls how the delta is displayed: `pct` (default, existing `↑ 150.0%` behaviour), `multiplier` (raw ratio — `↑ 2.50×`), or `both` (percentage + ratio — `↑ 150.0% (2.50×)`).
- **Per-run reports filter** — a row of per-variable `<select>` dropdowns appears above the Per-run reports / Per-run links section. Selecting a value narrows the visible run blocks in real time (multiple dropdowns combine with AND logic); a Reset button appears while any filter is active; a `N / M shown` count badge updates live. Variables with only one distinct value are excluded from the bar. No filter is active by default.

### Fixed

- Fixed `data-run-vars` HTML attribute being truncated by the HTML parser: the attribute value was generated with embedded `"` characters that closed the double-quoted attribute early, causing `JSON.parse` in the filter script to silently fail. The value is now HTML-escaped so `"` becomes `&quot;`.

## [0.12.2] - 2026-05-25

### Fixed

- Fixed `--where` / `--filter-tag` / `--filter-exclude-tag` being silently ignored in the Regression Summary section and in per-plot baseline overlay series when `--baseline` is used. Both `_prepend_regression_summary` and the per-plot `baseline_rows` query now forward the CLI filter parameters.

## [0.12.1] - 2026-05-24

### Fixed

- Fixed Ctrl+C not killing the benchmark subprocess. Subprocesses are launched with `start_new_session=True` so the terminal's SIGINT never reached them; the Python parent caught `KeyboardInterrupt` but `Popen.__exit__` then hung in `proc.wait()` indefinitely. All four subprocess execution paths (non-streaming and streaming in `executor.py` and `_phases.py`) now explicitly kill the process group via `os.killpg` on `KeyboardInterrupt` before re-raising. Per-rep and per-sweep teardown still run after the interrupt.
- Fixed inlined run report iframes not expanding to fill available height (set to 90vh).

## [0.12.0] - 2026-05-24

### Added

- **Conditional step syntax** — steps in `setup`, `teardown`, `post_run`, `setup_per_sweep`, `teardown_per_sweep`, and `scopes` now accept a `{run, when}` dict form alongside plain strings. The `when` dict is a variable equality filter; steps are silently skipped when any key-value pair does not match the current combo vars. Numeric `when` values are coerced to strings for comparison. Plain-string steps are unchanged.
- **Auto-axis labels** — bar, line, and scatter plots now automatically derive x/y axis titles from `pretty_names` and `units` in the tool YAML when no explicit `x_title` / `y_title` is set.
- **Variables table in per-run report blocks** — expanding a run's `<details>` block in the HTML report now shows a collapsible **Variables** table listing all combo key/value pairs, respecting `var_values` display labels. Nested sub-blocks have a left-border accent to indicate hierarchy.
- **`var_values` labels in `${var}` text interpolation** — `${var}` references in `report_title`, `report_description`, section `title`/`description`, and `text` blocks now expand to the `var_values` display label rather than the raw value.
- **Setup/teardown/post\_run/command output streamed to log files** — stdout and stderr from these phases are now written to sidecar `.out.log` / `.err.log` files alongside the sweep, making `tail -f` and `abench-speckz tail` output complete.
- **Interpolated command saved in raw JSON** — `raw/{run_id}.json` now includes the fully-interpolated `command` string, making it easy to reproduce or debug individual runs.

### Fixed

- Fixed embedded flamegraph iframes never loading: the `ontoggle` attribute was double-quoted while `json.dumps` also produces double-quoted strings inside, causing the HTML parser to terminate the attribute early and the `loadRunReport` handler to never fire. Attribute now uses single-quote delimiters.
- Fixed `grafana_links` not appearing in the report when there are no per-run HTML report files.
- Fixed `abench-speckz tail` not following monitor log files when started mid-sweep.

### Changed

- **Browse TUI** — runs table is now transposed (runs as columns, metric fields as rows) for easier comparison across reps; panel widths are dynamically rebalanced based on content length.

## [0.11.0] - 2026-05-23

### Added

- **Adaptive `--ci-target` stopping** — `abench-speckz run --ci-target METRIC=THRESHOLD` (repeatable) stops a combo as soon as every named metric's 95% CI half-width is within `THRESHOLD * |mean|`, bounded by new `--min-repeat` (default 3) and `--max-repeat` (default `max(--repeat, 30)`) flags. Stable combos finish in the minimum; noisy combos use more, up to the cap. Stopping decisions are scoped to one per_sweep / scope group at a time.
- **`Scheduler` protocol** (`src/abench_speckz/scheduling.py`) — `FixedScheduler` (preserves the historical pre-flattened plan) and `CITargetScheduler` (the new stopping rule) share a `next(state) -> WorkItem | None` interface. The runner now drives every rep through a scheduler; future benchmark-specific planner plugins will plug into the same seam.

### Changed

- The `run` command now reports `sweep: up to N rep(s) across …` instead of a fixed N — `N` is the per-combo upper bound (`warmup + repeat` for Fixed, `warmup + max_repeat` for CI-target).

## [0.10.0] - 2026-05-22

### Added

- **Baseline comparison mode** — `abench-speckz stats --baseline <dir>` compares a results directory against a baseline. The HTML report gains a **Regression Summary** section (all combo×metric deltas, sorted worst-first, with colour coding), ghost overlay series on every bar/line chart, and Δ/Δ% columns in per-plot data tables. A delta is flagged only when it exceeds `--regression-threshold` (default 5%) **and** is statistically significant (CI bands disjoint). Direction follows `higher_is_better` from the tool YAML.
- **`error_bars` plot field** — set to `ci95` or `stddev` on `bar` or `line` plots to draw whisker error bars on each data point using CI or ±stddev from aggregated stats.

### Fixed

- Fixed terminal clear on `browse` exit to use `os.system("clear")` for portable behaviour across platforms.

## [0.9.0] - 2026-05-22

### Added

- **Progress output during sweep startup** — a startup banner (`sweep: N rep(s) across M combo(s)`) is printed immediately after planning. An env-probe notice prints before `env_probes` run. Per-sweep and scope setup phases print a `setup [vars] (N step(s))...` line before executing. A `run` line is emitted per rep before the benchmark command starts, so the terminal is never silent during long-running benchmarks or slow setup steps.
- **`abench-speckz tail` monitor log streaming** — monitor log content (`.out.log` / `.err.log` sidecar files) is now streamed inline in `tail` output as new lines appear, interleaved with run result lines.
- **Separate `.out.log` / `.err.log` sidecar files** — monitor stdout and stderr are now written to distinct `<prefix>.out.log` and `<prefix>.err.log` files instead of a single combined log, making it easier to grep or `tail -f` each stream independently.
- **PTY relay for monitor log files** — monitor processes are run under a PTY relay so `tail -f` on the sidecar files works correctly (line-buffered output rather than fully-buffered).
- **Browse TUI tree view** — the combo browser now uses a hierarchical tree view instead of a flat table, grouping combos by varying variable values.
- **`monitor_start`/`monitor_stop` stdout/stderr sidecar files** — stdout and stderr from monitor start/stop phases are spilled to sidecar files alongside the structured JSON records.
- **Plain-text stdout/stderr sidecar files** — `monitor_start` and `monitor_stop` output is always written to plain text sidecar files for easy inspection.

## [0.8.0] - 2026-05-22

### Added

- **`abench-speckz browse`** — interactive Textual TUI browser for a results directory. Three-pane layout: combos (left), per-combo metric stats with n / mean / p50 / p95 / p99 / stddev (middle), individual runs with per-rep metric values (right). Navigate panes with `Tab` / `1` / `2` / `3`; reload from disk with `r`; rebuild aggregates with `R`. Requires `pip install 'abench-speckz[tui]'` (new `[tui]` optional extra; already included in `[dev]`).
- **`abench-speckz tail`** — live-follow a running sweep: prints each completed run as it lands in `runs.jsonl`, showing exit code, duration, and extracted metric values.
- **`log_files` monitor field** — each monitor entry now accepts a `log_files` mapping of label → path to collect and display alongside monitor start/stop records.

### Fixed

- Fixed hang when stopping `shell=True` subprocesses with pipe commands. SIGTERM was sent only to the shell process, leaving piped children alive and causing the sweep to block indefinitely. Now uses `os.killpg` to signal the entire process group.
- Fixed column-alignment bug in the `log_files` display when monitor names differ in length.

## [0.7.0] - 2026-05-22

### Added

- **`--where` filter operators** — `!=`, `>`, `<`, `>=`, `<=` are now supported in addition to `=`. Numeric values are compared numerically; non-numeric values fall back to lexicographic comparison. The same operators work in both the Python filter path (`make_filter`) and the DuckDB SQL path (`stats`).

## [0.6.1] - 2026-05-22

### Fixed

- Piped commands and shell operators (`|`, `&&`, `;`, redirects) now work in all tool YAML fields (`command`, `setup`, `teardown`, `post_run`, `monitor`, `env_probes`, `combo_probes`). Previously all commands were run via `shlex.split()` without `shell=True`, silently breaking any shell syntax.

## [0.6.0] - 2026-05-22

### Added

- **`scopes` tool YAML field** — nested per-sweep setup/teardown hierarchy. Each scope entry has `vars` (same string/list/negation syntax as `per_sweep_var`), `setup`, and `teardown`. Outer scopes bracket inner scopes which bracket the actual reps, enabling patterns like deploying a service once per backend while loading fixtures once per (backend, workload) pair. Inner scope steps may reference vars from all enclosing outer scopes. Mutually exclusive with the flat `per_sweep_var`/`setup_per_sweep`/`teardown_per_sweep` fields.

### Fixed

- KPI scorecard cards now always tile horizontally when space permits. The flex layout with `flex-basis: auto` caused cards to size to their content width, making two cards wrap onto separate rows even with room to spare. Switched `.kpi-grid` to CSS grid with `auto-fill / minmax(180px, 1fr)` so cards fill the row evenly.

## [0.5.1] - 2026-05-22

### Fixed

- `--skip-existing` now skips warmup iterations in addition to measured reps. Previously, if all measured reps for a combo were already recorded, warmup reps still ran unnecessarily on re-invocation.

## [0.5.0] - 2026-05-21

### Added

- **`kpi` block kind** — grid of big-number scorecard cards in editorial sections. Each card specifies a `metric`, a `stat` aggregator (`max` | `min` | `mean` across matching combos), optional `where` filter, `unit`, `precision`, and an optional `compare.where` selection that produces a Δ% arrow colored green/red by `higher_is_better`.
- **`headline` block kind** — deterministic comparison sentence rendered as a styled callout banner. Compares values of a `metric` across the levels of a `compare` variable, fills template tokens `{best}`, `{worst}`, `{best_value}`, `{worst_value}`, `{ratio}`, `{delta_pct}`. Sort direction (which end is "best") follows the metric's `higher_is_better` flag from `tools/*.yaml`.
- **`normalize` plot field** (bar / line only) — pins one series to 1.0× and expresses all others relative to it. Specify `against` (a `group_by` variable) and `reference` (the value pinned to 1.0×). Y-axis becomes "× speedup"; a dashed reference line at y = 1.0 is drawn by the new inline `abenchRefLine` Chart.js plugin.
- **`report/_select.py`** — shared value-selection helpers used by `kpi` and `headline` renderers: `resolve_value()` (scalar reduction across combos), `rank_by()` (ranked pairs for a variable), and `higher_is_better_map()` (reads direction flags from `tools/*.yaml` snapshots).

## [0.4.9] - 2026-05-21

### Added

- `theme` top-level field in the plots/tool YAML — controls the report's visual appearance without the interactive switcher:
  - `palette` — color palette: `tableau` (default), `vivid`, `pastel`, `paired`, `muted`, `accessible`.
  - `dark_mode` — `true` (always dark), `false` (always light), or `auto` (default, follows OS preference).
  - `switcher` — `true` shows the interactive palette picker and dark-mode toggle in the report header; `false` (default) hides it. When the switcher is shown, `palette` and `dark_mode` serve as initial defaults overridable by the reader.

### Changed

- The interactive theme toolbar (palette picker + dark-mode toggle) is now **hidden by default**. Set `theme.switcher: true` in the plots YAML to restore it.

## [0.4.8] - 2026-05-20

### Added

- `${_results_dir}` synthetic variable — resolves to the absolute path of the results directory. Available in `command`, `setup`, `teardown`, `post_run`, `output_file`, `setup_per_sweep`, `teardown_per_sweep`, `combo_probes`, and all `monitor` spans.

### Fixed

- `--where`, `--filter-tag`, and `--filter-exclude-tag` filters passed to `stats --report` now correctly apply to plot chart data. Previously the filters were applied to the tabular output only; charts always showed the full unfiltered dataset.

## [0.4.7] - 2026-05-19

### Added

- `data_table: false` plot field — set to `false` to suppress the collapsible "Data" table rendered below each chart in the HTML report. Default is `true` (existing behavior). Mirrors the `fixed_vars` boolean field.
- `dark mode` and color theme switcher in HTML reports — a theme picker in the top-right corner lets users choose between Tableau, Vivid, and Pastel color palettes. The selected theme persists in `localStorage` across page loads, and chart colors update live without re-rendering.
- `var_values` tool YAML field — a mapping of variable name → `{raw_value: display_label}` used to substitute friendly labels for raw variable values in plot legends and axis labels (e.g. map `"pg16"` → `"PostgreSQL 16"`).
- `series_label` plot field — an optional `${var}` template string that replaces the multi-column legend table with Chart.js's compact single-line legend. Values are automatically mapped through `var_values`.
- `fixed_vars: false` plot field — set to `false` to suppress the "Fixed: k = v" static-variable annotation above a chart. Default is `true`.
- `${var}` and `${env:VAR}` interpolation in plot `title` and report `report_title` / `report_description` / section `title` / section `description` / `text` block values. Substitution uses variables that take a single unique value across the result set.
- Arithmetic expression support for plot `x` and `y` fields — supports `+`, `-`, `*`, `/`, `**`, `//`, `%` over variable names, metric names, and numeric literals. For `y` expressions exactly one identifier must be a metric name; the rest are treated as combo variables. Syntax is validated at YAML load time.

### Fixed

- `per_sweep_var` negation expansion and `group_by` negation now correctly exclude variables whose value is the same across all combos (static values), matching the documented behavior.

## [0.4.6] - 2026-05-17

### Added

- `smooth: true` on line plots — enables spline interpolation (Chart.js `tension: 0.4`) instead of straight line segments. Only valid on `type: line`; rejected with a clear error on other plot types.

### Changed

- Report sections are now rendered as rounded-rectangle cards (border, `border-radius`, white background against a light-grey page) so sections are visually separated.
- Chart and Data collapsibles are styled as bordered cards with a shaded header, an animated chevron indicator, and padded content. A `.details-body` wrapper ensures consistent inner padding.
- Plot `title` is now rendered as an `<h3>` inside each plot block in the sectioned layout, so multiple plots within one section are individually labelled without relying solely on the section heading.
- When a `group_by` plot has only one unique series in the data (e.g. the grouped variable takes a single value), the legend table is suppressed and the variable value is folded into the "Fixed:" static-vars line instead.

## [0.4.5] - 2026-05-17

### Added

- `per_sweep_var` now accepts a list of variable names to group by a composite key, and `!var` negation entries to group by all variables except the excluded ones — mirroring the `group_by` negation syntax used in plots. For example, `per_sweep_var: [backend, workload]` fires `setup_per_sweep` / `teardown_per_sweep` once per unique `(backend, workload)` combination while letting `concurrency` vary freely within each group; `per_sweep_var: "!concurrency"` achieves the same by exclusion. The group slug in raw file names joins all group variable values with `-`. Validation ensures that `setup_per_sweep` / `teardown_per_sweep` steps only reference variables that appear in the resolved `per_sweep_var` list.

## [0.4.4] - 2026-05-15

### Added

- `${_envfile}` and `${_run_id}` are now available in `setup_per_sweep` and `teardown_per_sweep`. For per_sweep phases, `_envfile` resolves to the first entry's env file in the group; `_run_id` is a single UUID generated per group and shared between setup and teardown (enabling scripts to coordinate across the two phases). Any reference to these vars in per_sweep steps no longer triggers the "disallowed combo var" validation error.

### Changed

- README expanded with full documentation for the report layout features (`report_title`, `report_description`, `sections`, `blocks`) introduced in 0.4.3, including a worked example with two editorial sections. Synthetic variables table and per_sweep bullets updated to reflect the new `_envfile`/`_run_id` availability.

## [0.4.3] - 2026-05-14

### Changed

- Internal reorganization: `runner.py` (770 lines) and `report.py` (678 lines) split into focused packages. The runner is now `runner/{_manifest, _probes, _phases, _monitors, _planning, _rep, _sweep}`; the report is `report/{_html, _charts, _runs, _plots, _render}`. Table/TSV/JSON output previously in `query.py` moved to a new top-level `render.py`. No CLI, YAML, or on-disk format changes; the public Python API (`runner.execute_sweep`, `report.generate_html`, `report.generate_plots_html`, etc.) is preserved via package `__init__` re-exports.
- `runner.collect_probe_results` now also handles combo-scoped probes via an optional `vars_=` kwarg; the previously separate `collect_combo_probe_results` is gone.
- `__version__` in `abench_speckz/__init__.py` had been stale at `0.1.0` since the initial release; it is now kept in sync with `pyproject.toml`.

## [0.4.2] - 2026-05-13

### Added

- `monitor` tool YAML field — a list of shell commands launched as **background processes** after `setup` completes and before the benchmark command runs. Each process is sent SIGTERM when the benchmark finishes (success or failure); if it does not exit within 5 seconds it receives SIGKILL. Commands interpolate combo vars (`${var}`, `${_envfile}`, `${_run_id}`, `${env:VAR}`). A monitor that fails to start is recorded but never aborts the run. Start records (`monitor_start`) and stop records (`monitor_stop`) are written to `raw/{run_id}.json` under `--keep-raw` or when any monitor fails to start. Useful for side-car collectors that must run concurrently with each rep (e.g. scraping a Prometheus endpoint, streaming perf counters, or sampling a flame graph).

## [0.4.1] - 2026-05-13

### Changed

- Plot legends for multi-key `group_by` now render as a color-keyed HTML table instead of concatenating all variable values with ` / `. Group-by keys whose value is identical across all series are shown once in a "Fixed: key = value" block above the chart rather than repeated in every legend entry. Chart.js's built-in legend is hidden when the table legend is active. This particularly improves readability when negation syntax (e.g. `group_by: "!concurrency"`) expands to many variables. The `--group-by` CLI path receives the same treatment: axis labels omit constant keys and the fixed-vars block appears above each metric chart.

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
