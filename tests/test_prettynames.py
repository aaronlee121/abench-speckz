from abench_speckz import prettynames
from abench_speckz.runspec import ToolSpec


def test_from_tool():
    t = ToolSpec(name="oha", command="x", pretty_names={"rps": "Requests/sec"})
    assert prettynames.from_tool(t) == {"oha": {"rps": "Requests/sec"}}


def test_merge_namespaced():
    a = {"oha": {"rps": "Requests/sec"}}
    b = {"oha": {"p95": "P95 (ms)"}, "vars": {"workload": "Workload"}}
    merged = prettynames.merge(a, b)
    assert merged == {
        "oha": {"rps": "Requests/sec", "p95": "P95 (ms)"},
        "vars": {"workload": "Workload"},
    }


def test_round_trip(tmp_path):
    p = tmp_path / "pn.json"
    prettynames.write(p, {"oha": {"rps": "Requests/sec"}})
    loaded = prettynames.load(p)
    assert loaded == {"oha": {"rps": "Requests/sec"}}


def test_resolve_fallthrough():
    m = {"oha": {"rps": "Requests/sec"}, "vars": {"workload": "Workload"}}
    assert prettynames.resolve(m, "rps", tool="oha") == "Requests/sec"
    assert prettynames.resolve(m, "workload") == "Workload"
    assert prettynames.resolve(m, "unknown") == "unknown"


def test_resolve_finds_metric_in_any_tool_when_tool_unspecified():
    m = {
        "oha": {"rps": "Requests/sec"},
        "vegeta": {"p95": "P95 (ms)"},
        "vars": {"workload": "Workload"},
    }
    assert prettynames.resolve(m, "rps") == "Requests/sec"
    assert prettynames.resolve(m, "p95") == "P95 (ms)"
    assert prettynames.resolve(m, "workload") == "Workload"
    assert prettynames.resolve(m, "unknown") == "unknown"


def test_resolve_with_tool_falls_through_to_vars():
    # Tool is specified but key is not under that tool — falls through to vars.
    m = {"oha": {"rps": "Requests/sec"}, "vars": {"workload": "Workload"}}
    assert prettynames.resolve(m, "workload", tool="oha") == "Workload"


def test_merge_overrides_scalar_with_non_dict():
    # If incoming has a non-dict at a namespace, it should override (not merge).
    a = {"key": "old"}
    b = {"key": "new"}
    assert prettynames.merge(a, b) == {"key": "new"}


def test_load_missing_returns_empty():
    from pathlib import Path
    assert prettynames.load(Path("/nonexistent/path/pn.json")) == {}
