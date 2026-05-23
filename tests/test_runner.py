import io
import json
from pathlib import Path

import pytest

from abench_speckz import runner, store
from abench_speckz.model import RunError

REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_manifest(tmp_path: Path, combos):
    """Create out/ with .env files and manifest.json for the given combos."""
    out = tmp_path / "out"
    out.mkdir()
    manifest = []
    for i, vars_ in enumerate(combos):
        fn = f"combo-{i}.env"
        (out / fn).write_text("\n".join(f"{k}={v}" for k, v in vars_.items()) + "\n")
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
    manifest_dir = _make_manifest(
        tmp_path,
        [
            {"workload": "read", "concurrency": "1"},
            {"workload": "write", "concurrency": "8"},
        ],
    )
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
        manifest_path=manifest_dir,
        tool_path=tool,
        results_dir=results,
        repeat=3,
    )
    assert s1["succeeded"] == 3

    s2 = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool,
        results_dir=results,
        repeat=3,
        skip_existing=True,
    )
    assert s2["skipped"] == 3
    assert s2["succeeded"] == 0

    # Asking for more reps than recorded should not skip
    s3 = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool,
        results_dir=results,
        repeat=5,
        skip_existing=True,
    )
    assert s3["skipped"] == 0
    assert s3["succeeded"] == 5


def test_skip_existing_skips_warmups(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "8"}])
    tool = _tool_yaml(tmp_path)
    results = tmp_path / "results"

    s1 = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool,
        results_dir=results,
        repeat=3,
        warmup=2,
    )
    assert s1["succeeded"] == 5  # 2 warmup + 3 measured

    # Re-run with skip_existing: warmups should also be skipped
    s2 = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool,
        results_dir=results,
        repeat=3,
        warmup=2,
        skip_existing=True,
    )
    assert s2["skipped"] == 5  # 2 warmup + 3 measured, all skipped
    assert s2["succeeded"] == 0


def test_dry_run_writes_nothing(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool = _tool_yaml(tmp_path)
    results = tmp_path / "results"

    runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool,
        results_dir=results,
        repeat=2,
        dry_run=True,
    )
    assert not (results / "runs.jsonl").exists()


def test_output_file_extraction(tmp_path):
    out_file = tmp_path / "result.json"
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py --output-file {out_file}"
timeout_seconds: 10
output_file: "{out_file}"
capture:
  p50_latency_ms: $.summary.latencies.p50
  rps:           $.summary.requestsPerSec
"""
    )
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=2,
    )
    assert summary["succeeded"] == 2
    assert summary["failed"] == 0

    runs = store.read_runs(results)
    for r in runs:
        assert r["results"]["p50_latency_ms"] == 10.1
        assert r["results"]["rps"] == 1000.0


def test_output_file_missing_is_failure(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
output_file: "{tmp_path}/nonexistent.json"
capture:
  rps: $.summary.requestsPerSec
"""
    )
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    assert summary["failed"] == 1

    runs = store.read_runs(results)
    assert runs[0]["failure_reason"].startswith("output_file: not found")


def _phase_script(tmp_path: Path) -> Path:
    """A tiny helper script: `python phase.py <label> <marker_path>` appends a line to the marker."""
    p = tmp_path / "phase.py"
    p.write_text("import sys; open(sys.argv[2], 'a').write(sys.argv[1] + '\\n')\n")
    return p


def test_setup_and_teardown_run_around_each_rep(tmp_path):
    marker = tmp_path / "marker.txt"
    phase = _phase_script(tmp_path)
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
setup:
  - "python {phase} setup {marker}"
teardown:
  - "python {phase} teardown {marker}"
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
    assert summary["succeeded"] == 2
    lines = marker.read_text().splitlines()
    assert lines == ["setup", "teardown", "setup", "teardown"]


def test_setup_failure_aborts_rep_and_runs_teardown(tmp_path):
    marker = tmp_path / "marker.txt"
    phase = _phase_script(tmp_path)
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
setup:
  - "python -c \\"import sys; sys.exit(7)\\""
teardown:
  - "python {phase} teardown {marker}"
capture:
  rps: $.summary.requestsPerSec
"""
    )
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    assert summary["failed"] == 1
    assert summary["succeeded"] == 0
    runs = store.read_runs(results)
    assert runs[0]["failure_reason"].startswith("setup[0]")
    assert runs[0]["exit_code"] == -1
    # Teardown still ran best-effort
    assert marker.read_text().splitlines() == ["teardown"]


def test_teardown_failure_surfaces_but_keeps_exit_code(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
teardown:
  - "python -c \\"import sys; sys.exit(9)\\""
capture:
  rps: $.summary.requestsPerSec
"""
    )
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    # Counted as failed because failure_reason is set, but the benchmark itself succeeded.
    assert summary["failed"] == 1
    runs = store.read_runs(results)
    assert runs[0]["exit_code"] == 0
    assert runs[0]["failure_reason"].startswith("teardown[0]")
    # Measurement still captured
    assert runs[0]["results"].get("rps") is not None


def test_setup_interpolates_combo_vars(tmp_path):
    marker = tmp_path / "marker.txt"
    phase = _phase_script(tmp_path)
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
setup:
  - "python {phase} ${{workload}} {marker}"
capture:
  rps: $.summary.requestsPerSec
"""
    )
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    assert summary["succeeded"] == 1
    assert marker.read_text().splitlines() == ["read"]


def test_raw_output_captures_setup_teardown_steps(tmp_path):
    phase = _phase_script(tmp_path)
    marker = tmp_path / "marker.txt"
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
setup:
  - "python {phase} setup-out {marker}"
teardown:
  - "python {phase} teardown-out {marker}"
capture:
  rps: $.summary.requestsPerSec
"""
    )
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
        keep_raw=True,
    )
    assert summary["succeeded"] == 1

    run = store.read_runs(results)[0]
    assert run["raw_output_path"]
    raw = json.loads((results / run["raw_output_path"]).read_text())
    assert "setup" in raw and "teardown" in raw
    assert len(raw["setup"]) == 1 and len(raw["teardown"]) == 1
    assert raw["setup"][0]["exit_code"] == 0
    assert "setup-out" in raw["setup"][0]["command"]
    assert raw["teardown"][0]["exit_code"] == 0
    assert "teardown-out" in raw["teardown"][0]["command"]


def test_raw_output_separates_tool_stdout_from_output_file(tmp_path):
    out_file = tmp_path / "result.json"
    tool_script = tmp_path / "noisy_tool.py"
    tool_script.write_text(
        "import json, sys\n"
        "print('hello from stdout')\n"
        "sys.stderr.write('warn line\\n')\n"
        f"open(r'{out_file}', 'w').write(json.dumps({{'summary': {{'requestsPerSec': 7.0}}}}))\n"
    )
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {tool_script}"
timeout_seconds: 10
output_file: "{out_file}"
capture:
  rps: $.summary.requestsPerSec
"""
    )
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
        keep_raw=True,
    )
    assert summary["succeeded"] == 1

    run = store.read_runs(results)[0]
    raw = json.loads((results / run["raw_output_path"]).read_text())
    assert raw["stdout"].strip() == "hello from stdout"
    assert "warn line" in raw["stderr"]
    assert raw["output_file"]["path"] == str(out_file)
    assert '"requestsPerSec": 7.0' in raw["output_file"]["content"]


def test_raw_output_captures_setup_failure_with_teardown(tmp_path):
    phase = _phase_script(tmp_path)
    marker = tmp_path / "marker.txt"
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
setup:
  - "python -c \\"import sys; sys.stdout.write('setup-stdout'); sys.exit(7)\\""
teardown:
  - "python {phase} td {marker}"
capture:
  rps: $.summary.requestsPerSec
"""
    )
    results = tmp_path / "results"

    runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    run = store.read_runs(results)[0]
    assert run["raw_output_path"]  # written on failure regardless of keep_raw
    raw = json.loads((results / run["raw_output_path"]).read_text())
    assert raw["setup"][0]["exit_code"] == 7
    assert "setup-stdout" in raw["setup"][0]["stdout"]
    assert raw["teardown"][0]["exit_code"] == 0


def test_output_file_with_var_interpolation(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py --output-file {tmp_path}/result_${{workload}}.json"
timeout_seconds: 10
output_file: "{tmp_path}/result_${{workload}}.json"
capture:
  rps: $.summary.requestsPerSec
"""
    )
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    assert summary["succeeded"] == 1
    runs = store.read_runs(results)
    assert runs[0]["results"]["rps"] == 1000.0


# ---------- per_sweep phase ----------


def _per_sweep_tool_yaml(
    tmp_path: Path, *, per_sweep_var=None, setup_per_sweep=None, teardown_per_sweep=None
) -> Path:
    p = tmp_path / "tool.yaml"
    lines = [
        "name: faketool",
        f'command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"',
        "timeout_seconds: 10",
        "capture:",
        "  rps: $.summary.requestsPerSec",
    ]
    if per_sweep_var:
        lines.append(f"per_sweep_var: {per_sweep_var}")
    if setup_per_sweep:
        lines.append("setup_per_sweep:")
        for step in setup_per_sweep:
            lines.append(f"  - {json.dumps(step)}")
    if teardown_per_sweep:
        lines.append("teardown_per_sweep:")
        for step in teardown_per_sweep:
            lines.append(f"  - {json.dumps(step)}")
    p.write_text("\n".join(lines) + "\n")
    return p


def test_per_sweep_mode_a_runs_once(tmp_path):
    phase = _phase_script(tmp_path)
    marker = tmp_path / "marker.txt"
    manifest_dir = _make_manifest(
        tmp_path,
        [
            {"workload": "read", "concurrency": "1"},
            {"workload": "write", "concurrency": "8"},
        ],
    )
    tool_path = _per_sweep_tool_yaml(
        tmp_path,
        setup_per_sweep=[f"python {phase} sweep-setup {marker}"],
        teardown_per_sweep=[f"python {phase} sweep-teardown {marker}"],
    )
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=3,
    )
    assert summary["succeeded"] == 6
    # Setup fires once at start, teardown once at end, regardless of 6 reps.
    assert marker.read_text().splitlines() == ["sweep-setup", "sweep-teardown"]


def test_per_sweep_mode_b_runs_once_per_var_value(tmp_path):
    phase = _phase_script(tmp_path)
    marker = tmp_path / "marker.txt"
    manifest_dir = _make_manifest(
        tmp_path,
        [
            {"workload": "write", "concurrency": "8"},
            {"workload": "read", "concurrency": "1"},
            {"workload": "read", "concurrency": "8"},
            {"workload": "write", "concurrency": "1"},
        ],
    )
    tool_path = _per_sweep_tool_yaml(
        tmp_path,
        per_sweep_var="workload",
        setup_per_sweep=[f"python {phase} setup-${{workload}} {marker}"],
        teardown_per_sweep=[f"python {phase} teardown-${{workload}} {marker}"],
    )
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=2,
    )
    assert summary["succeeded"] == 8

    lines = marker.read_text().splitlines()
    # Two groups: read (sorted first), then write. Each gets one setup/teardown.
    assert lines == [
        "setup-read",
        "teardown-read",
        "setup-write",
        "teardown-write",
    ]


def test_per_sweep_mode_a_rejects_combo_var_refs(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool_path = _per_sweep_tool_yaml(
        tmp_path,
        setup_per_sweep=["echo ${workload}"],  # combo var not allowed in Mode A
    )
    results = tmp_path / "results"
    with pytest.raises(RunError) as ei:
        runner.execute_sweep(
            manifest_path=manifest_dir,
            tool_path=tool_path,
            results_dir=results,
            repeat=1,
        )
    assert "setup_per_sweep[0]" in str(ei.value)
    assert "${workload}" in str(ei.value)
    assert not (results / "runs.jsonl").exists()


def test_per_sweep_mode_b_rejects_other_combo_var_refs(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool_path = _per_sweep_tool_yaml(
        tmp_path,
        per_sweep_var="workload",
        setup_per_sweep=["echo ${concurrency}"],  # only ${workload} allowed in Mode B
    )
    results = tmp_path / "results"
    with pytest.raises(RunError) as ei:
        runner.execute_sweep(
            manifest_path=manifest_dir,
            tool_path=tool_path,
            results_dir=results,
            repeat=1,
        )
    assert "${concurrency}" in str(ei.value)
    assert "${workload}" in str(ei.value)  # the error names the allowed var


def test_per_sweep_var_missing_in_combos_errors(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    tool_path = _per_sweep_tool_yaml(
        tmp_path,
        per_sweep_var="missing_var",
        setup_per_sweep=["echo ${missing_var}"],
    )
    results = tmp_path / "results"
    with pytest.raises(RunError) as ei:
        runner.execute_sweep(
            manifest_path=manifest_dir,
            tool_path=tool_path,
            results_dir=results,
            repeat=1,
        )
    assert "per_sweep_var" in str(ei.value)
    assert "missing_var" in str(ei.value)


def test_per_sweep_setup_failure_records_failure_rows(tmp_path):
    phase = _phase_script(tmp_path)
    marker = tmp_path / "marker.txt"
    manifest_dir = _make_manifest(
        tmp_path,
        [
            {"workload": "read", "concurrency": "1"},
            {"workload": "read", "concurrency": "8"},
        ],
    )
    tool_path = _per_sweep_tool_yaml(
        tmp_path,
        setup_per_sweep=['python -c "import sys; sys.exit(5)"'],
        teardown_per_sweep=[f"python {phase} td {marker}"],
    )
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=2,
    )
    # 2 combos × 2 reps = 4 failure rows
    assert summary["failed"] == 4
    assert summary["succeeded"] == 0
    runs = store.read_runs(results)
    assert all(r["failure_reason"].startswith("per_sweep_setup[0]") for r in runs)
    # Teardown still ran best-effort
    assert marker.read_text().splitlines() == ["td"]
    # Raw sweep record written
    assert (results / "raw" / "sweep.json").exists()


def test_per_sweep_teardown_failure_folds_into_last_rep(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool_path = _per_sweep_tool_yaml(
        tmp_path,
        teardown_per_sweep=['python -c "import sys; sys.exit(9)"'],
    )
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=3,
    )
    runs = store.read_runs(results)
    # All 3 reps executed, but the last one inherits the teardown failure.
    assert len(runs) == 3
    assert runs[0]["failure_reason"] is None
    assert runs[1]["failure_reason"] is None
    assert runs[2]["failure_reason"].startswith("per_sweep_teardown[0]")
    assert summary["succeeded"] == 2
    assert summary["failed"] == 1


def test_per_sweep_skip_existing_skips_phases(tmp_path):
    phase = _phase_script(tmp_path)
    marker = tmp_path / "marker.txt"
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool_path = _per_sweep_tool_yaml(
        tmp_path,
        setup_per_sweep=[f"python {phase} sweep-setup {marker}"],
        teardown_per_sweep=[f"python {phase} sweep-teardown {marker}"],
    )
    results = tmp_path / "results"

    # First run: phases fire normally.
    runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=2,
    )
    assert marker.read_text().splitlines() == ["sweep-setup", "sweep-teardown"]

    # Second run with skip-existing: all reps skipped → phases must NOT fire again.
    marker.write_text("")
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=2,
        skip_existing=True,
    )
    assert summary["skipped"] == 2
    assert marker.read_text() == ""


def test_per_sweep_keep_raw_writes_per_group_files(tmp_path):
    manifest_dir = _make_manifest(
        tmp_path,
        [
            {"workload": "read", "concurrency": "1"},
            {"workload": "write", "concurrency": "1"},
        ],
    )
    tool_path = _per_sweep_tool_yaml(
        tmp_path,
        per_sweep_var="workload",
        setup_per_sweep=["echo hello-${workload}"],
    )
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
        keep_raw=True,
    )
    assert summary["succeeded"] == 2

    raw_read = json.loads((results / "raw" / "sweep-read.json").read_text())
    raw_write = json.loads((results / "raw" / "sweep-write.json").read_text())
    assert "hello-read" in raw_read["setup"][0]["stdout"]
    assert "hello-write" in raw_write["setup"][0]["stdout"]


def test_per_sweep_dry_run_prints_preview(tmp_path):
    manifest_dir = _make_manifest(
        tmp_path,
        [
            {"workload": "read", "concurrency": "1"},
            {"workload": "write", "concurrency": "1"},
        ],
    )
    tool_path = _per_sweep_tool_yaml(
        tmp_path,
        per_sweep_var="workload",
        setup_per_sweep=["echo setup-${workload}"],
        teardown_per_sweep=["echo teardown-${workload}"],
    )
    results = tmp_path / "results"
    buf = io.StringIO()

    runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
        dry_run=True,
        progress_stream=buf,
    )
    out = buf.getvalue()
    assert "[per_sweep setup 0, workload=read]: echo setup-read" in out
    assert "[per_sweep teardown 0, workload=read]: echo teardown-read" in out
    assert "[per_sweep setup 0, workload=write]: echo setup-write" in out
    assert not (results / "runs.jsonl").exists()


def test_per_sweep_run_id_interpolated_in_setup_and_teardown(tmp_path):
    """_run_id resolves to a UUID-shaped string in setup_per_sweep and teardown_per_sweep."""
    phase = _phase_script(tmp_path)
    setup_marker = tmp_path / "setup_id.txt"
    teardown_marker = tmp_path / "teardown_id.txt"
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool_path = _per_sweep_tool_yaml(
        tmp_path,
        setup_per_sweep=[f"python {phase} ${{_run_id}} {setup_marker}"],
        teardown_per_sweep=[f"python {phase} ${{_run_id}} {teardown_marker}"],
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    assert summary["succeeded"] == 1
    setup_id = setup_marker.read_text().strip()
    teardown_id = teardown_marker.read_text().strip()
    # Both are UUID-shaped and identical (same per-group UUID)
    assert len(setup_id) == 36
    assert setup_id == teardown_id


def test_per_sweep_envfile_interpolated_in_setup_and_teardown(tmp_path):
    """_envfile resolves to an existing file path in setup_per_sweep and teardown_per_sweep."""
    phase = _phase_script(tmp_path)
    setup_marker = tmp_path / "setup_envfile.txt"
    teardown_marker = tmp_path / "teardown_envfile.txt"
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool_path = _per_sweep_tool_yaml(
        tmp_path,
        setup_per_sweep=[f"python {phase} ${{_envfile}} {setup_marker}"],
        teardown_per_sweep=[f"python {phase} ${{_envfile}} {teardown_marker}"],
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    assert summary["succeeded"] == 1
    envfile_path = setup_marker.read_text().strip()
    assert envfile_path == teardown_marker.read_text().strip()
    assert Path(envfile_path).exists()


def test_per_sweep_mode_a_allows_envfile_and_run_id(tmp_path):
    """validate_per_sweep does not reject _envfile or _run_id references."""
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool_path = _per_sweep_tool_yaml(
        tmp_path,
        setup_per_sweep=["echo ${_envfile} ${_run_id}"],
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    assert summary["succeeded"] == 1


def test_per_sweep_mode_b_allows_envfile_and_run_id(tmp_path):
    """validate_per_sweep does not reject _envfile or _run_id in Mode B (per_sweep_var set)."""
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool_path = _per_sweep_tool_yaml(
        tmp_path,
        per_sweep_var="workload",
        setup_per_sweep=["echo ${workload} ${_envfile} ${_run_id}"],
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    assert summary["succeeded"] == 1


def _per_sweep_tool_yaml_raw(tmp_path: Path, *, per_sweep_var_yaml: str, setup_per_sweep=None) -> Path:
    """Like _per_sweep_tool_yaml but accepts a raw YAML snippet for per_sweep_var."""
    p = tmp_path / "tool.yaml"
    lines = [
        "name: faketool",
        f'command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"',
        "timeout_seconds: 10",
        "capture:",
        "  rps: $.summary.requestsPerSec",
        f"per_sweep_var: {per_sweep_var_yaml}",
    ]
    if setup_per_sweep:
        lines.append("setup_per_sweep:")
        for step in setup_per_sweep:
            lines.append(f"  - {json.dumps(step)}")
    p.write_text("\n".join(lines) + "\n")
    return p


def test_per_sweep_multi_var_groups_by_composite_key(tmp_path):
    """per_sweep_var list groups by all listed vars; free var varies within each group."""
    phase = _phase_script(tmp_path)
    marker = tmp_path / "marker.txt"
    manifest_dir = _make_manifest(
        tmp_path,
        [
            {"backend": "pg", "workload": "read", "concurrency": "1"},
            {"backend": "pg", "workload": "read", "concurrency": "8"},
            {"backend": "pg", "workload": "write", "concurrency": "1"},
            {"backend": "mysql", "workload": "read", "concurrency": "1"},
        ],
    )
    tool_path = _per_sweep_tool_yaml_raw(
        tmp_path,
        per_sweep_var_yaml="[backend, workload]",
        setup_per_sweep=[f"python {phase} setup-${{backend}}-${{workload}} {marker}"],
    )
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    assert summary["succeeded"] == 4

    lines = marker.read_text().splitlines()
    # Three unique (backend, workload) groups: (mysql, read), (pg, read), (pg, write)
    assert sorted(lines) == ["setup-mysql-read", "setup-pg-read", "setup-pg-write"]


def test_per_sweep_negation_groups_by_all_except_excluded(tmp_path):
    """per_sweep_var: '!concurrency' groups by all vars except concurrency."""
    phase = _phase_script(tmp_path)
    marker = tmp_path / "marker.txt"
    manifest_dir = _make_manifest(
        tmp_path,
        [
            {"workload": "read", "concurrency": "1"},
            {"workload": "read", "concurrency": "8"},
            {"workload": "write", "concurrency": "1"},
        ],
    )
    tool_path = _per_sweep_tool_yaml_raw(
        tmp_path,
        per_sweep_var_yaml="'!concurrency'",
        setup_per_sweep=[f"python {phase} setup-${{workload}} {marker}"],
    )
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    assert summary["succeeded"] == 3

    lines = marker.read_text().splitlines()
    # Two unique workload groups: read (with concurrency 1 and 8 inside), write
    assert sorted(lines) == ["setup-read", "setup-write"]


def test_per_sweep_negation_rejects_excluded_var_in_steps(tmp_path):
    """Negation: the excluded variable must not appear in per_sweep steps."""
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool_path = _per_sweep_tool_yaml_raw(
        tmp_path,
        per_sweep_var_yaml="'!concurrency'",
        setup_per_sweep=["echo ${concurrency}"],  # excluded var, not allowed
    )
    results = tmp_path / "results"
    with pytest.raises(RunError) as ei:
        runner.execute_sweep(
            manifest_path=manifest_dir,
            tool_path=tool_path,
            results_dir=results,
            repeat=1,
        )
    assert "${concurrency}" in str(ei.value)


# ---------------------------------------------------------------------------
# nested scopes
# ---------------------------------------------------------------------------


def _scopes_tool_yaml(tmp_path: Path, *, scopes_yaml: str, extra: str = "") -> Path:
    p = tmp_path / "tool.yaml"
    p.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
{extra}
scopes:
{scopes_yaml}
"""
    )
    return p


def test_scopes_two_levels_correct_execution_order(tmp_path):
    """Outer scope setup/teardown bracket inner scope setup/teardown bracket reps."""
    phase = _phase_script(tmp_path)
    marker = tmp_path / "marker.txt"
    manifest_dir = _make_manifest(
        tmp_path,
        [
            {"backend": "pg", "workload": "read"},
            {"backend": "pg", "workload": "write"},
            {"backend": "mysql", "workload": "read"},
        ],
    )
    tool_path = _scopes_tool_yaml(
        tmp_path,
        scopes_yaml=f"""
  - vars: backend
    setup:
      - "python {phase} outer-setup-${{backend}} {marker}"
    teardown:
      - "python {phase} outer-teardown-${{backend}} {marker}"
  - vars: [backend, workload]
    setup:
      - "python {phase} inner-setup-${{backend}}-${{workload}} {marker}"
    teardown:
      - "python {phase} inner-teardown-${{backend}}-${{workload}} {marker}"
""",
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=2,
    )
    assert summary["succeeded"] == 6
    lines = marker.read_text().splitlines()
    # Sorted order: mysql first, then pg (alphabetical). Within pg: read then write.
    assert lines == [
        "outer-setup-mysql",
        "inner-setup-mysql-read",
        "inner-teardown-mysql-read",
        "outer-teardown-mysql",
        "outer-setup-pg",
        "inner-setup-pg-read",
        "inner-teardown-pg-read",
        "inner-setup-pg-write",
        "inner-teardown-pg-write",
        "outer-teardown-pg",
    ]


def test_scopes_reps_run_within_innermost_group(tmp_path):
    """All reps for a combo run inside the innermost scope group, not repeatedly setting up."""
    phase = _phase_script(tmp_path)
    marker = tmp_path / "marker.txt"
    manifest_dir = _make_manifest(
        tmp_path,
        [{"backend": "pg", "workload": "read"}],
    )
    tool_path = _scopes_tool_yaml(
        tmp_path,
        scopes_yaml=f"""
  - vars: backend
    setup:
      - "python {phase} outer-setup {marker}"
    teardown:
      - "python {phase} outer-teardown {marker}"
  - vars: [backend, workload]
    setup:
      - "python {phase} inner-setup {marker}"
    teardown:
      - "python {phase} inner-teardown {marker}"
""",
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=3,
    )
    assert summary["succeeded"] == 3
    lines = marker.read_text().splitlines()
    # 3 reps produce no extra setup/teardown lines
    assert lines == ["outer-setup", "inner-setup", "inner-teardown", "outer-teardown"]


def test_scopes_outer_var_accessible_in_inner_steps(tmp_path):
    """Inner scope steps can reference vars from outer scopes."""
    phase = _phase_script(tmp_path)
    marker = tmp_path / "marker.txt"
    manifest_dir = _make_manifest(
        tmp_path,
        [{"backend": "pg", "workload": "read"}],
    )
    tool_path = _scopes_tool_yaml(
        tmp_path,
        scopes_yaml=f"""
  - vars: backend
    setup:
      - "python {phase} outer-${{backend}} {marker}"
  - vars: workload
    setup:
      - "python {phase} inner-${{backend}}-${{workload}} {marker}"
""",
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    assert summary["succeeded"] == 1
    assert marker.read_text().splitlines() == ["outer-pg", "inner-pg-read"]


def test_scopes_setup_failure_records_inner_reps_as_failed(tmp_path):
    """Setup failure at outer scope marks all reps in that outer group as failed."""
    manifest_dir = _make_manifest(
        tmp_path,
        [
            {"backend": "pg", "workload": "read"},
            {"backend": "pg", "workload": "write"},
            {"backend": "mysql", "workload": "read"},
        ],
    )
    tool_path = _scopes_tool_yaml(
        tmp_path,
        scopes_yaml="""
  - vars: backend
    setup:
      - "python -c \\"import sys; sys.exit(5 if '${backend}' == 'pg' else 0)\\""
  - vars: [backend, workload]
""",
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=2,
    )
    # pg group fails (2 combos × 2 reps = 4), mysql succeeds (1 combo × 2 reps = 2)
    assert summary["failed"] == 4
    assert summary["succeeded"] == 2


def test_scopes_inner_setup_failure_records_only_inner_group(tmp_path):
    """Setup failure at inner scope marks only that inner group's reps as failed."""
    manifest_dir = _make_manifest(
        tmp_path,
        [
            {"backend": "pg", "workload": "read"},
            {"backend": "pg", "workload": "write"},
        ],
    )
    tool_path = _scopes_tool_yaml(
        tmp_path,
        scopes_yaml="""
  - vars: backend
  - vars: [backend, workload]
    setup:
      - "python -c \\"import sys; sys.exit(3 if '${workload}' == 'write' else 0)\\""
""",
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=2,
    )
    # read succeeds (2 reps), write fails (2 reps)
    assert summary["succeeded"] == 2
    assert summary["failed"] == 2


def test_scopes_innermost_teardown_folds_into_last_rep(tmp_path):
    """Inner scope teardown failure is folded into the last rep's failure_reason."""
    manifest_dir = _make_manifest(tmp_path, [{"backend": "pg", "workload": "read"}])
    tool_path = _scopes_tool_yaml(
        tmp_path,
        scopes_yaml="""
  - vars: backend
  - vars: [backend, workload]
    teardown:
      - "python -c \\"import sys; sys.exit(7)\\""
""",
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=3,
    )
    runs = store.read_runs(results)
    assert len(runs) == 3
    assert runs[0]["failure_reason"] is None
    assert runs[1]["failure_reason"] is None
    assert runs[2]["failure_reason"].startswith("per_sweep_teardown[0]")
    assert summary["succeeded"] == 2
    assert summary["failed"] == 1


def test_scopes_rejects_outer_var_in_outer_steps_only_rule(tmp_path):
    """Inner scope steps referencing a var not in any scope raise RunError."""
    manifest_dir = _make_manifest(tmp_path, [{"backend": "pg", "workload": "read"}])
    tool_path = _scopes_tool_yaml(
        tmp_path,
        scopes_yaml="""
  - vars: backend
    setup:
      - "echo ${workload}"
  - vars: workload
""",
    )
    results = tmp_path / "results"
    with pytest.raises(RunError) as ei:
        runner.execute_sweep(
            manifest_path=manifest_dir,
            tool_path=tool_path,
            results_dir=results,
            repeat=1,
        )
    assert "${workload}" in str(ei.value)
    assert "scopes[0].setup[0]" in str(ei.value)


def test_scopes_rejects_combination_with_per_sweep_var(tmp_path):
    """Combining scopes with per_sweep_var raises RunError at load time."""
    p = tmp_path / "tool.yaml"
    p.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
per_sweep_var: backend
scopes:
  - vars: backend
"""
    )
    with pytest.raises(RunError) as ei:
        from abench_speckz import tools

        tools.load_tool(p)
    assert "scopes" in str(ei.value)
    assert "per_sweep_var" in str(ei.value)


def test_scopes_negation_in_vars(tmp_path):
    """Negation in scope vars expands to all vars except the excluded one."""
    phase = _phase_script(tmp_path)
    marker = tmp_path / "marker.txt"
    manifest_dir = _make_manifest(
        tmp_path,
        [
            {"backend": "pg", "workload": "read", "concurrency": "1"},
            {"backend": "pg", "workload": "read", "concurrency": "8"},
            {"backend": "pg", "workload": "write", "concurrency": "1"},
        ],
    )
    tool_path = _scopes_tool_yaml(
        tmp_path,
        scopes_yaml=f"""
  - vars: backend
    setup:
      - "python {phase} outer-${{backend}} {marker}"
  - vars: "!concurrency"
    setup:
      - "python {phase} inner-${{backend}}-${{workload}} {marker}"
""",
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    assert summary["succeeded"] == 3
    lines = marker.read_text().splitlines()
    # One outer group (pg), two inner groups (pg-read, pg-write)
    # concurrency varies within each inner group; outer scope has no teardown
    assert lines == ["outer-pg", "inner-pg-read", "inner-pg-write"]


def test_scopes_dry_run_shows_nested_structure(tmp_path):
    """Dry run with scopes prints setup/teardown for each scope level."""
    manifest_dir = _make_manifest(
        tmp_path,
        [
            {"backend": "pg", "workload": "read"},
            {"backend": "pg", "workload": "write"},
        ],
    )
    tool_path = _scopes_tool_yaml(
        tmp_path,
        scopes_yaml="""
  - vars: backend
    setup:
      - "echo outer-setup-${backend}"
    teardown:
      - "echo outer-teardown-${backend}"
  - vars: [backend, workload]
    setup:
      - "echo inner-setup-${backend}-${workload}"
""",
    )
    results = tmp_path / "results"
    buf = io.StringIO()
    runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
        dry_run=True,
        progress_stream=buf,
    )
    out = buf.getvalue()
    assert "[scope0 setup 0, backend=pg]: echo outer-setup-pg" in out
    assert "[scope1 setup 0, backend=pg, workload=read]: echo inner-setup-pg-read" in out
    assert "[scope1 setup 0, backend=pg, workload=write]: echo inner-setup-pg-write" in out
    assert "[scope0 teardown 0, backend=pg]: echo outer-teardown-pg" in out
    assert not (results / "runs.jsonl").exists()


def test_scopes_var_missing_from_combos_raises(tmp_path):
    """Scope var not in any combo raises RunError before any run."""
    manifest_dir = _make_manifest(tmp_path, [{"backend": "pg"}])
    tool_path = _scopes_tool_yaml(
        tmp_path,
        scopes_yaml="""
  - vars: backend
  - vars: missing_var
""",
    )
    results = tmp_path / "results"
    with pytest.raises(RunError) as ei:
        runner.execute_sweep(
            manifest_path=manifest_dir,
            tool_path=tool_path,
            results_dir=results,
            repeat=1,
        )
    assert "missing_var" in str(ei.value)
    assert not (results / "runs.jsonl").exists()


def test_scopes_scope_without_setup_teardown_is_valid(tmp_path):
    """A scope entry with no setup or teardown is a pure grouping scope."""
    manifest_dir = _make_manifest(
        tmp_path,
        [
            {"backend": "pg", "workload": "read"},
            {"backend": "pg", "workload": "write"},
        ],
    )
    # Outer scope has no steps — just groups; inner scope has setup
    phase = _phase_script(tmp_path)
    marker = tmp_path / "marker.txt"
    tool_path = _scopes_tool_yaml(
        tmp_path,
        scopes_yaml=f"""
  - vars: backend
  - vars: [backend, workload]
    setup:
      - "python {phase} inner-${{backend}}-${{workload}} {marker}"
""",
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    assert summary["succeeded"] == 2
    lines = sorted(marker.read_text().splitlines())
    assert lines == ["inner-pg-read", "inner-pg-write"]


# ---------------------------------------------------------------------------
# env_probes
# ---------------------------------------------------------------------------


def test_collect_probe_results_success():
    probes = {"py_version": "python --version", "echo_hi": "echo hi"}
    results = runner.collect_probe_results(probes, timeout_seconds=10)
    assert results["py_version"] is not None and "Python" in results["py_version"]
    assert results["echo_hi"] == "hi"


def test_collect_probe_results_nonzero_returns_none():
    probes = {"bad": 'python -c "import sys; sys.exit(1)"'}
    results = runner.collect_probe_results(probes, timeout_seconds=10)
    assert results["bad"] is None


def test_collect_probe_results_missing_command_returns_none():
    probes = {"nope": "no_such_command_abcxyz --version"}
    results = runner.collect_probe_results(probes, timeout_seconds=10)
    assert results["nope"] is None


def test_env_probes_written_to_snapshot(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
env_probes:
  echo_probe: "echo probe-value"
"""
    )
    results = tmp_path / "results"
    runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    snapshot = json.loads((results / "env.snapshot.json").read_text())
    assert "probes" in snapshot
    assert snapshot["probes"]["echo_probe"] == "probe-value"


def test_env_probes_absent_when_not_configured(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    tool_path = _tool_yaml(tmp_path)
    results = tmp_path / "results"
    runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    snapshot = json.loads((results / "env.snapshot.json").read_text())
    assert "probes" not in snapshot


# ---------------------------------------------------------------------------
# _envfile synthetic variable
# ---------------------------------------------------------------------------


def test_envfile_variable_available_in_command(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    # Command writes _envfile path to a sidecar so we can inspect it
    out_file = tmp_path / "envfile_path.txt"
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
setup:
  - "python -c \\"import sys; open('{out_file}', 'w').write(sys.argv[1])\\" ${{_envfile}}"
"""
    )
    results = tmp_path / "results"
    runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    written = out_file.read_text().strip()
    assert written.endswith(".env")
    assert Path(written).exists()


def test_results_dir_variable_available_in_setup(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    out_file = tmp_path / "results_dir_path.txt"
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
setup:
  - "python -c \\"import sys; open('{out_file}', 'w').write(sys.argv[1])\\" ${{_results_dir}}"
"""
    )
    results = tmp_path / "results"
    runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    written = out_file.read_text().strip()
    assert written == str(results.resolve())


def test_results_dir_variable_available_in_post_run(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    out_file = tmp_path / "post_run_results_dir.txt"
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
post_run:
  - "python -c \\"import sys; open('{out_file}', 'w').write(sys.argv[1])\\" ${{_results_dir}}"
"""
    )
    results = tmp_path / "results"
    runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    written = out_file.read_text().strip()
    assert written == str(results.resolve())


# ---------------------------------------------------------------------------
# post_run phase
# ---------------------------------------------------------------------------


def test_post_run_receives_result_vars(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    sidecar = tmp_path / "post_run_vars.txt"
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
post_run:
  - "python -c \\"import sys; open('{sidecar}', 'w').write(','.join(sys.argv[1:]))\\" ${{_exit_code}} ${{_run_id}} ${{_started_at}} ${{_finished_at}} ${{_duration_ms}}"
"""
    )
    results = tmp_path / "results"
    runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    parts = sidecar.read_text().strip().split(",")
    assert parts[0] == "0"  # _exit_code
    assert len(parts[1]) == 36  # _run_id is a UUID
    assert "T" in parts[2]  # _started_at is ISO timestamp
    assert "T" in parts[3]  # _finished_at is ISO timestamp
    assert parts[4].isdigit()  # _duration_ms


def test_post_run_runs_on_benchmark_failure(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    sidecar = tmp_path / "post_run_exit_code.txt"
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python -c \\"import sys; sys.exit(1)\\""
timeout_seconds: 10
post_run:
  - "python -c \\"open('{sidecar}', 'w').write('${{_exit_code}}')\\"  "
"""
    )
    results = tmp_path / "results"
    runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    assert sidecar.read_text().strip() == "1"


def test_post_run_run_id_matches_runs_jsonl(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    sidecar = tmp_path / "run_id.txt"
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
post_run:
  - "python -c \\"open('{sidecar}', 'w').write('${{_run_id}}')\\"  "
"""
    )
    results = tmp_path / "results"
    runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    runs = store.read_runs(results)
    assert len(runs) == 1
    assert runs[0]["run_id"] == sidecar.read_text().strip()


def test_post_run_steps_in_raw_output(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
post_run:
  - "python -c \\"print('collected')\\"  "
"""
    )
    results = tmp_path / "results"
    runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
        keep_raw=True,
    )
    runs = store.read_runs(results)
    raw = store.load_raw_run(results, runs[0]["run_id"])
    assert "post_run" in raw
    assert raw["post_run"][0]["exit_code"] == 0
    assert "collected" in raw["post_run"][0]["stdout"]


# ---------------------------------------------------------------------------
# combo_probes
# ---------------------------------------------------------------------------


def test_combo_probes_results_in_runs_jsonl(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
combo_probes:
  workload_echo: "python -c \\"import sys; print(sys.argv[1])\\" ${{workload}}"
"""
    )
    results = tmp_path / "results"
    runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    runs = store.read_runs(results)
    assert runs[0]["combo_probes"] == {"workload_echo": "read"}


def test_combo_probes_stored_in_combo_probes_json(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}, {"workload": "write"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
combo_probes:
  workload_echo: "python -c \\"import sys; print(sys.argv[1])\\" ${{workload}}"
"""
    )
    results = tmp_path / "results"
    runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    probes = store.read_combo_probes(results)
    assert len(probes) == 2
    values = {v["workload_echo"] for v in probes.values()}
    assert values == {"read", "write"}


def test_combo_probes_run_once_per_hash_not_per_rep(tmp_path):
    counter_file = tmp_path / "count.txt"
    counter_file.write_text("0")
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
combo_probes:
  count: "python -c \\"c=int(open('{counter_file}').read())+1; open('{counter_file}','w').write(str(c)); print(c)\\""
"""
    )
    results = tmp_path / "results"
    runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=3,
    )
    # Probe ran once; all 3 reps share the same probe result
    assert counter_file.read_text() == "1"
    runs = store.read_runs(results)
    assert all(r["combo_probes"]["count"] == "1" for r in runs)


def test_combo_probes_null_on_failure(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
combo_probes:
  failing: "python -c \\"import sys; sys.exit(1)\\""
  missing: "nonexistent-command-xyz"
"""
    )
    results = tmp_path / "results"
    runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    runs = store.read_runs(results)
    assert runs[0]["combo_probes"] == {"failing": None, "missing": None}
    # Benchmark still ran despite probe failures
    assert runs[0]["exit_code"] == 0


# ---------------------------------------------------------------------------
# monitor
# ---------------------------------------------------------------------------


def test_monitor_starts_and_stops_around_benchmark(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
monitor:
  - "python -c \\"import time; time.sleep(30)\\""
"""
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
        keep_raw=True,
    )
    assert summary["succeeded"] == 1

    runs = store.read_runs(results)
    raw = store.load_raw_run(results, runs[0]["run_id"])
    assert "monitor_start" in raw
    assert "monitor_stop" in raw
    assert raw["monitor_start"][0]["pid"] is not None
    assert raw["monitor_stop"][0]["exit_code"] is not None


def test_monitor_pipe_command_stops_without_hanging(tmp_path):
    # A pipeline monitor leaves grandchild processes alive after the shell is
    # killed. Without start_new_session+killpg, stop_monitors hangs indefinitely.
    import time

    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
monitor:
  - "sleep 30 | cat"
"""
    )
    results = tmp_path / "results"
    t0 = time.monotonic()
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    elapsed = time.monotonic() - t0

    assert summary["succeeded"] == 1
    assert elapsed < 30, f"sweep took {elapsed:.1f}s — likely hung stopping pipe monitor"


def test_monitor_does_not_abort_benchmark_on_start_failure(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
monitor:
  - "nonexistent-monitor-command-xyz"
"""
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    # Bad monitor command does not abort the benchmark sweep.
    assert summary["succeeded"] == 1
    runs = store.read_runs(results)
    assert runs[0]["exit_code"] == 0


def test_monitor_interpolates_combo_vars(tmp_path):
    marker = tmp_path / "marker.txt"
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
monitor:
  - "python -c \\"open('{marker}', 'w').write('${{workload}}'); import time; time.sleep(30)\\""
"""
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    assert summary["succeeded"] == 1
    assert marker.read_text() == "read"


def test_monitor_runs_concurrently_with_benchmark(tmp_path):
    started_file = tmp_path / "monitor_started.txt"
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    # Monitor writes a file immediately, then sleeps.
    # Benchmark is the fake_tool which runs after monitor has been started.
    monitor_script = tmp_path / "mon.py"
    monitor_script.write_text(f"open(r'{started_file}', 'w').write('1')\nimport time; time.sleep(30)\n")
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
monitor:
  - "python {monitor_script}"
"""
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    assert summary["succeeded"] == 1
    # Monitor wrote the started file, proving it ran while the benchmark was executing.
    assert started_file.exists()


# --- load_manifest / load_envfile / config_hash / monitor unit tests ---


def test_load_manifest_missing_raises(tmp_path):
    with pytest.raises(RunError, match="manifest not found"):
        runner.load_manifest(tmp_path / "nope.json")


def test_load_manifest_directory_form(tmp_path):
    out = _make_manifest(tmp_path, [{"a": "1"}])
    path, entries = runner.load_manifest(out)
    assert path == out / "manifest.json"
    assert len(entries) == 1 and entries[0]["vars"] == {"a": "1"}


def test_load_manifest_rejects_non_list(tmp_path):
    p = tmp_path / "manifest.json"
    p.write_text('{"not": "a list"}')
    with pytest.raises(RunError, match="expected an array"):
        runner.load_manifest(p)


def test_load_envfile_basic(tmp_path):
    p = tmp_path / "x.env"
    p.write_text("A=1\nB=hello world\n")
    assert runner.load_envfile(p) == {"A": "1", "B": "hello world"}


def test_load_envfile_skips_comments_and_blank_lines(tmp_path):
    p = tmp_path / "x.env"
    p.write_text("# this is a comment\n\nA=1\n# trailing comment\nB=2\n")
    assert runner.load_envfile(p) == {"A": "1", "B": "2"}


def test_load_envfile_skips_lines_without_equals(tmp_path):
    p = tmp_path / "x.env"
    p.write_text("A=1\nBADLINE\nC=3\n")
    assert runner.load_envfile(p) == {"A": "1", "C": "3"}


def test_load_envfile_value_containing_equals(tmp_path):
    p = tmp_path / "x.env"
    p.write_text("URL=http://x?a=b\n")
    # partition keeps everything after the first '=' as the value.
    assert runner.load_envfile(p) == {"URL": "http://x?a=b"}


def test_config_hash_stable_and_independent_of_key_order():
    a = runner.config_hash({"workload": "read", "backend": "postgres"})
    b = runner.config_hash({"backend": "postgres", "workload": "read"})
    assert a == b
    # Stable hex (16-char prefix); locks the hashing contract so a refactor
    # cannot silently invalidate every results dir written before the change.
    assert a == "941764a969a7517e"


def test_config_hash_handles_non_json_value():
    # Path is not JSON-serializable by default; default=str must serialize it.
    h = runner.config_hash({"p": Path("/tmp/x")})
    assert isinstance(h, str) and len(h) == 16


def test_monitor_interpolation_failure_recorded(tmp_path):
    started_file = tmp_path / "started.txt"
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    tool_path = tmp_path / "tool.yaml"
    # ${nonexistent} fails interpolation; the run should still succeed.
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
monitor:
  - "echo ${{nonexistent_var}} {started_file}"
"""
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    # Benchmark still succeeds, but raw is forced because a monitor failed to start.
    assert summary["succeeded"] == 1
    runs = store.read_runs(results)
    raw = store.load_raw_run(results, runs[0]["run_id"])
    assert raw is not None
    starts = raw.get("monitor_start") or []
    assert starts and any("interpolation" in (s.get("error") or "") for s in starts)


def test_monitor_string_syntax_defaults_to_per_rep(tmp_path):
    """Backwards compat: plain string monitor entries default to span=per_rep."""
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
monitor:
  - "python -c \\"import time; time.sleep(30)\\""
"""
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
        keep_raw=True,
    )
    assert summary["succeeded"] == 1
    runs = store.read_runs(results)
    raw = store.load_raw_run(results, runs[0]["run_id"])
    assert "monitor_start" in raw, "per_rep monitor records should appear in run raw"
    assert raw["monitor_start"][0]["pid"] is not None


def test_monitor_dict_per_rep_explicit(tmp_path):
    """Object-form monitor with explicit span: per_rep works same as string form."""
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
monitor:
  - command: "python -c \\"import time; time.sleep(30)\\""
    span: per_rep
"""
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
        keep_raw=True,
    )
    assert summary["succeeded"] == 1
    runs = store.read_runs(results)
    raw = store.load_raw_run(results, runs[0]["run_id"])
    assert "monitor_start" in raw
    assert raw["monitor_start"][0]["pid"] is not None


def test_monitor_per_sweep_spans_entire_sweep(tmp_path):
    """A per_sweep monitor runs for the whole sweep; records land in raw/sweep_monitors.json."""
    flag = tmp_path / "monitor_ran.txt"
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}, {"workload": "write"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
monitor:
  - command: "python -c \\"import time; open('{flag}', 'w').write('alive'); time.sleep(60)\\""
    span: per_sweep
"""
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
        keep_raw=True,
    )
    assert summary["succeeded"] == 2

    # Monitor records are NOT in individual run raw files
    runs = store.read_runs(results)
    for run in runs:
        raw = store.load_raw_run(results, run["run_id"])
        assert "monitor_start" not in raw, "per_sweep monitor should not appear in per-run raw"

    # Records land in raw/sweep_monitors.json
    sweep_mon = results / "raw" / "sweep_monitors.json"
    assert sweep_mon.exists(), "sweep_monitors.json should be written with keep_raw=True"
    doc = json.loads(sweep_mon.read_text())
    assert "monitor_start" in doc
    assert doc["monitor_start"][0]["pid"] is not None
    assert "monitor_stop" in doc
    assert doc["monitor_stop"][0]["exit_code"] is not None


def test_monitor_per_sweep_records_written_on_error(tmp_path):
    """sweep_monitors.json is written with keep_raw; bad command exits non-zero."""
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
monitor:
  - command: "nonexistent-sweep-monitor-xyz"
    span: per_sweep
"""
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
        keep_raw=True,
    )
    assert summary["succeeded"] == 1

    sweep_mon = results / "raw" / "sweep_monitors.json"
    assert sweep_mon.exists(), "sweep_monitors.json written when keep_raw=True"
    doc = json.loads(sweep_mon.read_text())
    # Shell started OK (pid present), but exited non-zero (command not found = 127).
    assert doc["monitor_start"][0].get("pid") is not None
    assert doc["monitor_stop"][0]["exit_code"] != 0


def test_monitor_per_group_spans_group(tmp_path):
    """A per_group monitor runs across all reps of a per_sweep group; records in sweep raw file."""
    flag = tmp_path / "monitor_alive.txt"
    manifest_dir = _make_manifest(tmp_path, [{"host": "a"}, {"host": "b"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
per_sweep_var: host
monitor:
  - command: "python -c \\"import time; open('{flag}', 'w').write('alive'); time.sleep(60)\\""
    span: per_group
"""
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
        keep_raw=True,
    )
    assert summary["succeeded"] == 2

    # Monitor records are NOT in per-run raw files
    runs = store.read_runs(results)
    for run in runs:
        raw = store.load_raw_run(results, run["run_id"])
        assert "monitor_start" not in raw, "per_group monitor should not appear in per-run raw"

    # Records appear in at least one per-group sweep raw file
    raw_dir = results / "raw"
    sweep_files = list(raw_dir.glob("sweep-*.json"))
    assert sweep_files, "per-group sweep raw files should exist with keep_raw=True"
    all_have_monitor = all("monitor_start" in json.loads(f.read_text()) for f in sweep_files)
    assert all_have_monitor, "each group's sweep raw file should contain monitor_start"


def test_monitor_mixed_spans(tmp_path):
    """per_sweep and per_rep monitors coexist; records land in correct locations."""
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
monitor:
  - command: "python -c \\"import time; time.sleep(60)\\""
    span: per_sweep
  - command: "python -c \\"import time; time.sleep(60)\\""
    span: per_rep
"""
    )
    results = tmp_path / "results"
    summary = runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
        keep_raw=True,
    )
    assert summary["succeeded"] == 1

    # per_rep monitor in run raw
    runs = store.read_runs(results)
    raw = store.load_raw_run(results, runs[0]["run_id"])
    assert "monitor_start" in raw, "per_rep monitor should be in run raw"

    # per_sweep monitor in sweep_monitors.json
    sweep_mon = results / "raw" / "sweep_monitors.json"
    assert sweep_mon.exists(), "sweep_monitors.json should exist for per_sweep monitor"
    doc = json.loads(sweep_mon.read_text())
    assert doc["monitor_start"][0]["pid"] is not None


def test_collect_env_metadata_returns_known_keys():
    info = runner.collect_env_metadata()
    for key in ("host", "system", "release", "machine", "python", "cpu_count"):
        assert key in info


# ---------------------------------------------------------------------------
# grafana_links
# ---------------------------------------------------------------------------


def test_grafana_links_stored_in_runs_jsonl(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
grafana_links:
  - label: Overview
    url: "https://grafana.example.com/d/abc?from=${{_started_at_ms}}&to=${{_finished_at_ms}}&workload=${{workload}}"
"""
    )
    results = tmp_path / "results"
    runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    runs = store.read_runs(results)
    assert len(runs) == 1
    links = runs[0].get("grafana_links")
    assert links is not None and len(links) == 1
    assert links[0]["label"] == "Overview"
    url = links[0]["url"]
    # URL should have ms-precision timestamps embedded
    assert "from=" in url and "to=" in url
    assert "workload=read" in url
    # Timestamps should be numeric ms strings (not empty)
    import re

    ms_matches = re.findall(r"(?:from|to)=(\d+)", url)
    assert len(ms_matches) == 2
    assert all(len(ms) > 10 for ms in ms_matches)  # epoch ms is >10 digits since ~2001


def test_grafana_links_absent_when_not_configured(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    tool_path = _tool_yaml(tmp_path)
    results = tmp_path / "results"
    runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    runs = store.read_runs(results)
    assert "grafana_links" not in runs[0]


def test_grafana_links_unresolvable_url_stored_as_null(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    tool_path = tmp_path / "tool.yaml"
    tool_path.write_text(
        f"""
name: faketool
command: "python {REPO_ROOT}/tests/fixtures/fake_tool.py"
timeout_seconds: 10
capture:
  rps: $.summary.requestsPerSec
grafana_links:
  - label: Broken
    url: "https://grafana.example.com/d/abc?var=${{no_such_var}}"
"""
    )
    results = tmp_path / "results"
    runner.execute_sweep(
        manifest_path=manifest_dir,
        tool_path=tool_path,
        results_dir=results,
        repeat=1,
    )
    runs = store.read_runs(results)
    links = runs[0].get("grafana_links")
    assert links is not None and len(links) == 1
    assert links[0]["label"] == "Broken"
    assert links[0]["url"] is None
    # Benchmark still ran successfully
    assert runs[0]["exit_code"] == 0
