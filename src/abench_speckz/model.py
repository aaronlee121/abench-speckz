"""Shared dataclasses and exceptions used by spec parsing and combo generation."""

from dataclasses import dataclass, field
from typing import Any


class SpecError(Exception):
    """Raised for invalid spec YAML or pipeline-time configuration errors."""


class RunError(Exception):
    """Raised for invalid tool YAML, missing manifests, or runtime failures."""


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
