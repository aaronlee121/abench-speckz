import pytest

from benchspec import filters
from benchspec.model import RunError


def test_parse_eq():
    p = filters.parse_kv(["a=1", "b=hi"])
    assert [(x.key, x.op, x.value) for x in p] == [("a", "=", "1"), ("b", "=", "hi")]


def test_parse_other_ops_lex_but_dont_apply():
    # Lexer accepts; calling raises until v1.1
    p = filters.parse_kv(["a!=1"])
    assert p[0].op == "!="
    with pytest.raises(RunError, match="not yet supported"):
        p[0]({"a": "2"})


def test_make_filter_kv():
    fn = filters.make_filter(where=["a=1"])
    assert fn({"a": "1"}, [])
    assert not fn({"a": "2"}, [])


def test_make_filter_tags():
    fn = filters.make_filter(require_tags=["red"], exclude_tags=["slow"])
    assert fn({}, ["red", "fast"])
    assert not fn({}, ["red", "slow"])
    assert not fn({}, ["blue"])


def test_kv_and_tag_combine():
    fn = filters.make_filter(where=["a=1"], require_tags=["red"])
    assert fn({"a": "1"}, ["red"])
    assert not fn({"a": "1"}, ["blue"])
    assert not fn({"a": "2"}, ["red"])


def test_rejects_malformed():
    with pytest.raises(RunError):
        filters.parse_kv(["nope"])
