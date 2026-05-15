import pytest

from abench_speckz import plots
from abench_speckz.model import RunError


def test_parse_minimal_bar():
    raw = [{"id": "p1", "type": "bar", "x": "workload", "y": "rps"}]
    specs = plots.parse_plots(raw, source="<t>")
    assert len(specs) == 1
    assert specs[0].id == "p1"
    assert specs[0].type == "bar"
    assert specs[0].x == "workload"
    assert specs[0].y == ["rps"]
    assert specs[0].group_by is None
    assert specs[0].title is None


def test_parse_y_as_list():
    raw = [{"id": "p1", "type": "stacked-bar", "x": "workload", "y": ["p50", "p95"]}]
    specs = plots.parse_plots(raw, source="<t>")
    assert specs[0].y == ["p50", "p95"]


def test_parse_with_group_by_string():
    raw = [{"id": "p1", "type": "line", "x": "concurrency", "y": "rps",
            "group_by": "backend", "title": "RPS"}]
    specs = plots.parse_plots(raw, source="<t>")
    assert specs[0].group_by == ["backend"]
    assert specs[0].title == "RPS"


def test_parse_with_group_by_list():
    raw = [{"id": "p1", "type": "line", "x": "concurrency", "y": "rps",
            "group_by": ["workload", "backend"]}]
    specs = plots.parse_plots(raw, source="<t>")
    assert specs[0].group_by == ["workload", "backend"]


def test_parse_group_by_empty_list_rejected():
    raw = [{"id": "p1", "type": "line", "x": "concurrency", "y": "rps", "group_by": []}]
    with pytest.raises(RunError, match="must not be empty"):
        plots.parse_plots(raw, source="<t>")


def test_parse_group_by_list_with_non_string_rejected():
    raw = [{"id": "p1", "type": "line", "x": "c", "y": "rps", "group_by": ["ok", 42]}]
    with pytest.raises(RunError, match=r"group_by\[1\]"):
        plots.parse_plots(raw, source="<t>")


def test_parse_none_returns_empty():
    assert plots.parse_plots(None, source="<t>") == []


def test_parse_rejects_non_list():
    with pytest.raises(RunError, match="must be a list"):
        plots.parse_plots({"id": "p1"}, source="<t>")


def test_parse_rejects_unknown_type():
    raw = [{"id": "p1", "type": "pie", "x": "w", "y": "rps"}]
    with pytest.raises(RunError, match="type must be one of"):
        plots.parse_plots(raw, source="<t>")


def test_parse_rejects_missing_id():
    raw = [{"type": "bar", "x": "w", "y": "rps"}]
    with pytest.raises(RunError, match="id is required"):
        plots.parse_plots(raw, source="<t>")


def test_parse_rejects_missing_x():
    raw = [{"id": "p1", "type": "bar", "y": "rps"}]
    with pytest.raises(RunError, match="x is required"):
        plots.parse_plots(raw, source="<t>")


def test_parse_rejects_missing_y():
    raw = [{"id": "p1", "type": "bar", "x": "w"}]
    with pytest.raises(RunError, match="y is required"):
        plots.parse_plots(raw, source="<t>")


def test_parse_rejects_duplicate_id():
    raw = [
        {"id": "p1", "type": "bar", "x": "w", "y": "rps"},
        {"id": "p1", "type": "line", "x": "w", "y": "rps"},
    ]
    with pytest.raises(RunError, match="duplicate plot id"):
        plots.parse_plots(raw, source="<t>")


def test_parse_rejects_scatter_with_multiple_y():
    raw = [{"id": "p1", "type": "scatter", "x": "rps", "y": ["lat1", "lat2"]}]
    with pytest.raises(RunError, match="scatter requires exactly one"):
        plots.parse_plots(raw, source="<t>")


def test_parse_rejects_stacked_bar_with_one_y():
    raw = [{"id": "p1", "type": "stacked-bar", "x": "w", "y": "rps"}]
    with pytest.raises(RunError, match="stacked-bar requires at least two"):
        plots.parse_plots(raw, source="<t>")


def test_parse_rejects_unknown_field():
    raw = [{"id": "p1", "type": "bar", "x": "w", "y": "rps", "color": "red"}]
    with pytest.raises(RunError, match="unknown fields"):
        plots.parse_plots(raw, source="<t>")


def test_load_plots_file_with_top_level_key(tmp_path):
    p = tmp_path / "plots.yaml"
    p.write_text("plots:\n  - id: p1\n    type: bar\n    x: w\n    y: rps\n")
    specs, title = plots.load_plots_file(p)
    assert len(specs) == 1
    assert specs[0].id == "p1"
    assert title is None


def test_load_plots_file_report_title(tmp_path):
    p = tmp_path / "plots.yaml"
    p.write_text("report_title: My Benchmark\nplots:\n  - id: p1\n    type: bar\n    x: w\n    y: rps\n")
    specs, title = plots.load_plots_file(p)
    assert specs[0].id == "p1"
    assert title == "My Benchmark"


def test_load_plots_file_top_level_list(tmp_path):
    p = tmp_path / "plots.yaml"
    p.write_text("- id: p1\n  type: bar\n  x: w\n  y: rps\n")
    specs, title = plots.load_plots_file(p)
    assert specs[0].id == "p1"
    assert title is None


def test_load_plots_file_missing(tmp_path):
    with pytest.raises(RunError, match="not found"):
        plots.load_plots_file(tmp_path / "does_not_exist.yaml")


def test_discover_from_tool_snapshots(tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "a.yaml").write_text(
        "name: a\ncommand: x\nplots:\n  - id: p1\n    type: bar\n    x: w\n    y: rps\n"
    )
    (tools_dir / "b.yaml").write_text(
        "name: b\ncommand: x\nplots:\n  - id: p2\n    type: line\n    x: c\n    y: lat\n"
    )
    specs, title = plots.discover_from_tool_snapshots(tmp_path)
    ids = [s.id for s in specs]
    assert "p1" in ids
    assert "p2" in ids
    assert title is None


def test_discover_from_tool_snapshots_report_title(tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "a.yaml").write_text(
        "name: a\ncommand: x\nreport_title: My Suite\n"
        "plots:\n  - id: p1\n    type: bar\n    x: w\n    y: rps\n"
    )
    specs, title = plots.discover_from_tool_snapshots(tmp_path)
    assert title == "My Suite"


def test_discover_from_tool_snapshots_no_dir(tmp_path):
    specs, title = plots.discover_from_tool_snapshots(tmp_path)
    assert specs == []
    assert title is None


def test_discover_dedupes_by_id(tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "a.yaml").write_text(
        "name: a\ncommand: x\nplots:\n  - id: shared\n    type: bar\n    x: w\n    y: rps\n"
    )
    (tools_dir / "b.yaml").write_text(
        "name: b\ncommand: x\nplots:\n  - id: shared\n    type: line\n    x: c\n    y: lat\n"
    )
    specs, _ = plots.discover_from_tool_snapshots(tmp_path)
    assert len([s for s in specs if s.id == "shared"]) == 1


def test_parse_group_by_negation_single_string():
    raw = [{"id": "p1", "type": "line", "x": "concurrency", "y": "rps",
            "group_by": "!concurrency"}]
    specs = plots.parse_plots(raw, source="<t>")
    assert specs[0].group_by == ["!concurrency"]


def test_parse_group_by_negation_in_list():
    raw = [{"id": "p1", "type": "line", "x": "concurrency", "y": "rps",
            "group_by": ["!concurrency", "workload"]}]
    specs = plots.parse_plots(raw, source="<t>")
    assert specs[0].group_by == ["!concurrency", "workload"]


def test_parse_group_by_bare_bang_rejected():
    raw = [{"id": "p1", "type": "line", "x": "c", "y": "rps",
            "group_by": ["!"]}]
    with pytest.raises(RunError, match="must be followed by"):
        plots.parse_plots(raw, source="<t>")
