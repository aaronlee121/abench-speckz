import pytest

from abench_speckz import validate
from abench_speckz.model import Combo, ExcludeRule, Spec, SpecError, WhenRule


def _spec(**overrides) -> Spec:
    base = dict(static={}, variables={"a": [1, 2]}, when=[], exclude=[], tags=[])
    base.update(overrides)
    return Spec(**base)


def test_unknown_when_if_key():
    s = _spec(when=[WhenRule(if_={"missing": 1}, set_={"X": "y"}, tag=(), source="when[0]")])
    with pytest.raises(SpecError, match="unknown axis keys"):
        validate.validate(s)


def test_when_set_collides_with_static():
    s = _spec(
        static={"X": "fixed"},
        when=[WhenRule(if_={"a": 1}, set_={"X": "override"}, tag=(), source="when[0]")],
    )
    with pytest.raises(SpecError, match="also appears in `static`"):
        validate.validate(s)


def test_empty_variable_list():
    s = _spec(variables={"a": []})
    with pytest.raises(SpecError, match="empty"):
        validate.validate(s)


def test_unknown_exclude_key():
    s = _spec(exclude=[ExcludeRule(match={"unknown": 1}, source="exclude[0]")])
    with pytest.raises(SpecError, match="unknown keys"):
        validate.validate(s)


def test_type_mixing_warns():
    s = _spec(variables={"a": [1, "two"]})
    with pytest.warns(UserWarning, match="mixes types"):
        validate.validate(s)


def test_invalid_env_key_rejected():
    combos = [Combo(vars={"1bad": "x"})]
    with pytest.raises(SpecError, match="not a valid env-var"):
        validate.validate_env_keys(combos)


def test_valid_env_keys_pass():
    combos = [Combo(vars={"GOOD": "x", "_OK_2": "y"})]
    validate.validate_env_keys(combos)
