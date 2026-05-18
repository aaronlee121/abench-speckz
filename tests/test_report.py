import io

from abench_speckz import query, store


def _seed(results_dir):
    store.init_results_dir(results_dir)
    combos = [
        ("h1", {"workload": "read", "backend": "postgres"}, {"rps": 1000.0, "p95_latency_ms": 50.0}),
        ("h2", {"workload": "write", "backend": "postgres"}, {"rps": 800.0, "p95_latency_ms": 80.0}),
    ]
    for hash_, vars_, results in combos:
        for i in range(5):
            store.append_run(
                results_dir,
                {
                    "run_id": f"{hash_}-{i}",
                    "config_hash": hash_,
                    "vars": vars_,
                    "tags": ["bench"],
                    "exit_code": 0,
                    "results": results,
                    "failure_reason": None,
                },
            )
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
    _write_plots(plots_path, "- id: p1\n  type: bar\n  x: workload\n  y: rps\n")
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
    _write_plots(plots_path, "- id: rps_bar\n  type: bar\n  x: workload\n  y: rps\n")
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=io.StringIO())
    html = report_path.read_text()
    assert "chart-rps_bar" in html
    assert '"type": "bar"' in html
    assert "read" in html and "write" in html
    # mean value 1000 from the seed
    assert "1000" in html


def test_line_plot(tmp_path):
    _seed(tmp_path)
    plots_path = tmp_path / "plots.yaml"
    _write_plots(plots_path, "- id: rps_line\n  type: line\n  x: workload\n  y: rps\n")
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=io.StringIO())
    html = report_path.read_text()
    assert "chart-rps_line" in html
    assert '"type": "line"' in html


def test_stacked_bar_plot(tmp_path):
    _seed(tmp_path)
    plots_path = tmp_path / "plots.yaml"
    _write_plots(plots_path, "- id: stack\n  type: stacked-bar\n  x: workload\n  y: [rps, p95_latency_ms]\n")
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=io.StringIO())
    html = report_path.read_text()
    assert "chart-stack" in html
    assert '"stacked": true' in html


def test_scatter_plot(tmp_path):
    _seed(tmp_path)
    plots_path = tmp_path / "plots.yaml"
    _write_plots(
        plots_path, "- id: scat\n  type: scatter\n  x: rps\n  y: p95_latency_ms\n  group_by: workload\n"
    )
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=io.StringIO())
    html = report_path.read_text()
    assert "chart-scat" in html
    assert '"type": "scatter"' in html
    # Each scatter point is {"x": ..., "y": ...} — search for the structure
    assert '"x":' in html and '"y":' in html


def test_plots_discovered_from_tool_snapshot(tmp_path):
    _seed(tmp_path)
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir(exist_ok=True)
    (tools_dir / "mytool.yaml").write_text(
        "name: mytool\ncommand: x\nplots:\n  - id: discovered\n    type: bar\n    x: workload\n    y: rps\n"
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

    assert _resolve_group_by(["workload", "backend"], ["workload", "backend", "concurrency"]) == [
        "workload",
        "backend",
    ]


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
        ("h1", {"workload": "read", "backend": "postgres", "region": "us-east-1"}, {"rps": 1000.0}),
        ("h2", {"workload": "write", "backend": "postgres", "region": "us-east-1"}, {"rps": 800.0}),
        ("h3", {"workload": "read", "backend": "mysql", "region": "us-east-1"}, {"rps": 900.0}),
    ]
    for hash_, vars_, results in combos:
        for i in range(3):
            store.append_run(
                results_dir,
                {
                    "run_id": f"{hash_}-{i}",
                    "config_hash": hash_,
                    "vars": vars_,
                    "tags": ["bench"],
                    "exit_code": 0,
                    "results": results,
                    "failure_reason": None,
                },
            )
        store.update_aggregates_for_hash(results_dir, hash_)


def test_fixed_vars_shown_for_constant_group_by(tmp_path):
    _seed_three_vars(tmp_path)
    plots_path = tmp_path / "plots.yaml"
    plots_path.write_text("- id: p1\n  type: bar\n  x: workload\n  y: rps\n  group_by: [backend, region]\n")
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
    plots_path.write_text("- id: p1\n  type: bar\n  x: workload\n  y: rps\n  group_by: [backend, region]\n")
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
    plots_path.write_text("- id: p1\n  type: bar\n  x: workload\n  y: rps\n  group_by: [backend, region]\n")
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=__import__("io").StringIO())
    html = report_path.read_text()
    # Dataset labels use only varying key (backend), not the static region
    assert '"postgres"' in html or "postgres" in html
    # The label should NOT include "us-east-1 / postgres" style (region in label)
    assert "us-east-1 / postgres" not in html


def test_single_series_legend_folded_into_fixed_vars(tmp_path):
    """When group_by yields only one unique series, no legend table — vars go into fixed block."""
    store.init_results_dir(tmp_path)
    # Only one backend value in the data
    for i in range(3):
        store.append_run(
            tmp_path,
            {
                "run_id": f"h1-{i}",
                "config_hash": "h1",
                "vars": {"workload": "read", "backend": "postgres"},
                "tags": ["bench"],
                "exit_code": 0,
                "results": {"rps": 1000.0},
                "failure_reason": None,
            },
        )
    store.update_aggregates_for_hash(tmp_path, "h1")
    plots_path = tmp_path / "plots.yaml"
    plots_path.write_text("- id: p1\n  type: bar\n  x: workload\n  y: rps\n  group_by: backend\n")
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=__import__("io").StringIO())
    html = report_path.read_text()
    # No legend table div (class appears in CSS but not as a DOM element)
    assert '<div class="legend-wrap' not in html
    # Backend value shown in the fixed-vars block instead
    assert "postgres" in html
    assert "Fixed:" in html


# ---------------------------------------------------------------------------
# Per-run HTML reports
# ---------------------------------------------------------------------------

_RUN_ID = "12345678-1234-1234-1234-123456789abc"
_RUN_ID2 = "abcdef12-abcd-abcd-abcd-abcdef123456"


def _seed_with_run(results_dir, run_id=_RUN_ID, exit_code=0, tags=None):
    """Seed one run row (no aggregates needed for per-run tests)."""
    store.init_results_dir(results_dir)
    store.append_run(
        results_dir,
        {
            "run_id": run_id,
            "config_hash": "h1",
            "vars": {"workload": "read"},
            "tags": tags or ["bench"],
            "exit_code": exit_code,
            "results": {"rps": 1000.0},
            "started_at": "2026-05-14T10:00:00Z",
            "failure_reason": None,
        },
    )
    store.update_aggregates_for_hash(results_dir, "h1")


def test_discover_run_reports_finds_html_files(tmp_path):
    from abench_speckz.report import _discover_run_reports

    store.init_results_dir(tmp_path)
    html_path = tmp_path / "raw" / f"{_RUN_ID}_flamegraph.html"
    html_path.write_text("<html><body>flame</body></html>")
    result = _discover_run_reports(tmp_path)
    assert _RUN_ID in result
    assert result[_RUN_ID] == [("flamegraph", html_path)]


def test_discover_run_reports_ignores_non_html(tmp_path):
    from abench_speckz.report import _discover_run_reports

    store.init_results_dir(tmp_path)
    # .json file — should be ignored
    (tmp_path / "raw" / f"{_RUN_ID}_data.json").write_text("{}")
    # bare UUID with no underscore-name — should be ignored
    (tmp_path / "raw" / f"{_RUN_ID}.html").write_text("<html/>")
    result = _discover_run_reports(tmp_path)
    assert result == {}


def test_runs_section_embedded_contains_content(tmp_path):
    from abench_speckz.report import _discover_run_reports, _runs_section

    store.init_results_dir(tmp_path)
    html_path = tmp_path / "raw" / f"{_RUN_ID}_flamegraph.html"
    html_path.write_text("<html><body>my-flame-content</body></html>")
    runs = [
        {
            "run_id": _RUN_ID,
            "config_hash": "h1",
            "vars": {"workload": "read"},
            "tags": ["bench"],
            "exit_code": 0,
            "started_at": "2026-05-14T10:00Z",
        }
    ]
    run_reports = _discover_run_reports(tmp_path)
    section_html, script_js = _runs_section(run_reports, runs, embed=True)
    assert "my-flame-content" in script_js


def test_runs_section_embedded_lazy_js_present(tmp_path):
    from abench_speckz.report import _discover_run_reports, _runs_section

    store.init_results_dir(tmp_path)
    (tmp_path / "raw" / f"{_RUN_ID}_flamegraph.html").write_text("<html/>")
    runs = [
        {
            "run_id": _RUN_ID,
            "config_hash": "h1",
            "vars": {"workload": "read"},
            "tags": ["bench"],
            "exit_code": 0,
            "started_at": "2026-05-14T10:00Z",
        }
    ]
    run_reports = _discover_run_reports(tmp_path)
    section_html, script_js = _runs_section(run_reports, runs, embed=True)
    assert "loadRunReport" in script_js
    assert "_runReports" in script_js


def test_runs_section_linked_contains_href(tmp_path):
    from abench_speckz.report import _discover_run_reports, _runs_section

    store.init_results_dir(tmp_path)
    (tmp_path / "raw" / f"{_RUN_ID}_flamegraph.html").write_text("<html/>")
    runs = [
        {
            "run_id": _RUN_ID,
            "config_hash": "h1",
            "vars": {"workload": "read"},
            "tags": ["bench"],
            "exit_code": 0,
            "started_at": "2026-05-14T10:00Z",
        }
    ]
    run_reports = _discover_run_reports(tmp_path)
    section_html, script_js = _runs_section(run_reports, runs, embed=False)
    assert f'href="raw/{_RUN_ID}_flamegraph.html"' in section_html
    assert script_js == ""


def test_runs_section_absent_when_no_reports(tmp_path):
    import io

    _seed_with_run(tmp_path)
    report_path = tmp_path / "report.html"
    query.stats(tmp_path, group_by=["workload"], report=report_path, out=io.StringIO())
    html = report_path.read_text()
    assert "Per-run reports" not in html


def test_runs_section_filters_failed_runs(tmp_path):
    from abench_speckz.report import _discover_run_reports, _runs_section

    store.init_results_dir(tmp_path)
    (tmp_path / "raw" / f"{_RUN_ID}_flamegraph.html").write_text("<html/>")
    runs = [
        {
            "run_id": _RUN_ID,
            "config_hash": "h1",
            "vars": {"workload": "read"},
            "tags": ["bench"],
            "exit_code": 1,
            "started_at": "2026-05-14T10:00Z",
        }
    ]
    run_reports = _discover_run_reports(tmp_path)
    section_html, script_js = _runs_section(run_reports, runs, embed=True)
    assert section_html == ""
    assert script_js == ""


def test_runs_section_filters_warmup_runs(tmp_path):
    from abench_speckz.report import _discover_run_reports, _runs_section

    store.init_results_dir(tmp_path)
    (tmp_path / "raw" / f"{_RUN_ID}_flamegraph.html").write_text("<html/>")
    runs = [
        {
            "run_id": _RUN_ID,
            "config_hash": "h1",
            "vars": {"workload": "read"},
            "tags": ["warmup"],
            "exit_code": 0,
            "started_at": "2026-05-14T10:00Z",
        }
    ]
    run_reports = _discover_run_reports(tmp_path)
    section_html, script_js = _runs_section(run_reports, runs, embed=True)
    assert section_html == ""
    assert script_js == ""


def test_multiple_reports_per_run(tmp_path):
    from abench_speckz.report import _discover_run_reports, _runs_section

    store.init_results_dir(tmp_path)
    (tmp_path / "raw" / f"{_RUN_ID}_flamegraph.html").write_text("<html>flame</html>")
    (tmp_path / "raw" / f"{_RUN_ID}_profile.html").write_text("<html>profile</html>")
    runs = [
        {
            "run_id": _RUN_ID,
            "config_hash": "h1",
            "vars": {"workload": "read"},
            "tags": ["bench"],
            "exit_code": 0,
            "started_at": "2026-05-14T10:00Z",
        }
    ]
    run_reports = _discover_run_reports(tmp_path)
    section_html, script_js = _runs_section(run_reports, runs, embed=True)
    assert "<summary>flamegraph</summary>" in section_html
    assert "<summary>profile</summary>" in section_html


def test_no_run_reports_mode_skips_section(tmp_path):
    import io

    _seed_with_run(tmp_path)
    (tmp_path / "raw" / f"{_RUN_ID}_flamegraph.html").write_text("<html/>")
    report_path = tmp_path / "report.html"
    query.stats(
        tmp_path, group_by=["workload"], report=report_path, run_reports_mode="none", out=io.StringIO()
    )
    html = report_path.read_text()
    assert "Per-run reports" not in html


def test_run_reports_appear_in_full_report(tmp_path):
    import io

    _seed_with_run(tmp_path)
    (tmp_path / "raw" / f"{_RUN_ID}_flamegraph.html").write_text("<html><body>flame</body></html>")
    report_path = tmp_path / "report.html"
    query.stats(tmp_path, group_by=["workload"], report=report_path, out=io.StringIO())
    html = report_path.read_text()
    assert "Per-run reports" in html
    assert "loadRunReport" in html
    assert "flame" in html


def test_link_mode_no_embedded_content(tmp_path):
    import io

    _seed_with_run(tmp_path)
    (tmp_path / "raw" / f"{_RUN_ID}_flamegraph.html").write_text("<html><body>FLAME_CONTENT</body></html>")
    report_path = tmp_path / "report.html"
    query.stats(
        tmp_path, group_by=["workload"], report=report_path, run_reports_mode="linked", out=io.StringIO()
    )
    html = report_path.read_text()
    assert "Per-run reports" in html
    assert f'href="raw/{_RUN_ID}_flamegraph.html"' in html
    assert "FLAME_CONTENT" not in html
    assert "loadRunReport" not in html


# ---------------------------------------------------------------------------
# Sections / blocks / report_description (editorial layout)
# ---------------------------------------------------------------------------


def test_report_section_title_and_markdown_description(tmp_path):
    _seed(tmp_path)
    plots_path = tmp_path / "plots.yaml"
    plots_path.write_text(
        "plots:\n  - id: p1\n    type: bar\n    x: workload\n    y: rps\n"
        "sections:\n"
        "  - title: My Throughput Section\n"
        "    description: How **RPS** scales.\n"
        "    blocks:\n      - plot_id: p1\n"
    )
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=io.StringIO())
    html = report_path.read_text()
    assert "My Throughput Section" in html
    assert "<strong>RPS</strong>" in html
    assert "chart-p1" in html


def test_report_text_block_rendered_as_markdown(tmp_path):
    _seed(tmp_path)
    plots_path = tmp_path / "plots.yaml"
    plots_path.write_text(
        "plots:\n  - id: p1\n    type: bar\n    x: workload\n    y: rps\n"
        "sections:\n"
        "  - title: S\n"
        "    blocks:\n"
        "      - text: A *note* before the chart.\n"
        "      - plot_id: p1\n"
    )
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=io.StringIO())
    html = report_path.read_text()
    assert "<em>note</em>" in html


def test_report_inline_html_block(tmp_path):
    _seed(tmp_path)
    plots_path = tmp_path / "plots.yaml"
    plots_path.write_text(
        "plots:\n  - id: p1\n    type: bar\n    x: workload\n    y: rps\n"
        "sections:\n"
        "  - title: S\n"
        "    blocks:\n"
        "      - html: \"<div class='cb' id='inline-marker'>RAW_HTML</div>\"\n"
    )
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=io.StringIO())
    html = report_path.read_text()
    assert "RAW_HTML" in html
    assert "id='inline-marker'" in html


def test_report_include_html_block(tmp_path):
    _seed(tmp_path)
    sidecar = tmp_path / "note.html"
    sidecar.write_text("<p id='from-file'>SIDECAR_CONTENT</p>")
    plots_path = tmp_path / "plots.yaml"
    plots_path.write_text(
        "plots:\n  - id: p1\n    type: bar\n    x: workload\n    y: rps\n"
        "sections:\n"
        "  - title: S\n"
        "    blocks:\n"
        "      - include_html: note.html\n"
    )
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=io.StringIO())
    html = report_path.read_text()
    assert "SIDECAR_CONTENT" in html
    assert "from-file" in html


def test_report_top_level_description(tmp_path):
    _seed(tmp_path)
    plots_path = tmp_path / "plots.yaml"
    plots_path.write_text(
        "report_title: My Suite\n"
        "report_description: |\n  An **intro** block.\n"
        "plots:\n  - id: p1\n    type: bar\n    x: workload\n    y: rps\n"
        "sections:\n"
        "  - title: S\n"
        "    blocks:\n      - plot_id: p1\n"
    )
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=io.StringIO())
    html = report_path.read_text()
    assert 'class="report-intro"' in html
    assert "<strong>intro</strong>" in html
    # intro should appear before the first section's <h2>
    assert html.index('class="report-intro"') < html.index("<h2>S</h2>")


def test_report_unreferenced_plot_omitted(tmp_path):
    _seed(tmp_path)
    plots_path = tmp_path / "plots.yaml"
    plots_path.write_text(
        "plots:\n"
        "  - id: used\n    type: bar\n    x: workload\n    y: rps\n"
        "  - id: unused\n    type: line\n    x: workload\n    y: rps\n"
        "sections:\n"
        "  - title: S\n"
        "    blocks:\n      - plot_id: used\n"
    )
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=io.StringIO())
    html = report_path.read_text()
    assert "chart-used" in html
    assert "chart-unused" not in html


def test_report_plot_referenced_twice_renders_twice(tmp_path):
    _seed(tmp_path)
    plots_path = tmp_path / "plots.yaml"
    plots_path.write_text(
        "plots:\n  - id: p1\n    type: bar\n    x: workload\n    y: rps\n"
        "sections:\n"
        "  - title: A\n"
        "    blocks:\n      - plot_id: p1\n"
        "  - title: B\n"
        "    blocks:\n      - plot_id: p1\n"
    )
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=io.StringIO())
    html = report_path.read_text()
    # Both occurrences should have distinct canvas ids
    assert 'id="chart-p1-0"' in html
    assert 'id="chart-p1-1"' in html


def test_report_back_compat_no_sections(tmp_path):
    """When sections key is absent, each plot still renders as its own section."""
    _seed(tmp_path)
    plots_path = tmp_path / "plots.yaml"
    plots_path.write_text(
        "plots:\n"
        "  - id: a\n    type: bar\n    x: workload\n    y: rps\n"
        "  - id: b\n    type: line\n    x: workload\n    y: rps\n"
    )
    report_path = tmp_path / "r.html"
    query.stats(tmp_path, report=report_path, plots=plots_path, out=io.StringIO())
    html = report_path.read_text()
    assert "chart-a" in html
    assert "chart-b" in html
    # Type meta shown next to each plot in back-compat layout
    assert "type: bar" in html
    assert "type: line" in html
