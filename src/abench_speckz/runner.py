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


def plan_sweep(
    manifest_entries: list[dict[str, Any]],
    repeat: int,
    warmup: int,
    where: list[str],
    require_tags: list[str],
    exclude_tags: list[str],
) -> list[tuple[dict[str, Any], int, bool]]:
    """Returns list of (entry, rep_idx, is_warmup)."""
    fn = make_filter(where=where, require_tags=require_tags, exclude_tags=exclude_tags)
    selected = [e for e in manifest_entries if fn(e.get("vars", {}), e.get("tags", []))]
    work: list[tuple[dict[str, Any], int, bool]] = []
    for entry in selected:
        for rep in range(warmup + repeat):
            work.append((entry, rep, rep < warmup))
    return work


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

    work = plan_sweep(manifest_entries, repeat, warmup, where, require_tags, exclude_tags)
    if not work:
        print("no combos selected after filters", file=progress_stream)
        return {"runs": 0, "failed": 0, "skipped": 0, "succeeded": 0}

    if dry_run:
        for entry, rep, is_warmup in work:
            ch = config_hash(entry["vars"])
            cmd = interpolate.resolve_string(tool.command, entry["vars"], env=os.environ)
            tag = " [warmup]" if is_warmup else ""
            print(f"{ch} rep={rep}{tag}: {cmd}", file=progress_stream)
        return {"runs": 0, "failed": 0, "skipped": 0, "succeeded": 0}

    with store.lock_results_dir(results_dir):
        store.init_results_dir(results_dir)
        store.snapshot_manifest(results_dir, actual_manifest_path)
        store.snapshot_tool(results_dir, tool_path, tool.name)
        store.write_env_snapshot(results_dir, collect_env_metadata())
        _persist_pretty_names(results_dir, tool)

        tool_version = tools.resolve_tool_version(tool)

        skip_counts = existing_counts(results_dir) if skip_existing else Counter()

        succeeded = failed = skipped = 0
        total = len(work)
        t_sweep = time.monotonic()

        for i, (entry, _rep, is_warmup) in enumerate(work, start=1):
            ch = config_hash(entry["vars"])
            if skip_existing and not is_warmup and skip_counts[ch] >= repeat:
                skipped += 1
                _progress(progress_stream, i, total, t_sweep, "skip", ch)
                continue

            tags = list(entry.get("tags") or [])
            if is_warmup:
                tags.append("warmup")

            try:
                cmd = interpolate.resolve_string(tool.command, entry["vars"], env=os.environ)
            except Exception as e:
                row = _make_row(
                    entry=entry, ch=ch, tags=tags, tool=tool, tool_version=tool_version,
                    command="<unresolved>", result=None, parsed={}, failure_reason=f"interpolation: {e}",
                )
                store.append_run(results_dir, row)
                failed += 1
                _progress(progress_stream, i, total, t_sweep, "FAIL", ch)
                continue

            env_file = manifest_dir / entry["filename"]
            env = {**os.environ, **load_envfile(env_file)} if env_file.exists() else {**os.environ, **{k: str(v) for k, v in entry["vars"].items()}}

            request = RunRequest(
                config_hash=ch, vars={k: str(v) for k, v in entry["vars"].items()},
                tags=tags, tool=tool.name, tool_version=tool_version,
                command=cmd, env=env, timeout_seconds=tool.timeout_seconds,
            )

            setup_failure: str | None = None
            if tool.setup:
                setup_failure = run_phase(
                    "setup", tool.setup, entry["vars"], env, tool.setup_timeout_seconds,
                )

            if setup_failure:
                if tool.teardown:
                    run_phase("teardown", tool.teardown, entry["vars"], env, tool.setup_timeout_seconds)
                row = _make_row(
                    entry=entry, ch=ch, tags=tags, tool=tool, tool_version=tool_version,
                    command=cmd, result=None, parsed={}, failure_reason=setup_failure,
                )
                store.append_run(results_dir, row)
                store.update_aggregates_for_hash(results_dir, ch)
                failed += 1
                _progress(progress_stream, i, total, t_sweep, "FAIL", ch)
                continue

            result = None
            teardown_failure: str | None = None
            try:
                try:
                    result = executor.execute(request)
                except KeyboardInterrupt:
                    _progress(progress_stream, i, total, t_sweep, "INT", ch)
                    raise
            finally:
                if tool.teardown:
                    teardown_failure = run_phase(
                        "teardown", tool.teardown, entry["vars"], env, tool.setup_timeout_seconds,
                    )

            parsed: dict[str, Any] = {}
            extract_failure: str | None = None
            raw_output = result.stdout
            if result.exit_code == 0:
                output_for_extraction = result.stdout
                if tool.output_file:
                    try:
                        out_path = interpolate.resolve_string(tool.output_file, entry["vars"], env=os.environ)
                        output_for_extraction = Path(out_path).read_text()
                        raw_output = output_for_extraction
                    except FileNotFoundError:
                        extract_failure = f"output_file: not found: {out_path}"
                    except Exception as e:
                        extract_failure = f"output_file: {type(e).__name__}: {e}"
                if extract_failure is None:
                    parsed, extract_failure = extract.apply(tool, output_for_extraction)

            failure_reason = result.failure_reason or extract_failure
            if teardown_failure:
                failure_reason = f"{failure_reason}; {teardown_failure}" if failure_reason else teardown_failure

            row = _make_row(
                entry=entry, ch=ch, tags=tags, tool=tool, tool_version=tool_version,
                command=cmd, result=result, parsed=parsed, failure_reason=failure_reason,
            )

            if keep_raw or extract_failure or result.exit_code != 0:
                raw_path = store.write_raw_stdout(results_dir, row["run_id"], raw_output)
                row["raw_output_path"] = str(raw_path.relative_to(results_dir))

            store.append_run(results_dir, row)
            store.update_aggregates_for_hash(results_dir, ch)

            if failure_reason or result.exit_code != 0:
                failed += 1
                _progress(progress_stream, i, total, t_sweep, "FAIL", ch)
            else:
                succeeded += 1
                _progress(progress_stream, i, total, t_sweep, "ok", ch)

        return {"runs": total - skipped, "failed": failed, "skipped": skipped, "succeeded": succeeded}


def run_phase(
    phase: str,
    steps: list[str],
    vars_: dict[str, Any],
    env: dict[str, str],
    timeout_seconds: int,
) -> str | None:
    """Run a list of shell-command steps. Returns None on success, else 'phase[i]: <reason>'."""
    for i, template in enumerate(steps):
        try:
            cmd = interpolate.resolve_string(template, vars_, env=env)
        except Exception as e:
            return f"{phase}[{i}]: interpolation: {e}"
        try:
            result = subprocess.run(
                shlex.split(cmd),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return f"{phase}[{i}]: timeout after {timeout_seconds}s: {cmd}"
        except FileNotFoundError as e:
            return f"{phase}[{i}]: command_not_found: {e}"
        if result.returncode != 0:
            tail = (result.stderr or result.stdout or "").strip().splitlines()
            hint = tail[-1] if tail else ""
            return f"{phase}[{i}]: exit_code={result.returncode}: {cmd}" + (f" ({hint})" if hint else "")
    return None


def _make_row(*, entry, ch, tags, tool: ToolSpec, tool_version, command, result, parsed, failure_reason):
    row = {
        "run_id": str(uuid.uuid4()),
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
