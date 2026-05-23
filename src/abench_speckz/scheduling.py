"""Scheduler protocol + built-in implementations.

A Scheduler decides, after each finished rep, what to run next inside a
single per_sweep / scope group: another rep of the same combo, the first
rep of the next combo, or stop. This is the seam future benchmark-specific
planner plugins hang off (same ``next(state) -> WorkItem | None`` shape).

Two built-ins ship:

- ``FixedScheduler``: warmup + repeat reps per entry, in input order.
  Reproduces the historical pre-flattened plan.
- ``CITargetScheduler``: stops a combo once each named metric's relative
  CI half-width is within the target, bounded by ``[min_repeat, max_repeat]``.

The runner owns a single ``SweepState`` per group and pushes finished rep
rows into it via ``state.record(ch, row)`` between calls to ``scheduler.next``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from abench_speckz import aggregate
from abench_speckz.runner._probes import config_hash
from abench_speckz.runspec import AggregateRow, ToolSpec


@dataclass(frozen=True)
class WorkItem:
    """One rep to execute."""

    entry: dict[str, Any]
    is_warmup: bool


class SweepState:
    """In-memory view of per-combo run history within the current group.

    Seeded from prior on-disk runs so the first ``scheduler.next()`` call
    sees an accurate picture under ``--skip-existing`` or partial resume.
    Updated incrementally via ``record()`` after each rep — the scheduler
    reads back ``aggregates_for(ch)``, ``attempt_count(ch)``,
    ``success_count(ch)`` to make its next decision.
    """

    def __init__(self, seed_rows: Iterable[dict[str, Any]] | None = None) -> None:
        self._rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._agg_cache: dict[str, list[AggregateRow] | None] = {}
        for row in seed_rows or ():
            ch = row.get("config_hash")
            if not ch:
                continue
            self._rows[ch].append(row)
            self._agg_cache[ch] = None

    def record(self, ch: str, row: dict[str, Any]) -> None:
        """Add a just-finished rep row to the in-memory history."""
        self._rows[ch].append(row)
        self._agg_cache[ch] = None

    def attempt_count(self, ch: str) -> int:
        """Non-warmup reps attempted (successful + failed)."""
        return sum(1 for r in self._rows.get(ch, ()) if "warmup" not in (r.get("tags") or []))

    def success_count(self, ch: str) -> int:
        """Non-warmup reps with exit_code 0 and a non-empty results dict."""
        return sum(
            1
            for r in self._rows.get(ch, ())
            if "warmup" not in (r.get("tags") or []) and r.get("exit_code") == 0 and r.get("results")
        )

    def aggregates_for(self, ch: str) -> list[AggregateRow]:
        """Per-metric aggregate rows for *ch*; cached and invalidated on record()."""
        cached = self._agg_cache.get(ch)
        if cached is not None:
            return cached
        rows = aggregate.compute_aggregate_rows(self._rows.get(ch, []))
        self._agg_cache[ch] = rows
        return rows


class Scheduler(Protocol):
    """Decides the next rep within one per_sweep group."""

    def next(self, state: SweepState) -> WorkItem | None: ...

    def validate(self, tool: ToolSpec) -> None:
        """Reject configurations the scheduler cannot fulfill (e.g. target
        metric not in tool.capture). Default is a no-op."""

    def expected_max_reps(self) -> int:
        """Upper bound on total reps the scheduler will yield. Used for
        progress display only — over-estimating is fine."""

    def runnable_entries(self) -> list[dict[str, Any]]:
        """Entries the scheduler would actually issue at least one rep for
        (i.e. excluding those skipped via ``--skip-existing``). Used to
        detect empty groups and to enumerate failure rows when per_sweep
        setup aborts before any rep runs."""

    def planned_warmup_for(self, entry: dict[str, Any]) -> int:
        """Warmup-rep count for *entry*. Used only for the per_sweep
        setup-failure path (one synthetic failed row per planned rep)."""

    def planned_measured_for(self, entry: dict[str, Any]) -> int:
        """Measured-rep count for *entry* in the setup-failure path. For
        dynamic schedulers, this is the guaranteed minimum (``min_repeat``)."""


class FixedScheduler:
    """Yields ``warmup + repeat`` reps per entry, in input order.

    Matches the historical pre-flattened plan. When ``skip_existing`` is
    set, entries already at or above ``repeat`` successful reps are
    skipped entirely.
    """

    def __init__(
        self,
        entries: list[dict[str, Any]],
        *,
        warmup: int,
        repeat: int,
        skip_counts: dict[str, int] | None = None,
        skip_existing: bool = False,
    ) -> None:
        self._entries = list(entries)
        self._warmup = warmup
        self._repeat = repeat
        self._skip = dict(skip_counts or {})
        self._skip_existing = skip_existing
        self._cursor = 0
        self._warmup_done = 0
        self._measured_done = 0

    def validate(self, tool: ToolSpec) -> None:
        return

    def runnable_entries(self) -> list[dict[str, Any]]:
        return [
            e
            for e in self._entries
            if not (self._skip_existing and self._skip.get(config_hash(e["vars"]), 0) >= self._repeat)
        ]

    def expected_max_reps(self) -> int:
        return len(self.runnable_entries()) * (self._warmup + self._repeat)

    def planned_warmup_for(self, entry: dict[str, Any]) -> int:
        return self._warmup

    def planned_measured_for(self, entry: dict[str, Any]) -> int:
        return self._repeat

    def next(self, state: SweepState) -> WorkItem | None:
        while self._cursor < len(self._entries):
            entry = self._entries[self._cursor]
            ch = config_hash(entry["vars"])
            if self._skip_existing and self._skip.get(ch, 0) >= self._repeat:
                self._advance()
                continue
            if self._warmup_done < self._warmup:
                self._warmup_done += 1
                return WorkItem(entry=entry, is_warmup=True)
            if self._measured_done < self._repeat:
                self._measured_done += 1
                return WorkItem(entry=entry, is_warmup=False)
            self._advance()
        return None

    def _advance(self) -> None:
        self._cursor += 1
        self._warmup_done = 0
        self._measured_done = 0


@dataclass(frozen=True)
class CITarget:
    """One ``metric=relative_threshold`` convergence target."""

    metric: str
    threshold: float


class CITargetScheduler:
    """Stop each combo once every target metric's CI half-width is within
    ``threshold * |mean|``, bounded by ``[min_repeat, max_repeat]``.

    Convergence is checked on **successful** measured reps (failures
    count toward the max-repeat cap but do not contribute to aggregates).
    All targets must converge before the combo is stopped.
    """

    def __init__(
        self,
        entries: list[dict[str, Any]],
        targets: list[CITarget],
        *,
        min_repeat: int,
        max_repeat: int,
        warmup: int = 0,
        skip_counts: dict[str, int] | None = None,
        skip_existing: bool = False,
    ) -> None:
        if not targets:
            raise ValueError("CITargetScheduler requires at least one target")
        if min_repeat < 2:
            raise ValueError("min_repeat must be >= 2 (CI needs at least 2 samples)")
        if max_repeat < min_repeat:
            raise ValueError("max_repeat must be >= min_repeat")
        self._entries = list(entries)
        self._targets = list(targets)
        self._min = min_repeat
        self._max = max_repeat
        self._warmup = warmup
        self._skip = dict(skip_counts or {})
        self._skip_existing = skip_existing
        self._cursor = 0
        self._warmup_done = 0

    def validate(self, tool: ToolSpec) -> None:
        if tool.parser:
            # Parser output is opaque; we can't statically check.
            return
        captured = set(tool.capture or {})
        # Strip trailing "[]" used by extract.apply to collect lists.
        captured = {k[:-2] if k.endswith("[]") else k for k in captured}
        missing = [t.metric for t in self._targets if t.metric not in captured]
        if missing:
            raise ValueError(f"--ci-target references metric(s) not in tool.capture: {', '.join(missing)}")

    def runnable_entries(self) -> list[dict[str, Any]]:
        return [
            e
            for e in self._entries
            if not (self._skip_existing and self._skip.get(config_hash(e["vars"]), 0) >= self._max)
        ]

    def expected_max_reps(self) -> int:
        return len(self.runnable_entries()) * (self._warmup + self._max)

    def planned_warmup_for(self, entry: dict[str, Any]) -> int:
        return self._warmup

    def planned_measured_for(self, entry: dict[str, Any]) -> int:
        # Guaranteed minimum on the setup-failure path; the actual run could
        # have gone up to max_repeat, but min is what we'd definitely have done.
        return self._min

    def next(self, state: SweepState) -> WorkItem | None:
        while self._cursor < len(self._entries):
            entry = self._entries[self._cursor]
            ch = config_hash(entry["vars"])
            if self._skip_existing and self._skip.get(ch, 0) >= self._max:
                self._advance()
                continue
            if self._warmup_done < self._warmup:
                self._warmup_done += 1
                return WorkItem(entry=entry, is_warmup=True)
            attempts = state.attempt_count(ch)
            if attempts >= self._max:
                self._advance()
                continue
            successes = state.success_count(ch)
            if successes >= self._min and self._converged(state, ch):
                self._advance()
                continue
            return WorkItem(entry=entry, is_warmup=False)
        return None

    def _converged(self, state: SweepState, ch: str) -> bool:
        rows_by_metric = {r.metric: r for r in state.aggregates_for(ch)}
        for tgt in self._targets:
            row = rows_by_metric.get(tgt.metric)
            if row is None or row.mean is None or row.ci95_low is None or row.ci95_high is None:
                return False
            half_width = (row.ci95_high - row.ci95_low) / 2.0
            denom = abs(row.mean)
            if denom == 0:
                # Zero mean: only "converged" if half_width is also zero.
                if half_width != 0:
                    return False
                continue
            if (half_width / denom) > tgt.threshold:
                return False
        return True

    def _advance(self) -> None:
        self._cursor += 1
        self._warmup_done = 0


def parse_ci_target(spec: str) -> CITarget:
    """Parse a ``METRIC=THRESHOLD`` CLI fragment into a CITarget."""
    if "=" not in spec:
        raise ValueError(f"--ci-target expects METRIC=THRESHOLD, got: {spec!r}")
    metric, _, raw = spec.partition("=")
    metric = metric.strip()
    if not metric:
        raise ValueError(f"--ci-target metric name is empty in {spec!r}")
    try:
        threshold = float(raw)
    except ValueError as e:
        raise ValueError(f"--ci-target threshold must be numeric in {spec!r}") from e
    if threshold <= 0:
        raise ValueError(f"--ci-target threshold must be > 0, got {threshold}")
    return CITarget(metric=metric, threshold=threshold)


def make_scheduler(
    *,
    entries: list[dict[str, Any]],
    warmup: int,
    repeat: int,
    ci_targets: list[CITarget] | None,
    min_repeat: int,
    max_repeat: int,
    skip_counts: dict[str, int] | None = None,
    skip_existing: bool = False,
) -> Scheduler:
    """Pick FixedScheduler vs CITargetScheduler based on whether targets were given."""
    if ci_targets:
        return CITargetScheduler(
            entries,
            ci_targets,
            min_repeat=min_repeat,
            max_repeat=max_repeat,
            warmup=warmup,
            skip_counts=skip_counts,
            skip_existing=skip_existing,
        )
    return FixedScheduler(
        entries,
        warmup=warmup,
        repeat=repeat,
        skip_counts=skip_counts,
        skip_existing=skip_existing,
    )
