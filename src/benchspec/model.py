from dataclasses import dataclass, field
from typing import Any


class SpecError(Exception):
    pass


class RunError(Exception):
    pass


@dataclass(frozen=True)
class WhenRule:
    if_: dict[str, Any]
    set_: dict[str, Any]
    tag: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class ExcludeRule:
    match: dict[str, Any]
    source: str


@dataclass
class Spec:
    static: dict[str, Any]
    variables: dict[str, list[Any]]
    when: list[WhenRule]
    exclude: list[ExcludeRule]
    tags: list[str]
    profile_name: str | None = None


@dataclass
class Combo:
    vars: dict[str, Any]
    tags: set[str] = field(default_factory=set)
    varying_keys: tuple[str, ...] = ()
