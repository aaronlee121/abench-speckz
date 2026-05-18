import pytest

from abench_speckz import tools
from abench_speckz.model import RunError
from abench_speckz.runspec import MonitorEntry


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
    p.write_text("name: t\ncommand: x\nplots:\n  - id: p1\n    type: bar\n    x: workload\n    y: rps\n")
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


def test_env_probes_loaded(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\nenv_probes:\n  kernel: 'uname -r'\n  py: 'python --version'\n")
    spec = tools.load_tool(p)
    assert spec.env_probes == {"kernel": "uname -r", "py": "python --version"}


def test_env_probes_default_empty(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\n")
    spec = tools.load_tool(p)
    assert spec.env_probes == {}


def test_env_probes_must_be_mapping(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\nenv_probes: [a, b]\n")
    with pytest.raises(RunError, match="env_probes must be a mapping"):
        tools.load_tool(p)


def test_env_probes_value_must_be_string(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\nenv_probes:\n  key: 42\n")
    with pytest.raises(RunError, match=r"env_probes\[.key.\]"):
        tools.load_tool(p)


def test_monitor_field_loaded(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\nmonitor:\n  - 'python monitor.py'\n  - 'python metrics.py'\n")
    spec = tools.load_tool(p)
    assert spec.monitor == [
        MonitorEntry(command="python monitor.py", span="per_rep"),
        MonitorEntry(command="python metrics.py", span="per_rep"),
    ]


def test_monitor_dict_form(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text(
        "name: t\ncommand: x\nmonitor:\n"
        "  - command: 'sweep.sh'\n    span: per_sweep\n"
        "  - command: 'group.sh'\n    span: per_group\n"
        "  - command: 'rep.sh'\n    span: per_rep\n"
    )
    spec = tools.load_tool(p)
    assert spec.monitor == [
        MonitorEntry(command="sweep.sh", span="per_sweep"),
        MonitorEntry(command="group.sh", span="per_group"),
        MonitorEntry(command="rep.sh", span="per_rep"),
    ]


def test_monitor_invalid_span(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\nmonitor:\n  - command: 'x'\n    span: per_unknown\n")
    with pytest.raises(RunError, match=r"span must be one of"):
        tools.load_tool(p)


def test_monitor_dict_missing_command(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\nmonitor:\n  - span: per_sweep\n")
    with pytest.raises(RunError, match=r"monitor\[0\].command must be a non-empty string"):
        tools.load_tool(p)


def test_monitor_default_empty(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\n")
    spec = tools.load_tool(p)
    assert spec.monitor == []


def test_monitor_must_be_list(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\nmonitor: 'python monitor.py'\n")
    with pytest.raises(RunError, match="tool.monitor must be a list"):
        tools.load_tool(p)


def test_monitor_steps_must_be_strings(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\nmonitor:\n  - 42\n")
    with pytest.raises(RunError, match=r"tool.monitor\[0\] must be a string"):
        tools.load_tool(p)


# --- root, parser, capture, combo_probes, per_sweep_var, setup_timeout edge paths ---


def test_tool_root_must_be_mapping(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("- a\n- b\n")
    with pytest.raises(RunError, match="root must be a mapping"):
        tools.load_tool(p)


def test_tool_name_required(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("command: x\n")
    with pytest.raises(RunError, match="tool.name"):
        tools.load_tool(p)


def test_tool_name_must_be_string(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: 7\ncommand: x\n")
    with pytest.raises(RunError, match="tool.name"):
        tools.load_tool(p)


def test_tool_command_must_be_string(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: 7\n")
    with pytest.raises(RunError, match="tool.command"):
        tools.load_tool(p)


def test_parser_must_be_string(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\nparser: [a, b]\n")
    with pytest.raises(RunError, match="tool.parser"):
        tools.load_tool(p)


def test_capture_must_be_mapping(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\ncapture: [a, b]\n")
    with pytest.raises(RunError, match="tool.capture"):
        tools.load_tool(p)


def test_combo_probes_loaded(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\ncombo_probes:\n  shape: 'redis-cli config get maxmemory'\n")
    spec = tools.load_tool(p)
    assert spec.combo_probes == {"shape": "redis-cli config get maxmemory"}


def test_combo_probes_default_empty(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\n")
    spec = tools.load_tool(p)
    assert spec.combo_probes == {}


def test_combo_probes_must_be_mapping(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\ncombo_probes: [a, b]\n")
    with pytest.raises(RunError, match="combo_probes must be a mapping"):
        tools.load_tool(p)


def test_combo_probes_value_must_be_non_empty_string(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\ncombo_probes:\n  key: 42\n")
    with pytest.raises(RunError, match=r"combo_probes\[.key.\]"):
        tools.load_tool(p)


def test_combo_probes_empty_key_rejected(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\ncombo_probes:\n  '': 'echo'\n")
    with pytest.raises(RunError, match="combo_probes keys"):
        tools.load_tool(p)


def test_per_sweep_var_must_be_non_empty_string(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\nper_sweep_var: ''\n")
    with pytest.raises(RunError, match="per_sweep_var"):
        tools.load_tool(p)


def test_per_sweep_var_must_not_be_int(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\nper_sweep_var: 42\n")
    with pytest.raises(RunError, match="per_sweep_var"):
        tools.load_tool(p)


def test_setup_timeout_rejects_bool(tmp_path):
    # bool is an int subclass; the guard must reject it explicitly.
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\nsetup_timeout_seconds: true\n")
    with pytest.raises(RunError, match="setup_timeout_seconds"):
        tools.load_tool(p)


def test_setup_timeout_rejects_negative(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\nsetup_timeout_seconds: -5\n")
    with pytest.raises(RunError, match="setup_timeout_seconds"):
        tools.load_tool(p)


def test_resolve_tool_version_none_when_no_command(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\n")
    spec = tools.load_tool(p)
    assert tools.resolve_tool_version(spec) is None


def test_resolve_tool_version_command_not_found(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: t\ncommand: x\nversion_command: 'definitely-not-a-real-binary-xyz --version'\n")
    spec = tools.load_tool(p)
    v = tools.resolve_tool_version(spec)
    assert v is not None and "FileNotFoundError" in v
