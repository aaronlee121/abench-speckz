"""Orchestrate a benchmark sweep: plan reps, wrap each rep in setup/teardown, persist results.

The runner is split across private submodules:

- ``_manifest``  — load manifest.json / env-files; capture host metadata.
- ``_probes``    — config_hash + env/combo probe execution.
- ``_phases``    — run_phase: sequential shell-command steps with structured records.
- ``_monitors``  — background side-car processes started before the benchmark.
- ``_planning``  — expand manifest entries into reps; group; validate per_sweep config.
- ``_rep``       — execute one rep (setup → tool → teardown → post_run); build the row.
- ``_sweep``     — top-level ``execute_sweep`` orchestrator.

The public surface (re-exported here) is intentionally minimal.
"""
from abench_speckz.runner._manifest import (
    collect_env_metadata,
    load_envfile,
    load_manifest,
)
from abench_speckz.runner._phases import run_phase
from abench_speckz.runner._planning import plan_sweep
from abench_speckz.runner._probes import collect_probe_results, config_hash
from abench_speckz.runner._sweep import execute_sweep

__all__ = [
    "collect_env_metadata",
    "collect_probe_results",
    "config_hash",
    "execute_sweep",
    "load_envfile",
    "load_manifest",
    "plan_sweep",
    "run_phase",
]
