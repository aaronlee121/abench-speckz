import json

from benchspec import extract
from benchspec.runspec import ToolSpec


def _tool(capture=None, parser=None):
    return ToolSpec(name="t", command="x", capture=capture or {}, parser=parser)


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
