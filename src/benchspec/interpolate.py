import re
from typing import Any

from benchspec.model import SpecError


_PATTERN = re.compile(r"\$\{(env:)?([A-Za-z_][A-Za-z0-9_]*)\}")
_DOLLAR_SENTINEL = "\x00BENCHSPEC_DOLLAR\x00"
_MAX_DEPTH = 16


def resolve(values: dict[str, Any], *, env: dict[str, str] | None = None) -> dict[str, str]:
    out: dict[str, str] = {k: _stringify(v).replace("$$", _DOLLAR_SENTINEL) for k, v in values.items()}

    for _ in range(_MAX_DEPTH):
        changed = False
        for k, v in list(out.items()):
            new = _PATTERN.sub(lambda m: _lookup(out, env, m.group(1), m.group(2)), v)
            if new != v:
                out[k] = new
                changed = True
        if not changed:
            break

    unresolved = sorted(k for k, v in out.items() if _PATTERN.search(v))
    if unresolved:
        raise SpecError(f"interpolation cycle or depth>{_MAX_DEPTH} in keys: {unresolved}")
    return {k: v.replace(_DOLLAR_SENTINEL, "$") for k, v in out.items()}


def resolve_string(template: str, values: dict[str, Any], *, env: dict[str, str] | None = None) -> str:
    s = _stringify(template).replace("$$", _DOLLAR_SENTINEL)
    s = _PATTERN.sub(lambda m: _lookup(values, env, m.group(1), m.group(2)), s)
    return s.replace(_DOLLAR_SENTINEL, "$")


def _stringify(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _lookup(values: dict[str, Any], env: dict[str, str] | None, prefix: str | None, name: str) -> str:
    if prefix == "env:":
        if env is None or name not in env:
            raise SpecError(f"interpolation references unknown env var: ${{env:{name}}}")
        return env[name]
    if name not in values:
        raise SpecError(f"interpolation references unknown key: ${{{name}}}")
    return _stringify(values[name])
