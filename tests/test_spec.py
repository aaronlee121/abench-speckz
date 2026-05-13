import pytest

from abench_speckz import spec
from abench_speckz.model import SpecError


def test_load_minimal(fixtures_dir):
    s = spec.load(fixtures_dir / "minimal.yaml")
    assert s.static == {"IMAGE": "minimal:1.0"}
    assert s.variables == {"A": [1, 2], "B": ["x", "y"]}
    assert s.when == []
    assert s.exclude == []


def test_default_profile_applied(fixtures_dir):
    s = spec.load(fixtures_dir / "full.yaml")
    assert s.profile_name == "smoke"
    assert s.variables["concurrency"] == [1]
    assert s.variables["workload"] == ["read"]
    assert s.variables["backend"] == ["postgres", "mysql"]


def test_explicit_profile_overrides(fixtures_dir):
    s = spec.load(fixtures_dir / "full.yaml", profile="full")
    assert s.profile_name == "full"
    assert s.variables["concurrency"] == [1, 8, 64]


def test_unknown_profile(fixtures_dir):
    with pytest.raises(SpecError, match="unknown profile"):
        spec.load(fixtures_dir / "full.yaml", profile="nope")


def test_unknown_top_level_key(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("garbage: 1\n")
    with pytest.raises(SpecError, match="unknown top-level"):
        spec.load(p)


def test_when_concat_in_profile(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text(
        """
variables: { x: [1, 2] }
when:
  - if: { x: 1 }
    set: { A: base }
profiles:
  p:
    when:
      - if: { x: 2 }
        set: { A: profile }
default_profile: p
"""
    )
    s = spec.load(p)
    assert len(s.when) == 2
    assert s.when[0].set_["A"] == "base"
    assert s.when[1].set_["A"] == "profile"


def test_underscore_variable_rejected(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text("variables:\n  _myvar: [1, 2]\n")
    with pytest.raises(SpecError, match="reserved"):
        spec.load(p)


def test_underscore_static_rejected(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text("static:\n  _mykey: val\n")
    with pytest.raises(SpecError, match="reserved"):
        spec.load(p)


def test_underscore_when_set_rejected(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text(
        "variables:\n  x: [1]\n"
        "when:\n  - if: {x: 1}\n    set: {_synthetic: val}\n"
    )
    with pytest.raises(SpecError, match="reserved"):
        spec.load(p)


def test_underscore_in_profile_variable_rejected(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text(
        "variables:\n  x: [1]\n"
        "profiles:\n  p:\n    variables:\n      _bad: [1]\n"
        "default_profile: p\n"
    )
    with pytest.raises(SpecError, match="reserved"):
        spec.load(p)
