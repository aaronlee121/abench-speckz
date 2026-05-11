import pytest

from abench_speckz import filters
from abench_speckz.model import RunError


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


def test_rejects_injection_key():
    with pytest.raises(RunError, match="invalid filter key"):
        filters.parse_kv(["x'); DROP TABLE foo;--=bar"])


def test_rejects_key_with_space():
    with pytest.raises(RunError, match="invalid filter key"):
        filters.parse_kv(["a b=1"])


def test_check_ident_accepts_normal_names():
    assert filters.check_ident("workload", context="x") == "workload"
    assert filters.check_ident("THREAD_POOL", context="x") == "THREAD_POOL"
    assert filters.check_ident("a1_b2", context="x") == "a1_b2"


def test_check_ident_rejects_quote():
    with pytest.raises(RunError):
        filters.check_ident("a'", context="x")


def test_check_ident_rejects_leading_digit():
    with pytest.raises(RunError):
        filters.check_ident("1abc", context="x")
