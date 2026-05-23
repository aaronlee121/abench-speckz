"""Execute a single rep end-to-end and build the resulting ``runs.jsonl`` row."""

import os
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from abench_speckz import extract, interpolate, store
from abench_speckz.executor import Executor
from abench_speckz.runner._manifest import load_envfile
from abench_speckz.runner._monitors import run_monitors, stop_monitors
from abench_speckz.runner._phases import run_phase
from abench_speckz.runner._probes import config_hash
from abench_speckz.runspec import RunRequest, ToolSpec


def execute_one_rep(
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
    """Run one rep (per_rep setup → tool → monitors stop → teardown → post_run)
    and return the row dict.

    Does NOT append to runs.jsonl — the caller appends after optionally folding
    in a per_sweep_teardown failure.
    """
    run_id = str(uuid.uuid4())
    ch = config_hash(entry["vars"])
    tags = list(entry.get("tags") or [])
    if is_warmup:
        tags.append("warmup")

    env_file = manifest_dir / entry["filename"]
    combo_vars = {
        **entry["vars"],
        "_envfile": str(env_file.resolve()),
        "_run_id": run_id,
        "_results_dir": str(results_dir.resolve()),
    }

    try:
        cmd = interpolate.resolve_string(tool.command, combo_vars, env=os.environ)
    except Exception as e:
        return make_row(
            run_id=run_id,
            entry=entry,
            ch=ch,
            tags=tags,
            tool=tool,
            tool_version=tool_version,
            command="<unresolved>",
            result=None,
            parsed={},
            failure_reason=f"interpolation: {e}",
            probe_results=probe_results,
        )

    env = _build_env(entry, env_file)
    request = RunRequest(
        config_hash=ch,
        vars={k: str(v) for k, v in entry["vars"].items()},
        tags=tags,
        tool=tool.name,
        tool_version=tool_version,
        command=cmd,
        env=env,
        timeout_seconds=tool.timeout_seconds,
    )

    setup_failure, setup_steps = (
        run_phase("setup", tool.setup, combo_vars, env, tool.setup_timeout_seconds)
        if tool.setup
        else (None, [])
    )

    teardown_steps: list[dict[str, Any]] = []
    if setup_failure:
        _, teardown_steps = (
            run_phase("teardown", tool.teardown, combo_vars, env, tool.setup_timeout_seconds)
            if tool.teardown
            else (None, [])
        )
        row = make_row(
            run_id=run_id,
            entry=entry,
            ch=ch,
            tags=tags,
            tool=tool,
            tool_version=tool_version,
            command=cmd,
            result=None,
            parsed={},
            failure_reason=setup_failure,
            probe_results=probe_results,
        )
        raw_path = store.write_raw_run(
            results_dir,
            row["run_id"],
            build_raw(
                result=None, output_file_record=None, setup=setup_steps, teardown=teardown_steps, post_run=[]
            ),
        )
        row["raw_output_path"] = str(raw_path.relative_to(results_dir))
        return row

    monitor_procs: list[subprocess.Popen] = []
    monitor_start_records: list[dict[str, Any]] = []
    monitor_stop_records: list[dict[str, Any]] = []
    per_rep_cmds = [m.command for m in tool.monitor if m.span == "per_rep"]
    if per_rep_cmds:
        monitor_procs, monitor_start_records = run_monitors(per_rep_cmds, combo_vars, env)

    result = None
    teardown_failure: str | None = None
    try:
        result = executor.execute(request)
    finally:
        if monitor_procs:
            monitor_stop_records = stop_monitors(monitor_procs)
        if tool.teardown:
            teardown_failure, teardown_steps = run_phase(
                "teardown",
                tool.teardown,
                combo_vars,
                env,
                tool.setup_timeout_seconds,
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
            "post_run",
            tool.post_run,
            post_run_vars,
            env,
            tool.setup_timeout_seconds,
        )

    parsed, extract_failure, output_file_record = _extract_metrics(
        tool=tool,
        result=result,
        combo_vars=combo_vars,
    )

    primary = (result.failure_reason if result else None) or extract_failure
    failure_reason = _join_failures(primary, teardown_failure, post_run_failure)

    row = make_row(
        run_id=run_id,
        entry=entry,
        ch=ch,
        tags=tags,
        tool=tool,
        tool_version=tool_version,
        command=cmd,
        result=result,
        parsed=parsed,
        failure_reason=failure_reason,
        probe_results=probe_results,
    )

    monitor_start_failure = any(r.get("error") for r in monitor_start_records)
    should_write_raw = (
        keep_raw
        or extract_failure
        or result.exit_code != 0
        or teardown_failure
        or post_run_failure
        or monitor_start_failure
    )
    if should_write_raw:
        raw_path = store.write_raw_run(
            results_dir,
            row["run_id"],
            build_raw(
                result=result,
                output_file_record=output_file_record,
                setup=setup_steps,
                teardown=teardown_steps,
                post_run=post_run_steps,
                monitor_start=monitor_start_records,
                monitor_stop=monitor_stop_records,
            ),
        )
        row["raw_output_path"] = str(raw_path.relative_to(results_dir))

    return row


def make_row(
    *,
    run_id: str,
    entry: dict[str, Any],
    ch: str,
    tags: list[str],
    tool: ToolSpec,
    tool_version: str | None,
    command: str,
    result,
    parsed: dict[str, Any],
    failure_reason: str | None,
    probe_results: dict[str, Any],
) -> dict[str, Any]:
    """Build a ``runs.jsonl`` row. ``result`` may be ``None`` when the benchmark didn't run."""
    row: dict[str, Any] = {
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
    if tool.grafana_links:
        row["grafana_links"] = _resolve_grafana_links(tool, entry["vars"], run_id, result)
    return row


def _iso_to_unix_ms(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return str(int(dt.timestamp() * 1000))
    except (ValueError, TypeError):
        return ""


def _resolve_grafana_links(
    tool: ToolSpec,
    entry_vars: dict[str, Any],
    run_id: str,
    result,
) -> list[dict[str, str | None]]:
    """Resolve grafana_links URL templates for this run. Returns list of {label, url} dicts."""
    started_at = result.started_at if result is not None else None
    finished_at = result.finished_at if result is not None else None
    link_vars: dict[str, str] = {
        **{k: str(v) for k, v in entry_vars.items()},
        "_run_id": run_id,
        "_started_at": started_at or "",
        "_finished_at": finished_at or "",
        "_started_at_ms": _iso_to_unix_ms(started_at),
        "_finished_at_ms": _iso_to_unix_ms(finished_at),
    }
    resolved: list[dict[str, str | None]] = []
    for link in tool.grafana_links:
        try:
            url = interpolate.resolve_string(link["url"], link_vars, env=os.environ)
        except Exception:
            url = None
        resolved.append({"label": link["label"], "url": url})
    return resolved


def build_raw(
    *,
    result,
    output_file_record: dict[str, Any] | None,
    setup: list[dict[str, Any]],
    teardown: list[dict[str, Any]],
    post_run: list[dict[str, Any]],
    monitor_start: list[dict[str, Any]] | None = None,
    monitor_stop: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the document persisted to ``raw/{run_id}.json``."""
    raw: dict[str, Any] = {
        "stdout": result.stdout if result is not None else "",
        "stderr": result.stderr if result is not None else "",
    }
    if output_file_record is not None:
        raw["output_file"] = output_file_record
    if setup:
        raw["setup"] = setup
    if monitor_start:
        raw["monitor_start"] = monitor_start
    if monitor_stop:
        raw["monitor_stop"] = monitor_stop
    if teardown:
        raw["teardown"] = teardown
    if post_run:
        raw["post_run"] = post_run
    return raw


def _build_env(entry: dict[str, Any], env_file: Path) -> dict[str, str]:
    """Compose the subprocess environment from the env-file (or var dict fallback)."""
    if env_file.exists():
        return {**os.environ, **load_envfile(env_file)}
    return {**os.environ, **{k: str(v) for k, v in entry["vars"].items()}}


def _extract_metrics(
    *,
    tool: ToolSpec,
    result,
    combo_vars: dict[str, Any],
) -> tuple[dict[str, Any], str | None, dict[str, Any] | None]:
    """Resolve output_file (if any), then apply the tool's capture/parser rules."""
    if result is None or result.exit_code != 0:
        return {}, None, None

    output_for_extraction = result.stdout
    output_file_record: dict[str, Any] | None = None
    if tool.output_file:
        try:
            out_path = interpolate.resolve_string(tool.output_file, combo_vars, env=os.environ)
            output_for_extraction = Path(out_path).read_text()
            output_file_record = {"path": out_path, "content": output_for_extraction}
        except FileNotFoundError:
            return {}, f"output_file: not found: {out_path}", {"path": out_path, "content": None}
        except Exception as e:
            return {}, f"output_file: {type(e).__name__}: {e}", {"path": out_path, "content": None}

    parsed, extract_failure = extract.apply(tool, output_for_extraction)
    return parsed, extract_failure, output_file_record


def _join_failures(*reasons: str | None) -> str | None:
    """Append non-empty trailing reasons to the first non-empty one with ``'; '``."""
    parts = [r for r in reasons if r]
    return "; ".join(parts) if parts else None
