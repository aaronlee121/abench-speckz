import json
import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def _run(*args, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", "abench_speckz", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def test_dry_run_default_profile():
    r = _run("gen", str(FIXTURES / "full.yaml"), "--dry-run")
    assert r.returncode == 0, r.stderr
    # smoke profile narrows to 1 combo (workload=read, concurrency=1, backend in [postgres, mysql])
    # exclude removes mysql/c=1 → 1 combo
    assert "1 combo" in r.stdout


def test_dry_run_full_profile():
    r = _run("gen", str(FIXTURES / "full.yaml"), "--profile", "full", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "15 combo" in r.stdout


def test_list_is_tsv():
    r = _run("gen", str(FIXTURES / "full.yaml"), "--profile", "full", "--list")
    assert r.returncode == 0
    lines = r.stdout.strip().splitlines()
    assert len(lines) == 16  # header + 15 rows
    assert "\t" in lines[0]


def test_real_write(tmp_path):
    out = tmp_path / "out"
    r = _run("gen", str(FIXTURES / "full.yaml"), "--profile", "full", "--out", str(out))
    assert r.returncode == 0, r.stderr
    files = list(out.glob("*.env"))
    assert len(files) == 15
    manifest = json.loads((out / "manifest.json").read_text())
    assert len(manifest) == 15
    assert all("filename" in e and "vars" in e and "tags" in e for e in manifest)


def test_tag_filter():
    r = _run("gen", str(FIXTURES / "full.yaml"), "--profile", "full", "--tag", "stress", "--list")
    assert r.returncode == 0
    lines = r.stdout.strip().splitlines()
    # 2 backends * 3 workloads at concurrency=64, minus mysql/c=1 not affected here = 6 combos
    assert len(lines) == 7  # header + 6


def test_invalid_spec_exits_nonzero(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("variables: { x: [] }\n")
    r = _run("gen", str(bad), "--dry-run")
    assert r.returncode != 0
    assert "error" in r.stderr.lower()


def test_run_and_stats_end_to_end(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "c.env").write_text("workload=read\nconcurrency=8\n")
    (out / "manifest.json").write_text(
        '[{"filename":"c.env","vars":{"workload":"read","concurrency":"8"},"tags":[]}]'
    )
    tool = tmp_path / "tool.yaml"
    tool.write_text(
        f"name: faketool\n"
        f'command: "{sys.executable} {FIXTURES / "fake_tool.py"}"\n'
        f"capture:\n  rps: $.summary.requestsPerSec\n"
    )
    results = tmp_path / "results"

    r = _run("run", str(out), "--tool", str(tool), "--repeat", "3", "--results", str(results))
    assert r.returncode == 0, r.stderr
    assert "succeeded=3" in r.stdout

    s = _run("stats", str(results), "--metric", "rps", "--format", "tsv")
    assert s.returncode == 0, s.stderr
    lines = s.stdout.strip().splitlines()
    assert len(lines) == 2  # header + one metric row
    assert "rps" in lines[1]


def test_run_all_failed_exits_3(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "c.env").write_text("workload=read\n")
    (out / "manifest.json").write_text('[{"filename":"c.env","vars":{"workload":"read"},"tags":[]}]')
    tool = tmp_path / "tool.yaml"
    tool.write_text(
        f"name: faketool\n"
        f'command: "{sys.executable} {FIXTURES / "fake_tool.py"} --mode=fail"\n'
        f"capture:\n  rps: $.summary.requestsPerSec\n"
    )
    results = tmp_path / "results"
    r = _run("run", str(out), "--tool", str(tool), "--repeat", "2", "--results", str(results))
    assert r.returncode == 3


def test_rebuild_aggregates(tmp_path):
    from abench_speckz import store

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
                "results": {"rps": 100 + i},
            },
        )
    r = _run("rebuild-aggregates", str(tmp_path))
    assert r.returncode == 0


def test_gen_zero_combos_after_filter(tmp_path):
    # --tag matches nothing → gen prints a warning and exits 0 without writing.
    out = tmp_path / "out"
    r = _run(
        "gen",
        str(FIXTURES / "full.yaml"),
        "--profile",
        "full",
        "--tag",
        "nonexistent-tag",
        "--out",
        str(out),
    )
    assert r.returncode == 0
    assert "0 combos" in r.stderr
    # No manifest written when nothing to emit.
    assert not (out / "manifest.json").exists()


def test_run_dry_run_writes_no_results(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "c.env").write_text("workload=read\n")
    (out / "manifest.json").write_text('[{"filename":"c.env","vars":{"workload":"read"},"tags":[]}]')
    tool = tmp_path / "tool.yaml"
    tool.write_text(
        f"name: faketool\n"
        f'command: "{sys.executable} {FIXTURES / "fake_tool.py"}"\n'
        f"capture:\n  rps: $.summary.requestsPerSec\n"
    )
    results = tmp_path / "results"
    r = _run("run", str(out), "--tool", str(tool), "--results", str(results), "--dry-run")
    assert r.returncode == 0
    # Dry run must not create the results directory or write any runs.
    assert not (results / "runs.jsonl").exists()


def test_stats_report_writes_html_file(tmp_path):
    # End-to-end wiring of --report through argparse → query → report renderer.
    out = tmp_path / "out"
    out.mkdir()
    (out / "c.env").write_text("workload=read\nconcurrency=1\n")
    (out / "manifest.json").write_text(
        '[{"filename":"c.env","vars":{"workload":"read","concurrency":"1"},"tags":[]}]'
    )
    tool = tmp_path / "tool.yaml"
    tool.write_text(
        f"name: faketool\n"
        f'command: "{sys.executable} {FIXTURES / "fake_tool.py"}"\n'
        f"capture:\n  rps: $.summary.requestsPerSec\n"
    )
    results = tmp_path / "results"
    r = _run("run", str(out), "--tool", str(tool), "--repeat", "2", "--results", str(results))
    assert r.returncode == 0, r.stderr

    report = tmp_path / "report.html"
    s = _run("stats", str(results), "--metric", "rps", "--report", str(report), "--no-run-reports")
    assert s.returncode == 0, s.stderr
    assert report.exists()
    body = report.read_text()
    assert "<!DOCTYPE html>" in body
    assert "rps" in body


def test_stats_link_run_reports_flag(tmp_path):
    # Exercise --link-run-reports wiring (mutually exclusive with --no-run-reports).
    out = tmp_path / "out"
    out.mkdir()
    (out / "c.env").write_text("workload=read\n")
    (out / "manifest.json").write_text('[{"filename":"c.env","vars":{"workload":"read"},"tags":[]}]')
    tool = tmp_path / "tool.yaml"
    tool.write_text(
        f"name: faketool\n"
        f'command: "{sys.executable} {FIXTURES / "fake_tool.py"}"\n'
        f"capture:\n  rps: $.summary.requestsPerSec\n"
    )
    results = tmp_path / "results"
    _run("run", str(out), "--tool", str(tool), "--results", str(results))

    report = tmp_path / "report.html"
    s = _run("stats", str(results), "--metric", "rps", "--report", str(report), "--link-run-reports")
    assert s.returncode == 0, s.stderr
    assert report.exists()
