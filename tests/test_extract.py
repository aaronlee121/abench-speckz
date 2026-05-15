import json

from abench_speckz import extract
from abench_speckz.runspec import ToolSpec


def _tool(capture=None, parser=None, output_format="json"):
    return ToolSpec(name="t", command="x", capture=capture or {}, parser=parser, output_format=output_format)


def test_single_match():
    out, fail = extract.apply(
        _tool({"x": "$.a"}), json.dumps({"a": 1.5})
    )
    assert fail is None
    assert out == {"x": 1.5}


def test_missing_match_is_null():
    out, fail = extract.apply(_tool({"x": "$.missing"}), json.dumps({"a": 1}))
    assert fail is None
    assert out == {"x": None}


def test_multi_match_errors_without_brackets():
    out, fail = extract.apply(
        _tool({"x": "$.results[*].v"}),
        json.dumps({"results": [{"v": 1}, {"v": 2}]}),
    )
    assert fail and "multi-match" in fail


def test_multi_match_with_brackets_returns_list():
    out, fail = extract.apply(
        _tool({"x[]": "$.results[*].v"}),
        json.dumps({"results": [{"v": 1}, {"v": 2}]}),
    )
    assert fail is None
    assert out == {"x": [1, 2]}


def test_invalid_json_fails():
    out, fail = extract.apply(_tool({"x": "$.a"}), "not json")
    assert fail and "not JSON" in fail
    assert out == {}


def test_numeric_string_coerced():
    out, _ = extract.apply(_tool({"x": "$.a"}), json.dumps({"a": "42.5"}))
    assert out == {"x": 42.5}


def test_int_string_coerced():
    out, _ = extract.apply(_tool({"x": "$.a"}), json.dumps({"a": "42"}))
    assert out == {"x": 42}


def test_plugin_loader(tmp_path, monkeypatch):
    pkg = tmp_path / "myparser"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("def parse(s):\n    return {'k': 7}\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    out, fail = extract.apply(_tool(parser="myparser:parse"), "anything")
    assert fail is None
    assert out == {"k": 7}


def test_plugin_returns_non_dict(tmp_path, monkeypatch):
    pkg = tmp_path / "badparser"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("def parse(s):\n    return [1, 2]\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    out, fail = extract.apply(_tool(parser="badparser:parse"), "anything")
    assert fail and "expected dict" in fail


def test_plugin_raises(tmp_path, monkeypatch):
    pkg = tmp_path / "raiser"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("def parse(s):\n    raise ValueError('nope')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    out, fail = extract.apply(_tool(parser="raiser:parse"), "anything")
    assert fail and "ValueError" in fail


# JSONL tests

def _jsonl(*rows):
    return "\n".join(json.dumps(r) for r in rows)


def test_jsonl_collect_all_values():
    tool = _tool({"v[]": "$[*].value"}, output_format="jsonl")
    out, fail = extract.apply(tool, _jsonl({"value": 1}, {"value": 2}, {"value": 3}))
    assert fail is None
    assert out == {"v": [1, 2, 3]}


def test_jsonl_last_line():
    tool = _tool({"rps": "$[-1].rps"}, output_format="jsonl")
    out, fail = extract.apply(tool, _jsonl({"rps": 100}, {"rps": 200}, {"rps": 300}))
    assert fail is None
    assert out == {"rps": 300}


def test_jsonl_first_line():
    tool = _tool({"rps": "$[0].rps"}, output_format="jsonl")
    out, fail = extract.apply(tool, _jsonl({"rps": 100}, {"rps": 200}))
    assert fail is None
    assert out == {"rps": 100}


def test_jsonl_single_line():
    tool = _tool({"v[]": "$[*].value"}, output_format="jsonl")
    out, fail = extract.apply(tool, json.dumps({"value": 42}))
    assert fail is None
    assert out == {"v": [42]}


def test_jsonl_empty_output():
    tool = _tool({"v": "$[0].value"}, output_format="jsonl")
    out, fail = extract.apply(tool, "")
    assert fail == "parse_error: empty JSONL output"
    assert out == {}


def test_jsonl_blank_lines_ignored():
    tool = _tool({"v[]": "$[*].value"}, output_format="jsonl")
    out, fail = extract.apply(tool, "\n" + _jsonl({"value": 1}, {"value": 2}) + "\n")
    assert fail is None
    assert out == {"v": [1, 2]}


def test_jsonl_bad_line_reports_line_number():
    tool = _tool({"v": "$[0].value"}, output_format="jsonl")
    out, fail = extract.apply(tool, _jsonl({"value": 1}) + "\nnot json\n" + _jsonl({"value": 3}))
    assert fail and "line 2" in fail
    assert out == {}


def test_jsonl_missing_field_is_null():
    tool = _tool({"v": "$[0].missing"}, output_format="jsonl")
    out, fail = extract.apply(tool, _jsonl({"value": 1}))
    assert fail is None
    assert out == {"v": None}


def test_json_format_rejects_jsonl_input():
    tool = _tool({"v": "$.value"}, output_format="json")
    out, fail = extract.apply(tool, _jsonl({"value": 1}, {"value": 2}))
    assert fail and "not JSON" in fail


# --- empty capture, bad jsonpath, parser format, _coerce branches ---


def test_empty_capture_json_returns_empty():
    out, fail = extract.apply(_tool({}), '{"a": 1}')
    assert fail is None and out == {}


def test_empty_capture_jsonl_returns_empty():
    out, fail = extract.apply(_tool({}, output_format="jsonl"), '{"a": 1}\n')
    assert fail is None and out == {}


def test_bad_jsonpath_reports_error():
    out, fail = extract.apply(_tool({"x": "$$$ not jsonpath"}), '{"a": 1}')
    assert fail and "bad jsonpath" in fail and "x" in fail
    assert out == {}


def test_parser_missing_colon():
    out, fail = extract.apply(_tool(parser="no_colon_here"), "anything")
    assert fail and "expected 'module:func'" in fail


def test_parser_module_not_importable():
    out, fail = extract.apply(_tool(parser="nonexistent_pkg_xyz123:fn"), "anything")
    assert fail and "cannot load" in fail


def test_parser_missing_attribute(tmp_path, monkeypatch):
    pkg = tmp_path / "halfpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("def other():\n    return {}\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    out, fail = extract.apply(_tool(parser="halfpkg:missing_fn"), "anything")
    assert fail and "cannot load" in fail


def test_coerce_bool_preserved():
    # Plugin returns a bool; should stay bool, not become int.
    out, _ = extract.apply(
        _tool({"x": "$.flag"}),
        json.dumps({"flag": True}),
    )
    assert out == {"x": True} and isinstance(out["x"], bool)


def test_coerce_scientific_notation_string():
    out, _ = extract.apply(_tool({"x": "$.a"}), json.dumps({"a": "1.5e3"}))
    assert out == {"x": 1500.0}


def test_coerce_negative_int_string():
    out, _ = extract.apply(_tool({"x": "$.a"}), json.dumps({"a": "-42"}))
    assert out == {"x": -42}


def test_coerce_leaves_plain_string():
    out, _ = extract.apply(_tool({"x": "$.a"}), json.dumps({"a": "hello"}))
    assert out == {"x": "hello"}


def test_coerce_list_passes_through():
    out, _ = extract.apply(_tool({"x": "$.a"}), json.dumps({"a": [1, 2]}))
    assert out == {"x": [1, 2]}


def test_coerce_dict_passes_through():
    out, _ = extract.apply(_tool({"x": "$.a"}), json.dumps({"a": {"k": 1}}))
    assert out == {"x": {"k": 1}}


def test_coerce_none_becomes_string(tmp_path, monkeypatch):
    # A parser plugin returning {key: None} should produce "None" via _coerce's fallback.
    pkg = tmp_path / "noneparser"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("def parse(s):\n    return {'k': None}\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    out, fail = extract.apply(_tool(parser="noneparser:parse"), "anything")
    assert fail is None and out == {"k": "None"}
