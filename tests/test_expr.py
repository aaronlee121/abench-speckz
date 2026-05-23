"""Tests for the safe arithmetic expression evaluator."""

import pytest

from abench_speckz.expr import eval_expr, extract_names, is_expr, validate_expr_syntax
from abench_speckz.model import RunError

# --- is_expr -----------------------------------------------------------------


def test_is_expr_plain_name():
    assert not is_expr("concurrency")


def test_is_expr_arithmetic():
    assert is_expr("workers * connections")


def test_is_expr_with_constant():
    assert is_expr("latency_ms / 1000")


def test_is_expr_with_parens():
    assert is_expr("(a + b) / 2")


def test_is_expr_invalid_syntax():
    # ast.parse fails → is_expr returns False; validation happens separately in parse_plots
    assert not is_expr("workers *")


# --- extract_names -----------------------------------------------------------


def test_extract_names_single():
    assert extract_names("rps") == {"rps"}


def test_extract_names_multiple():
    assert extract_names("workers * connections_per_worker") == {"workers", "connections_per_worker"}


def test_extract_names_with_constant():
    assert extract_names("latency_us / 1000") == {"latency_us"}


def test_extract_names_complex():
    assert extract_names("(a + b) / c") == {"a", "b", "c"}


def test_extract_names_no_names():
    assert extract_names("42 * 2") == set()


# --- eval_expr ---------------------------------------------------------------


def test_eval_add():
    assert eval_expr("a + b", {"a": 3.0, "b": 4.0}) == 7.0


def test_eval_subtract():
    assert eval_expr("a - b", {"a": 10.0, "b": 3.0}) == 7.0


def test_eval_multiply():
    assert eval_expr("workers * connections", {"workers": 4.0, "connections": 8.0}) == 32.0


def test_eval_divide():
    assert eval_expr("latency_us / 1000", {"latency_us": 5000.0}) == 5.0


def test_eval_floor_divide():
    assert eval_expr("a // b", {"a": 7.0, "b": 2.0}) == 3.0


def test_eval_modulo():
    assert eval_expr("a % b", {"a": 7.0, "b": 3.0}) == 1.0


def test_eval_power():
    assert eval_expr("a ** 2", {"a": 4.0}) == 16.0


def test_eval_unary_neg():
    assert eval_expr("-x", {"x": 5.0}) == -5.0


def test_eval_unary_pos():
    assert eval_expr("+x", {"x": 5.0}) == 5.0


def test_eval_complex_expr():
    result = eval_expr("(a + b) * c / 2", {"a": 1.0, "b": 3.0, "c": 4.0})
    assert result == 8.0


def test_eval_constant_only():
    assert eval_expr("1000 * 2", {}) == 2000.0


def test_eval_unknown_name_raises():
    with pytest.raises(ValueError, match="unknown name"):
        eval_expr("a + b", {"a": 1.0})


def test_eval_division_by_zero_raises():
    with pytest.raises(ZeroDivisionError):
        eval_expr("a / b", {"a": 1.0, "b": 0.0})


def test_eval_unsupported_node_raises():
    # Function calls are not supported
    with pytest.raises(ValueError, match="unsupported"):
        eval_expr("abs(x)", {"x": -1.0})


def test_eval_string_literal_raises():
    with pytest.raises(ValueError, match="unsupported literal"):
        eval_expr("'hello'", {})


# --- validate_expr_syntax ----------------------------------------------------


def test_validate_valid_expr():
    validate_expr_syntax("a * b + c", "test")  # should not raise


def test_validate_invalid_syntax_raises():
    with pytest.raises(RunError, match="invalid expression syntax"):
        validate_expr_syntax("a *", "test")


def test_validate_unsupported_node_raises():
    with pytest.raises(RunError, match="unsupported"):
        validate_expr_syntax("abs(x)", "test")


# --- integration: plots.parse_plots accepts expressions ----------------------


def test_parse_plots_x_expression():
    from abench_speckz import plots

    raw = [{"id": "p1", "type": "bar", "x": "workers * connections", "y": "rps"}]
    specs = plots.parse_plots(raw, source="<t>")
    assert specs[0].x == "workers * connections"


def test_parse_plots_y_expression():
    from abench_speckz import plots

    raw = [{"id": "p1", "type": "bar", "x": "concurrency", "y": "latency_us / 1000"}]
    specs = plots.parse_plots(raw, source="<t>")
    assert specs[0].y == ["latency_us / 1000"]


def test_parse_plots_y_list_with_expression():
    from abench_speckz import plots

    raw = [{"id": "p1", "type": "stacked-bar", "x": "w", "y": ["rps", "latency_ms / 1000"]}]
    specs = plots.parse_plots(raw, source="<t>")
    assert specs[0].y == ["rps", "latency_ms / 1000"]


def test_parse_plots_invalid_x_expression_rejected():
    from abench_speckz import plots
    from abench_speckz.model import RunError

    raw = [{"id": "p1", "type": "bar", "x": "workers *", "y": "rps"}]
    with pytest.raises(RunError, match="invalid expression syntax"):
        plots.parse_plots(raw, source="<t>")


def test_parse_plots_invalid_y_expression_rejected():
    from abench_speckz import plots
    from abench_speckz.model import RunError

    raw = [{"id": "p1", "type": "bar", "x": "concurrency", "y": "abs(rps)"}]
    with pytest.raises(RunError, match="unsupported"):
        plots.parse_plots(raw, source="<t>")


# --- integration: _plots helpers ---------------------------------------------


def test_resolve_y_axes_plain():
    from abench_speckz.report._plots import _resolve_y_axes

    metrics, expr_map = _resolve_y_axes(["rps", "p95_ms"], set())
    assert metrics == ["rps", "p95_ms"]
    assert expr_map == {}


def test_resolve_y_axes_expression():
    from abench_speckz.report._plots import _resolve_y_axes

    metrics, expr_map = _resolve_y_axes(["latency_us / 1000"], set())
    assert metrics == ["latency_us"]
    assert expr_map == {"latency_us": "latency_us / 1000"}


def test_resolve_y_axes_expression_with_var():
    from abench_speckz.report._plots import _resolve_y_axes

    metrics, expr_map = _resolve_y_axes(["rps / concurrency"], {"concurrency"})
    assert metrics == ["rps"]
    assert expr_map == {"rps": "rps / concurrency"}


def test_resolve_y_axes_ambiguous_multiple_metrics_raises():
    from abench_speckz.model import RunError
    from abench_speckz.report._plots import _resolve_y_axes

    with pytest.raises(RunError, match="exactly one metric"):
        _resolve_y_axes(["metric1 + metric2"], set())


def test_apply_y_exprs_scale():
    from abench_speckz.report._plots import _apply_y_exprs

    rows = [
        {"metric": "latency_us", "mean": 5000.0, "p95": 8000.0, "stddev": 100.0},
    ]
    result = _apply_y_exprs(rows, {"latency_us": "latency_us / 1000"}, set())
    assert result[0]["metric"] == "latency_us / 1000"
    assert result[0]["mean"] == pytest.approx(5.0)
    assert result[0]["p95"] == pytest.approx(8.0)
    assert result[0]["stddev"] == pytest.approx(0.1)


def test_apply_y_exprs_with_var():
    from abench_speckz.report._plots import _apply_y_exprs

    rows = [
        {"metric": "rps", "concurrency": "4", "mean": 1200.0, "p95": 1400.0},
    ]
    result = _apply_y_exprs(rows, {"rps": "rps / concurrency"}, {"concurrency"})
    assert result[0]["metric"] == "rps / concurrency"
    assert result[0]["mean"] == pytest.approx(300.0)
    assert result[0]["p95"] == pytest.approx(350.0)


def test_apply_y_exprs_skips_non_expression_metric():
    from abench_speckz.report._plots import _apply_y_exprs

    rows = [
        {"metric": "rps", "mean": 100.0},
        {"metric": "latency_us", "mean": 5000.0},
    ]
    result = _apply_y_exprs(rows, {"latency_us": "latency_us / 1000"}, set())
    rps_row = next(r for r in result if r["metric"] == "rps")
    assert rps_row["mean"] == 100.0


def test_apply_y_exprs_division_by_zero_yields_none():
    from abench_speckz.report._plots import _apply_y_exprs

    rows = [
        {"metric": "rps", "concurrency": "0", "mean": 1200.0},
    ]
    result = _apply_y_exprs(rows, {"rps": "rps / concurrency"}, {"concurrency"})
    assert result[0]["mean"] is None


# --- integration: _make_x_getter in _charts ----------------------------------


def test_make_x_getter_plain():
    from abench_speckz.report._charts import _make_x_getter

    getter = _make_x_getter("concurrency")
    assert getter({"concurrency": "4"}) == "4"
    assert getter({}) == ""


def test_make_x_getter_expression():
    from abench_speckz.report._charts import _make_x_getter

    getter = _make_x_getter("workers * connections")
    assert getter({"workers": "4", "connections": "8"}) == "32"


def test_make_x_getter_expression_float():
    from abench_speckz.report._charts import _make_x_getter

    getter = _make_x_getter("a / b")
    assert getter({"a": "10", "b": "4"}) == "2.5"


def test_make_x_getter_expression_integer_result():
    from abench_speckz.report._charts import _make_x_getter

    getter = _make_x_getter("a / b")
    assert getter({"a": "10", "b": "2"}) == "5"


def test_make_x_getter_expression_div_zero_returns_empty():
    from abench_speckz.report._charts import _make_x_getter

    getter = _make_x_getter("a / b")
    assert getter({"a": "1", "b": "0"}) == ""
