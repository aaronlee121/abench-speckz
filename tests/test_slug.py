from abench_speckz import slug
from abench_speckz.model import Combo


def test_slug_lowercases_and_replaces():
    assert slug.slug("A100/40GB") == "a100-40gb"


def test_slug_collapses_dashes():
    assert slug.slug("a???b") == "a-b"


def test_slug_strips_edges():
    assert slug.slug("--abc--") == "abc"


def test_slug_empty_becomes_empty():
    assert slug.slug("???") == "empty"


def test_filename_uses_varying_keys_only():
    c = Combo(vars={"A": 1, "B": "x", "STATIC": "z"}, varying_keys=("A", "B"))
    names = slug.assign_filenames([c])
    assert names == ["a-1__b-x.env"]


def test_filename_collision_disambiguated():
    c1 = Combo(vars={"A": "x?", "B": "y"}, varying_keys=("A", "B"))
    c2 = Combo(vars={"A": "x!", "B": "y"}, varying_keys=("A", "B"))
    # Both slug to "a-x__b-y" naively
    names = slug.assign_filenames([c1, c2])
    assert len(set(names)) == 2
    assert all(n.endswith(".env") for n in names)


def test_long_filename_truncated():
    big = "x" * 300
    c = Combo(vars={"K": big}, varying_keys=("K",))
    names = slug.assign_filenames([c])
    assert len(names[0]) <= 200
