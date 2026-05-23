"""Top-level orchestrator for ``abench-speckz run``: plan, execute, persist, summarize."""

import os
import sys
import time
import uuid
from collections import Counter
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
    group_work,
    plan_sweep,
    resolve_per_sweep_vars,
    resolve_scopes,
    validate_per_sweep,
    validate_scopes,
)
from abench_speckz.runner._probes import collect_probe_results, config_hash
from abench_speckz.runner._rep import execute_one_rep, make_row
from abench_speckz.runspec import ToolSpec


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
) -> dict[str, int]:
    """Run the benchmark tool across every combo in the manifest.

    For each rep (warmup + measured) the flow is: optional setup steps →
    interpolated tool command → optional teardown steps. Teardown always runs
    (even on benchmark failure or KeyboardInterrupt). Results are appended to
    ``runs.jsonl`` and aggregates are rewritten after every run.
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
    work = plan_sweep(
        manifest_entries,
        repeat,
        warmup,
        where,
        require_tags,
        exclude_tags,
        per_sweep_vars=sort_vars,
    )
    if not work:
        print("no combos selected after filters", file=progress_stream)
        return _empty_summary()

    selected = [e for (e, _, _) in work]
    if resolved_scopes:
        validate_scopes(resolved_scopes, selected)
    else:
        validate_per_sweep(tool, selected, per_sweep_vars)

    if dry_run:
        if resolved_scopes:
            _dry_run_nested(work, resolved_scopes, tool, progress_stream, manifest_dir, results_dir)
        else:
            groups = group_work(work, per_sweep_vars)
            _dry_run_preview(groups, tool, per_sweep_vars, progress_stream, manifest_dir, results_dir)
        return _empty_summary()

    groups = group_work(work, per_sweep_vars) if not resolved_scopes else []

    with store.lock_results_dir(results_dir):
        _init_sweep(results_dir, actual_manifest_path, tool_path, tool)
        tool_version = tools.resolve_tool_version(tool)
        skip_counts = existing_counts(results_dir) if skip_existing else Counter()

        sweep_monitor_procs: list = []
        sweep_monitor_start_records: list[dict] = []
        sweep_monitor_stop_records: list[dict] = []
        per_sweep_cmds = [m.command for m in tool.monitor if m.span == "per_sweep"]
        if per_sweep_cmds:
            sweep_run_id = str(uuid.uuid4())
            sweep_monitor_procs, sweep_monitor_start_records = run_monitors(
                per_sweep_cmds,
                {"_run_id": sweep_run_id, "_results_dir": str(results_dir.resolve())},
                dict(os.environ),
            )

        try:
            if resolved_scopes:
                state = {"sweep_idx": 0, "total": len(work), "t_sweep": time.monotonic()}
                per_group_cmds = [m.command for m in tool.monitor if m.span == "per_group"]
                summary = _run_nested_groups(
                    work=work,
                    scopes=resolved_scopes,
                    scope_idx=0,
                    inherited_phase_vars={},
                    tool=tool,
                    tool_version=tool_version,
                    manifest_dir=manifest_dir,
                    results_dir=results_dir,
                    executor=executor,
                    keep_raw=keep_raw,
                    skip_existing=skip_existing,
                    skip_counts=skip_counts,
                    repeat=repeat,
                    state=state,
                    base_env=dict(os.environ),
                    probed_hashes=set(),
                    combo_probe_cache={},
                    per_group_cmds=per_group_cmds,
                    progress_stream=progress_stream,
                )
                return {
                    "runs": len(work) - summary["skipped"],
                    "failed": summary["failed"],
                    "skipped": summary["skipped"],
                    "succeeded": summary["succeeded"],
                }
            else:
                return _run_groups(
                    groups=groups,
                    tool=tool,
                    tool_version=tool_version,
                    per_sweep_vars=per_sweep_vars,
                    manifest_dir=manifest_dir,
                    results_dir=results_dir,
                    executor=executor,
                    keep_raw=keep_raw,
                    skip_existing=skip_existing,
                    skip_counts=skip_counts,
                    repeat=repeat,
                    total=len(work),
                    progress_stream=progress_stream,
                )
        finally:
            if sweep_monitor_procs:
                sweep_monitor_stop_records = stop_monitors(sweep_monitor_procs)
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
) -> None:
    """Prepare the results dir: init layout, snapshot inputs, capture env + pretty names."""
    store.init_results_dir(results_dir)
    store.snapshot_manifest(results_dir, manifest_path)
    store.snapshot_tool(results_dir, tool_path, tool.name)

    env_meta = collect_env_metadata()
    if tool.env_probes:
        env_meta["probes"] = collect_probe_results(tool.env_probes, tool.setup_timeout_seconds)
    store.write_env_snapshot(results_dir, env_meta)

    pretty_path = results_dir / "pretty_names.json"
    existing = prettynames.load(pretty_path)
    merged = prettynames.merge(existing, prettynames.from_tool(tool))
    prettynames.write(pretty_path, merged)


def _run_groups(
    *,
    groups: list[WorkGroup],
    tool: ToolSpec,
    tool_version: str | None,
    per_sweep_vars: list[str] | None,
    manifest_dir: Path,
    results_dir: Path,
    executor: Executor,
    keep_raw: bool,
    skip_existing: bool,
    skip_counts: Counter,
    repeat: int,
    total: int,
    progress_stream,
) -> dict[str, int]:
    """Walk each per_sweep group: run per_sweep setup/teardown, then each rep."""
    counts = {"succeeded": 0, "failed": 0, "skipped": 0}
    sweep_idx = 0
    t_sweep = time.monotonic()
    base_env = {**os.environ}
    probed_hashes: set[str] = set()
    combo_probe_cache: dict[str, dict[str, Any]] = {}
    per_group_cmds = [m.command for m in tool.monitor if m.span == "per_group"]

    for group_key, items in groups:
        group_slug = (
            "-".join(slug_mod.slug(v) for v in group_key)
            if (per_sweep_vars and group_key is not None)
            else None
        )
        first_env_file = manifest_dir / items[0][0]["filename"]
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

        runnable_idx = [
            j
            for j, (entry, _r, is_warmup) in enumerate(items)
            if not (skip_existing and skip_counts[config_hash(entry["vars"])] >= repeat)
        ]
        if not runnable_idx:
            for entry, _r, _is_warmup in items:
                sweep_idx += 1
                counts["skipped"] += 1
                _progress(progress_stream, sweep_idx, total, t_sweep, "skip", config_hash(entry["vars"]))
            continue

        sweep_setup_failure, sweep_setup_records = (
            run_phase(
                "per_sweep_setup", tool.setup_per_sweep, phase_vars, base_env, tool.setup_timeout_seconds
            )
            if tool.setup_per_sweep
            else (None, [])
        )

        group_monitor_procs: list = []
        group_monitor_start_records: list[dict[str, Any]] = []
        group_monitor_stop_records: list[dict[str, Any]] = []

        sweep_teardown_failure: str | None = None
        sweep_teardown_records: list[dict[str, Any]] = []
        if sweep_setup_failure:
            sweep_teardown_failure, sweep_teardown_records = (
                run_phase(
                    "per_sweep_teardown",
                    tool.teardown_per_sweep,
                    phase_vars,
                    base_env,
                    tool.setup_timeout_seconds,
                )
                if tool.teardown_per_sweep
                else (None, [])
            )
            sweep_idx = _record_sweep_setup_failures(
                items=items,
                results_dir=results_dir,
                tool=tool,
                tool_version=tool_version,
                sweep_setup_failure=sweep_setup_failure,
                skip_existing=skip_existing,
                skip_counts=skip_counts,
                repeat=repeat,
                counts=counts,
                sweep_idx=sweep_idx,
                total=total,
                t_sweep=t_sweep,
                progress_stream=progress_stream,
            )
            _maybe_write_sweep_raw(
                results_dir,
                group_slug,
                sweep_setup_records,
                sweep_teardown_records,
                force=True,
            )
            continue

        if per_group_cmds:
            group_monitor_procs, group_monitor_start_records = run_monitors(
                per_group_cmds, phase_vars, base_env
            )

        last_runnable = runnable_idx[-1]
        try:
            for j, (entry, _r, is_warmup) in enumerate(items):
                sweep_idx += 1
                ch = config_hash(entry["vars"])
                if skip_existing and skip_counts[ch] >= repeat:
                    counts["skipped"] += 1
                    _progress(progress_stream, sweep_idx, total, t_sweep, "skip", ch)
                    continue

                _maybe_collect_combo_probes(
                    tool=tool,
                    entry=entry,
                    ch=ch,
                    manifest_dir=manifest_dir,
                    results_dir=results_dir,
                    probed_hashes=probed_hashes,
                    cache=combo_probe_cache,
                )

                try:
                    row = execute_one_rep(
                        entry=entry,
                        is_warmup=is_warmup,
                        tool=tool,
                        tool_version=tool_version,
                        manifest_dir=manifest_dir,
                        results_dir=results_dir,
                        executor=executor,
                        keep_raw=keep_raw,
                        probe_results=combo_probe_cache.get(ch, {}),
                    )
                except KeyboardInterrupt:
                    _progress(progress_stream, sweep_idx, total, t_sweep, "INT", ch)
                    raise

                if j == last_runnable:
                    if group_monitor_procs:
                        group_monitor_stop_records = stop_monitors(group_monitor_procs)
                        group_monitor_procs = []
                    if tool.teardown_per_sweep:
                        sweep_teardown_failure, sweep_teardown_records = run_phase(
                            "per_sweep_teardown",
                            tool.teardown_per_sweep,
                            phase_vars,
                            base_env,
                            tool.setup_timeout_seconds,
                        )
                        if sweep_teardown_failure:
                            prior = row.get("failure_reason")
                            row["failure_reason"] = (
                                f"{prior}; {sweep_teardown_failure}" if prior else sweep_teardown_failure
                            )

                store.append_run(results_dir, row)
                store.update_aggregates_for_hash(results_dir, ch)
                if row.get("failure_reason") or row.get("exit_code", -1) != 0:
                    counts["failed"] += 1
                    _progress(progress_stream, sweep_idx, total, t_sweep, "FAIL", ch)
                else:
                    counts["succeeded"] += 1
                    _progress(progress_stream, sweep_idx, total, t_sweep, "ok", ch)
        except KeyboardInterrupt:
            if group_monitor_procs:
                stop_monitors(group_monitor_procs)
            if tool.teardown_per_sweep:
                run_phase(
                    "per_sweep_teardown",
                    tool.teardown_per_sweep,
                    phase_vars,
                    base_env,
                    tool.setup_timeout_seconds,
                )
            raise

        _maybe_write_sweep_raw(
            results_dir,
            group_slug,
            sweep_setup_records,
            sweep_teardown_records,
            force=bool(sweep_teardown_failure or keep_raw),
            monitor_start=group_monitor_start_records,
            monitor_stop=group_monitor_stop_records,
        )

    return {
        "runs": total - counts["skipped"],
        "failed": counts["failed"],
        "skipped": counts["skipped"],
        "succeeded": counts["succeeded"],
    }


def _group_slug(group_key: tuple[str, ...] | None) -> str | None:
    if not group_key:
        return None
    return "-".join(slug_mod.slug(v) for v in group_key)


def _record_scope_setup_failures(
    *,
    items: list,
    results_dir: Any,
    tool: ToolSpec,
    tool_version: str | None,
    failure: str,
    skip_existing: bool,
    skip_counts: Counter,
    repeat: int,
    state: dict,
    progress_stream,
) -> dict[str, int]:
    """Record every rep in a scope group as failed when setup aborted."""
    counts = {"succeeded": 0, "failed": 0, "skipped": 0}
    from abench_speckz.runner._probes import config_hash
    from abench_speckz.runner._rep import make_row

    for entry, _r, is_warmup in items:
        state["sweep_idx"] += 1
        ch = config_hash(entry["vars"])
        if skip_existing and skip_counts[ch] >= repeat:
            counts["skipped"] += 1
            _progress(progress_stream, state["sweep_idx"], state["total"], state["t_sweep"], "skip", ch)
            continue
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
        counts["failed"] += 1
        _progress(progress_stream, state["sweep_idx"], state["total"], state["t_sweep"], "FAIL", ch)
    return counts


def _run_innermost_group_reps(
    *,
    items: list,
    phase_vars: dict,
    scope_teardown: list[str],
    tool: ToolSpec,
    tool_version: str | None,
    manifest_dir: Any,
    results_dir: Any,
    executor: Any,
    keep_raw: bool,
    skip_existing: bool,
    skip_counts: Counter,
    repeat: int,
    state: dict,
    base_env: dict,
    probed_hashes: set,
    combo_probe_cache: dict,
    per_group_cmds: list[str],
    runnable_idx: list[int],
    progress_stream,
) -> tuple[dict[str, int], str | None, list, dict]:
    """Run all reps for the innermost scope group.

    Mirrors the per-rep loop in ``_run_groups`` — teardown failure is folded into
    the last rep's row before it is written (same contract as the flat path).
    Returns ``(counts, teardown_failure, teardown_records, monitor_records)``.
    """
    from abench_speckz.runner._probes import config_hash

    counts = {"succeeded": 0, "failed": 0, "skipped": 0}
    teardown_failure: str | None = None
    teardown_records: list = []

    group_monitor_procs: list = []
    group_monitor_start_records: list = []
    group_monitor_stop_records: list = []
    if per_group_cmds:
        group_monitor_procs, group_monitor_start_records = run_monitors(per_group_cmds, phase_vars, base_env)

    last_runnable = runnable_idx[-1]
    try:
        for j, (entry, _r, is_warmup) in enumerate(items):
            state["sweep_idx"] += 1
            ch = config_hash(entry["vars"])
            if skip_existing and skip_counts[ch] >= repeat:
                counts["skipped"] += 1
                _progress(progress_stream, state["sweep_idx"], state["total"], state["t_sweep"], "skip", ch)
                continue

            _maybe_collect_combo_probes(
                tool=tool,
                entry=entry,
                ch=ch,
                manifest_dir=manifest_dir,
                results_dir=results_dir,
                probed_hashes=probed_hashes,
                cache=combo_probe_cache,
            )

            try:
                row = execute_one_rep(
                    entry=entry,
                    is_warmup=is_warmup,
                    tool=tool,
                    tool_version=tool_version,
                    manifest_dir=manifest_dir,
                    results_dir=results_dir,
                    executor=executor,
                    keep_raw=keep_raw,
                    probe_results=combo_probe_cache.get(ch, {}),
                )
            except KeyboardInterrupt:
                _progress(progress_stream, state["sweep_idx"], state["total"], state["t_sweep"], "INT", ch)
                raise

            if j == last_runnable:
                if group_monitor_procs:
                    group_monitor_stop_records = stop_monitors(group_monitor_procs)
                    group_monitor_procs = []
                if scope_teardown:
                    teardown_failure, teardown_records = run_phase(
                        "per_sweep_teardown",
                        scope_teardown,
                        phase_vars,
                        base_env,
                        tool.setup_timeout_seconds,
                    )
                    if teardown_failure:
                        prior = row.get("failure_reason")
                        row["failure_reason"] = f"{prior}; {teardown_failure}" if prior else teardown_failure

            store.append_run(results_dir, row)
            store.update_aggregates_for_hash(results_dir, ch)
            if row.get("failure_reason") or row.get("exit_code", -1) != 0:
                counts["failed"] += 1
                _progress(progress_stream, state["sweep_idx"], state["total"], state["t_sweep"], "FAIL", ch)
            else:
                counts["succeeded"] += 1
                _progress(progress_stream, state["sweep_idx"], state["total"], state["t_sweep"], "ok", ch)

    except KeyboardInterrupt:
        if group_monitor_procs:
            stop_monitors(group_monitor_procs)
        if scope_teardown:
            run_phase("per_sweep_teardown", scope_teardown, phase_vars, base_env, tool.setup_timeout_seconds)
        raise

    return (
        counts,
        teardown_failure,
        teardown_records,
        {
            "start": group_monitor_start_records,
            "stop": group_monitor_stop_records,
        },
    )


def _run_nested_groups(
    *,
    work: list,
    scopes: list[ResolvedScope],
    scope_idx: int,
    inherited_phase_vars: dict,
    tool: ToolSpec,
    tool_version: str | None,
    manifest_dir: Any,
    results_dir: Any,
    executor: Any,
    keep_raw: bool,
    skip_existing: bool,
    skip_counts: Counter,
    repeat: int,
    state: dict,
    base_env: dict,
    probed_hashes: set,
    combo_probe_cache: dict,
    per_group_cmds: list[str],
    progress_stream,
) -> dict[str, int]:
    """Recursively execute nested scope levels.

    At each scope level, work is partitioned into groups by that scope's vars.
    Setup/teardown for the scope run once per group; the next scope (or the actual
    reps, at the innermost level) runs inside each group.

    Teardown failures at the innermost scope are folded into the last rep's
    ``failure_reason`` (same contract as the flat path). Teardown failures at
    outer scopes are written to a raw file keyed by scope index and group slug.
    """
    from abench_speckz.runner._probes import config_hash

    scope = scopes[scope_idx]
    is_innermost = scope_idx == len(scopes) - 1
    counts = {"succeeded": 0, "failed": 0, "skipped": 0}

    for group_key, group_items in group_work(work, scope.vars):
        assert group_key is not None
        first_env_file = manifest_dir / group_items[0][0]["filename"]
        phase_vars: dict = {
            **inherited_phase_vars,
            **dict(zip(scope.vars, group_key, strict=True)),
            "_run_id": str(uuid.uuid4()),
            "_envfile": str(first_env_file.resolve()),
            "_results_dir": str(results_dir.resolve()),
        }

        runnable_idx = [
            j
            for j, (entry, _r, _) in enumerate(group_items)
            if not (skip_existing and skip_counts[config_hash(entry["vars"])] >= repeat)
        ]
        if not runnable_idx:
            for entry, _r, _ in group_items:
                state["sweep_idx"] += 1
                counts["skipped"] += 1
                _progress(
                    progress_stream,
                    state["sweep_idx"],
                    state["total"],
                    state["t_sweep"],
                    "skip",
                    config_hash(entry["vars"]),
                )
            continue

        setup_failure, setup_records = (
            run_phase("per_sweep_setup", scope.setup, phase_vars, base_env, tool.setup_timeout_seconds)
            if scope.setup
            else (None, [])
        )
        teardown_failure: str | None = None
        teardown_records: list = []

        if setup_failure:
            teardown_failure, teardown_records = (
                run_phase(
                    "per_sweep_teardown", scope.teardown, phase_vars, base_env, tool.setup_timeout_seconds
                )
                if scope.teardown
                else (None, [])
            )
            sub_counts = _record_scope_setup_failures(
                items=group_items,
                results_dir=results_dir,
                tool=tool,
                tool_version=tool_version,
                failure=setup_failure,
                skip_existing=skip_existing,
                skip_counts=skip_counts,
                repeat=repeat,
                state=state,
                progress_stream=progress_stream,
            )
            group_slug = _group_slug(group_key)
            scope_slug = f"scope{scope_idx}-{group_slug}" if group_slug else f"scope{scope_idx}"
            _maybe_write_scope_raw(results_dir, scope_slug, setup_records, teardown_records, force=True)

        elif is_innermost:
            sub_counts, teardown_failure, teardown_records, monitor_records = _run_innermost_group_reps(
                items=group_items,
                phase_vars=phase_vars,
                scope_teardown=scope.teardown,
                tool=tool,
                tool_version=tool_version,
                manifest_dir=manifest_dir,
                results_dir=results_dir,
                executor=executor,
                keep_raw=keep_raw,
                skip_existing=skip_existing,
                skip_counts=skip_counts,
                repeat=repeat,
                state=state,
                base_env=base_env,
                probed_hashes=probed_hashes,
                combo_probe_cache=combo_probe_cache,
                per_group_cmds=per_group_cmds,
                runnable_idx=runnable_idx,
                progress_stream=progress_stream,
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
            sub_counts = _run_nested_groups(
                work=group_items,
                scopes=scopes,
                scope_idx=scope_idx + 1,
                inherited_phase_vars=phase_vars,
                tool=tool,
                tool_version=tool_version,
                manifest_dir=manifest_dir,
                results_dir=results_dir,
                executor=executor,
                keep_raw=keep_raw,
                skip_existing=skip_existing,
                skip_counts=skip_counts,
                repeat=repeat,
                state=state,
                base_env=base_env,
                probed_hashes=probed_hashes,
                combo_probe_cache=combo_probe_cache,
                per_group_cmds=per_group_cmds,
                progress_stream=progress_stream,
            )
            teardown_failure, teardown_records = (
                run_phase(
                    "per_sweep_teardown", scope.teardown, phase_vars, base_env, tool.setup_timeout_seconds
                )
                if scope.teardown
                else (None, [])
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

        for k in sub_counts:
            counts[k] += sub_counts[k]

    return counts


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
        label = ", ".join(f"{v}={group_key[i]}" for i, v in enumerate(scope.vars))
        for k, template in enumerate(scope.setup):
            cmd = interpolate.resolve_string(template, phase_vars, env=os.environ)
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
        for k, template in enumerate(scope.teardown):
            cmd = interpolate.resolve_string(template, phase_vars, env=os.environ)
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


def _record_sweep_setup_failures(
    *,
    items: list,
    results_dir: Path,
    tool: ToolSpec,
    tool_version: str | None,
    sweep_setup_failure: str,
    skip_existing: bool,
    skip_counts: Counter,
    repeat: int,
    counts: dict[str, int],
    sweep_idx: int,
    total: int,
    t_sweep: float,
    progress_stream,
) -> int:
    """Per_sweep_setup failed: record every rep in this group as failed and return new sweep_idx."""
    for entry, _r, is_warmup in items:
        sweep_idx += 1
        ch = config_hash(entry["vars"])
        if skip_existing and skip_counts[ch] >= repeat:
            counts["skipped"] += 1
            _progress(progress_stream, sweep_idx, total, t_sweep, "skip", ch)
            continue
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
            failure_reason=sweep_setup_failure,
            probe_results={},
        )
        store.append_run(results_dir, row)
        store.update_aggregates_for_hash(results_dir, ch)
        counts["failed"] += 1
        _progress(progress_stream, sweep_idx, total, t_sweep, "FAIL", ch)
    return sweep_idx


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
        label = (
            ", " + ", ".join(f"{v}={group_key[i]}" for i, v in enumerate(per_sweep_vars))
            if (per_sweep_vars and group_key is not None)
            else ""
        )
        for k, template in enumerate(tool.setup_per_sweep):
            cmd = interpolate.resolve_string(template, phase_vars, env=os.environ)
            print(f"[per_sweep setup {k}{label}]: {cmd}", file=stream)
        for entry, rep, is_warmup in items:
            ch = config_hash(entry["vars"])
            cmd = interpolate.resolve_string(tool.command, entry["vars"], env=os.environ)
            tag = " [warmup]" if is_warmup else ""
            print(f"{ch} rep={rep}{tag}: {cmd}", file=stream)
        for k, template in enumerate(tool.teardown_per_sweep):
            cmd = interpolate.resolve_string(template, phase_vars, env=os.environ)
            print(f"[per_sweep teardown {k}{label}]: {cmd}", file=stream)


def _progress(stream, idx: int, total: int, t_start: float, status: str, ch: str) -> None:
    elapsed = time.monotonic() - t_start
    eta = (elapsed / idx) * (total - idx) if idx > 0 else 0
    print(
        f"[{idx:>4}/{total}] {status:<4} {ch}  elapsed={elapsed:5.1f}s eta={eta:5.1f}s",
        file=stream,
        flush=True,
    )


def _empty_summary() -> dict[str, int]:
    return {"runs": 0, "failed": 0, "skipped": 0, "succeeded": 0}
