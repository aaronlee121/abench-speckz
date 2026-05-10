import importlib
import json
import re
from typing import Any

from jsonpath_ng.ext import parse as jsonpath_parse

from benchspec.runspec import ToolSpec


_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d+\.\d+([eE][+-]?\d+)?$|^-?\d+[eE][+-]?\d+$")


def apply(tool: ToolSpec, stdout: str) -> tuple[dict[str, Any], str | None]:
    if tool.parser:
        return _apply_plugin(tool.parser, stdout)
    return _apply_capture(tool.capture, stdout)


def _apply_capture(capture: dict[str, str], stdout: str) -> tuple[dict[str, Any], str | None]:
    if not capture:
        return {}, None
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        return {}, f"parse_error: not JSON: {e.msg}"

    results: dict[str, Any] = {}
    for raw_key, expr in capture.items():
        is_list, key = (True, raw_key[:-2]) if raw_key.endswith("[]") else (False, raw_key)
        try:
            matches = jsonpath_parse(expr).find(data)
        except Exception as e:
            return {}, f"parse_error: bad jsonpath for {key!r}: {e}"
        if not matches:
            results[key] = None
            continue
        if is_list:
            results[key] = [_coerce(m.value) for m in matches]
        elif len(matches) > 1:
            return {}, f"parse_error: multi-match for {key!r}"
        else:
            results[key] = _coerce(matches[0].value)
    return results, None


def _apply_plugin(parser: str, stdout: str) -> tuple[dict[str, Any], str | None]:
    if ":" not in parser:
        return {}, f"parser_error: expected 'module:func', got {parser!r}"
    mod_name, func_name = parser.split(":", 1)
    try:
        module = importlib.import_module(mod_name)
        func = getattr(module, func_name)
    except (ImportError, AttributeError) as e:
        return {}, f"parser_error: cannot load {parser}: {e}"
    try:
        out = func(stdout)
    except Exception as e:
        return {}, f"parser_error: {type(e).__name__}: {e}"
    if not isinstance(out, dict):
        return {}, f"parser_error: returned {type(out).__name__}, expected dict"
    return {k: _coerce(v) for k, v in out.items()}, None


def _coerce(v: Any) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        if _INT_RE.fullmatch(v):
            return int(v)
        if _FLOAT_RE.fullmatch(v):
            return float(v)
        return v
    if isinstance(v, (list, dict)):
        return v
    return str(v)
