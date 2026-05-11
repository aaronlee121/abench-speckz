"""Build filesystem-safe env-file names from a combo's varying keys."""
import hashlib
import json
import re
from collections import defaultdict

from abench_speckz.model import Combo

_BAD_CHAR = re.compile(r"[^a-z0-9._-]")
_DASH_RUN = re.compile(r"-+")
_MAX_FILENAME_LEN = 200
_TRUNC_TARGET_LEN = 180


def slug(value: object) -> str:
    s = str(value).lower()
    s = _BAD_CHAR.sub("-", s)
    s = _DASH_RUN.sub("-", s)
    s = s.strip("-.")
    return s or "empty"


def _combo_hash(combo: Combo) -> str:
    canonical = json.dumps(combo.vars, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:8]


def _naive_filename(combo: Combo) -> str:
    parts = [f"{slug(k)}-{slug(combo.vars[k])}" for k in combo.varying_keys]
    body = "__".join(parts) if parts else "combo"
    if len(body) + 4 > _MAX_FILENAME_LEN:
        body = body[:_TRUNC_TARGET_LEN] + "-" + _combo_hash(combo)
    return body + ".env"


def assign_filenames(combos: list[Combo]) -> list[str]:
    """Return one ``*.env`` filename per combo. Filenames that collide after
    slugification get an 8-char SHA-256 suffix derived from the combo vars."""
    naive = [_naive_filename(c) for c in combos]

    groups: dict[str, list[int]] = defaultdict(list)
    for i, name in enumerate(naive):
        groups[name].append(i)

    final = list(naive)
    for name, indices in groups.items():
        if len(indices) <= 1:
            continue
        for i in indices:
            stem = name[:-4]
            final[i] = f"{stem}-{_combo_hash(combos[i])}.env"
    return final
