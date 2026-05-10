import json

from benchspec import store
from benchspec.runspec import AggregateRow


def test_append_and_read(tmp_path):
    store.init_results_dir(tmp_path)
    store.append_run(tmp_path, {"run_id": "1", "config_hash": "h", "results": {"x": 1}})
    store.append_run(tmp_path, {"run_id": "2", "config_hash": "h", "results": {"x": 2}})
    rows = store.read_runs(tmp_path)
    assert len(rows) == 2
    assert rows[0]["run_id"] == "1"


def test_read_runs_for_hash(tmp_path):
    store.init_results_dir(tmp_path)
    store.append_run(tmp_path, {"run_id": "1", "config_hash": "h1"})
    store.append_run(tmp_path, {"run_id": "2", "config_hash": "h2"})
    store.append_run(tmp_path, {"run_id": "3", "config_hash": "h1"})
    rows = store.read_runs_for_hash(tmp_path, "h1")
    assert [r["run_id"] for r in rows] == ["1", "3"]


def test_aggregates_atomic_write(tmp_path):
    store.init_results_dir(tmp_path)
    row = AggregateRow(
        config_hash="h", metric="rps", n=2, n_failed=0,
        mean=100.0, stddev=10.0, p50=100.0, p95=110.0, p99=110.0,
        ci95_low=85.0, ci95_high=115.0, vars={"a": "1"}, tags=["bench"],
    )
    store.write_aggregates_atomic(tmp_path, {"h": [row]})
    table = store.load_aggregates(tmp_path)
    assert "h" in table
    assert table["h"][0].n == 2
    assert table["h"][0].vars == {"a": "1"}


def test_update_aggregates_for_hash(tmp_path):
    store.init_results_dir(tmp_path)
    store.append_run(tmp_path, {
        "run_id": "1", "config_hash": "h", "vars": {"a": "1"},
        "tags": [], "exit_code": 0, "results": {"rps": 100},
    })
    store.append_run(tmp_path, {
        "run_id": "2", "config_hash": "h", "vars": {"a": "1"},
        "tags": [], "exit_code": 0, "results": {"rps": 110},
    })
    store.update_aggregates_for_hash(tmp_path, "h")
    table = store.load_aggregates(tmp_path)
    assert table["h"][0].n == 2
    assert table["h"][0].mean == 105.0


def test_rebuild_aggregates(tmp_path):
    store.init_results_dir(tmp_path)
    for i in range(3):
        store.append_run(tmp_path, {
            "run_id": f"r{i}", "config_hash": "h", "vars": {"a": "1"},
            "tags": [], "exit_code": 0, "results": {"rps": 100 + i * 10},
        })
    n = store.rebuild_aggregates(tmp_path)
    assert n == 1
    table = store.load_aggregates(tmp_path)
    assert table["h"][0].n == 3


def test_manifest_snapshot_dedupe(tmp_path):
    src = tmp_path / "manifest.json"
    src.write_text("[]")
    store.init_results_dir(tmp_path)
    a = store.snapshot_manifest(tmp_path, src)
    assert a.name == "manifest.snapshot.json"
    b = store.snapshot_manifest(tmp_path, src)
    assert b.name.startswith("manifest.snapshot.") and b != a
