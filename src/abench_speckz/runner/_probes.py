"""Hash combo vars and run env/combo probe commands."""

import hashlib
import json
import shlex
import subprocess
from typing import Any

from abench_speckz import interpolate


def config_hash(vars_: dict[str, Any]) -> str:
    """Stable 16-char hash of a combo's variable assignment."""
    canonical = json.dumps(vars_, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def collect_probe_results(
    probes: dict[str, str],
    timeout_seconds: int,
    *,
    vars_: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run each probe command once and return key → stripped stdout (or None).

    When ``vars_`` is provided, each command template is first interpolated
    against the combo vars (combo-scoped probes); otherwise it runs verbatim
    (env-scoped probes). Any failure — non-zero exit, missing command, timeout,
    or interpolation error — stores ``None`` for that key. Probes never abort
    a sweep.
    """
    results: dict[str, Any] = {}
    for key, template in probes.items():
        try:
            command = (
                interpolate.resolve_string(template, vars_, env=env or {}) if vars_ is not None else template
            )
            result = subprocess.run(
                shlex.split(command),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            results[key] = result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            results[key] = None
    return results
