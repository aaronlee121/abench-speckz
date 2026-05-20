"""Serialize combos to per-combo env-files and a directory-wide ``manifest.json``."""

import json
from pathlib import Path

from abench_speckz.model import Combo


def write_envfile(path: Path, vars_: dict[str, str]) -> None:
    lines = [f"{k}={vars_[k]}" for k in vars_]
    path.write_text("\n".join(lines) + "\n")


def write_manifest(path: Path, combos: list[Combo], filenames: list[str]) -> None:
    entries = [
        {
            "filename": fn,
            "vars": {k: v for k, v in c.vars.items()},
            "varying_keys": list(c.varying_keys),
            "tags": sorted(c.tags),
        }
        for c, fn in zip(combos, filenames, strict=True)
    ]
    path.write_text(json.dumps(entries, indent=2, sort_keys=False) + "\n")


def write_all(out_dir: Path, combos: list[Combo], filenames: list[str]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for combo, fn in zip(combos, filenames, strict=True):
        write_envfile(out_dir / fn, combo.vars)
    write_manifest(out_dir / "manifest.json", combos, filenames)
