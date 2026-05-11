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
        manifest_path=manifest_dir, tool_path=tool_path, results_dir=results, repeat=2,
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
        manifest_path=manifest_dir, tool_path=tool_path, results_dir=results, repeat=1,
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
        manifest_path=manifest_dir, tool_path=tool_path, results_dir=results, repeat=1,
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
        manifest_path=manifest_dir, tool_path=tool_path, results_dir=results, repeat=1,
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
        manifest_path=manifest_dir, tool_path=tool_path, results_dir=results,
        repeat=1, keep_raw=True,
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
        manifest_path=manifest_dir, tool_path=tool_path, results_dir=results,
        repeat=1, keep_raw=True,
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
        manifest_path=manifest_dir, tool_path=tool_path, results_dir=results, repeat=1,
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

def _per_sweep_tool_yaml(tmp_path: Path, *, per_sweep_var=None,
                         setup_per_sweep=None, teardown_per_sweep=None) -> Path:
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
    manifest_dir = _make_manifest(tmp_path, [
        {"workload": "read", "concurrency": "1"},
        {"workload": "write", "concurrency": "8"},
    ])
    tool_path = _per_sweep_tool_yaml(
        tmp_path,
        setup_per_sweep=[f"python {phase} sweep-setup {marker}"],
        teardown_per_sweep=[f"python {phase} sweep-teardown {marker}"],
    )
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir, tool_path=tool_path, results_dir=results, repeat=3,
    )
    assert summary["succeeded"] == 6
    # Setup fires once at start, teardown once at end, regardless of 6 reps.
    assert marker.read_text().splitlines() == ["sweep-setup", "sweep-teardown"]


def test_per_sweep_mode_b_runs_once_per_var_value(tmp_path):
    phase = _phase_script(tmp_path)
    marker = tmp_path / "marker.txt"
    manifest_dir = _make_manifest(tmp_path, [
        {"workload": "write", "concurrency": "8"},
        {"workload": "read", "concurrency": "1"},
        {"workload": "read", "concurrency": "8"},
        {"workload": "write", "concurrency": "1"},
    ])
    tool_path = _per_sweep_tool_yaml(
        tmp_path, per_sweep_var="workload",
        setup_per_sweep=[f"python {phase} setup-${{workload}} {marker}"],
        teardown_per_sweep=[f"python {phase} teardown-${{workload}} {marker}"],
    )
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir, tool_path=tool_path, results_dir=results, repeat=2,
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
            manifest_path=manifest_dir, tool_path=tool_path, results_dir=results, repeat=1,
        )
    assert "setup_per_sweep[0]" in str(ei.value)
    assert "${workload}" in str(ei.value)
    assert not (results / "runs.jsonl").exists()


def test_per_sweep_mode_b_rejects_other_combo_var_refs(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read", "concurrency": "1"}])
    tool_path = _per_sweep_tool_yaml(
        tmp_path, per_sweep_var="workload",
        setup_per_sweep=["echo ${concurrency}"],  # only ${workload} allowed in Mode B
    )
    results = tmp_path / "results"
    with pytest.raises(RunError) as ei:
        runner.execute_sweep(
            manifest_path=manifest_dir, tool_path=tool_path, results_dir=results, repeat=1,
        )
    assert "${concurrency}" in str(ei.value)
    assert "${workload}" in str(ei.value)  # the error names the allowed var


def test_per_sweep_var_missing_in_combos_errors(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [{"workload": "read"}])
    tool_path = _per_sweep_tool_yaml(
        tmp_path, per_sweep_var="missing_var",
        setup_per_sweep=["echo ${missing_var}"],
    )
    results = tmp_path / "results"
    with pytest.raises(RunError) as ei:
        runner.execute_sweep(
            manifest_path=manifest_dir, tool_path=tool_path, results_dir=results, repeat=1,
        )
    assert "per_sweep_var" in str(ei.value)
    assert "missing_var" in str(ei.value)


def test_per_sweep_setup_failure_records_failure_rows(tmp_path):
    phase = _phase_script(tmp_path)
    marker = tmp_path / "marker.txt"
    manifest_dir = _make_manifest(tmp_path, [
        {"workload": "read", "concurrency": "1"},
        {"workload": "read", "concurrency": "8"},
    ])
    tool_path = _per_sweep_tool_yaml(
        tmp_path,
        setup_per_sweep=['python -c "import sys; sys.exit(5)"'],
        teardown_per_sweep=[f"python {phase} td {marker}"],
    )
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir, tool_path=tool_path, results_dir=results, repeat=2,
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
        manifest_path=manifest_dir, tool_path=tool_path, results_dir=results, repeat=3,
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
        manifest_path=manifest_dir, tool_path=tool_path, results_dir=results, repeat=2,
    )
    assert marker.read_text().splitlines() == ["sweep-setup", "sweep-teardown"]

    # Second run with skip-existing: all reps skipped → phases must NOT fire again.
    marker.write_text("")
    summary = runner.execute_sweep(
        manifest_path=manifest_dir, tool_path=tool_path, results_dir=results, repeat=2,
        skip_existing=True,
    )
    assert summary["skipped"] == 2
    assert marker.read_text() == ""


def test_per_sweep_keep_raw_writes_per_group_files(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [
        {"workload": "read", "concurrency": "1"},
        {"workload": "write", "concurrency": "1"},
    ])
    tool_path = _per_sweep_tool_yaml(
        tmp_path, per_sweep_var="workload",
        setup_per_sweep=["echo hello-${workload}"],
    )
    results = tmp_path / "results"

    summary = runner.execute_sweep(
        manifest_path=manifest_dir, tool_path=tool_path, results_dir=results,
        repeat=1, keep_raw=True,
    )
    assert summary["succeeded"] == 2

    raw_read = json.loads((results / "raw" / "sweep-read.json").read_text())
    raw_write = json.loads((results / "raw" / "sweep-write.json").read_text())
    assert "hello-read" in raw_read["setup"][0]["stdout"]
    assert "hello-write" in raw_write["setup"][0]["stdout"]


def test_per_sweep_dry_run_prints_preview(tmp_path):
    manifest_dir = _make_manifest(tmp_path, [
        {"workload": "read", "concurrency": "1"},
        {"workload": "write", "concurrency": "1"},
    ])
    tool_path = _per_sweep_tool_yaml(
        tmp_path, per_sweep_var="workload",
        setup_per_sweep=["echo setup-${workload}"],
        teardown_per_sweep=["echo teardown-${workload}"],
    )
    results = tmp_path / "results"
    buf = io.StringIO()

    runner.execute_sweep(
        manifest_path=manifest_dir, tool_path=tool_path, results_dir=results,
        repeat=1, dry_run=True, progress_stream=buf,
    )
    out = buf.getvalue()
    assert "[per_sweep setup 0, workload=read]: echo setup-read" in out
    assert "[per_sweep teardown 0, workload=read]: echo teardown-read" in out
    assert "[per_sweep setup 0, workload=write]: echo setup-write" in out
    assert not (results / "runs.jsonl").exists()
