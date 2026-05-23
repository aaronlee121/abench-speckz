"""Safe arithmetic expression evaluator for plot axis expressions."""

import ast
import operator as _op
from collections.abc import Callable

_SAFE_BIN_OPS: dict[type, Callable[..., float]] = {
    ast.Add: _op.add,
    ast.Sub: _op.sub,
    ast.Mult: _op.mul,
    ast.Div: _op.truediv,
    ast.Pow: _op.pow,
    ast.FloorDiv: _op.floordiv,
    ast.Mod: _op.mod,
}
_SAFE_UNARY_OPS: dict[type, Callable[..., float]] = {
    ast.USub: _op.neg,
    ast.UAdd: _op.pos,
}


def is_expr(s: str) -> bool:
    """Return True if s is an arithmetic expression rather than a plain identifier."""
    try:
        tree = ast.parse(s.strip(), mode="eval")
    except SyntaxError:
        return False
    return not isinstance(tree.body, ast.Name)


def extract_names(expr: str) -> set[str]:
    """Return all identifier names referenced in the expression."""
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError:
        return set()
    return {node.id for node in ast.walk(tree.body) if isinstance(node, ast.Name)}


def eval_expr(expr: str, namespace: dict[str, float]) -> float:
    """Safely evaluate an arithmetic expression over a namespace of float values.

    Supports numeric literals, named variables, and the operators +, -, *, /,
    **, //, %.  Raises ValueError for unknown names or unsupported constructs.
    """
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise ValueError(f"invalid expression {expr!r}: {e}") from e
    return _eval(tree.body, namespace, expr)


def validate_expr_syntax(expr: str, source: str) -> None:
    """Raise RunError if expr is not valid arithmetic syntax.

    Uses a dummy namespace (all names = 1.0) so every operator path is
    exercised at parse time.
    """
    from abench_speckz.model import RunError

    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise RunError(f"{source}: invalid expression syntax {expr!r}: {e}") from e
    names = extract_names(expr)
    try:
        _eval(tree.body, {n: 1.0 for n in names}, expr)
    except ValueError as e:
        raise RunError(f"{source}: {e}") from e


def _eval(node: ast.expr, ns: dict[str, float], src: str) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        raise ValueError(f"unsupported literal {node.value!r} in {src!r}")
    if isinstance(node, ast.Name):
        if node.id not in ns:
            raise ValueError(f"unknown name {node.id!r} in {src!r}")
        v = ns[node.id]
        if v is None:
            raise ValueError(f"{node.id!r} is None in {src!r}")
        return float(v)
    if isinstance(node, ast.BinOp):
        fn = _SAFE_BIN_OPS.get(type(node.op))
        if fn is None:
            raise ValueError(f"unsupported operator {type(node.op).__name__} in {src!r}")
        return fn(_eval(node.left, ns, src), _eval(node.right, ns, src))
    if isinstance(node, ast.UnaryOp):
        fn = _SAFE_UNARY_OPS.get(type(node.op))
        if fn is None:
            raise ValueError(f"unsupported operator {type(node.op).__name__} in {src!r}")
        return fn(_eval(node.operand, ns, src))
    raise ValueError(f"unsupported expression node {type(node).__name__} in {src!r}")
