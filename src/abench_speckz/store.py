"""Read/write the ``results/`` directory: runs.jsonl, aggregates.jsonl, snapshots, raw stdout."""
import dataclasses
import fcntl
import json
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from abench_speckz import aggregate
from abench_speckz.runspec import AggregateRow

SCHEMA_VERSION = 1

# Output content larger than this is written to a sidecar file and replaced
# with {"$ref": "raw/<name>.<field>"} so the raw JSON stays small for metadata
# queries that don't need the full output.
RAW_INLINE_THRESHOLD = 8 * 1024  # 8 KiB


@contextmanager
def lock_results_dir(results_dir: Path) -> Iterator[None]:
    results_dir.mkdir(parents=True, exist_ok=True)
    lock_path = results_dir / ".lock"
    with open(lock_path, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def init_results_dir(results_dir: Path) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "tools").mkdir(exist_ok=True)
    (results_dir / "raw").mkdir(exist_ok=True)
    schema_path = results_dir / ".schema_version"
    if not schema_path.exists():
        schema_path.write_text(f"{SCHEMA_VERSION}\n")


def append_run(results_dir: Path, row: dict[str, Any]) -> None:
    runs_path = results_dir / "runs.jsonl"
    with open(runs_path, "a") as f:
        f.write(json.dumps(row) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_runs(results_dir: Path) -> list[dict[str, Any]]:
    runs_path = results_dir / "runs.jsonl"
    if not runs_path.exists():
        return []
    return [json.loads(line) for line in runs_path.read_text().splitlines() if line.strip()]


def read_runs_for_hash(results_dir: Path, config_hash: str) -> list[dict[str, Any]]:
    return [r for r in read_runs(results_dir) if r.get("config_hash") == config_hash]


def write_aggregates_atomic(results_dir: Path, table: dict[str, list[AggregateRow]]) -> None:
    target = results_dir / "aggregates.jsonl"
    tmp = target.with_suffix(".jsonl.tmp")
    with open(tmp, "w") as f:
        for config_hash in sorted(table):
            for row in table[config_hash]:
                f.write(json.dumps(dataclasses.asdict(row)) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp, target)


def load_aggregates(results_dir: Path) -> dict[str, list[AggregateRow]]:
    path = results_dir / "aggregates.jsonl"
    if not path.exists():
        return {}
    table: dict[str, list[AggregateRow]] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        row = AggregateRow(**d)
        table.setdefault(row.config_hash, []).append(row)
    return table


def update_aggregates_for_hash(results_dir: Path, config_hash: str) -> None:
    """Recompute aggregates for one config_hash and rewrite the whole
    aggregates.jsonl atomically. The full rewrite keeps the file consistent
    if the sweep is interrupted mid-update; cost scales with total combos,
    not total reps, so it stays cheap in practice."""
    table = load_aggregates(results_dir)
    runs = read_runs_for_hash(results_dir, config_hash)
    rows = aggregate.compute_aggregate_rows(runs)
    if rows:
        table[config_hash] = rows
    else:
        table.pop(config_hash, None)
    write_aggregates_atomic(results_dir, table)


def rebuild_aggregates(results_dir: Path) -> int:
    runs = read_runs(results_dir)
    table = aggregate.rebuild_table(runs)
    write_aggregates_atomic(results_dir, table)
    return sum(len(v) for v in table.values())


def _maybe_spill(content: str, sidecar_path: Path, results_dir: Path) -> "str | dict[str, str]":
    """Return *content* inline when it fits within the threshold.

    Otherwise write it to *sidecar_path* and return a ``{"$ref": rel_path}``
    pointer where *rel_path* is relative to *results_dir*.
    """
    if len(content.encode()) <= RAW_INLINE_THRESHOLD:
        return content
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(content)
    return {"$ref": str(sidecar_path.relative_to(results_dir))}


def _spill_phases(
    doc: dict[str, Any], base_name: str, raw_dir: Path, results_dir: Path
) -> dict[str, Any]:
    """Spill stdout/stderr in setup/teardown step lists. Returns a new dict."""
    doc = dict(doc)
    for phase in ("setup", "teardown"):
        if not isinstance(doc.get(phase), list):
            continue
        steps = []
        for i, step in enumerate(doc[phase]):
            step = dict(step)
            for field in ("stdout", "stderr"):
                if isinstance(step.get(field), str):
                    step[field] = _maybe_spill(
                        step[field],
                        raw_dir / f"{base_name}.{phase}-{i}.{field}",
                        results_dir,
                    )
            steps.append(step)
        doc[phase] = steps
    return doc


def _resolve_refs(obj: Any, results_dir: Path) -> Any:
    """Recursively replace ``{"$ref": rel_path}`` leaves with the file's content."""
    if isinstance(obj, dict):
        if tuple(obj) == ("$ref",):
            ref_path = results_dir / obj["$ref"]
            return ref_path.read_text() if ref_path.exists() else None
        return {k: _resolve_refs(v, results_dir) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_refs(item, results_dir) for item in obj]
    return obj


def write_raw_run(results_dir: Path, run_id: str, raw: dict[str, Any]) -> Path:
    """Persist a structured raw record for a run.

    The document captures, when applicable: the tool's ``stdout`` and ``stderr``,
    an ``output_file`` block (path + content) when the tool wrote to a file, and
    ``setup`` / ``teardown`` arrays with one entry per step (command, exit_code,
    stdout, stderr).

    Any text field exceeding ``RAW_INLINE_THRESHOLD`` bytes is written to a
    sidecar file (``raw/{run_id}.{field}``) and replaced with a
    ``{"$ref": "raw/…"}`` pointer so metadata queries stay fast.  Use
    ``load_raw_run()`` to read the record with all pointers resolved.
    """
    target = results_dir / "raw" / f"{run_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    raw_dir = results_dir / "raw"

    doc = dict(raw)
    for field in ("stdout", "stderr"):
        if isinstance(doc.get(field), str):
            doc[field] = _maybe_spill(doc[field], raw_dir / f"{run_id}.{field}", results_dir)

    if isinstance(doc.get("output_file"), dict) and isinstance(
        doc["output_file"].get("content"), str
    ):
        doc["output_file"] = dict(doc["output_file"])
        doc["output_file"]["content"] = _maybe_spill(
            doc["output_file"]["content"],
            raw_dir / f"{run_id}.output_file",
            results_dir,
        )

    doc = _spill_phases(doc, run_id, raw_dir, results_dir)
    target.write_text(json.dumps(doc, indent=2))
    return target


def load_raw_run(results_dir: Path, run_id: str) -> "dict[str, Any] | None":
    """Load the raw record for *run_id*, resolving any spilled ``{"$ref": …}`` pointers."""
    target = results_dir / "raw" / f"{run_id}.json"
    if not target.exists():
        return None
    return _resolve_refs(json.loads(target.read_text()), results_dir)


def write_raw_sweep_phase(results_dir: Path, group_slug: str | None, raw: dict[str, Any]) -> Path:
    """Persist per_sweep setup/teardown records.

    Without a scoping variable (Mode A), the record goes to ``raw/sweep.json``.
    When ``per_sweep_var`` is set (Mode B), one file per distinct value is
    written as ``raw/sweep-{slug}.json``.

    Step stdout/stderr exceeding ``RAW_INLINE_THRESHOLD`` is spilled to sidecar
    files alongside the JSON (same convention as ``write_raw_run``).
    """
    base_name = "sweep" if group_slug is None else f"sweep-{group_slug}"
    target = results_dir / "raw" / f"{base_name}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    raw_dir = results_dir / "raw"
    doc = _spill_phases(raw, base_name, raw_dir, results_dir)
    target.write_text(json.dumps(doc, indent=2))
    return target


def snapshot_manifest(results_dir: Path, manifest_path: Path) -> Path:
    target = results_dir / "manifest.snapshot.json"
    if target.exists():
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = results_dir / f"manifest.snapshot.{ts}.json"
    shutil.copyfile(manifest_path, target)
    return target


def snapshot_tool(results_dir: Path, tool_path: Path, tool_name: str) -> Path:
    target = results_dir / "tools" / f"{tool_name}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(tool_path, target)
    return target


def write_env_snapshot(results_dir: Path, env: dict[str, Any]) -> Path:
    target = results_dir / "env.snapshot.json"
    target.write_text(json.dumps(env, indent=2, sort_keys=True) + "\n")
    return target
