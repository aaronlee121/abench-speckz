"""Plan a sweep: filter manifest entries, expand into reps, validate per_sweep config."""

from collections import Counter
from pathlib import Path
from typing import Any

from abench_speckz import interpolate, store
from abench_speckz.filters import make_filter
from abench_speckz.model import RunError
from abench_speckz.runspec import ToolSpec

# One unit of work: (manifest_entry, rep_index, is_warmup).
Work = tuple[dict[str, Any], int, bool]
# Contiguous reps sharing a per_sweep_var value; key is None for non-grouped sweeps.
WorkGroup = tuple[str | None, list[Work]]


def plan_sweep(
    manifest_entries: list[dict[str, Any]],
    repeat: int,
    warmup: int,
    where: list[str],
    require_tags: list[str],
    exclude_tags: list[str],
    per_sweep_var: str | None = None,
) -> list[Work]:
    """Select and order the manifest entries, then expand them into reps.

    When ``per_sweep_var`` is set, selected entries are stably sorted by the
    value of that variable so all reps for one value are contiguous — letting
    the runner fire per_sweep setup/teardown once per group.
    """
    fn = make_filter(where=where, require_tags=require_tags, exclude_tags=exclude_tags)
    selected = [e for e in manifest_entries if fn(e.get("vars", {}), e.get("tags", []))]
    if per_sweep_var is not None:
        selected = sorted(
            selected,
            key=lambda e: str((e.get("vars") or {}).get(per_sweep_var, "")),
        )
    work: list[Work] = []
    for entry in selected:
        for rep in range(warmup + repeat):
            work.append((entry, rep, rep < warmup))
    return work


def group_work(work: list[Work], per_sweep_var: str | None) -> list[WorkGroup]:
    """Partition the (already-sorted) work list into contiguous groups sharing
    ``per_sweep_var``'s value. With no scoping var, returns a single group
    keyed by ``None``."""
    if per_sweep_var is None:
        return [(None, list(work))]
    groups: list[WorkGroup] = []
    current_key: str | None = None
    current_items: list[Work] = []
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


_SYNTHETIC_PER_SWEEP = frozenset({"_envfile", "_run_id"})


def validate_per_sweep(tool: ToolSpec, selected_entries: list[dict[str, Any]]) -> None:
    """Reject configs that would error mid-sweep: per_sweep steps referencing
    disallowed combo vars, or a ``per_sweep_var`` missing from some combos."""
    if not (tool.setup_per_sweep or tool.teardown_per_sweep) and tool.per_sweep_var is None:
        return
    allowed = ({tool.per_sweep_var} if tool.per_sweep_var else set()) | _SYNTHETIC_PER_SWEEP
    for phase_name, steps in [
        ("setup_per_sweep", tool.setup_per_sweep),
        ("teardown_per_sweep", tool.teardown_per_sweep),
    ]:
        for i, step in enumerate(steps):
            for ref in interpolate.find_combo_var_refs(step):
                if ref in allowed:
                    continue
                if tool.per_sweep_var:
                    suffix = (
                        f"only ${{{tool.per_sweep_var}}}, ${{_envfile}}, ${{_run_id}} "
                        f"are allowed (set via per_sweep_var)"
                    )
                else:
                    suffix = "per_sweep steps may only reference ${_envfile}, ${_run_id}, or ${env:VAR}"
                raise RunError(f"tool.{phase_name}[{i}] references combo var ${{{ref}}}; {suffix}")
    if tool.per_sweep_var:
        missing = [e for e in selected_entries if tool.per_sweep_var not in (e.get("vars") or {})]
        if missing:
            raise RunError(
                f"tool.per_sweep_var={tool.per_sweep_var!r} not present in {len(missing)} selected combo(s)"
            )


def existing_counts(results_dir: Path) -> Counter:
    """Count successful, non-warmup runs already in the results dir, keyed by config_hash."""
    runs = store.read_runs(results_dir)
    return Counter(
        r["config_hash"] for r in runs if r.get("exit_code") == 0 and "warmup" not in (r.get("tags") or [])
    )
