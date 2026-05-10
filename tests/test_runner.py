import json
from pathlib import Path

from benchspec import runner, store


REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_manifest(tmp_path: Path, combos):
    """Create out/ with .env files and manifest.json for the given combos."""
    out = tmp_path / "out"
    out.mkdir()
    manifest = []
    for i, vars_ in enumerate(combos):
        fn = f"combo-{i}.env"
        (out / fn).write_text(
            "\n".join(f"{k}={v}" for k, v in vars_.items()) + "\n"
        )
        manifest.append({"filename": fn, "vars": vars_, "tags": []})
    (out / "manifest.json").write_text(json.dumps(manifest))
    return out


def _tool_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "tool.yaml"
    p.write_text(
        f"""
name: faketool
version_command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py --version"
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  p50_latency_ms: $.summary.latencies.p50
  p95_latency_ms: $.summary.latencies.p95
  rps:           $.summary.requestsPerSec
"""
    )
    return p


def test_full_sweep(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [
        {"workload": "read", "concurrency": "1"},
        {"workload": "write", "concurrency": "8"},
    ])
    tool = _tool_yaml(tmp_path)
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool,
        results_dir=results,
        repeat=3,
    )
    assert summary["runs"] == 6
    assert summary["succeeded"] == 6
    assert summary["failed"] == 0

    runs = store.read_runs(results)
    assert len(runs) == 6
    table = store.load_aggregates(results)
    assert len(table) == 2
    for rows in table.values():
        # 3 metrics per config_hash
        assert len(rows) == 3
        for r in rows:
            assert r.n == 3

    # Snapshots present
    assert (results / "manifest.snapshot.json").exists()
    assert (results / "tools" / "faketool.yaml").exists()
    assert (results / "env.snapshot.json").exists()
    assert (results / "pretty_names.json").exists()


def test_failure_path(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py --mode=fail"
timeout_seconds: 5
capture:
  rps: $.summary.requestsPerSec
"""
    )
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=2,
    )
    assert summary["failed"] == 2
    assert summary["succeeded"] == 0

    runs = store.read_runs(results)
    assert all(r["exit_code"] != 0 for r in runs)
    assert all(r["failure_reason"] for r in runs)
    table = store.load_aggregates(results)
    assert table == {}


def test_warmup_excluded_from_aggregate(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "8"}])
    tool = _tool_yaml(tmp_path)
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool,
        results_dir=results,
        repeat=3,
        warmup=2,
    )
    assert summary["runs"] == 5  # 2 warmup + 3 measured
    assert summary["succeeded"] == 5

    runs = store.read_runs(results)
    warmup_runs = [r for r in runs if "warmup" in r["tags"]]
    assert len(warmup_runs) == 2

    table = store.load_aggregates(results)
    rps = next(r for r in next(iter(table.values())) if r.metric == "rps")
    assert rps.n == 3  # warmups not counted


def test_skip_existing(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "8"}])
    tool = _tool_yaml(tmp_path)
    results = tmp_path / "results"

    s1 = runner.execute_sweep(
        manifest_path=manifest_dir, tool_path=tool, results_dir=results, repeat=3,
    )
    assert s1["succeeded"] == 3

    s2 = runner.execute_sweep(
        manifest_path=manifest_dir, tool_path=tool, results_dir=results, repeat=3,
        skip_existing=True,
    )
    assert s2["skipped"] == 3
    assert s2["succeeded"] == 0

    # Asking for more reps than recorded should not skip
    s3 = runner.execute_sweep(
        manifest_path=manifest_dir, tool_path=tool, results_dir=results, repeat=5,
        skip_existing=True,
    )
    assert s3["skipped"] == 0
    assert s3["succeeded"] == 5


def test_dry_run_writes_nothing(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool = _tool_yaml(tmp_path)
    results = tmp_path / "results"

    runner.execute_sweep(
        manifest_path=manifest_dir, tool_path=tool, results_dir=results,
        repeat=2, dry_run=True,
    )
    assert not (results / "runs.jsonl").exists()
