import io
import json

import pytest

from abench_speckz import query, store
from abench_speckz.model import RunError


def _seed(results_dir):
    store.init_results_dir(results_dir)
    combos = [
        ("h1", {"workload": "read", "backend": "postgres"}, {"rps": 1000.0, "p95_latency_ms": 50.0}),
        ("h2", {"workload": "write", "backend": "postgres"}, {"rps": 800.0, "p95_latency_ms": 80.0}),
    ]
    for hash_, vars_, results in combos:
        for i in range(5):
            store.append_run(results_dir, {
                "run_id": f"{hash_}-{i}", "config_hash": hash_,
                "vars": vars_, "tags": ["bench"], "exit_code": 0,
                "results": results, "failure_reason": None,
            })
        store.update_aggregates_for_hash(results_dir, hash_)


def test_group_by_workload(tmp_path):
    _seed(tmp_path)
    out = io.StringIO()
    query.stats(tmp_path, group_by=["workload"], metrics=["rps"], fmt="tsv", out=out)
    lines = out.getvalue().strip().splitlines()
    assert len(lines) == 3  # header + 2 workloads
    assert "workload" in lines[0]


def test_metric_filter(tmp_path):
    _seed(tmp_path)
    out = io.StringIO()
    query.stats(tmp_path, group_by=["workload"], metrics=["p95_latency_ms"],
                fmt="tsv", out=out)
    lines = out.getvalue().strip().splitlines()
    assert len(lines) == 3
    assert all("p95_latency_ms" in line for line in lines[1:])


def test_where_kv_param_bound_no_injection(tmp_path):
    _seed(tmp_path)
    out = io.StringIO()
    # If user input were string-interpolated this would explode. With params it's just a literal compare.
    query.stats(tmp_path, group_by=["workload"], where=["workload='; DROP TABLE x; --"],
                metrics=["rps"], fmt="tsv", out=out)
    lines = out.getvalue().strip().splitlines()
    # 0 matches plus header for empty result
    assert len(lines) == 1 or "0 rows" in out.getvalue()


def test_json_format(tmp_path):
    _seed(tmp_path)
    out = io.StringIO()
    query.stats(tmp_path, group_by=["workload"], metrics=["rps"], fmt="json", out=out)
    data = json.loads(out.getvalue())
    assert isinstance(data, list)
    assert len(data) == 2


def test_from_raw_matches_aggregates(tmp_path):
    store.init_results_dir(tmp_path)
    for i, rps in enumerate([1000.0, 1010.0, 1020.0, 1030.0, 1040.0]):
        store.append_run(tmp_path, {
            "run_id": f"r{i}", "config_hash": "h1",
            "vars": {"workload": "read", "backend": "postgres"},
            "tags": ["bench"], "exit_code": 0,
            "results": {"rps": rps},
        })
    store.update_aggregates_for_hash(tmp_path, "h1")

    a = io.StringIO()
    b = io.StringIO()
    query.stats(tmp_path, group_by=["workload"], metrics=["rps"], fmt="tsv", out=a)
    query.stats(tmp_path, group_by=["workload"], metrics=["rps"], fmt="tsv",
                from_raw=True, out=b)
    assert a.getvalue().strip().splitlines()[1].split("\t")[2] \
           == b.getvalue().strip().splitlines()[1].split("\t")[2]


def test_empty_dir(tmp_path):
    store.init_results_dir(tmp_path)
    out = io.StringIO()
    query.stats(tmp_path, group_by=["workload"], metrics=["rps"], fmt="tsv", out=out)
    assert "0 rows" in out.getvalue()


def test_group_by_rejects_injection_key(tmp_path):
    _seed(tmp_path)
    out = io.StringIO()
    with pytest.raises(RunError, match="invalid group-by key"):
        query.stats(
            tmp_path,
            group_by=["x'); DROP TABLE foo;--"],
            metrics=["rps"],
            fmt="tsv",
            out=out,
        )


def test_var_names_from_aggregates(tmp_path):
    _seed(tmp_path)
    names = query.var_names(tmp_path)
    assert names == ["workload", "backend"]


def test_var_names_empty_when_no_data(tmp_path):
    assert query.var_names(tmp_path) == []


def test_from_raw_with_require_tag(tmp_path):
    store.init_results_dir(tmp_path)
    for i, tags in enumerate([["bench"], ["bench"], ["other"]]):
        store.append_run(tmp_path, {
            "run_id": f"r{i}", "config_hash": "h1",
            "vars": {"workload": "read"}, "tags": tags,
            "exit_code": 0, "results": {"rps": 100.0 + i},
        })
    out = io.StringIO()
    query.stats(
        tmp_path, group_by=["workload"], metrics=["rps"], fmt="tsv",
        require_tags=["bench"], from_raw=True, out=out,
    )
    lines = out.getvalue().strip().splitlines()
    # 2 rows accepted (the 'other'-tagged row is filtered out): n=2 in the data column.
    assert len(lines) == 2  # header + one workload row
    row = lines[1].split("\t")
    # n column is at the position after group_by + metric: workload, metric, n
    assert "2" in row


def test_from_raw_with_exclude_tag(tmp_path):
    store.init_results_dir(tmp_path)
    for i, tags in enumerate([["bench"], ["bench", "warmup-flag"], ["bench"]]):
        store.append_run(tmp_path, {
            "run_id": f"r{i}", "config_hash": "h1",
            "vars": {"workload": "read"}, "tags": tags,
            "exit_code": 0, "results": {"rps": 100.0 + i},
        })
    out = io.StringIO()
    query.stats(
        tmp_path, group_by=["workload"], metrics=["rps"], fmt="tsv",
        exclude_tags=["warmup-flag"], from_raw=True, out=out,
    )
    lines = out.getvalue().strip().splitlines()
    assert len(lines) == 2
    assert "2" in lines[1].split("\t")  # only 2 of 3 runs survive


def test_compute_rows_returns_empty_for_missing_aggregates(tmp_path):
    # No aggregates.jsonl file at all.
    rows = query.compute_rows(tmp_path)
    assert rows == []


def test_compute_rows_returns_empty_for_zero_byte_runs(tmp_path):
    # from_raw forces the runs.jsonl path; an empty file returns no rows.
    store.init_results_dir(tmp_path)
    (tmp_path / "runs.jsonl").write_text("")
    rows = query.compute_rows(tmp_path, group_by=["workload"], from_raw=True)
    assert rows == []


def test_fmt_helper_strips_trailing_zeros():
    from abench_speckz.query import _fmt
    assert _fmt(1.5) == "1.5"
    assert _fmt(0.0) == "0"
    assert _fmt(2.0) == "2"
    assert _fmt(None) == ""
    assert _fmt("hello") == "hello"


def test_json_default_serializes_path():
    from pathlib import Path
    from abench_speckz.query import _json_default
    # Path has no isoformat; fall through to str().
    assert _json_default(Path("/tmp/x")) == "/tmp/x"


def test_json_default_serializes_datetime():
    from datetime import datetime
    from abench_speckz.query import _json_default
    dt = datetime(2026, 5, 14, 12, 0, 0)
    assert _json_default(dt) == "2026-05-14T12:00:00"


def test_stats_pretty_renders_table(tmp_path):
    _seed(tmp_path)
    # Persist a pretty-names mapping then render table format with --pretty.
    from abench_speckz import prettynames
    prettynames.write(tmp_path / "pretty_names.json", {"vars": {"workload": "Workload"}})
    out = io.StringIO()
    query.stats(tmp_path, group_by=["workload"], metrics=["rps"], fmt="table",
                pretty=True, out=out)
    text = out.getvalue()
    assert "Workload" in text  # pretty label substituted for header
