import pytest

from abench_speckz import tools
from abench_speckz.model import RunError


def test_load_minimal(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: 'echo hi'\n")
    spec = tools.load_tool(p)
    assert spec.name == "t"
    assert spec.command == "echo hi"
    assert spec.timeout_seconds == 600


def test_required_fields(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\n")
    with pytest.raises(RunError, match="command"):
        tools.load_tool(p)


def test_unknown_field(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\nbogus: 1\n")
    with pytest.raises(RunError, match="unknown tool fields"):
        tools.load_tool(p)


def test_full_round_trip(fixtures_dir):
    spec = tools.load_tool(fixtures_dir / "oha.tool.yaml")
    assert spec.name == "faketool"
    assert spec.timeout_seconds == 10
    assert "p95_latency_ms" in spec.capture
    assert spec.pretty_names["rps"] == "Requests/sec"
    assert spec.higher_is_better["rps"] is True


def test_output_file_field(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: 'run'\noutput_file: '/tmp/out.json'\n")
    spec = tools.load_tool(p)
    assert spec.output_file == "/tmp/out.json"


def test_output_file_not_string(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: 'run'\noutput_file: 123\n")
    with pytest.raises(RunError, match="output_file"):
        tools.load_tool(p)


def test_output_format_jsonl(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: 'run'\noutput_format: jsonl\n")
    spec = tools.load_tool(p)
    assert spec.output_format == "jsonl"


def test_output_format_default_is_json(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: 'run'\n")
    spec = tools.load_tool(p)
    assert spec.output_format == "json"


def test_output_format_invalid(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: 'run'\noutput_format: csv\n")
    with pytest.raises(RunError, match="output_format"):
        tools.load_tool(p)


def test_resolve_tool_version(fixtures_dir):
    spec = tools.load_tool(fixtures_dir / "oha.tool.yaml")
    v = tools.resolve_tool_version(spec)
    assert v == "fake-tool 1.2.3"


def test_plots_field_accepted(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text(
        "name: t\ncommand: x\n"
        "plots:\n  - id: p1\n    type: bar\n    x: workload\n    y: rps\n"
    )
    spec = tools.load_tool(p)
    assert len(spec.plots) == 1
    assert spec.plots[0]["id"] == "p1"


def test_plots_field_must_be_list(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\nplots: not_a_list\n")
    with pytest.raises(RunError, match="plots must be a list"):
        tools.load_tool(p)


def test_plots_field_default_empty(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\n")
    spec = tools.load_tool(p)
    assert spec.plots == []


def test_setup_and_teardown_loaded(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text(
        "name: t\ncommand: x\n"
        "setup:\n  - 'docker compose up -d redis'\n  - 'sleep 1'\n"
        "teardown:\n  - 'docker compose down -v'\n"
    )
    spec = tools.load_tool(p)
    assert spec.setup == ["docker compose up -d redis", "sleep 1"]
    assert spec.teardown == ["docker compose down -v"]


def test_setup_default_empty(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\n")
    spec = tools.load_tool(p)
    assert spec.setup == []
    assert spec.teardown == []
    assert spec.setup_timeout_seconds == 120


def test_setup_must_be_list(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\nsetup: 'docker compose up'\n")
    with pytest.raises(RunError, match="tool.setup must be a list"):
        tools.load_tool(p)


def test_setup_steps_must_be_strings(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\nsetup:\n  - 42\n")
    with pytest.raises(RunError, match=r"tool.setup\[0\] must be a string"):
        tools.load_tool(p)


def test_setup_timeout_must_be_positive_int(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\nsetup_timeout_seconds: 0\n")
    with pytest.raises(RunError, match="setup_timeout_seconds"):
        tools.load_tool(p)
