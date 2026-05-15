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
from abench_speckz.runner._phases import run_phase
from abench_speckz.runner._planning import (
    WorkGroup,
    existing_counts,
    group_work,
    plan_sweep,
    validate_per_sweep,
)
from abench_speckz.runner._probes import collect_probe_results, config_hash
from abench_speckz.runner._rep import build_raw, execute_one_rep, make_row
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

    work = plan_sweep(
        manifest_entries, repeat, warmup, where, require_tags, exclude_tags,
        per_sweep_var=tool.per_sweep_var,
    )
    if not work:
        print("no combos selected after filters", file=progress_stream)
        return _empty_summary()

    validate_per_sweep(tool, [e for (e, _, _) in work])
    groups = group_work(work, tool.per_sweep_var)

    if dry_run:
        _dry_run_preview(groups, tool, progress_stream, manifest_dir)
        return _empty_summary()

    with store.lock_results_dir(results_dir):
        _init_sweep(results_dir, actual_manifest_path, tool_path, tool)
        tool_version = tools.resolve_tool_version(tool)
        skip_counts = existing_counts(results_dir) if skip_existing else Counter()
        return _run_groups(
            groups=groups, tool=tool, tool_version=tool_version,
            manifest_dir=manifest_dir, results_dir=results_dir,
            executor=executor, keep_raw=keep_raw,
            skip_existing=skip_existing, skip_counts=skip_counts,
            repeat=repeat, total=len(work), progress_stream=progress_stream,
        )


def _init_sweep(
    results_dir: Path, manifest_path: Path, tool_path: Path, tool: ToolSpec,
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

    for group_key, items in groups:
        group_slug = (
            slug_mod.slug(group_key)
            if (tool.per_sweep_var and group_key is not None) else None
        )
        first_env_file = manifest_dir / items[0][0]["filename"]
        phase_vars: dict[str, Any] = {
            **({tool.per_sweep_var: group_key} if tool.per_sweep_var else {}),
            "_run_id": str(uuid.uuid4()),
            "_envfile": str(first_env_file.resolve()),
        }

        runnable_idx = [
            j for j, (entry, _r, is_warmup) in enumerate(items)
            if not (
                skip_existing and not is_warmup
                and skip_counts[config_hash(entry["vars"])] >= repeat
            )
        ]
        if not runnable_idx:
            for entry, _r, _is_warmup in items:
                sweep_idx += 1
                counts["skipped"] += 1
                _progress(progress_stream, sweep_idx, total, t_sweep,
                          "skip", config_hash(entry["vars"]))
            continue

        sweep_setup_failure, sweep_setup_records = (
            run_phase("per_sweep_setup", tool.setup_per_sweep, phase_vars,
                      base_env, tool.setup_timeout_seconds)
            if tool.setup_per_sweep else (None, [])
        )

        if sweep_setup_failure:
            sweep_teardown_failure, sweep_teardown_records = (
                run_phase("per_sweep_teardown", tool.teardown_per_sweep, phase_vars,
                          base_env, tool.setup_timeout_seconds)
                if tool.teardown_per_sweep else (None, [])
            )
            sweep_idx = _record_sweep_setup_failures(
                items=items, results_dir=results_dir, tool=tool, tool_version=tool_version,
                sweep_setup_failure=sweep_setup_failure,
                skip_existing=skip_existing, skip_counts=skip_counts, repeat=repeat,
                counts=counts, sweep_idx=sweep_idx, total=total,
                t_sweep=t_sweep, progress_stream=progress_stream,
            )
            _maybe_write_sweep_raw(
                results_dir, group_slug, sweep_setup_records, sweep_teardown_records,
                force=True,
            )
            continue

        sweep_teardown_failure: str | None = None
        sweep_teardown_records: list[dict[str, Any]] = []
        last_runnable = runnable_idx[-1]
        try:
            for j, (entry, _r, is_warmup) in enumerate(items):
                sweep_idx += 1
                ch = config_hash(entry["vars"])
                if skip_existing and not is_warmup and skip_counts[ch] >= repeat:
                    counts["skipped"] += 1
                    _progress(progress_stream, sweep_idx, total, t_sweep, "skip", ch)
                    continue

                _maybe_collect_combo_probes(
                    tool=tool, entry=entry, ch=ch,
                    manifest_dir=manifest_dir, results_dir=results_dir,
                    probed_hashes=probed_hashes, cache=combo_probe_cache,
                )

                try:
                    row = execute_one_rep(
                        entry=entry, is_warmup=is_warmup, tool=tool,
                        tool_version=tool_version,
                        manifest_dir=manifest_dir, results_dir=results_dir,
                        executor=executor, keep_raw=keep_raw,
                        probe_results=combo_probe_cache.get(ch, {}),
                    )
                except KeyboardInterrupt:
                    _progress(progress_stream, sweep_idx, total, t_sweep, "INT", ch)
                    raise

                if j == last_runnable and tool.teardown_per_sweep:
                    sweep_teardown_failure, sweep_teardown_records = run_phase(
                        "per_sweep_teardown", tool.teardown_per_sweep, phase_vars,
                        base_env, tool.setup_timeout_seconds,
                    )
                    if sweep_teardown_failure:
                        prior = row.get("failure_reason")
                        row["failure_reason"] = (
                            f"{prior}; {sweep_teardown_failure}" if prior
                            else sweep_teardown_failure
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
            if tool.teardown_per_sweep:
                run_phase("per_sweep_teardown", tool.teardown_per_sweep, phase_vars,
                          base_env, tool.setup_timeout_seconds)
            raise

        _maybe_write_sweep_raw(
            results_dir, group_slug, sweep_setup_records, sweep_teardown_records,
            force=bool(sweep_teardown_failure or keep_raw),
        )

    return {
        "runs": total - counts["skipped"],
        "failed": counts["failed"],
        "skipped": counts["skipped"],
        "succeeded": counts["succeeded"],
    }


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
    probe_vars = {**entry["vars"], "_envfile": str(env_file.resolve())}
    probe_env = (
        {**os.environ, **load_envfile(env_file)} if env_file.exists()
        else {**os.environ, **{k: str(v) for k, v in entry["vars"].items()}}
    )
    cache[ch] = collect_probe_results(
        tool.combo_probes, tool.setup_timeout_seconds,
        vars_=probe_vars, env=probe_env,
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
        if skip_existing and not is_warmup and skip_counts[ch] >= repeat:
            counts["skipped"] += 1
            _progress(progress_stream, sweep_idx, total, t_sweep, "skip", ch)
            continue
        tags = list(entry.get("tags") or [])
        if is_warmup:
            tags.append("warmup")
        row = make_row(
            run_id=str(uuid.uuid4()), entry=entry, ch=ch, tags=tags,
            tool=tool, tool_version=tool_version,
            command="<not-run>", result=None, parsed={},
            failure_reason=sweep_setup_failure, probe_results={},
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
) -> None:
    if not force:
        return
    raw: dict[str, Any] = {}
    if setup_records:
        raw["setup"] = setup_records
    if teardown_records:
        raw["teardown"] = teardown_records
    if raw:
        store.write_raw_sweep_phase(results_dir, group_slug, raw)


def _dry_run_preview(
    groups: list[WorkGroup], tool: ToolSpec, stream, manifest_dir: Path,
) -> None:
    for group_key, items in groups:
        first_env_file = manifest_dir / items[0][0]["filename"]
        phase_vars: dict[str, Any] = {
            **({tool.per_sweep_var: group_key} if tool.per_sweep_var else {}),
            "_run_id": str(uuid.uuid4()),
            "_envfile": str(first_env_file.resolve()),
        }
        label = f", {tool.per_sweep_var}={group_key}" if tool.per_sweep_var else ""
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
        file=stream, flush=True,
    )


def _empty_summary() -> dict[str, int]:
    return {"runs": 0, "failed": 0, "skipped": 0, "succeeded": 0}
