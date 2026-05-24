"""Top-level orchestrator for ``abench-speckz run``: plan, execute, persist, summarize."""

import os
import sys
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from abench_speckz import interpolate, prettynames, store, tools
from abench_speckz import slug as slug_mod
from abench_speckz.executor import Executor, LocalSubprocessExecutor
from abench_speckz.runner._manifest import collect_env_metadata, load_envfile, load_manifest
from abench_speckz.runner._monitors import run_monitors, stop_monitors
from abench_speckz.runner._phases import run_phase
from abench_speckz.runner._planning import (
    ResolvedScope,
    WorkGroup,
    all_scope_sort_vars,
    existing_counts,
    group_entries,
    group_work,
    plan_sweep,
    resolve_per_sweep_vars,
    resolve_scopes,
    select_entries,
    validate_per_sweep,
    validate_scopes,
)
from abench_speckz.runner._probes import collect_probe_results, config_hash
from abench_speckz.runner._rep import execute_one_rep, make_row
from abench_speckz.runspec import ToolSpec
from abench_speckz.scheduling import FixedScheduler, Scheduler, SweepState

SchedulerFactory = Callable[[list[dict[str, Any]], dict[str, int]], Scheduler]


@dataclass
class SweepProgress:
    """Shared sweep counter + status printer.

    Owns the running index, expected total, per-status counts, and the
    progress stream. Methods print one line per state transition (run start,
    ok / FAIL / skip / INT) and increment counts in lockstep so callers
    cannot get them out of sync.
    """

    total: int
    stream: Any
    idx: int = 0
    counts: dict[str, int] = field(
        default_factory=lambda: {"succeeded": 0, "failed": 0, "skipped": 0}
    )
    t_start: float = field(default_factory=time.monotonic)

    def start_run(self, ch: str, is_warmup: bool) -> None:
        self.idx += 1
        tag = " [warmup]" if is_warmup else ""
        print(f"[{self.idx:>4}/{self.total}] run  {ch}{tag}", file=self.stream, flush=True)

    def finish_ok(self, ch: str) -> None:
        self.counts["succeeded"] += 1
        self._emit("ok", ch)

    def finish_fail(self, ch: str) -> None:
        self.counts["failed"] += 1
        self._emit("FAIL", ch)

    def record_skip(self, ch: str) -> None:
        self.idx += 1
        self.counts["skipped"] += 1
        self._emit("skip", ch)

    def record_setup_failure(self, ch: str) -> None:
        self.idx += 1
        self.counts["failed"] += 1
        self._emit("FAIL", ch)

    def interrupt(self, ch: str) -> None:
        self._emit("INT", ch)

    def summary(self) -> dict[str, int]:
        return {
            "runs": self.counts["succeeded"] + self.counts["failed"],
            "succeeded": self.counts["succeeded"],
            "failed": self.counts["failed"],
            "skipped": self.counts["skipped"],
        }

    @staticmethod
    def empty_summary() -> dict[str, int]:
        return {"runs": 0, "succeeded": 0, "failed": 0, "skipped": 0}

    def _emit(self, status: str, ch: str) -> None:
        elapsed = time.monotonic() - self.t_start
        eta = (elapsed / self.idx) * (self.total - self.idx) if self.idx > 0 else 0.0
        print(
            f"[{self.idx:>4}/{self.total}] {status:<4} {ch}  elapsed={elapsed:5.1f}s eta={eta:5.1f}s",
            file=self.stream,
            flush=True,
        )


def execute_sweep(
    *,
    manifest_path: Path,
    tool_path: Path,
    results_dir: Path,
    repeat: int,
    warmup: int = 0,
    where: list[str] | None = None,
    require_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    keep_raw: bool = False,
    skip_existing: bool = False,
    dry_run: bool = False,
    executor: Executor | None = None,
    progress_stream=sys.stderr,
    scheduler_factory: SchedulerFactory | None = None,
) -> dict[str, int]:
    """Run the benchmark tool across every combo in the manifest.

    For each rep (warmup + measured) the flow is: optional setup steps →
    interpolated tool command → optional teardown steps. Teardown always runs
    (even on benchmark failure or KeyboardInterrupt). Results are appended to
    ``runs.jsonl`` and aggregates are rewritten after every run.

    The ``scheduler_factory`` controls how reps are scheduled within each
    per_sweep group (called with that group's entries and a skip-counts dict).
    When omitted, a ``FixedScheduler`` is built from ``repeat``/``warmup``/
    ``skip_existing`` — preserving the historical pre-flattened plan.
    """
    where = where or []
    require_tags = require_tags or []
    exclude_tags = exclude_tags or []
    executor = executor or LocalSubprocessExecutor()

    actual_manifest_path, manifest_entries = load_manifest(manifest_path)
    manifest_dir = actual_manifest_path.parent
    tool = tools.load_tool(tool_path)

    all_var_names = sorted(
        {k for e in manifest_entries for k in (e.get("varying_keys") or e.get("vars") or {})}
    )

    resolved_scopes = resolve_scopes(tool.scopes, all_var_names) if tool.scopes else []
    per_sweep_vars = resolve_per_sweep_vars(tool.per_sweep_var, all_var_names)

    sort_vars = all_scope_sort_vars(resolved_scopes) if resolved_scopes else per_sweep_vars
    selected = select_entries(
        manifest_entries,
        where,
        require_tags,
        exclude_tags,
        per_sweep_vars=sort_vars,
    )
    if not selected:
        print("no combos selected after filters", file=progress_stream)
        return SweepProgress.empty_summary()

    if resolved_scopes:
        validate_scopes(resolved_scopes, selected)
    else:
        validate_per_sweep(tool, selected, per_sweep_vars)

    if dry_run:
        # Dry-run preview uses the historical pre-flattened plan — schedulers
        # only matter at execution time.
        work = plan_sweep(
            manifest_entries,
            repeat,
            warmup,
            where,
            require_tags,
            exclude_tags,
            per_sweep_vars=sort_vars,
        )
        if resolved_scopes:
            _dry_run_nested(work, resolved_scopes, tool, progress_stream, manifest_dir, results_dir)
        else:
            groups = group_work(work, per_sweep_vars)
            _dry_run_preview(groups, tool, per_sweep_vars, progress_stream, manifest_dir, results_dir)
        return SweepProgress.empty_summary()

    if scheduler_factory is None:

        def _default_factory(entries: list[dict[str, Any]], skip_counts: dict[str, int]) -> Scheduler:
            return FixedScheduler(
                entries,
                warmup=warmup,
                repeat=repeat,
                skip_counts=skip_counts,
                skip_existing=skip_existing,
            )

        scheduler_factory = _default_factory

    # Pre-instantiate schedulers per group only to (a) compute total for
    # progress display and (b) early-validate against the tool. The execution
    # path re-instantiates per group so each one starts with a clean cursor.
    n_combos = len({config_hash(e["vars"]) for e in selected})
    _preview_skip = existing_counts(results_dir) if skip_existing else Counter()
    if resolved_scopes:
        preview_groups = group_entries(selected, sort_vars)
    else:
        preview_groups = group_entries(selected, per_sweep_vars)
    total = 0
    for _gk, group_es in preview_groups:
        sched = scheduler_factory(group_es, dict(_preview_skip))
        sched.validate(tool)
        total += sched.expected_max_reps()
    print(
        f"sweep: up to {total} rep(s) across {n_combos} combo(s)",
        file=progress_stream,
        flush=True,
    )

    with store.lock_results_dir(results_dir):
        _init_sweep(results_dir, actual_manifest_path, tool_path, tool, progress_stream)
        tool_version = tools.resolve_tool_version(tool)
        skip_counts = existing_counts(results_dir) if skip_existing else Counter()

        sweep_monitor_procs: list = []
        sweep_monitor_start_records: list[dict] = []
        sweep_monitor_stop_records: list[dict] = []
        sweep_log_files: list = []
        per_sweep_cmds = [m.command for m in tool.monitor if m.span == "per_sweep"]
        if per_sweep_cmds:
            sweep_run_id = str(uuid.uuid4())
            sweep_monitor_procs, sweep_monitor_start_records, sweep_log_files = run_monitors(
                per_sweep_cmds,
                {"_run_id": sweep_run_id, "_results_dir": str(results_dir.resolve())},
                dict(os.environ),
                log_dir=results_dir / "logs",
                log_prefix="monitor-sweep",
            )

        progress = SweepProgress(total=total, stream=progress_stream)
        try:
            if resolved_scopes:
                per_group_cmds = [m.command for m in tool.monitor if m.span == "per_group"]
                _run_nested_groups(
                    entries=selected,
                    scopes=resolved_scopes,
                    scope_idx=0,
                    inherited_phase_vars={},
                    tool=tool,
                    tool_version=tool_version,
                    manifest_dir=manifest_dir,
                    results_dir=results_dir,
                    executor=executor,
                    keep_raw=keep_raw,
                    skip_counts=skip_counts,
                    scheduler_factory=scheduler_factory,
                    progress=progress,
                    base_env=dict(os.environ),
                    probed_hashes=set(),
                    combo_probe_cache={},
                    per_group_cmds=per_group_cmds,
                )
            else:
                entry_groups: list[tuple[tuple[str, ...] | None, list[dict[str, Any]]]] = group_entries(
                    selected, per_sweep_vars
                )
                _run_groups(
                    groups=entry_groups,
                    tool=tool,
                    tool_version=tool_version,
                    per_sweep_vars=per_sweep_vars,
                    manifest_dir=manifest_dir,
                    results_dir=results_dir,
                    executor=executor,
                    keep_raw=keep_raw,
                    skip_counts=skip_counts,
                    scheduler_factory=scheduler_factory,
                    progress=progress,
                )
            return progress.summary()
        finally:
            if sweep_monitor_procs:
                sweep_monitor_stop_records = stop_monitors(sweep_monitor_procs, sweep_log_files)
            has_error = any(r.get("error") for r in sweep_monitor_start_records)
            if sweep_monitor_start_records and (keep_raw or has_error):
                store.write_raw_sweep_monitors(
                    results_dir, sweep_monitor_start_records, sweep_monitor_stop_records
                )


def _init_sweep(
    results_dir: Path,
    manifest_path: Path,
    tool_path: Path,
    tool: ToolSpec,
    progress_stream=sys.stderr,
) -> None:
    """Prepare the results dir: init layout, snapshot inputs, capture env + pretty names."""
    store.init_results_dir(results_dir)
    store.snapshot_manifest(results_dir, manifest_path)
    store.snapshot_tool(results_dir, tool_path, tool.name)

    env_meta = collect_env_metadata()
    if tool.env_probes:
        print(
            f"env probes: {len(tool.env_probes)} probe(s)...",
            file=progress_stream,
            flush=True,
        )
        env_meta["probes"] = collect_probe_results(tool.env_probes, tool.setup_timeout_seconds)
    store.write_env_snapshot(results_dir, env_meta)

    pretty_path = results_dir / "pretty_names.json"
    existing = prettynames.load(pretty_path)
    merged = prettynames.merge(existing, prettynames.from_tool(tool))
    prettynames.write(pretty_path, merged)


def _run_groups(
    *,
    groups: list[tuple[tuple[str, ...] | None, list[dict[str, Any]]]],
    tool: ToolSpec,
    tool_version: str | None,
    per_sweep_vars: list[str] | None,
    manifest_dir: Path,
    results_dir: Path,
    executor: Executor,
    keep_raw: bool,
    skip_counts: Counter,
    scheduler_factory: SchedulerFactory,
    progress: SweepProgress,
) -> None:
    """Walk each per_sweep group: run per_sweep setup/teardown, then each rep."""
    base_env = {**os.environ}
    probed_hashes: set[str] = set()
    combo_probe_cache: dict[str, dict[str, Any]] = {}
    per_group_cmds = [m.command for m in tool.monitor if m.span == "per_group"]

    for group_key, entries in groups:
        scheduler = scheduler_factory(entries, dict(skip_counts))
        runnable = scheduler.runnable_entries()
        group_slug = (
            "-".join(slug_mod.slug(v) for v in group_key)
            if (per_sweep_vars and group_key is not None)
            else None
        )
        first_env_file = manifest_dir / entries[0]["filename"]
        phase_vars: dict[str, Any] = {
            **(
                dict(zip(per_sweep_vars, group_key, strict=True))
                if (per_sweep_vars and group_key is not None)
                else {}
            ),
            "_run_id": str(uuid.uuid4()),
            "_envfile": str(first_env_file.resolve()),
            "_results_dir": str(results_dir.resolve()),
        }

        if not runnable:
            for entry in entries:
                ch = config_hash(entry["vars"])
                n_total = scheduler.planned_warmup_for(entry) + scheduler.planned_measured_for(entry)
                for _ in range(n_total):
                    progress.record_skip(ch)
            continue

        if tool.setup_per_sweep:
            _label = _format_group_label(per_sweep_vars, group_key)
            _suffix = f" [{_label}]" if _label else ""
            print(
                f"per_sweep setup{_suffix} ({len(tool.setup_per_sweep)} step(s))...",
                file=progress.stream,
                flush=True,
            )
        _group_run_id = phase_vars["_run_id"]
        _log_dir = results_dir / "logs"
        sweep_setup_failure, sweep_setup_records = _run_setup_phase(
            tool.setup_per_sweep, phase_vars, base_env, tool.setup_timeout_seconds,
            log_dir=_log_dir, log_prefix=f"sweep_setup--{_group_run_id}",
        )

        sweep_teardown_failure: str | None = None
        sweep_teardown_records: list[dict[str, Any]] = []
        if sweep_setup_failure:
            sweep_teardown_failure, sweep_teardown_records = _run_teardown_phase(
                tool.teardown_per_sweep, phase_vars, base_env, tool.setup_timeout_seconds,
                log_dir=_log_dir, log_prefix=f"sweep_teardown--{_group_run_id}",
            )
            _record_setup_failures(
                entries=entries,
                scheduler=scheduler,
                results_dir=results_dir,
                tool=tool,
                tool_version=tool_version,
                failure=sweep_setup_failure,
                progress=progress,
            )
            _maybe_write_sweep_raw(
                results_dir,
                group_slug,
                sweep_setup_records,
                sweep_teardown_records,
                force=True,
            )
            continue

        sweep_teardown_failure, sweep_teardown_records, monitor_records = _drive_scheduler_loop(
            scheduler=scheduler,
            results_dir=results_dir,
            tool=tool,
            tool_version=tool_version,
            manifest_dir=manifest_dir,
            executor=executor,
            keep_raw=keep_raw,
            phase_vars=phase_vars,
            base_env=base_env,
            teardown_steps=tool.teardown_per_sweep,
            per_group_cmds=per_group_cmds,
            group_log_prefix=f"monitor-group-{group_slug or 'default'}",
            probed_hashes=probed_hashes,
            combo_probe_cache=combo_probe_cache,
            progress=progress,
            seed_rows=store.read_runs(results_dir),
        )

        _maybe_write_sweep_raw(
            results_dir,
            group_slug,
            sweep_setup_records,
            sweep_teardown_records,
            force=bool(sweep_teardown_failure or keep_raw),
            monitor_start=monitor_records.get("start"),
            monitor_stop=monitor_records.get("stop"),
        )


def _drive_scheduler_loop(
    *,
    scheduler: Scheduler,
    results_dir: Path,
    tool: ToolSpec,
    tool_version: str | None,
    manifest_dir: Path,
    executor: Executor,
    keep_raw: bool,
    phase_vars: dict[str, Any],
    base_env: dict[str, str],
    teardown_steps: list[str],
    per_group_cmds: list[str],
    group_log_prefix: str,
    probed_hashes: set[str],
    combo_probe_cache: dict[str, dict[str, Any]],
    progress: SweepProgress,
    seed_rows: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]], dict[str, list]]:
    """Drive a Scheduler within an already-setup per_sweep group.

    Writes are deferred by one rep so that ``teardown_per_sweep`` failures can
    still be folded into the *last* rep's row (the historical contract). When
    the scheduler returns ``None``, we know the previously-produced row was
    the final one — run teardown, fold any failure in, then write.
    """
    state = SweepState(seed_rows=seed_rows)
    log_dir = results_dir / "logs"
    teardown_log_prefix = f"sweep_teardown--{phase_vars['_run_id']}"

    prev_row: dict[str, Any] | None = None
    prev_ch: str | None = None
    teardown_failure: str | None = None
    teardown_records: list[dict[str, Any]] = []

    with _per_group_monitors(
        per_group_cmds, phase_vars, base_env, log_dir=log_dir, log_prefix=group_log_prefix,
    ) as monitors:
        try:
            while True:
                if prev_row is not None:
                    # Update the in-memory state immediately so the scheduler's
                    # next decision sees this rep. Disk write is still deferred
                    # until we know whether this is the final rep (so any
                    # teardown_per_sweep failure can be folded in).
                    assert prev_ch is not None
                    state.record(prev_ch, prev_row)
                item = scheduler.next(state)
                if item is None:
                    break
                if prev_row is not None:
                    # Not the last rep — commit the previous one to disk now.
                    _commit_disk(prev_row, prev_ch, results_dir=results_dir, progress=progress)
                ch = config_hash(item.entry["vars"])
                _maybe_collect_combo_probes(
                    tool=tool,
                    entry=item.entry,
                    ch=ch,
                    manifest_dir=manifest_dir,
                    results_dir=results_dir,
                    probed_hashes=probed_hashes,
                    cache=combo_probe_cache,
                )
                progress.start_run(ch, item.is_warmup)
                try:
                    prev_row = execute_one_rep(
                        entry=item.entry,
                        is_warmup=item.is_warmup,
                        tool=tool,
                        tool_version=tool_version,
                        manifest_dir=manifest_dir,
                        results_dir=results_dir,
                        executor=executor,
                        keep_raw=keep_raw,
                        probe_results=combo_probe_cache.get(ch, {}),
                    )
                    prev_ch = ch
                except KeyboardInterrupt:
                    progress.interrupt(ch)
                    raise

            # Scheduler exhausted — stop monitors first so teardown noise
            # stays out of monitor output, then fold any teardown failure
            # into the final row before committing.
            if prev_row is not None:
                monitors.stop_now()
                teardown_failure, teardown_records = _run_teardown_phase(
                    teardown_steps, phase_vars, base_env, tool.setup_timeout_seconds,
                    log_dir=log_dir, log_prefix=teardown_log_prefix,
                )
                if teardown_failure:
                    prior = prev_row.get("failure_reason")
                    prev_row["failure_reason"] = (
                        f"{prior}; {teardown_failure}" if prior else teardown_failure
                    )
                _commit_disk(prev_row, prev_ch, results_dir=results_dir, progress=progress)
        except KeyboardInterrupt:
            monitors.stop_now()
            _run_teardown_phase(
                teardown_steps, phase_vars, base_env, tool.setup_timeout_seconds,
                log_dir=log_dir, log_prefix=teardown_log_prefix,
            )
            raise

    return (
        teardown_failure,
        teardown_records,
        {"start": monitors.start_records, "stop": monitors.stop_records},
    )


def _commit_disk(
    row: dict[str, Any],
    ch: str | None,
    *,
    results_dir: Path,
    progress: SweepProgress,
) -> None:
    """Persist a rep to disk and emit its progress line.

    In-memory ``SweepState`` is updated separately, immediately after the rep
    finishes — so the scheduler's next decision is never stale by one rep.
    """
    assert ch is not None
    store.append_run(results_dir, row)
    store.update_aggregates_for_hash(results_dir, ch)
    if row.get("failure_reason") or row.get("exit_code", -1) != 0:
        progress.finish_fail(ch)
    else:
        progress.finish_ok(ch)


def _record_setup_failures(
    *,
    entries: list[dict[str, Any]],
    scheduler: Scheduler,
    results_dir: Path,
    tool: ToolSpec,
    tool_version: str | None,
    failure: str,
    progress: SweepProgress,
) -> None:
    """Emit one failure row per planned rep for every runnable entry.

    Used when per_sweep_setup aborted before any rep ran. The scheduler
    advises how many reps were planned per entry (warmup + measured); for
    dynamic schedulers, "measured" is the guaranteed minimum.
    """
    runnable = {config_hash(e["vars"]): e for e in scheduler.runnable_entries()}
    for entry in entries:
        ch = config_hash(entry["vars"])
        if ch not in runnable:
            progress.record_skip(ch)
            continue
        n_warmup = scheduler.planned_warmup_for(entry)
        n_measured = scheduler.planned_measured_for(entry)
        for i in range(n_warmup + n_measured):
            is_warmup = i < n_warmup
            tags = list(entry.get("tags") or [])
            if is_warmup:
                tags.append("warmup")
            row = make_row(
                run_id=str(uuid.uuid4()),
                entry=entry,
                ch=ch,
                tags=tags,
                tool=tool,
                tool_version=tool_version,
                command="<not-run>",
                result=None,
                parsed={},
                failure_reason=failure,
                probe_results={},
            )
            store.append_run(results_dir, row)
            store.update_aggregates_for_hash(results_dir, ch)
            progress.record_setup_failure(ch)


@dataclass
class _GroupMonitorHandle:
    """Handle returned by ``_per_group_monitors`` so callers can stop early.

    ``stop_now`` is idempotent: explicit calls (to halt monitors before the
    teardown phase so their output stays clean) and the context manager's
    finally block (to guarantee cleanup on KeyboardInterrupt) cooperate
    safely.
    """

    start_records: list[dict[str, Any]] = field(default_factory=list)
    stop_records: list[dict[str, Any]] = field(default_factory=list)
    _procs: list = field(default_factory=list)
    _log_files: list = field(default_factory=list)

    def stop_now(self) -> None:
        if self._procs:
            self.stop_records = stop_monitors(self._procs, self._log_files)
            self._procs = []


@contextmanager
def _per_group_monitors(
    cmds: list[str],
    phase_vars: dict[str, Any],
    base_env: dict[str, str],
    *,
    log_dir: Path,
    log_prefix: str,
) -> Iterator[_GroupMonitorHandle]:
    """Run ``per_group`` monitors for the lifetime of the with-block."""
    handle = _GroupMonitorHandle()
    if cmds:
        handle._procs, handle.start_records, handle._log_files = run_monitors(
            cmds, phase_vars, base_env, log_dir=log_dir, log_prefix=log_prefix,
        )
    try:
        yield handle
    finally:
        handle.stop_now()


def _run_setup_phase(
    steps: list[str],
    phase_vars: dict[str, Any],
    base_env: dict[str, str],
    timeout: int,
    *,
    log_dir: Path,
    log_prefix: str,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Run a per_sweep_setup phase, short-circuiting when there are no steps."""
    if not steps:
        return None, []
    return run_phase(
        "per_sweep_setup", steps, phase_vars, base_env, timeout,
        log_dir=log_dir, log_prefix=log_prefix,
    )


def _run_teardown_phase(
    steps: list[str],
    phase_vars: dict[str, Any],
    base_env: dict[str, str],
    timeout: int,
    *,
    log_dir: Path,
    log_prefix: str,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Run a per_sweep_teardown phase, short-circuiting when there are no steps."""
    if not steps:
        return None, []
    return run_phase(
        "per_sweep_teardown", steps, phase_vars, base_env, timeout,
        log_dir=log_dir, log_prefix=log_prefix,
    )


def _group_slug(group_key: tuple[str, ...] | None) -> str | None:
    if not group_key:
        return None
    return "-".join(slug_mod.slug(v) for v in group_key)


def _format_group_label(
    vars_: list[str] | None, group_key: tuple[str, ...] | None
) -> str:
    """Format ``"var1=val1, var2=val2"`` for log/preview lines; ``""`` if no group."""
    if not vars_ or group_key is None:
        return ""
    return ", ".join(f"{v}={group_key[i]}" for i, v in enumerate(vars_))


def _run_nested_groups(
    *,
    entries: list[dict[str, Any]],
    scopes: list[ResolvedScope],
    scope_idx: int,
    inherited_phase_vars: dict,
    tool: ToolSpec,
    tool_version: str | None,
    manifest_dir: Any,
    results_dir: Any,
    executor: Any,
    keep_raw: bool,
    skip_counts: Counter,
    scheduler_factory: SchedulerFactory,
    progress: SweepProgress,
    base_env: dict,
    probed_hashes: set,
    combo_probe_cache: dict,
    per_group_cmds: list[str],
) -> None:
    """Recursively execute nested scope levels.

    At each scope level, entries are partitioned into groups by that scope's vars.
    Setup/teardown for the scope run once per group; the next scope (or the actual
    reps, at the innermost level) runs inside each group.

    Teardown failures at the innermost scope are folded into the last rep's
    ``failure_reason`` (same contract as the flat path). Teardown failures at
    outer scopes are written to a raw file keyed by scope index and group slug.
    """
    scope = scopes[scope_idx]
    is_innermost = scope_idx == len(scopes) - 1

    for group_key, group_entries_list in group_entries(entries, scope.vars):
        assert group_key is not None
        first_env_file = manifest_dir / group_entries_list[0]["filename"]
        phase_vars: dict = {
            **inherited_phase_vars,
            **dict(zip(scope.vars, group_key, strict=True)),
            "_run_id": str(uuid.uuid4()),
            "_envfile": str(first_env_file.resolve()),
            "_results_dir": str(results_dir.resolve()),
        }

        # Build a scheduler even at outer scopes — needed for runnable_entries()
        # and per-entry planned-rep counts on the skip / setup-failure paths.
        scheduler = scheduler_factory(group_entries_list, dict(skip_counts))
        if not scheduler.runnable_entries():
            for entry in group_entries_list:
                ch = config_hash(entry["vars"])
                n_total = scheduler.planned_warmup_for(entry) + scheduler.planned_measured_for(entry)
                for _ in range(n_total):
                    progress.record_skip(ch)
            continue

        if scope.setup:
            _label = _format_group_label(scope.vars, group_key)
            print(
                f"scope{scope_idx} setup [{_label}] ({len(scope.setup)} step(s))...",
                file=progress.stream,
                flush=True,
            )
        _scope_run_id = phase_vars.get("_run_id", "group")
        _scope_log_dir = results_dir / "logs"
        setup_failure, setup_records = _run_setup_phase(
            scope.setup, phase_vars, base_env, tool.setup_timeout_seconds,
            log_dir=_scope_log_dir, log_prefix=f"scope{scope_idx}_setup--{_scope_run_id}",
        )
        teardown_failure: str | None = None
        teardown_records: list = []

        if setup_failure:
            teardown_failure, teardown_records = _run_teardown_phase(
                scope.teardown, phase_vars, base_env, tool.setup_timeout_seconds,
                log_dir=_scope_log_dir, log_prefix=f"scope{scope_idx}_teardown--{_scope_run_id}",
            )
            _record_setup_failures(
                entries=group_entries_list,
                scheduler=scheduler,
                results_dir=results_dir,
                tool=tool,
                tool_version=tool_version,
                failure=setup_failure,
                progress=progress,
            )
            group_slug = _group_slug(group_key)
            scope_slug = f"scope{scope_idx}-{group_slug}" if group_slug else f"scope{scope_idx}"
            _maybe_write_scope_raw(results_dir, scope_slug, setup_records, teardown_records, force=True)

        elif is_innermost:
            teardown_failure, teardown_records, monitor_records = _drive_scheduler_loop(
                scheduler=scheduler,
                results_dir=results_dir,
                tool=tool,
                tool_version=tool_version,
                manifest_dir=manifest_dir,
                executor=executor,
                keep_raw=keep_raw,
                phase_vars=phase_vars,
                base_env=base_env,
                teardown_steps=scope.teardown,
                per_group_cmds=per_group_cmds,
                group_log_prefix=f"monitor-group-{str(phase_vars.get('_run_id', 'group'))[:8]}",
                probed_hashes=probed_hashes,
                combo_probe_cache=combo_probe_cache,
                progress=progress,
                seed_rows=store.read_runs(results_dir),
            )
            group_slug = _group_slug(group_key)
            _maybe_write_sweep_raw(
                results_dir,
                group_slug,
                setup_records,
                teardown_records,
                force=bool(teardown_failure or keep_raw),
                monitor_start=monitor_records.get("start"),
                monitor_stop=monitor_records.get("stop"),
            )

        else:
            _run_nested_groups(
                entries=group_entries_list,
                scopes=scopes,
                scope_idx=scope_idx + 1,
                inherited_phase_vars=phase_vars,
                tool=tool,
                tool_version=tool_version,
                manifest_dir=manifest_dir,
                results_dir=results_dir,
                executor=executor,
                keep_raw=keep_raw,
                skip_counts=skip_counts,
                scheduler_factory=scheduler_factory,
                progress=progress,
                base_env=base_env,
                probed_hashes=probed_hashes,
                combo_probe_cache=combo_probe_cache,
                per_group_cmds=per_group_cmds,
            )
            teardown_failure, teardown_records = _run_teardown_phase(
                scope.teardown, phase_vars, base_env, tool.setup_timeout_seconds,
                log_dir=_scope_log_dir, log_prefix=f"scope{scope_idx}_teardown--{_scope_run_id}",
            )
            group_slug = _group_slug(group_key)
            scope_slug = f"scope{scope_idx}-{group_slug}" if group_slug else f"scope{scope_idx}"
            _maybe_write_scope_raw(
                results_dir,
                scope_slug,
                setup_records,
                teardown_records,
                force=bool(teardown_failure or keep_raw),
            )


def _maybe_write_scope_raw(
    results_dir: Any,
    scope_slug: str | None,
    setup_records: list,
    teardown_records: list,
    *,
    force: bool,
) -> None:
    if not force:
        return
    raw: dict = {}
    if setup_records:
        raw["setup"] = setup_records
    if teardown_records:
        raw["teardown"] = teardown_records
    if raw:
        store.write_raw_sweep_phase(results_dir, scope_slug, raw)


def _dry_run_nested(
    work: list,
    scopes: list[ResolvedScope],
    tool: ToolSpec,
    stream,
    manifest_dir: Any,
    results_dir: Any,
) -> None:
    """Print a dry-run preview for nested scopes, showing scope hierarchy with indentation."""
    _dry_run_nested_level(work, scopes, 0, {}, tool, stream, manifest_dir, results_dir, indent=0)


def _dry_run_nested_level(
    work: list,
    scopes: list[ResolvedScope],
    scope_idx: int,
    inherited_vars: dict,
    tool: ToolSpec,
    stream,
    manifest_dir: Any,
    results_dir: Any,
    indent: int,
) -> None:
    from abench_speckz.runner._probes import config_hash

    scope = scopes[scope_idx]
    is_innermost = scope_idx == len(scopes) - 1
    pad = "  " * indent

    for group_key, group_items in group_work(work, scope.vars):
        assert group_key is not None
        first_env_file = manifest_dir / group_items[0][0]["filename"]
        phase_vars = {
            **inherited_vars,
            **dict(zip(scope.vars, group_key, strict=True)),
            "_run_id": "<run_id>",
            "_envfile": str(first_env_file.resolve()),
            "_results_dir": str(results_dir.resolve()) if results_dir else "<results_dir>",
        }
        label = _format_group_label(scope.vars, group_key)
        for k, step in enumerate(scope.setup):
            if not step.matches(phase_vars):
                continue
            cmd = interpolate.resolve_string(step.run, phase_vars, env=os.environ)
            print(f"{pad}[scope{scope_idx} setup {k}, {label}]: {cmd}", file=stream)
        if is_innermost:
            for entry, rep, is_warmup in group_items:
                ch = config_hash(entry["vars"])
                cmd = interpolate.resolve_string(tool.command, entry["vars"], env=os.environ)
                tag = " [warmup]" if is_warmup else ""
                print(f"{pad}  {ch} rep={rep}{tag}: {cmd}", file=stream)
        else:
            _dry_run_nested_level(
                group_items,
                scopes,
                scope_idx + 1,
                phase_vars,
                tool,
                stream,
                manifest_dir,
                results_dir,
                indent=indent + 1,
            )
        for k, step in enumerate(scope.teardown):
            if not step.matches(phase_vars):
                continue
            cmd = interpolate.resolve_string(step.run, phase_vars, env=os.environ)
            print(f"{pad}[scope{scope_idx} teardown {k}, {label}]: {cmd}", file=stream)


def _maybe_collect_combo_probes(
    *,
    tool: ToolSpec,
    entry: dict[str, Any],
    ch: str,
    manifest_dir: Path,
    results_dir: Path,
    probed_hashes: set[str],
    cache: dict[str, dict[str, Any]],
) -> None:
    """First time we see a config_hash, run combo_probes for it and persist results."""
    if not tool.combo_probes or ch in probed_hashes:
        return
    env_file = manifest_dir / entry["filename"]
    probe_vars = {
        **entry["vars"],
        "_envfile": str(env_file.resolve()),
        "_results_dir": str(results_dir.resolve()),
    }
    probe_env = (
        {**os.environ, **load_envfile(env_file)}
        if env_file.exists()
        else {**os.environ, **{k: str(v) for k, v in entry["vars"].items()}}
    )
    cache[ch] = collect_probe_results(
        tool.combo_probes,
        tool.setup_timeout_seconds,
        vars_=probe_vars,
        env=probe_env,
    )
    store.update_combo_probes(results_dir, ch, cache[ch])
    probed_hashes.add(ch)


def _maybe_write_sweep_raw(
    results_dir: Path,
    group_slug: str | None,
    setup_records: list[dict[str, Any]],
    teardown_records: list[dict[str, Any]],
    *,
    force: bool,
    monitor_start: list[dict[str, Any]] | None = None,
    monitor_stop: list[dict[str, Any]] | None = None,
) -> None:
    has_monitor_error = any(r.get("error") for r in (monitor_start or []))
    if not force and not has_monitor_error:
        return
    raw: dict[str, Any] = {}
    if setup_records:
        raw["setup"] = setup_records
    if teardown_records:
        raw["teardown"] = teardown_records
    if monitor_start:
        raw["monitor_start"] = monitor_start
    if monitor_stop:
        raw["monitor_stop"] = monitor_stop
    if raw:
        store.write_raw_sweep_phase(results_dir, group_slug, raw)


def _dry_run_preview(
    groups: list[WorkGroup],
    tool: ToolSpec,
    per_sweep_vars: list[str] | None,
    stream,
    manifest_dir: Path,
    results_dir: Path | None = None,
) -> None:
    for group_key, items in groups:
        first_env_file = manifest_dir / items[0][0]["filename"]
        phase_vars: dict[str, Any] = {
            **(
                dict(zip(per_sweep_vars, group_key, strict=True))
                if (per_sweep_vars and group_key is not None)
                else {}
            ),
            "_run_id": str(uuid.uuid4()),
            "_envfile": str(first_env_file.resolve()),
            "_results_dir": str(results_dir.resolve()) if results_dir else "<results_dir>",
        }
        _inner = _format_group_label(per_sweep_vars, group_key)
        label = f", {_inner}" if _inner else ""
        for k, step in enumerate(tool.setup_per_sweep):
            if not step.matches(phase_vars):
                continue
            cmd = interpolate.resolve_string(step.run, phase_vars, env=os.environ)
            print(f"[per_sweep setup {k}{label}]: {cmd}", file=stream)
        for entry, rep, is_warmup in items:
            ch = config_hash(entry["vars"])
            cmd = interpolate.resolve_string(tool.command, entry["vars"], env=os.environ)
            tag = " [warmup]" if is_warmup else ""
            print(f"{ch} rep={rep}{tag}: {cmd}", file=stream)
        for k, step in enumerate(tool.teardown_per_sweep):
            if not step.matches(phase_vars):
                continue
            cmd = interpolate.resolve_string(step.run, phase_vars, env=os.environ)
            print(f"[per_sweep teardown {k}{label}]: {cmd}", file=stream)


