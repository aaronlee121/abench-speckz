"""Orchestrate a benchmark sweep: plan reps, wrap each rep in setup/teardown, persist results."""
import hashlib
import json
import os
import platform
import shlex
import subprocess
import sys
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from abench_speckz import extract, interpolate, prettynames, store, tools
from abench_speckz import slug as slug_mod
from abench_speckz.executor import Executor, LocalSubprocessExecutor
from abench_speckz.filters import make_filter
from abench_speckz.model import RunError
from abench_speckz.runspec import RunRequest, ToolSpec


def config_hash(vars_: dict[str, Any]) -> str:
    canonical = json.dumps(vars_, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def load_manifest(path: Path) -> tuple[Path, list[dict[str, Any]]]:
    if path.is_dir():
        manifest_path = path / "manifest.json"
    else:
        manifest_path = path
    if not manifest_path.exists():
        raise RunError(f"manifest not found: {manifest_path}")
    data = json.loads(manifest_path.read_text())
    if not isinstance(data, list):
        raise RunError(f"{manifest_path}: expected an array")
    return manifest_path, data


def load_envfile(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v
    return out


def collect_env_metadata() -> dict[str, Any]:
    info: dict[str, Any] = {
        "host": platform.node(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
    }
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        if result.returncode == 0:
            info["git_sha"] = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return info


def collect_combo_probe_results(
    probes: dict[str, str],
    vars_: dict[str, Any],
    env: dict[str, str],
    timeout_seconds: int,
) -> dict[str, Any]:
    """Run combo-scoped probe commands once per unique config hash.

    Commands are interpolated against combo vars before execution. Non-zero
    exit, missing command, timeout, or interpolation failure stores None for
    that key — probes never abort a sweep.
    """
    results: dict[str, Any] = {}
    for key, template in probes.items():
        try:
            command = interpolate.resolve_string(template, vars_, env=env)
            result = subprocess.run(
                shlex.split(command),
                env=env,
                capture_output=True, text=True,
                timeout=timeout_seconds, check=False,
            )
            results[key] = result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            results[key] = None
    return results


def collect_probe_results(env_probes: dict[str, str], timeout_seconds: int) -> dict[str, Any]:
    """Run each probe command once and return key → stripped stdout (or None on failure)."""
    results: dict[str, Any] = {}
    for key, command in env_probes.items():
        try:
            result = subprocess.run(
                shlex.split(command),
                capture_output=True, text=True,
                timeout=timeout_seconds, check=False,
            )
            results[key] = result.stdout.strip() if result.returncode == 0 else None
        except (FileNotFoundError, subprocess.TimeoutExpired):
            results[key] = None
    return results


def plan_sweep(
    manifest_entries: list[dict[str, Any]],
    repeat: int,
    warmup: int,
    where: list[str],
    require_tags: list[str],
    exclude_tags: list[str],
    per_sweep_var: str | None = None,
) -> list[tuple[dict[str, Any], int, bool]]:
    """Returns list of (entry, rep_idx, is_warmup).

    When ``per_sweep_var`` is set, selected entries are stably sorted by the
    value of that variable so all reps for one value are contiguous — letting
    the runner fire per_sweep setup/teardown once per group.
    """
    fn = make_filter(where=where, require_tags=require_tags, exclude_tags=exclude_tags)
    selected = [e for e in manifest_entries if fn(e.get("vars", {}), e.get("tags", []))]
    if per_sweep_var is not None:
        selected = sorted(selected, key=lambda e: str((e.get("vars") or {}).get(per_sweep_var, "")))
    work: list[tuple[dict[str, Any], int, bool]] = []
    for entry in selected:
        for rep in range(warmup + repeat):
            work.append((entry, rep, rep < warmup))
    return work


def _group_work(
    work: list[tuple[dict[str, Any], int, bool]],
    per_sweep_var: str | None,
) -> list[tuple[str | None, list[tuple[dict[str, Any], int, bool]]]]:
    """Partition the (already-sorted) work list into contiguous groups sharing
    ``per_sweep_var``'s value. With no scoping var, returns a single group
    keyed by ``None``."""
    if per_sweep_var is None:
        return [(None, list(work))]
    groups: list[tuple[str | None, list[tuple[dict[str, Any], int, bool]]]] = []
    current_key: str | None = None
    current_items: list[tuple[dict[str, Any], int, bool]] = []
    started = False
    for entry, rep, is_warmup in work:
        k = str(entry["vars"][per_sweep_var])
        if not started or k != current_key:
            if current_items:
                groups.append((current_key, current_items))
            current_key = k
            current_items = []
            started = True
        current_items.append((entry, rep, is_warmup))
    if current_items:
        groups.append((current_key, current_items))
    return groups


def _validate_per_sweep(tool: ToolSpec, selected_entries: list[dict[str, Any]]) -> None:
    """Reject configs that would error mid-sweep: per_sweep steps referencing
    disallowed combo vars, or a ``per_sweep_var`` missing from some combos."""
    if not (tool.setup_per_sweep or tool.teardown_per_sweep) and tool.per_sweep_var is None:
        return
    allowed = {tool.per_sweep_var} if tool.per_sweep_var else set()
    for phase_name, steps in [
        ("setup_per_sweep", tool.setup_per_sweep),
        ("teardown_per_sweep", tool.teardown_per_sweep),
    ]:
        for i, step in enumerate(steps):
            for ref in interpolate.find_combo_var_refs(step):
                if ref in allowed:
                    continue
                if tool.per_sweep_var:
                    suffix = f"only ${{{tool.per_sweep_var}}} is allowed (set via per_sweep_var)"
                else:
                    suffix = "per_sweep steps may only reference ${env:VAR}"
                raise RunError(
                    f"tool.{phase_name}[{i}] references combo var ${{{ref}}}; {suffix}"
                )
    if tool.per_sweep_var:
        missing = [
            e for e in selected_entries
            if tool.per_sweep_var not in (e.get("vars") or {})
        ]
        if missing:
            raise RunError(
                f"tool.per_sweep_var={tool.per_sweep_var!r} not present in "
                f"{len(missing)} selected combo(s)"
            )


def existing_counts(results_dir: Path) -> Counter:
    runs = store.read_runs(results_dir)
    return Counter(
        r["config_hash"] for r in runs
        if r.get("exit_code") == 0 and "warmup" not in (r.get("tags") or [])
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
        return {"runs": 0, "failed": 0, "skipped": 0, "succeeded": 0}

    _validate_per_sweep(tool, [e for (e, _, _) in work])
    groups = _group_work(work, tool.per_sweep_var)

    if dry_run:
        _dry_run_preview(groups, tool, progress_stream)
        return {"runs": 0, "failed": 0, "skipped": 0, "succeeded": 0}

    with store.lock_results_dir(results_dir):
        store.init_results_dir(results_dir)
        store.snapshot_manifest(results_dir, actual_manifest_path)
        store.snapshot_tool(results_dir, tool_path, tool.name)
        env_meta = collect_env_metadata()
        if tool.env_probes:
            env_meta["probes"] = collect_probe_results(tool.env_probes, tool.setup_timeout_seconds)
        store.write_env_snapshot(results_dir, env_meta)
        _persist_pretty_names(results_dir, tool)

        tool_version = tools.resolve_tool_version(tool)

        skip_counts = existing_counts(results_dir) if skip_existing else Counter()

        succeeded = failed = skipped = 0
        total = len(work)
        sweep_idx = 0
        t_sweep = time.monotonic()
        base_env = {**os.environ}
        probed_hashes: set[str] = set()
        combo_probe_cache: dict[str, dict[str, Any]] = {}

        for group_key, items in groups:
            group_slug = slug_mod.slug(group_key) if (tool.per_sweep_var and group_key is not None) else None
            phase_vars: dict[str, Any] = {tool.per_sweep_var: group_key} if tool.per_sweep_var else {}

            runnable_idx = [
                j for j, (entry, _r, is_warmup) in enumerate(items)
                if not (skip_existing and not is_warmup and skip_counts[config_hash(entry["vars"])] >= repeat)
            ]
            if not runnable_idx:
                for entry, _r, _is_warmup in items:
                    sweep_idx += 1
                    skipped += 1
                    _progress(progress_stream, sweep_idx, total, t_sweep, "skip", config_hash(entry["vars"]))
                continue

            sweep_setup_failure: str | None = None
            sweep_setup_records: list[dict[str, Any]] = []
            sweep_teardown_failure: str | None = None
            sweep_teardown_records: list[dict[str, Any]] = []

            if tool.setup_per_sweep:
                sweep_setup_failure, sweep_setup_records = run_phase(
                    "per_sweep_setup", tool.setup_per_sweep, phase_vars, base_env, tool.setup_timeout_seconds,
                )

            if sweep_setup_failure:
                if tool.teardown_per_sweep:
                    sweep_teardown_failure, sweep_teardown_records = run_phase(
                        "per_sweep_teardown", tool.teardown_per_sweep, phase_vars, base_env, tool.setup_timeout_seconds,
                    )
                for entry, _r, is_warmup in items:
                    sweep_idx += 1
                    ch = config_hash(entry["vars"])
                    if skip_existing and not is_warmup and skip_counts[ch] >= repeat:
                        skipped += 1
                        _progress(progress_stream, sweep_idx, total, t_sweep, "skip", ch)
                        continue
                    tags = list(entry.get("tags") or [])
                    if is_warmup:
                        tags.append("warmup")
                    row = _make_row(
                        run_id=str(uuid.uuid4()), entry=entry, ch=ch, tags=tags, tool=tool, tool_version=tool_version,
                        command="<not-run>", result=None, parsed={}, failure_reason=sweep_setup_failure,
                        probe_results={},
                    )
                    store.append_run(results_dir, row)
                    store.update_aggregates_for_hash(results_dir, ch)
                    failed += 1
                    _progress(progress_stream, sweep_idx, total, t_sweep, "FAIL", ch)
                _maybe_write_sweep_raw(results_dir, group_slug, sweep_setup_records, sweep_teardown_records, force=True)
                continue

            last_runnable = runnable_idx[-1]
            try:
                for j, (entry, _r, is_warmup) in enumerate(items):
                    sweep_idx += 1
                    ch = config_hash(entry["vars"])
                    if skip_existing and not is_warmup and skip_counts[ch] >= repeat:
                        skipped += 1
                        _progress(progress_stream, sweep_idx, total, t_sweep, "skip", ch)
                        continue
                    if tool.combo_probes and ch not in probed_hashes:
                        env_file = manifest_dir / entry["filename"]
                        probe_vars = {**entry["vars"], "_envfile": str(env_file.resolve())}
                        probe_env = (
                            {**os.environ, **load_envfile(env_file)} if env_file.exists()
                            else {**os.environ, **{k: str(v) for k, v in entry["vars"].items()}}
                        )
                        combo_probe_cache[ch] = collect_combo_probe_results(
                            tool.combo_probes, probe_vars, probe_env, tool.setup_timeout_seconds,
                        )
                        store.update_combo_probes(results_dir, ch, combo_probe_cache[ch])
                        probed_hashes.add(ch)

                    try:
                        row = _execute_one_rep(
                            entry=entry, is_warmup=is_warmup, tool=tool, tool_version=tool_version,
                            manifest_dir=manifest_dir, results_dir=results_dir,
                            executor=executor, keep_raw=keep_raw,
                            probe_results=combo_probe_cache.get(ch, {}),
                        )
                    except KeyboardInterrupt:
                        _progress(progress_stream, sweep_idx, total, t_sweep, "INT", ch)
                        raise

                    if j == last_runnable and tool.teardown_per_sweep:
                        sweep_teardown_failure, sweep_teardown_records = run_phase(
                            "per_sweep_teardown", tool.teardown_per_sweep, phase_vars, base_env, tool.setup_timeout_seconds,
                        )
                        if sweep_teardown_failure:
                            prior = row.get("failure_reason")
                            row["failure_reason"] = (
                                f"{prior}; {sweep_teardown_failure}" if prior else sweep_teardown_failure
                            )

                    store.append_run(results_dir, row)
                    store.update_aggregates_for_hash(results_dir, ch)
                    if row.get("failure_reason") or row.get("exit_code", -1) != 0:
                        failed += 1
                        _progress(progress_stream, sweep_idx, total, t_sweep, "FAIL", ch)
                    else:
                        succeeded += 1
                        _progress(progress_stream, sweep_idx, total, t_sweep, "ok", ch)
            except KeyboardInterrupt:
                if tool.teardown_per_sweep:
                    run_phase(
                        "per_sweep_teardown", tool.teardown_per_sweep, phase_vars, base_env, tool.setup_timeout_seconds,
                    )
                raise

            _maybe_write_sweep_raw(
                results_dir, group_slug, sweep_setup_records, sweep_teardown_records,
                force=bool(sweep_teardown_failure or keep_raw),
            )

        return {"runs": total - skipped, "failed": failed, "skipped": skipped, "succeeded": succeeded}


def run_phase(
    phase: str,
    steps: list[str],
    vars_: dict[str, Any],
    env: dict[str, str],
    timeout_seconds: int,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Run a list of shell-command steps.

    Returns (failure_reason, step_records). failure_reason is None on success,
    otherwise 'phase[i]: <reason>'. step_records is a list of dicts with
    ``command``, ``exit_code``, ``stdout``, ``stderr`` for each step that was
    attempted (including the failing one when the failure came from the
    subprocess). Interpolation failures produce no record for that step.
    """
    records: list[dict[str, Any]] = []
    for i, template in enumerate(steps):
        try:
            cmd = interpolate.resolve_string(template, vars_, env=env)
        except Exception as e:
            return f"{phase}[{i}]: interpolation: {e}", records
        try:
            result = subprocess.run(
                shlex.split(cmd),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            records.append({
                "command": cmd,
                "exit_code": -1,
                "stdout": _to_str(e.stdout),
                "stderr": _to_str(e.stderr),
            })
            return f"{phase}[{i}]: timeout after {timeout_seconds}s: {cmd}", records
        except FileNotFoundError as e:
            records.append({"command": cmd, "exit_code": -1, "stdout": "", "stderr": str(e)})
            return f"{phase}[{i}]: command_not_found: {e}", records
        records.append({
            "command": cmd,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        })
        if result.returncode != 0:
            tail = (result.stderr or result.stdout or "").strip().splitlines()
            hint = tail[-1] if tail else ""
            return f"{phase}[{i}]: exit_code={result.returncode}: {cmd}" + (f" ({hint})" if hint else ""), records
    return None, records


def _to_str(buf: Any) -> str:
    if buf is None:
        return ""
    if isinstance(buf, bytes):
        return buf.decode(errors="replace")
    return buf


def _execute_one_rep(
    *,
    entry: dict[str, Any],
    is_warmup: bool,
    tool: ToolSpec,
    tool_version: str | None,
    manifest_dir: Path,
    results_dir: Path,
    executor: Executor,
    keep_raw: bool,
    probe_results: dict[str, Any],
) -> dict[str, Any]:
    """Run one rep end-to-end (per_rep setup → tool → per_rep teardown) and
    return the row dict. Does NOT append to runs.jsonl — the caller appends
    after optionally folding in a per_sweep_teardown failure."""
    run_id = str(uuid.uuid4())
    ch = config_hash(entry["vars"])
    tags = list(entry.get("tags") or [])
    if is_warmup:
        tags.append("warmup")

    env_file = manifest_dir / entry["filename"]
    combo_vars = {**entry["vars"], "_envfile": str(env_file.resolve()), "_run_id": run_id}

    try:
        cmd = interpolate.resolve_string(tool.command, combo_vars, env=os.environ)
    except Exception as e:
        return _make_row(
            run_id=run_id, entry=entry, ch=ch, tags=tags, tool=tool, tool_version=tool_version,
            command="<unresolved>", result=None, parsed={}, failure_reason=f"interpolation: {e}",
            probe_results=probe_results,
        )

    env = (
        {**os.environ, **load_envfile(env_file)} if env_file.exists()
        else {**os.environ, **{k: str(v) for k, v in entry["vars"].items()}}
    )

    request = RunRequest(
        config_hash=ch, vars={k: str(v) for k, v in entry["vars"].items()},
        tags=tags, tool=tool.name, tool_version=tool_version,
        command=cmd, env=env, timeout_seconds=tool.timeout_seconds,
    )

    setup_failure: str | None = None
    setup_steps: list[dict[str, Any]] = []
    if tool.setup:
        setup_failure, setup_steps = run_phase(
            "setup", tool.setup, combo_vars, env, tool.setup_timeout_seconds,
        )

    if setup_failure:
        teardown_steps: list[dict[str, Any]] = []
        if tool.teardown:
            _, teardown_steps = run_phase(
                "teardown", tool.teardown, combo_vars, env, tool.setup_timeout_seconds,
            )
        row = _make_row(
            run_id=run_id, entry=entry, ch=ch, tags=tags, tool=tool, tool_version=tool_version,
            command=cmd, result=None, parsed={}, failure_reason=setup_failure,
            probe_results=probe_results,
        )
        raw_path = store.write_raw_run(
            results_dir, row["run_id"],
            _build_raw(result=None, output_file_record=None,
                       setup=setup_steps, teardown=teardown_steps, post_run=[]),
        )
        row["raw_output_path"] = str(raw_path.relative_to(results_dir))
        return row

    result = None
    teardown_failure: str | None = None
    teardown_steps = []
    try:
        result = executor.execute(request)
    finally:
        if tool.teardown:
            teardown_failure, teardown_steps = run_phase(
                "teardown", tool.teardown, combo_vars, env, tool.setup_timeout_seconds,
            )

    post_run_failure: str | None = None
    post_run_steps: list[dict[str, Any]] = []
    if tool.post_run:
        post_run_vars = {
            **combo_vars,
            "_started_at": result.started_at,
            "_finished_at": result.finished_at,
            "_duration_ms": str(result.duration_ms),
            "_exit_code": str(result.exit_code),
        }
        post_run_failure, post_run_steps = run_phase(
            "post_run", tool.post_run, post_run_vars, env, tool.setup_timeout_seconds,
        )

    parsed: dict[str, Any] = {}
    extract_failure: str | None = None
    output_file_record: dict[str, Any] | None = None
    if result.exit_code == 0:
        output_for_extraction = result.stdout
        if tool.output_file:
            try:
                out_path = interpolate.resolve_string(tool.output_file, combo_vars, env=os.environ)
                output_for_extraction = Path(out_path).read_text()
                output_file_record = {"path": out_path, "content": output_for_extraction}
            except FileNotFoundError:
                extract_failure = f"output_file: not found: {out_path}"
                output_file_record = {"path": out_path, "content": None}
            except Exception as e:
                extract_failure = f"output_file: {type(e).__name__}: {e}"
                output_file_record = {"path": out_path, "content": None}
        if extract_failure is None:
            parsed, extract_failure = extract.apply(tool, output_for_extraction)

    failure_reason = result.failure_reason or extract_failure
    if teardown_failure:
        failure_reason = f"{failure_reason}; {teardown_failure}" if failure_reason else teardown_failure
    if post_run_failure:
        failure_reason = f"{failure_reason}; {post_run_failure}" if failure_reason else post_run_failure

    row = _make_row(
        run_id=run_id, entry=entry, ch=ch, tags=tags, tool=tool, tool_version=tool_version,
        command=cmd, result=result, parsed=parsed, failure_reason=failure_reason,
        probe_results=probe_results,
    )

    if keep_raw or extract_failure or result.exit_code != 0 or teardown_failure or post_run_failure:
        raw_path = store.write_raw_run(
            results_dir, row["run_id"],
            _build_raw(result=result, output_file_record=output_file_record,
                       setup=setup_steps, teardown=teardown_steps, post_run=post_run_steps),
        )
        row["raw_output_path"] = str(raw_path.relative_to(results_dir))

    return row


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
    groups: list[tuple[str | None, list[tuple[dict[str, Any], int, bool]]]],
    tool: ToolSpec,
    stream,
) -> None:
    for group_key, items in groups:
        phase_vars: dict[str, Any] = {tool.per_sweep_var: group_key} if tool.per_sweep_var else {}
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


def _build_raw(
    *,
    result,
    output_file_record: dict[str, Any] | None,
    setup: list[dict[str, Any]],
    teardown: list[dict[str, Any]],
    post_run: list[dict[str, Any]],
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "stdout": result.stdout if result is not None else "",
        "stderr": result.stderr if result is not None else "",
    }
    if output_file_record is not None:
        raw["output_file"] = output_file_record
    if setup:
        raw["setup"] = setup
    if teardown:
        raw["teardown"] = teardown
    if post_run:
        raw["post_run"] = post_run
    return raw


def _make_row(*, run_id, entry, ch, tags, tool: ToolSpec, tool_version, command, result, parsed, failure_reason, probe_results):
    row = {
        "run_id": run_id,
        "config_hash": ch,
        "vars": {k: str(v) for k, v in entry["vars"].items()},
        "tags": tags,
        "tool": tool.name,
        "tool_version": tool_version,
        "command": command,
        "exit_code": result.exit_code if result is not None else -1,
        "duration_ms": result.duration_ms if result is not None else 0,
        "started_at": result.started_at if result is not None else None,
        "finished_at": result.finished_at if result is not None else None,
        "results": parsed,
        "combo_probes": probe_results,
        "failure_reason": failure_reason,
        "raw_output_path": None,
    }
    return row


def _persist_pretty_names(results_dir: Path, tool: ToolSpec) -> None:
    path = results_dir / "pretty_names.json"
    existing = prettynames.load(path)
    merged = prettynames.merge(existing, prettynames.from_tool(tool))
    prettynames.write(path, merged)


def _progress(stream, idx: int, total: int, t_start: float, status: str, ch: str) -> None:
    elapsed = time.monotonic() - t_start
    eta = (elapsed / idx) * (total - idx) if idx > 0 else 0
    print(f"[{idx:>4}/{total}] {status:<4} {ch}  elapsed={elapsed:5.1f}s eta={eta:5.1f}s",
          file=stream, flush=True)
