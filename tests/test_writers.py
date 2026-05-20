import json

from abench_speckz import writers
from abench_speckz.model import Combo


def test_envfile_format(tmp_path):
    p = tmp_path / "c.env"
    writers.write_envfile(p, {"A": "1", "B": "two"})
    assert p.read_text() == "A=1\nB=two\n"


def test_manifest_shape(tmp_path):
    combos = [
        Combo(vars={"A": "1", "B": "x"}, tags={"red"}, varying_keys=("A", "B")),
        Combo(vars={"A": "2", "B": "y"}, tags=set(), varying_keys=("A", "B")),
    ]
    filenames = ["a-1__b-x.env", "a-2__b-y.env"]
    p = tmp_path / "manifest.json"
    writers.write_manifest(p, combos, filenames)
    data = json.loads(p.read_text())
    assert len(data) == 2
    assert data[0]["filename"] == "a-1__b-x.env"
    assert data[0]["vars"] == {"A": "1", "B": "x"}
    assert data[0]["varying_keys"] == ["A", "B"]
    assert data[0]["tags"] == ["red"]
    assert data[1]["tags"] == []


def test_write_all_creates_dir(tmp_path):
    out = tmp_path / "out"
    combos = [Combo(vars={"A": "1"}, varying_keys=("A",))]
    writers.write_all(out, combos, ["a-1.env"])
    assert (out / "a-1.env").exists()
    assert (out / "manifest.json").exists()
