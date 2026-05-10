import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from benchspec.model import RunError


_OP_RE = re.compile(r"^([^=!<>]+)\s*(=|!=|>=|<=|>|<)\s*(.*)$")


@dataclass(frozen=True)
class Predicate:
    key: str
    op: str
    value: str

    def __call__(self, vars_: dict[str, Any]) -> bool:
        if self.op != "=":
            raise RunError(f"operator {self.op!r} not yet supported (planned for v1.1)")
        actual = str(vars_.get(self.key, ""))
        return actual == self.value


def parse_kv(expressions: Iterable[str]) -> list[Predicate]:
    out: list[Predicate] = []
    for raw in expressions:
        m = _OP_RE.match(raw.strip())
        if not m:
            raise RunError(f"could not parse filter expression: {raw!r}")
        key, op, value = m.group(1).strip(), m.group(2), m.group(3).strip()
        if not key:
            raise RunError(f"empty key in filter: {raw!r}")
        out.append(Predicate(key=key, op=op, value=value))
    return out


def make_filter(
    where: Iterable[str] = (),
    require_tags: Iterable[str] = (),
    exclude_tags: Iterable[str] = (),
) -> Callable[[dict[str, Any], Iterable[str]], bool]:
    predicates = parse_kv(where)
    require_set = set(require_tags)
    exclude_set = set(exclude_tags)

    def fn(vars_: dict[str, Any], tags: Iterable[str]) -> bool:
        if predicates and not all(p(vars_) for p in predicates):
            return False
        tag_set = set(tags)
        if require_set and not require_set.issubset(tag_set):
            return False
        if exclude_set and (exclude_set & tag_set):
            return False
        return True

    return fn
