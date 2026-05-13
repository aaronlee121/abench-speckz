import io

from abench_speckz import query, store


def _seed(results_dir):
    store.init_results_dir(results_dir)
    combos = [
        ("h1", {"workload": "read",  "backend": "postgres"}, {"rps": 1000.0, "p95_latency_ms": 50.0}),
        ("h2", {"workload": "write", "backend": "postgres"}, {"rps":  800.0, "p95_latency_ms": 80.0}),
    ]
    for hash_, vars_, results in combos:
        for i in range(5):
            store.append_run(results_dir, {
                "run_id": f"{hash_}-{i}", "config_hash": hash_,
                "vars": vars_, "tags": ["bench"], "exit_code": 0,
                "results": results, "failure_reason": None,
            })
        store.update_aggregates_for_hash(results_dir, hash_)


def test_report_creates_file(tmp_path):
    _seed(tmp_path)
    report_path = tmp_path / "report.html"
    query.stats(tmp_path, group_by=["workload"], report=report_path, out=io.StringIO())
    assert report_path.exists()
    assert report_path.stat().st_size > 0


def test_report_contains_metrics(tmp_path):
    _seed(tmp_path)
    report_path = tmp_path / "report.html"
    query.stats(tmp_path, group_by=["workload"], report=report_path, out=io.StringIO())
    html = report_path.read_text()
    assert "rps" in html
    assert "p95_latency_ms" in html


def test_report_contains_group_labels(tmp_path):
    _seed(tmp_path)
    report_path = tmp_path / "report.html"
    query.stats(tmp_path, group_by=["workload"], report=report_path, out=io.StringIO())
    html = report_path.read_text()
    assert "read" in html
    assert "write" in html


def test_report_chartjs_present(tmp_path):
    _seed(tmp_path)
    report_path = tmp_path / "report.html"
    query.stats(tmp_path, group_by=["workload"], report=report_path, out=io.StringIO())
    html = report_path.read_text()
    assert "chart.js" in html


def test_report_no_group_by(tmp_path):
    _seed(tmp_path)
    report_path = tmp_path / "report.html"
    query.stats(tmp_path, report=report_path, out=io.StringIO())
    html = report_path.read_text()
    assert report_path.exists()
    assert "all" in html


def test_report_stdout_confirms_path(tmp_path):
    _seed(tmp_path)
    report_path = tmp_path / "report.html"
    out = io.StringIO()
    query.stats(tmp_path, group_by=["workload"], report=report_path, out=out)
    assert "report written to" in out.getvalue()
    assert str(report_path) in out.getvalue()


def test_report_pretty_names(tmp_path):
    _seed(tmp_path)
    from abench_speckz import prettynames
    prettynames.write(
        tmp_path / "pretty_names.json",
        {"my-tool": {"rps": "Requests/sec", "p95_latency_ms": "P95 Latency (ms)"}},
    )
    report_path = tmp_path / "report.html"
    query.stats(tmp_path, group_by=["workload"], report=report_path, out=io.StringIO())
    html = report_path.read_text()
    assert "Requests/sec" in html
    assert "P95 Latency (ms)" in html


def _write_plots(path, plots_yaml):
    path.write_text(plots_yaml)


def test_plots_replace_default_when_specified(tmp_path):
    _seed(tmp_path)
    plots_path = tmp_path / "plots.yaml"
    _write_plots(plots_path,
                 "- id: p1\n  type: bar\n  x: workload\n  y: rps\n")
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=io.StringIO())
    html = report_path.read_text()
    # plot id appears as canvas id
    assert "chart-p1" in html
    # default per-metric chart for p95_latency_ms should NOT appear
    assert "chart-p95_latency_ms" not in html


def test_bar_plot(tmp_path):
    _seed(tmp_path)
    plots_path = tmp_path / "plots.yaml"
    _write_plots(plots_path,
                 "- id: rps_bar\n  type: bar\n  x: workload\n  y: rps\n")
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=io.StringIO())
    html = report_path.read_text()
    assert "chart-rps_bar" in html
    assert "\"type\": \"bar\"" in html
    assert "read" in html and "write" in html
    # mean value 1000 from the seed
    assert "1000" in html


def test_line_plot(tmp_path):
    _seed(tmp_path)
    plots_path = tmp_path / "plots.yaml"
    _write_plots(plots_path,
                 "- id: rps_line\n  type: line\n  x: workload\n  y: rps\n")
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=io.StringIO())
    html = report_path.read_text()
    assert "chart-rps_line" in html
    assert "\"type\": \"line\"" in html


def test_stacked_bar_plot(tmp_path):
    _seed(tmp_path)
    plots_path = tmp_path / "plots.yaml"
    _write_plots(plots_path,
                 "- id: stack\n  type: stacked-bar\n  x: workload\n  y: [rps, p95_latency_ms]\n")
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=io.StringIO())
    html = report_path.read_text()
    assert "chart-stack" in html
    assert "\"stacked\": true" in html


def test_scatter_plot(tmp_path):
    _seed(tmp_path)
    plots_path = tmp_path / "plots.yaml"
    _write_plots(plots_path,
                 "- id: scat\n  type: scatter\n  x: rps\n  y: p95_latency_ms\n  group_by: workload\n")
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=io.StringIO())
    html = report_path.read_text()
    assert "chart-scat" in html
    assert "\"type\": \"scatter\"" in html
    # Each scatter point is {"x": ..., "y": ...} — search for the structure
    assert "\"x\":" in html and "\"y\":" in html


def test_plots_discovered_from_tool_snapshot(tmp_path):
    _seed(tmp_path)
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir(exist_ok=True)
    (tools_dir / "mytool.yaml").write_text(
        "name: mytool\ncommand: x\n"
        "plots:\n  - id: discovered\n    type: bar\n    x: workload\n    y: rps\n"
    )
    report_path = tmp_path / "r.html"
    # No --plots, no --group-by — should auto-discover and use plot-driven rendering
    query.stats(tmp_path, report=report_path, out=io.StringIO())
    html = report_path.read_text()
    assert "chart-discovered" in html


def test_default_report_when_no_plots(tmp_path):
    # No plots in --plots and no tool snapshots → default report renders
    _seed(tmp_path)
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, group_by=["workload"], report=report_path, out=io.StringIO())
    html = report_path.read_text()
    # Default uses metric name as chart id
    assert "chart-rps" in html
    assert "chart-p95_latency_ms" in html


def test_resolve_group_by_no_negation():
    from abench_speckz.report import _resolve_group_by
    assert _resolve_group_by(["workload", "backend"], ["workload", "backend", "concurrency"]) == ["workload", "backend"]


def test_resolve_group_by_negation_excludes_var():
    from abench_speckz.report import _resolve_group_by
    result = _resolve_group_by(["!concurrency"], ["workload", "backend", "concurrency"])
    assert result == ["workload", "backend"]


def test_resolve_group_by_negation_mixed():
    from abench_speckz.report import _resolve_group_by
    result = _resolve_group_by(["workload", "!concurrency"], ["workload", "backend", "concurrency"])
    assert result == ["workload", "backend"]


def test_resolve_group_by_negation_unknown_var_ignored():
    from abench_speckz.report import _resolve_group_by
    result = _resolve_group_by(["!nonexistent"], ["workload", "backend"])
    assert result == ["workload", "backend"]


def test_resolve_group_by_empty_all_vars():
    from abench_speckz.report import _resolve_group_by
    assert _resolve_group_by(["!concurrency"], []) == []


# ---------------------------------------------------------------------------
# _split_group_by_vars
# ---------------------------------------------------------------------------

def test_split_group_by_all_varying(tmp_path):
    from abench_speckz.report import _split_group_by_vars
    rows = [{"workload": "read", "backend": "postgres"}, {"workload": "write", "backend": "mysql"}]
    static, varying = _split_group_by_vars(rows, ["workload", "backend"])
    assert static == {}
    assert varying == ["workload", "backend"]


def test_split_group_by_one_static(tmp_path):
    from abench_speckz.report import _split_group_by_vars
    rows = [{"workload": "read", "region": "us-east-1"}, {"workload": "write", "region": "us-east-1"}]
    static, varying = _split_group_by_vars(rows, ["workload", "region"])
    assert static == {"region": "us-east-1"}
    assert varying == ["workload"]


def test_split_group_by_empty_rows():
    from abench_speckz.report import _split_group_by_vars
    static, varying = _split_group_by_vars([], ["workload", "backend"])
    assert static == {}
    assert varying == ["workload", "backend"]


# ---------------------------------------------------------------------------
# Table legend and fixed-vars rendering
# ---------------------------------------------------------------------------

def _seed_three_vars(results_dir):
    """Seed with workload×backend×region where region is always us-east-1."""
    store.init_results_dir(results_dir)
    combos = [
        ("h1", {"workload": "read",  "backend": "postgres", "region": "us-east-1"}, {"rps": 1000.0}),
        ("h2", {"workload": "write", "backend": "postgres", "region": "us-east-1"}, {"rps":  800.0}),
        ("h3", {"workload": "read",  "backend": "mysql",    "region": "us-east-1"}, {"rps":  900.0}),
    ]
    for hash_, vars_, results in combos:
        for i in range(3):
            store.append_run(results_dir, {
                "run_id": f"{hash_}-{i}", "config_hash": hash_,
                "vars": vars_, "tags": ["bench"], "exit_code": 0,
                "results": results, "failure_reason": None,
            })
        store.update_aggregates_for_hash(results_dir, hash_)


def test_fixed_vars_shown_for_constant_group_by(tmp_path):
    _seed_three_vars(tmp_path)
    plots_path = tmp_path / "plots.yaml"
    plots_path.write_text(
        "- id: p1\n  type: bar\n  x: workload\n  y: rps\n"
        "  group_by: [backend, region]\n"
    )
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=__import__("io").StringIO())
    html = report_path.read_text()
    # region is constant across all series — should appear in fixed-vars block
    assert "Fixed:" in html
    assert "us-east-1" in html
    # backend varies — should NOT appear in fixed-vars
    assert "backend" in html  # appears in legend table header


def test_legend_table_rendered_for_varying_keys(tmp_path):
    _seed_three_vars(tmp_path)
    plots_path = tmp_path / "plots.yaml"
    plots_path.write_text(
        "- id: p1\n  type: bar\n  x: workload\n  y: rps\n"
        "  group_by: [backend, region]\n"
    )
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=__import__("io").StringIO())
    html = report_path.read_text()
    # Legend table should be present with color swatches
    assert "legend-wrap" in html
    assert "border-radius" in html  # color swatch style
    # Chart.js legend should be disabled
    assert '"display": false' in html


def test_chartjs_legend_kept_for_no_group_by(tmp_path):
    _seed(tmp_path)
    plots_path = tmp_path / "plots.yaml"
    plots_path.write_text("- id: p1\n  type: bar\n  x: workload\n  y: rps\n")
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=__import__("io").StringIO())
    html = report_path.read_text()
    # No group_by → no table legend div, Chart.js legend kept
    assert '<div class="legend-wrap' not in html
    assert '"display": false' not in html


def test_short_labels_exclude_static_vars(tmp_path):
    _seed_three_vars(tmp_path)
    plots_path = tmp_path / "plots.yaml"
    plots_path.write_text(
        "- id: p1\n  type: bar\n  x: workload\n  y: rps\n"
        "  group_by: [backend, region]\n"
    )
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=__import__("io").StringIO())
    html = report_path.read_text()
    # Dataset labels use only varying key (backend), not the static region
    assert '"postgres"' in html or "postgres" in html
    # The label should NOT include "us-east-1 / postgres" style (region in label)
    assert "us-east-1 / postgres" not in html
