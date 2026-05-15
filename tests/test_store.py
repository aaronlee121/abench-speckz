from abench_speckz import store
from abench_speckz.runspec import AggregateRow

_SMALL = "x" * 10
_LARGE = "y" * (store.RAW_INLINE_THRESHOLD + 1)


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
        config_hash="h",
        metric="rps",
        n=2,
        n_failed=0,
        mean=100.0,
        stddev=10.0,
        p50=100.0,
        p95=110.0,
        p99=110.0,
        ci95_low=85.0,
        ci95_high=115.0,
        vars={"a": "1"},
        tags=["bench"],
    )
    store.write_aggregates_atomic(tmp_path, {"h": [row]})
    table = store.load_aggregates(tmp_path)
    assert "h" in table
    assert table["h"][0].n == 2
    assert table["h"][0].vars == {"a": "1"}


def test_update_aggregates_for_hash(tmp_path):
    store.init_results_dir(tmp_path)
    store.append_run(
        tmp_path,
        {
            "run_id": "1",
            "config_hash": "h",
            "vars": {"a": "1"},
            "tags": [],
            "exit_code": 0,
            "results": {"rps": 100},
        },
    )
    store.append_run(
        tmp_path,
        {
            "run_id": "2",
            "config_hash": "h",
            "vars": {"a": "1"},
            "tags": [],
            "exit_code": 0,
            "results": {"rps": 110},
        },
    )
    store.update_aggregates_for_hash(tmp_path, "h")
    table = store.load_aggregates(tmp_path)
    assert table["h"][0].n == 2
    assert table["h"][0].mean == 105.0


def test_rebuild_aggregates(tmp_path):
    store.init_results_dir(tmp_path)
    for i in range(3):
        store.append_run(
            tmp_path,
            {
                "run_id": f"r{i}",
                "config_hash": "h",
                "vars": {"a": "1"},
                "tags": [],
                "exit_code": 0,
                "results": {"rps": 100 + i * 10},
            },
        )
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


# --- raw spilling ---


def test_raw_small_content_stays_inline(tmp_path):
    store.init_results_dir(tmp_path)
    raw = {"stdout": _SMALL, "stderr": _SMALL}
    store.write_raw_run(tmp_path, "run-1", raw)
    doc = (tmp_path / "raw" / "run-1.json").read_text()
    assert _SMALL in doc
    assert "$ref" not in doc


def test_raw_large_stdout_is_spilled(tmp_path):
    store.init_results_dir(tmp_path)
    raw = {"stdout": _LARGE, "stderr": _SMALL}
    store.write_raw_run(tmp_path, "run-2", raw)

    doc = __import__("json").loads((tmp_path / "raw" / "run-2.json").read_text())
    assert isinstance(doc["stdout"], dict) and "$ref" in doc["stdout"]
    assert doc["stderr"] == _SMALL  # small field stays inline

    sidecar = tmp_path / doc["stdout"]["$ref"]
    assert sidecar.exists()
    assert sidecar.read_text() == _LARGE


def test_raw_load_resolves_spilled(tmp_path):
    store.init_results_dir(tmp_path)
    raw = {"stdout": _LARGE, "stderr": _SMALL}
    store.write_raw_run(tmp_path, "run-3", raw)

    loaded = store.load_raw_run(tmp_path, "run-3")
    assert loaded["stdout"] == _LARGE
    assert loaded["stderr"] == _SMALL


def test_raw_output_file_content_spilled(tmp_path):
    store.init_results_dir(tmp_path)
    raw = {"stdout": "", "stderr": "", "output_file": {"path": "/tmp/out.json", "content": _LARGE}}
    store.write_raw_run(tmp_path, "run-4", raw)

    doc = __import__("json").loads((tmp_path / "raw" / "run-4.json").read_text())
    assert isinstance(doc["output_file"]["content"], dict)
    assert "$ref" in doc["output_file"]["content"]

    loaded = store.load_raw_run(tmp_path, "run-4")
    assert loaded["output_file"]["content"] == _LARGE
    assert loaded["output_file"]["path"] == "/tmp/out.json"


def test_raw_step_stdout_spilled(tmp_path):
    store.init_results_dir(tmp_path)
    raw = {
        "stdout": _SMALL,
        "stderr": _SMALL,
        "setup": [{"command": "echo hi", "exit_code": 0, "stdout": _LARGE, "stderr": ""}],
    }
    store.write_raw_run(tmp_path, "run-5", raw)

    doc = __import__("json").loads((tmp_path / "raw" / "run-5.json").read_text())
    assert isinstance(doc["setup"][0]["stdout"], dict)
    assert "$ref" in doc["setup"][0]["stdout"]

    loaded = store.load_raw_run(tmp_path, "run-5")
    assert loaded["setup"][0]["stdout"] == _LARGE


def test_raw_load_missing_returns_none(tmp_path):
    store.init_results_dir(tmp_path)
    assert store.load_raw_run(tmp_path, "nonexistent") is None


def test_raw_sweep_phase_spills_step_output(tmp_path):
    store.init_results_dir(tmp_path)
    raw = {
        "setup": [{"command": "echo", "exit_code": 0, "stdout": _LARGE, "stderr": ""}],
    }
    store.write_raw_sweep_phase(tmp_path, None, raw)

    doc = __import__("json").loads((tmp_path / "raw" / "sweep.json").read_text())
    assert isinstance(doc["setup"][0]["stdout"], dict)
    sidecar = tmp_path / doc["setup"][0]["stdout"]["$ref"]
    assert sidecar.read_text() == _LARGE
