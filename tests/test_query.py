import io
import json

import pytest

from benchspec import query, store
from benchspec.model import RunError
from benchspec.runspec import AggregateRow


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
