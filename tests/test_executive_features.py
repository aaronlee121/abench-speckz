"""Tests for executive-level features: _select, KPI blocks, headline blocks, normalize."""
import io

import pytest

from abench_speckz import plots, query, store
from abench_speckz.model import RunError
from abench_speckz.plots import (
    Block,
    HeadlineDef,
    KpiCard,
    KpiCompare,
    NormalizeSpec,
)
from abench_speckz.report._select import higher_is_better_map, rank_by, resolve_value


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed(results_dir, combos=None):
    """Populate a results directory with synthetic runs."""
    store.init_results_dir(results_dir)
    if combos is None:
        combos = [
            ("h1", {"backend": "postgres", "workload": "read"}, {"rps": 1000.0, "lat": 50.0}),
            ("h2", {"backend": "mysql", "workload": "read"}, {"rps": 500.0, "lat": 80.0}),
            ("h3", {"backend": "postgres", "workload": "write"}, {"rps": 600.0, "lat": 70.0}),
            ("h4", {"backend": "mysql", "workload": "write"}, {"rps": 300.0, "lat": 120.0}),
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


def _make_tools_dir(results_dir, higher_is_better: dict):
    tools_dir = results_dir / "tools"
    tools_dir.mkdir(exist_ok=True)
    import yaml
    (tools_dir / "mytool.yaml").write_text(
        yaml.dump({"name": "mytool", "command": "x", "higher_is_better": higher_is_better})
    )


# ---------------------------------------------------------------------------
# Phase 0: _select helpers
# ---------------------------------------------------------------------------


class TestHigherIsBetterMap:
    def test_empty_when_no_tools_dir(self, tmp_path):
        assert higher_is_better_map(tmp_path) == {}

    def test_reads_flags(self, tmp_path):
        _make_tools_dir(tmp_path, {"rps": True, "lat": False})
        hib = higher_is_better_map(tmp_path)
        assert hib["rps"] is True
        assert hib["lat"] is False

    def test_first_tool_wins_on_conflict(self, tmp_path):
        import yaml
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "a.yaml").write_text(yaml.dump({"name": "a", "command": "x", "higher_is_better": {"rps": True}}))
        (tools_dir / "b.yaml").write_text(yaml.dump({"name": "b", "command": "x", "higher_is_better": {"rps": False}}))
        hib = higher_is_better_map(tmp_path)
        assert hib["rps"] is True


class TestResolveValue:
    def test_returns_none_when_no_data(self, tmp_path):
        assert resolve_value(tmp_path, "rps") is None

    def test_returns_mean_across_combos(self, tmp_path):
        _seed(tmp_path)
        val = resolve_value(tmp_path, "rps")
        assert val is not None
        assert val > 0

    def test_stat_max(self, tmp_path):
        _seed(tmp_path)
        val_max = resolve_value(tmp_path, "rps", stat="max")
        val_mean = resolve_value(tmp_path, "rps", stat="mean")
        assert val_max >= val_mean

    def test_stat_min(self, tmp_path):
        _seed(tmp_path)
        val_min = resolve_value(tmp_path, "rps", stat="min")
        val_mean = resolve_value(tmp_path, "rps", stat="mean")
        assert val_min <= val_mean

    def test_where_filter(self, tmp_path):
        _seed(tmp_path)
        val_pg = resolve_value(tmp_path, "rps", where=["backend=postgres"])
        val_all = resolve_value(tmp_path, "rps")
        # postgres rps avg (1000+600)/2 = 800; overall avg (1000+500+600+300)/4 = 600
        assert val_pg > val_all

    def test_unknown_metric_returns_none(self, tmp_path):
        _seed(tmp_path)
        assert resolve_value(tmp_path, "nonexistent") is None


class TestRankBy:
    def test_returns_sorted_descending(self, tmp_path):
        _seed(tmp_path)
        pairs = rank_by(tmp_path, "rps", "backend")
        assert len(pairs) == 2
        vals = [p[1] for p in pairs]
        assert vals == sorted(vals, reverse=True)

    def test_best_backend_is_postgres(self, tmp_path):
        _seed(tmp_path)
        pairs = rank_by(tmp_path, "rps", "backend")
        assert pairs[0][0] == "postgres"
        assert pairs[-1][0] == "mysql"

    def test_where_filter_narrows_results(self, tmp_path):
        _seed(tmp_path)
        pairs_read = rank_by(tmp_path, "rps", "backend", where=["workload=read"])
        assert len(pairs_read) == 2
        # read: postgres=1000, mysql=500
        assert pairs_read[0][0] == "postgres"

    def test_empty_when_no_data(self, tmp_path):
        assert rank_by(tmp_path, "rps", "backend") == []


# ---------------------------------------------------------------------------
# Phase 1: KPI block parsing
# ---------------------------------------------------------------------------


class TestKpiBlockParsing:
    def _section_yaml(self, blocks_yaml: str) -> str:
        return (
            "plots:\n  - id: p1\n    type: bar\n    x: workload\n    y: rps\n"
            "sections:\n  - title: KPIs\n    blocks:\n"
            + blocks_yaml
        )

    def test_parse_minimal_kpi(self, tmp_path):
        p = tmp_path / "plots.yaml"
        p.write_text(
            self._section_yaml(
                "      - kpi:\n"
                "          - label: Peak RPS\n"
                "            metric: rps\n"
            )
        )
        doc = plots.load_plots_file(p)
        block = doc.sections[0].blocks[0]
        assert block.kind == "kpi"
        assert len(block.data) == 1
        card: KpiCard = block.data[0]
        assert card.label == "Peak RPS"
        assert card.metric == "rps"
        assert card.stat == "mean"
        assert card.where == []
        assert card.unit == ""
        assert card.precision == 2
        assert card.compare is None

    def test_parse_kpi_full(self, tmp_path):
        p = tmp_path / "plots.yaml"
        p.write_text(
            self._section_yaml(
                "      - kpi:\n"
                "          - label: Peak throughput\n"
                "            metric: rps\n"
                "            stat: max\n"
                "            where: {workload: heavy}\n"
                "            unit: req/s\n"
                "            precision: 0\n"
                "            compare:\n"
                "              where: {workload: light}\n"
            )
        )
        doc = plots.load_plots_file(p)
        card: KpiCard = doc.sections[0].blocks[0].data[0]
        assert card.stat == "max"
        assert card.where == ["workload=heavy"]
        assert card.unit == "req/s"
        assert card.precision == 0
        assert card.compare is not None
        assert card.compare.where == ["workload=light"]

    def test_kpi_invalid_stat(self, tmp_path):
        p = tmp_path / "plots.yaml"
        p.write_text(
            self._section_yaml(
                "      - kpi:\n"
                "          - label: X\n            metric: rps\n            stat: p95\n"
            )
        )
        with pytest.raises(RunError, match="stat must be one of"):
            plots.load_plots_file(p)

    def test_kpi_missing_label(self, tmp_path):
        p = tmp_path / "plots.yaml"
        p.write_text(
            self._section_yaml("      - kpi:\n          - metric: rps\n")
        )
        with pytest.raises(RunError, match="label must be a non-empty string"):
            plots.load_plots_file(p)

    def test_kpi_missing_metric(self, tmp_path):
        p = tmp_path / "plots.yaml"
        p.write_text(
            self._section_yaml("      - kpi:\n          - label: X\n")
        )
        with pytest.raises(RunError, match="metric must be a non-empty string"):
            plots.load_plots_file(p)

    def test_kpi_unknown_field(self, tmp_path):
        p = tmp_path / "plots.yaml"
        p.write_text(
            self._section_yaml(
                "      - kpi:\n          - label: X\n            metric: rps\n            color: red\n"
            )
        )
        with pytest.raises(RunError, match="unknown fields"):
            plots.load_plots_file(p)

    def test_kpi_not_list(self, tmp_path):
        p = tmp_path / "plots.yaml"
        p.write_text(
            self._section_yaml("      - kpi: not_a_list\n")
        )
        with pytest.raises(RunError, match="kpi must be a list"):
            plots.load_plots_file(p)

    def test_kpi_precision_negative_rejected(self, tmp_path):
        p = tmp_path / "plots.yaml"
        p.write_text(
            self._section_yaml(
                "      - kpi:\n          - label: X\n            metric: rps\n            precision: -1\n"
            )
        )
        with pytest.raises(RunError, match="precision must be a non-negative integer"):
            plots.load_plots_file(p)


# ---------------------------------------------------------------------------
# Phase 2: Headline block parsing
# ---------------------------------------------------------------------------


class TestHeadlineBlockParsing:
    def _section_yaml(self, blocks_yaml: str) -> str:
        return (
            "plots:\n  - id: p1\n    type: bar\n    x: workload\n    y: rps\n"
            "sections:\n  - title: Headlines\n    blocks:\n"
            + blocks_yaml
        )

    def test_parse_minimal_headline(self, tmp_path):
        p = tmp_path / "plots.yaml"
        p.write_text(
            self._section_yaml(
                "      - headline:\n"
                "          metric: rps\n"
                "          compare: backend\n"
                "          template: '{best} is faster than {worst}'\n"
            )
        )
        doc = plots.load_plots_file(p)
        block = doc.sections[0].blocks[0]
        assert block.kind == "headline"
        h: HeadlineDef = block.data
        assert h.metric == "rps"
        assert h.compare == "backend"
        assert h.template == "{best} is faster than {worst}"
        assert h.where == []

    def test_parse_headline_with_where(self, tmp_path):
        p = tmp_path / "plots.yaml"
        p.write_text(
            self._section_yaml(
                "      - headline:\n"
                "          metric: rps\n"
                "          compare: backend\n"
                "          template: '{best} wins'\n"
                "          where: {workload: read}\n"
            )
        )
        doc = plots.load_plots_file(p)
        h: HeadlineDef = doc.sections[0].blocks[0].data
        assert h.where == ["workload=read"]

    def test_headline_missing_metric(self, tmp_path):
        p = tmp_path / "plots.yaml"
        p.write_text(
            self._section_yaml(
                "      - headline:\n          compare: backend\n          template: x\n"
            )
        )
        with pytest.raises(RunError, match="headline.metric must be a non-empty string"):
            plots.load_plots_file(p)

    def test_headline_missing_compare(self, tmp_path):
        p = tmp_path / "plots.yaml"
        p.write_text(
            self._section_yaml(
                "      - headline:\n          metric: rps\n          template: x\n"
            )
        )
        with pytest.raises(RunError, match="headline.compare must be a non-empty string"):
            plots.load_plots_file(p)

    def test_headline_unknown_field(self, tmp_path):
        p = tmp_path / "plots.yaml"
        p.write_text(
            self._section_yaml(
                "      - headline:\n"
                "          metric: rps\n          compare: backend\n"
                "          template: x\n          color: red\n"
            )
        )
        with pytest.raises(RunError, match="unknown fields"):
            plots.load_plots_file(p)


# ---------------------------------------------------------------------------
# Phase 1 + 2: HTML rendering
# ---------------------------------------------------------------------------


class TestKpiRendering:
    def test_kpi_block_renders_value(self, tmp_path):
        _seed(tmp_path)
        _make_tools_dir(tmp_path, {"rps": True})
        from abench_speckz.report._kpi import render_kpi_block

        cards = [KpiCard(label="Peak RPS", metric="rps", stat="max", unit="req/s", precision=0)]
        html = render_kpi_block(cards, tmp_path, {})
        assert "kpi-grid" in html
        assert "kpi-card" in html
        assert "Peak RPS" in html
        assert "req/s" in html

    def test_kpi_no_data_shows_dash(self, tmp_path):
        from abench_speckz.report._kpi import render_kpi_block

        cards = [KpiCard(label="Nothing", metric="nonexistent")]
        html = render_kpi_block(cards, tmp_path, {})
        assert "—" in html

    def test_kpi_with_compare_shows_delta(self, tmp_path):
        _seed(tmp_path)
        _make_tools_dir(tmp_path, {"rps": True})
        from abench_speckz.report._kpi import render_kpi_block

        compare = KpiCompare(where=["backend=mysql"])
        cards = [KpiCard(
            label="RPS vs mysql",
            metric="rps",
            stat="mean",
            where=["backend=postgres"],
            compare=compare,
        )]
        html = render_kpi_block(cards, tmp_path, {})
        # postgres > mysql so delta should be positive/good
        assert "kpi-delta-good" in html
        assert "↑" in html


class TestHeadlineRendering:
    def test_headline_renders_best_worst(self, tmp_path):
        _seed(tmp_path)
        _make_tools_dir(tmp_path, {"rps": True})
        from abench_speckz.report._headline import render_headline_block

        h = HeadlineDef(
            metric="rps",
            compare="backend",
            template="{best} beats {worst} by {ratio}",
        )
        html = render_headline_block(h, tmp_path, {})
        assert "callout" in html
        assert "postgres" in html
        assert "mysql" in html
        assert "x" in html  # ratio contains "x"

    def test_headline_insufficient_data(self, tmp_path):
        from abench_speckz.report._headline import render_headline_block

        h = HeadlineDef(metric="rps", compare="backend", template="{best}")
        html = render_headline_block(h, tmp_path, {})
        assert "insufficient data" in html

    def test_headline_lower_is_better_reverses_best_worst(self, tmp_path):
        _seed(tmp_path)
        _make_tools_dir(tmp_path, {"lat": False})
        from abench_speckz.report._headline import render_headline_block

        h = HeadlineDef(
            metric="lat",
            compare="backend",
            template="{best} has lower latency than {worst}",
        )
        html = render_headline_block(h, tmp_path, {})
        # postgres lat=50 < mysql lat=80, so with lower-is-better postgres is "best"
        assert "postgres" in html
        # The "worst" in the template should be mysql (high latency)
        assert "mysql" in html


# ---------------------------------------------------------------------------
# Phase 3: normalize field parsing
# ---------------------------------------------------------------------------


class TestNormalizeParsing:
    def test_parse_normalize(self, tmp_path):
        p = tmp_path / "plots.yaml"
        p.write_text(
            "- id: speedup\n"
            "  type: bar\n"
            "  x: workload\n"
            "  y: rps\n"
            "  group_by: backend\n"
            "  normalize:\n"
            "    against: backend\n"
            "    reference: postgres\n"
        )
        doc = plots.load_plots_file(p)
        norm = doc.plots[0].normalize
        assert norm is not None
        assert norm.against == "backend"
        assert norm.reference == "postgres"

    def test_normalize_defaults_to_none(self, tmp_path):
        p = tmp_path / "plots.yaml"
        p.write_text("- id: p1\n  type: bar\n  x: workload\n  y: rps\n")
        doc = plots.load_plots_file(p)
        assert doc.plots[0].normalize is None

    def test_normalize_missing_against(self, tmp_path):
        p = tmp_path / "plots.yaml"
        p.write_text(
            "- id: p1\n  type: bar\n  x: w\n  y: rps\n"
            "  normalize:\n    reference: postgres\n"
        )
        with pytest.raises(RunError, match="normalize.against must be a non-empty string"):
            plots.load_plots_file(p)

    def test_normalize_missing_reference(self, tmp_path):
        p = tmp_path / "plots.yaml"
        p.write_text(
            "- id: p1\n  type: bar\n  x: w\n  y: rps\n"
            "  normalize:\n    against: backend\n"
        )
        with pytest.raises(RunError, match="normalize.reference must be a non-empty value"):
            plots.load_plots_file(p)

    def test_normalize_unknown_field(self, tmp_path):
        p = tmp_path / "plots.yaml"
        p.write_text(
            "- id: p1\n  type: bar\n  x: w\n  y: rps\n"
            "  normalize:\n    against: backend\n    reference: pg\n    color: red\n"
        )
        with pytest.raises(RunError, match="normalize: unknown fields"):
            plots.load_plots_file(p)

    def test_normalize_rejected_on_scatter(self, tmp_path):
        p = tmp_path / "plots.yaml"
        p.write_text(
            "- id: p1\n  type: scatter\n  x: rps\n  y: lat\n"
            "  normalize:\n    against: backend\n    reference: pg\n"
        )
        with pytest.raises(RunError, match="normalize is not allowed on 'scatter'"):
            plots.load_plots_file(p)

    def test_normalize_rejected_on_stacked_bar(self, tmp_path):
        p = tmp_path / "plots.yaml"
        p.write_text(
            "- id: p1\n  type: stacked-bar\n  x: w\n  y: [rps, lat]\n"
            "  normalize:\n    against: backend\n    reference: pg\n"
        )
        with pytest.raises(RunError, match="normalize is not allowed on 'stacked-bar'"):
            plots.load_plots_file(p)


# ---------------------------------------------------------------------------
# Phase 3: normalize chart rendering
# ---------------------------------------------------------------------------


class TestNormalizeChart:
    def test_normalize_divides_by_reference(self, tmp_path):
        _seed(tmp_path)
        from abench_speckz.plots import PlotSpec
        from abench_speckz.report._charts import build_bar_or_line_config

        # postgres rps=1000 (mean over read+write: (1000+600)/2=800),
        # mysql rps=500 (mean over read+write: (500+300)/2=400)
        rows = [
            {"workload": "read", "backend": "postgres", "metric": "rps", "mean": 1000.0, "stddev": 0.0, "n": 3, "n_failed": 0, "p50": 1000.0, "p95": 1000.0, "p99": 1000.0, "ci95_low": 1000.0, "ci95_high": 1000.0},
            {"workload": "read", "backend": "mysql", "metric": "rps", "mean": 500.0, "stddev": 0.0, "n": 3, "n_failed": 0, "p50": 500.0, "p95": 500.0, "p99": 500.0, "ci95_low": 500.0, "ci95_high": 500.0},
        ]
        plot = PlotSpec(
            id="speedup",
            type="bar",
            x="workload",
            y=["rps"],
            group_by=["backend"],
            normalize=NormalizeSpec(against="backend", reference="postgres"),
        )
        config, _ = build_bar_or_line_config(
            plot, rows, {}, chart_type="bar", stacked=False,
            group_by=["backend"], varying_keys=["backend"],
        )
        datasets = config["data"]["datasets"]
        # postgres should be 1.0, mysql should be ~0.5
        pg_ds = next(d for d in datasets if "postgres" in d["label"])
        my_ds = next(d for d in datasets if "mysql" in d["label"])
        assert pg_ds["data"][0] == pytest.approx(1.0)
        assert my_ds["data"][0] == pytest.approx(0.5)

    def test_normalize_sets_speedup_y_label(self, tmp_path):
        from abench_speckz.plots import PlotSpec
        from abench_speckz.report._charts import build_bar_or_line_config

        rows = [
            {"workload": "read", "backend": "postgres", "metric": "rps", "mean": 100.0, "stddev": 0.0, "n": 1, "n_failed": 0, "p50": 100.0, "p95": 100.0, "p99": 100.0, "ci95_low": 100.0, "ci95_high": 100.0},
            {"workload": "read", "backend": "mysql", "metric": "rps", "mean": 50.0, "stddev": 0.0, "n": 1, "n_failed": 0, "p50": 50.0, "p95": 50.0, "p99": 50.0, "ci95_low": 50.0, "ci95_high": 50.0},
        ]
        plot = PlotSpec(
            id="speedup", type="bar", x="workload", y=["rps"], group_by=["backend"],
            normalize=NormalizeSpec(against="backend", reference="postgres"),
        )
        config, _ = build_bar_or_line_config(
            plot, rows, {}, chart_type="bar", stacked=False,
            group_by=["backend"], varying_keys=["backend"],
        )
        y_scale = config["options"]["scales"]["y"]
        assert "× speedup" in y_scale.get("title", {}).get("text", "")
        # Reference line plugin config present
        assert config["options"]["plugins"].get("abenchRefLine", {}).get("y") == 1.0

    def test_normalize_against_not_in_group_by_raises(self, tmp_path):
        from abench_speckz.plots import PlotSpec
        from abench_speckz.report._charts import build_bar_or_line_config

        rows = [
            {"workload": "read", "backend": "postgres", "metric": "rps", "mean": 100.0, "stddev": 0.0, "n": 1, "n_failed": 0, "p50": 100.0, "p95": 100.0, "p99": 100.0, "ci95_low": 100.0, "ci95_high": 100.0},
        ]
        plot = PlotSpec(
            id="p1", type="bar", x="workload", y=["rps"], group_by=["workload"],
            normalize=NormalizeSpec(against="backend", reference="postgres"),
        )
        with pytest.raises(RunError, match="normalize.against"):
            build_bar_or_line_config(
                plot, rows, {}, chart_type="bar", stacked=False,
                group_by=["workload"], varying_keys=["workload"],
            )


# ---------------------------------------------------------------------------
# Integration: sections with kpi/headline blocks render to HTML
# ---------------------------------------------------------------------------


class TestExecutiveBlocksE2E:
    def test_report_with_kpi_block(self, tmp_path):
        _seed(tmp_path)
        _make_tools_dir(tmp_path, {"rps": True})

        plots_yaml = tmp_path / "plots.yaml"
        plots_yaml.write_text(
            "plots:\n"
            "  - id: p1\n    type: bar\n    x: workload\n    y: rps\n    group_by: backend\n"
            "sections:\n"
            "  - title: Summary\n"
            "    blocks:\n"
            "      - kpi:\n"
            "          - label: Peak RPS\n"
            "            metric: rps\n"
            "            stat: max\n"
            "            unit: req/s\n"
            "            precision: 0\n"
        )
        report_path = tmp_path / "report.html"
        doc = plots.load_plots_file(plots_yaml)
        query.stats(
            tmp_path,
            report=report_path,
            plots=plots_yaml,
            out=io.StringIO(),
        )
        html = report_path.read_text()
        assert "kpi-grid" in html
        assert "Peak RPS" in html
        assert "req/s" in html

    def test_report_with_headline_block(self, tmp_path):
        _seed(tmp_path)
        _make_tools_dir(tmp_path, {"rps": True})

        plots_yaml = tmp_path / "plots.yaml"
        plots_yaml.write_text(
            "plots:\n"
            "  - id: p1\n    type: bar\n    x: workload\n    y: rps\n    group_by: backend\n"
            "sections:\n"
            "  - title: Headlines\n"
            "    blocks:\n"
            "      - headline:\n"
            "          metric: rps\n"
            "          compare: backend\n"
            "          template: '{best} delivers {ratio} the throughput of {worst}'\n"
        )
        report_path = tmp_path / "report.html"
        query.stats(
            tmp_path,
            report=report_path,
            plots=plots_yaml,
            out=io.StringIO(),
        )
        html = report_path.read_text()
        assert "callout" in html
        assert "postgres" in html or "mysql" in html
