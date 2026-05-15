import pytest

from abench_speckz import interpolate
from abench_speckz.model import SpecError


def test_simple_substitution():
    out = interpolate.resolve({"A": "hello", "B": "${A} world"})
    assert out["B"] == "hello world"


def test_nested_two_levels():
    out = interpolate.resolve({"A": "1", "B": "${A}", "C": "${B}-${B}"})
    assert out["C"] == "1-1"


def test_cycle_raises():
    with pytest.raises(SpecError, match="cycle"):
        interpolate.resolve({"A": "${B}", "B": "${A}"})


def test_missing_reference_raises():
    with pytest.raises(SpecError, match="unknown key"):
        interpolate.resolve({"A": "${MISSING}"})


def test_dollar_escape():
    out = interpolate.resolve({"A": "$${LITERAL}"})
    assert out["A"] == "${LITERAL}"


def test_int_stringified():
    out = interpolate.resolve({"N": 42, "M": "n=${N}"})
    assert out["N"] == "42"
    assert out["M"] == "n=42"


def test_bool_lowercased():
    out = interpolate.resolve({"B": True})
    assert out["B"] == "true"


def test_env_var_resolved():
    out = interpolate.resolve({"A": "${env:FOO}"}, env={"FOO": "bar"})
    assert out["A"] == "bar"


def test_env_var_missing_raises():
    from abench_speckz.model import SpecError

    with pytest.raises(SpecError, match="unknown env var"):
        interpolate.resolve({"A": "${env:MISSING}"}, env={})


def test_env_var_with_none_env_raises():
    from abench_speckz.model import SpecError

    with pytest.raises(SpecError, match="unknown env var"):
        interpolate.resolve({"A": "${env:FOO}"})


def test_depth_exceeded_raises():
    # 20 hops via A0 → A1 → … → A19 → unresolved exceeds _MAX_DEPTH=16
    chain = {f"A{i}": f"${{A{i + 1}}}" for i in range(19)}
    chain["A19"] = "${MISSING}"
    from abench_speckz.model import SpecError

    with pytest.raises(SpecError):
        interpolate.resolve(chain)


def test_resolve_string_standalone():
    out = interpolate.resolve_string("hello ${name}", {"name": "world"})
    assert out == "hello world"


def test_resolve_string_env_lookup():
    out = interpolate.resolve_string("${env:X}", {}, env={"X": "v"})
    assert out == "v"


def test_resolve_string_dollar_escape():
    out = interpolate.resolve_string("price=$$5", {})
    assert out == "price=$5"


def test_find_combo_var_refs_excludes_env():
    refs = interpolate.find_combo_var_refs("${a} ${env:HOME} ${b}")
    assert refs == ["a", "b"]


def test_find_combo_var_refs_respects_dollar_escape():
    refs = interpolate.find_combo_var_refs("$${not_a_ref} ${real}")
    assert refs == ["real"]


def test_find_combo_var_refs_on_int_template():
    # Non-string template should be stringified and contain no refs.
    assert interpolate.find_combo_var_refs(42) == []
