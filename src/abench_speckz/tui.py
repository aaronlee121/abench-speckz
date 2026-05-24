"""Textual TUI for browsing a results directory."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class _ComboSummary:
    config_hash: str
    vars: dict[str, str]
    metrics: list[dict[str, Any]] = field(default_factory=list)
    n_runs: int = 0
    n_failed: int = 0


def _load_combos(results_dir: Path) -> tuple[list[_ComboSummary], list[str]]:
    path = results_dir / "aggregates.jsonl"
    if not path.exists():
        return [], []

    combos: dict[str, _ComboSummary] = {}
    var_keys: list[str] = []

    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        h = row.get("config_hash", "")
        if h not in combos:
            vars_ = row.get("vars") or {}
            if not var_keys and vars_:
                var_keys = list(vars_.keys())
            combos[h] = _ComboSummary(
                config_hash=h,
                vars=vars_,
                n_runs=row.get("n", 0),
                n_failed=row.get("n_failed", 0),
            )
        else:
            combos[h].n_runs = max(combos[h].n_runs, row.get("n", 0))
            combos[h].n_failed = max(combos[h].n_failed, row.get("n_failed", 0))

        combos[h].metrics.append(
            {
                "metric": row.get("metric", ""),
                "n": row.get("n", 0),
                "mean": row.get("mean"),
                "p50": row.get("p50"),
                "p95": row.get("p95"),
                "p99": row.get("p99"),
                "stddev": row.get("stddev"),
            }
        )

    # Prefer the manifest snapshot's original key order
    snapshot = results_dir / "manifest.snapshot.json"
    if snapshot.exists():
        try:
            data = json.loads(snapshot.read_text())
            if isinstance(data, list) and data:
                keys = data[0].get("varying_keys")
                if isinstance(keys, list):
                    var_keys = keys
        except (json.JSONDecodeError, OSError):
            pass

    return list(combos.values()), var_keys


def _load_runs_for_hash(results_dir: Path, config_hash: str) -> list[dict[str, Any]]:
    path = results_dir / "runs.jsonl"
    if not path.exists():
        return []
    runs = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("config_hash") == config_hash:
            if "warmup" not in (row.get("tags") or []):
                runs.append(row)
    return runs


def _fmt(v: Any, precision: int = 3) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{float(v):.{precision}f}"
    return str(v)


def browse(results_dir: Path) -> None:
    """Launch the TUI browser for a results directory."""
    try:
        from abench_speckz._browser import BrowserApp
    except ImportError as exc:
        raise SystemExit(
            "textual is required for 'abench-speckz browse'.\nInstall it: pip install 'abench-speckz[tui]'"
        ) from exc

    BrowserApp(results_dir).run()
    os.system("cls" if os.name == "nt" else "clear")
