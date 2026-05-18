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
# Contiguous reps sharing a per_sweep_var group key; key is None for non-grouped sweeps.
WorkGroup = tuple[tuple[str, ...] | None, list[Work]]


def resolve_per_sweep_vars(
    raw: str | list[str] | None,
    all_var_names: list[str],
) -> list[str] | None:
    """Normalize per_sweep_var to a resolved list[str] | None.

    Entries prefixed with '!' exclude that variable from the auto-expanded set,
    mirroring the group_by negation syntax used in plots.
    """
    if raw is None:
        return None
    keys: list[str] = [raw] if isinstance(raw, str) else list(raw)
    if not any(k.startswith("!") for k in keys):
        return keys
    excluded = {k[1:] for k in keys if k.startswith("!")}
    positives = [k for k in keys if not k.startswith("!")]
    expanded = [v for v in all_var_names if v not in excluded and v not in positives]
    return positives + expanded


def plan_sweep(
    manifest_entries: list[dict[str, Any]],
    repeat: int,
    warmup: int,
    where: list[str],
    require_tags: list[str],
    exclude_tags: list[str],
    per_sweep_vars: list[str] | None = None,
) -> list[Work]:
    """Select and order the manifest entries, then expand them into reps.

    When ``per_sweep_vars`` is set, selected entries are stably sorted by the
    composite key of those variables so all reps for one combination are
    contiguous — letting the runner fire per_sweep setup/teardown once per group.
    """
    fn = make_filter(where=where, require_tags=require_tags, exclude_tags=exclude_tags)
    selected = [e for e in manifest_entries if fn(e.get("vars", {}), e.get("tags", []))]
    if per_sweep_vars is not None:
        selected = sorted(
            selected,
            key=lambda e: tuple(str((e.get("vars") or {}).get(v, "")) for v in per_sweep_vars),
        )
    work: list[Work] = []
    for entry in selected:
        for rep in range(warmup + repeat):
            work.append((entry, rep, rep < warmup))
    return work


def group_work(work: list[Work], per_sweep_vars: list[str] | None) -> list[WorkGroup]:
    """Partition the (already-sorted) work list into contiguous groups sharing
    the composite ``per_sweep_vars`` value. With no scoping vars, returns a
    single group keyed by ``None``."""
    if per_sweep_vars is None:
        return [(None, list(work))]
    groups: list[WorkGroup] = []
    current_key: tuple[str, ...] | None = None
    current_items: list[Work] = []
    started = False
    for entry, rep, is_warmup in work:
        k = tuple(str(entry["vars"][v]) for v in per_sweep_vars)
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


def validate_per_sweep(
    tool: ToolSpec,
    selected_entries: list[dict[str, Any]],
    per_sweep_vars: list[str] | None,
) -> None:
    """Reject configs that would error mid-sweep: per_sweep steps referencing
    disallowed combo vars, or a ``per_sweep_var`` missing from some combos."""
    if not (tool.setup_per_sweep or tool.teardown_per_sweep) and per_sweep_vars is None:
        return
    allowed = (set(per_sweep_vars) if per_sweep_vars else set()) | _SYNTHETIC_PER_SWEEP
    for phase_name, steps in [
        ("setup_per_sweep", tool.setup_per_sweep),
        ("teardown_per_sweep", tool.teardown_per_sweep),
    ]:
        for i, step in enumerate(steps):
            for ref in interpolate.find_combo_var_refs(step):
                if ref in allowed:
                    continue
                if per_sweep_vars:
                    allowed_names = ", ".join(f"${{{v}}}" for v in per_sweep_vars)
                    suffix = (
                        f"only {allowed_names}, ${{_envfile}}, ${{_run_id}} "
                        f"are allowed (set via per_sweep_var)"
                    )
                else:
                    suffix = "per_sweep steps may only reference ${_envfile}, ${_run_id}, or ${env:VAR}"
                raise RunError(f"tool.{phase_name}[{i}] references combo var ${{{ref}}}; {suffix}")
    if per_sweep_vars:
        for var in per_sweep_vars:
            missing = [e for e in selected_entries if var not in (e.get("vars") or {})]
            if missing:
                raise RunError(
                    f"tool.per_sweep_var={var!r} not present in {len(missing)} selected combo(s)"
                )


def existing_counts(results_dir: Path) -> Counter:
    """Count successful, non-warmup runs already in the results dir, keyed by config_hash."""
    runs = store.read_runs(results_dir)
    return Counter(
        r["config_hash"] for r in runs if r.get("exit_code") == 0 and "warmup" not in (r.get("tags") or [])
    )
