import math

from abench_speckz import aggregate


def _run(config_hash="h", exit_code=0, results=None, tags=()):
    return {
        "config_hash": config_hash,
        "vars": {"a": "1"},
        "tags": list(tags),
        "exit_code": exit_code,
        "results": results or {},
        "failure_reason": None if exit_code == 0 else f"exit_code={exit_code}",
    }


def test_n_zero():
    s = aggregate.stats_for([])
    assert s["n"] == 0


def test_n_one_no_ci():
    s = aggregate.stats_for([5.0])
    assert s["n"] == 1
    assert s["mean"] == 5.0
    assert s["stddev"] == 0.0
    assert s["ci95_low"] is None


def test_ci95_at_n5():
    # Hand-computed: values [10,12,11,13,14], mean=12, stdev≈1.581, t(4)=2.776
    # half = 2.776 * 1.581 / sqrt(5) ≈ 1.962
    s = aggregate.stats_for([10.0, 12.0, 11.0, 13.0, 14.0])
    assert s["n"] == 5
    assert math.isclose(s["mean"], 12.0)
    assert math.isclose(s["stddev"], 1.581, abs_tol=0.01)
    assert math.isclose(s["ci95_high"] - s["mean"], 1.962, abs_tol=0.05)


def test_n_failed_counted():
    runs = [
        _run(results={"rps": 100}),
        _run(results={"rps": 110}),
        _run(exit_code=2),
    ]
    rows = aggregate.compute_aggregate_rows(runs)
    assert len(rows) == 1
    assert rows[0].metric == "rps"
    assert rows[0].n == 2
    assert rows[0].n_failed == 1


def test_warmup_excluded():
    runs = [
        _run(results={"rps": 0}, tags=["warmup"]),
        _run(results={"rps": 0}, tags=["warmup"]),
        _run(results={"rps": 100}),
        _run(results={"rps": 110}),
    ]
    rows = aggregate.compute_aggregate_rows(runs)
    assert rows[0].n == 2
    assert math.isclose(rows[0].mean, 105.0)


def test_per_metric_n_with_missing():
    runs = [
        _run(results={"rps": 100, "p95": 50}),
        _run(results={"rps": 110}),  # p95 missing
        _run(results={"rps": 120, "p95": None}),  # null filtered out
    ]
    rows = {r.metric: r for r in aggregate.compute_aggregate_rows(runs)}
    assert rows["rps"].n == 3
    assert rows["p95"].n == 1


def test_list_valued_skipped():
    runs = [
        _run(results={"latencies": [1, 2, 3]}),
        _run(results={"latencies": [4, 5, 6]}),
    ]
    rows = aggregate.compute_aggregate_rows(runs)
    assert rows == []


def test_rebuild_table_groups_by_hash():
    runs = [
        _run("h1", results={"rps": 100}),
        _run("h1", results={"rps": 110}),
        _run("h2", results={"rps": 50}),
    ]
    table = aggregate.rebuild_table(runs)
    assert set(table) == {"h1", "h2"}
    assert table["h1"][0].n == 2
    assert table["h2"][0].n == 1


def test_t_critical_zero_df_is_nan():
    t = aggregate._t_critical_95(0)
    assert math.isnan(t)


def test_t_critical_large_df_falls_back_to_z():
    # df > 29 falls back to 1.96 (normal-distribution critical value).
    assert aggregate._t_critical_95(100) == 1.96


def test_compute_aggregate_rows_empty_returns_empty():
    assert aggregate.compute_aggregate_rows([]) == []


def test_bool_results_excluded_from_metric_aggregation():
    runs = [_run(results={"flag": True, "rps": 100}), _run(results={"flag": False, "rps": 110})]
    rows = {r.metric: r for r in aggregate.compute_aggregate_rows(runs)}
    assert "flag" not in rows  # bool is filtered out
    assert "rps" in rows and rows["rps"].n == 2


def test_string_numeric_results_excluded():
    # extract.apply coerces strings to numbers, but if a parser returns strings, aggregate skips them.
    runs = [_run(results={"rps": "100"}), _run(results={"rps": "110"})]
    rows = aggregate.compute_aggregate_rows(runs)
    assert rows == []
