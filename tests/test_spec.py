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
    p.write_text("variables:\n  x: [1]\nwhen:\n  - if: {x: 1}\n    set: {_synthetic: val}\n")
    with pytest.raises(SpecError, match="reserved"):
        spec.load(p)


def test_underscore_in_profile_variable_rejected(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text(
        "variables:\n  x: [1]\nprofiles:\n  p:\n    variables:\n      _bad: [1]\ndefault_profile: p\n"
    )
    with pytest.raises(SpecError, match="reserved"):
        spec.load(p)


# --- schema/shape error paths ---


def test_empty_yaml_returns_empty_spec(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("")
    s = spec.load(p)
    assert s.static == {} and s.variables == {} and s.when == [] and s.exclude == []


def test_root_must_be_mapping_not_list(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("- a\n- b\n")
    with pytest.raises(SpecError, match="root must be a mapping"):
        spec.load(p)


def test_root_must_be_mapping_not_scalar(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("hello\n")
    with pytest.raises(SpecError, match="root must be a mapping"):
        spec.load(p)


def test_profiles_must_be_mapping(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("profiles: [a, b]\n")
    with pytest.raises(SpecError, match="profiles.*mapping"):
        spec.load(p)


def test_profile_partial_must_be_mapping(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("profiles:\n  p: [1,2]\ndefault_profile: p\n")
    with pytest.raises(SpecError, match="profiles.p.*must be a mapping"):
        spec.load(p)


def test_static_must_be_mapping(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("static: [a, b]\n")
    with pytest.raises(SpecError, match="static.*must be a mapping"):
        spec.load(p)


def test_variables_must_be_mapping(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("variables: [a, b]\n")
    with pytest.raises(SpecError, match="variables.*must be a mapping"):
        spec.load(p)


def test_when_must_be_list(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("when: {if: {}}\n")
    with pytest.raises(SpecError, match="when.*must be a list"):
        spec.load(p)


def test_exclude_must_be_list(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("exclude: {a: 1}\n")
    with pytest.raises(SpecError, match="exclude.*must be a list"):
        spec.load(p)


def test_tags_must_be_list(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("tags: solo\n")
    with pytest.raises(SpecError, match="tags.*must be a list"):
        spec.load(p)


def test_variable_value_must_be_list(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("variables:\n  a: 42\n")
    with pytest.raises(SpecError, match="variables.a.*must be a list"):
        spec.load(p)


def test_when_item_must_be_mapping(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("variables: {x: [1]}\nwhen:\n  - hello\n")
    with pytest.raises(SpecError, match=r"when\[0\].*must be a mapping"):
        spec.load(p)


def test_when_if_must_be_mapping(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("variables: {x: [1]}\nwhen:\n  - if: [a, b]\n    set: {Y: 1}\n")
    with pytest.raises(SpecError, match=r"when\[0\].if.*must be a mapping"):
        spec.load(p)


def test_when_set_must_be_mapping(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("variables: {x: [1]}\nwhen:\n  - if: {x: 1}\n    set: [a, b]\n")
    with pytest.raises(SpecError, match=r"when\[0\].set.*must be a mapping"):
        spec.load(p)


def test_when_tag_string_accepted(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text("variables: {x: [1]}\nwhen:\n  - if: {x: 1}\n    tag: solo\n")
    s = spec.load(p)
    assert s.when[0].tag == ("solo",)


def test_when_tag_must_be_string_or_list(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("variables: {x: [1]}\nwhen:\n  - if: {x: 1}\n    tag: 42\n")
    with pytest.raises(SpecError, match=r"when\[0\].tag.*string or list"):
        spec.load(p)


def test_exclude_item_must_be_mapping(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("variables: {x: [1]}\nexclude:\n  - hello\n")
    with pytest.raises(SpecError, match=r"exclude\[0\].*must be a mapping"):
        spec.load(p)
