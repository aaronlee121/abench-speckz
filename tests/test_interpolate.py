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
