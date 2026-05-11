import pytest

from abench_speckz import generate
from abench_speckz.model import Combo, ExcludeRule, Spec, SpecError, WhenRule


def _spec(**overrides) -> Spec:
    base = dict(static={}, variables={}, when=[], exclude=[], tags=[])
    base.update(overrides)
    return Spec(**base)


def test_expand_cartesian():
    s = _spec(static={"S": "1"}, variables={"a": [1, 2], "b": ["x", "y"]})
    combos = generate.expand(s)
    assert len(combos) == 4
    for c in combos:
        assert c.vars["S"] == "1"
        assert c.varying_keys == ("a", "b")


def test_expand_no_variables():
    s = _spec(static={"S": "1"})
    combos = generate.expand(s)
    assert len(combos) == 1
    assert combos[0].vars == {"S": "1"}


def test_apply_excludes():
    combos = [
        Combo(vars={"a": 1, "b": "x"}, varying_keys=("a", "b")),
        Combo(vars={"a": 1, "b": "y"}, varying_keys=("a", "b")),
        Combo(vars={"a": 2, "b": "x"}, varying_keys=("a", "b")),
    ]
    rules = [ExcludeRule(match={"a": 1, "b": "x"}, source="exclude[0]")]
    out = generate.apply_excludes(combos, rules)
    assert len(out) == 2
    assert all(not (c.vars["a"] == 1 and c.vars["b"] == "x") for c in out)


def test_apply_when_pins_value_and_tags():
    combos = [
        Combo(vars={"a": 1}, varying_keys=("a",)),
        Combo(vars={"a": 2}, varying_keys=("a",)),
    ]
    rules = [WhenRule(if_={"a": 1}, set_={"X": "pinned"}, tag=("t1",), source="when[0]")]
    out = generate.apply_when(combos, rules)
    assert out[0].vars["X"] == "pinned"
    assert "t1" in out[0].tags
    assert "X" not in out[1].vars


def test_when_conflict_raises():
    combos = [Combo(vars={"a": 1}, varying_keys=("a",))]
    rules = [
        WhenRule(if_={"a": 1}, set_={"X": "v1"}, tag=(), source="when[0]"),
        WhenRule(if_={"a": 1}, set_={"X": "v2"}, tag=(), source="when[1]"),
    ]
    with pytest.raises(SpecError, match="conflicting"):
        generate.apply_when(combos, rules)


def test_dedupe_after_axis_collapse():
    combos = [
        Combo(vars={"a": 1, "b": "shared"}, tags={"t1"}, varying_keys=("a", "b")),
        Combo(vars={"a": 1, "b": "shared"}, tags={"t2"}, varying_keys=("a", "b")),
    ]
    out = generate.dedupe(combos)
    assert len(out) == 1
    assert out[0].tags == {"t1", "t2"}


def test_filter_tags():
    combos = [
        Combo(vars={"a": 1}, tags={"red", "fast"}),
        Combo(vars={"a": 2}, tags={"red", "slow"}),
        Combo(vars={"a": 3}, tags={"blue"}),
    ]
    require = generate.filter_tags(combos, require=["red"])
    assert len(require) == 2
    excl = generate.filter_tags(combos, exclude=["slow"])
    assert len(excl) == 2 and all("slow" not in c.tags for c in excl)
    both = generate.filter_tags(combos, require=["red"], exclude=["slow"])
    assert len(both) == 1


def test_full_pipeline_smoke(fixtures_dir):
    from abench_speckz import spec as spec_mod

    s = spec_mod.load(fixtures_dir / "full.yaml", profile="full")
    combos = generate.run_pipeline(s)
    # 3 * 3 * 2 = 18; exclude mysql/c=1 removes 3 → 15
    assert len(combos) == 15
    # universal tag applied
    assert all("bench" in c.tags for c in combos)
    # stress tag applied where concurrency=64
    stress = [c for c in combos if c.vars.get("concurrency") == "64"]
    assert all("stress" in c.tags for c in stress)
    # interpolation resolved
    for c in stress:
        assert c.vars["THREAD_POOL"] == "64"
