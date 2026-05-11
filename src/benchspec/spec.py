"""Load a spec YAML and overlay an optional profile onto the base spec."""
from pathlib import Path
from typing import Any

import yaml

from benchspec.model import ExcludeRule, Spec, SpecError, WhenRule


_SECTIONS = {"static", "variables", "when", "exclude", "tags", "profiles", "default_profile"}


def load(path: str | Path, profile: str | None = None) -> Spec:
    """Parse ``path`` and return a Spec. If ``profile`` (or default_profile) is
    set, the named profile's partial spec is merged on top of the base."""
    raw = yaml.safe_load(Path(path).read_text())
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise SpecError(f"spec root must be a mapping, got {type(raw).__name__}")

    unknown = set(raw) - _SECTIONS
    if unknown:
        raise SpecError(f"unknown top-level keys: {sorted(unknown)}")

    profiles = raw.get("profiles") or {}
    if not isinstance(profiles, dict):
        raise SpecError("`profiles` must be a mapping of name -> partial spec")

    chosen = profile if profile is not None else raw.get("default_profile")
    if chosen is not None and chosen not in profiles:
        raise SpecError(f"unknown profile: {chosen!r} (available: {sorted(profiles)})")

    base = _extract_partial(raw, "<base>")
    if chosen is not None:
        overlay = _extract_partial(profiles[chosen], f"profiles.{chosen}")
        merged = _merge(base, overlay)
        merged.profile_name = chosen
        return merged
    return base


def _extract_partial(node: dict[str, Any], source: str) -> Spec:
    if not isinstance(node, dict):
        raise SpecError(f"{source}: must be a mapping")

    static = node.get("static") or {}
    variables = node.get("variables") or {}
    when_raw = node.get("when") or []
    exclude_raw = node.get("exclude") or []
    tags = node.get("tags") or []

    if not isinstance(static, dict):
        raise SpecError(f"{source}.static: must be a mapping")
    if not isinstance(variables, dict):
        raise SpecError(f"{source}.variables: must be a mapping")
    if not isinstance(when_raw, list):
        raise SpecError(f"{source}.when: must be a list")
    if not isinstance(exclude_raw, list):
        raise SpecError(f"{source}.exclude: must be a list")
    if not isinstance(tags, list):
        raise SpecError(f"{source}.tags: must be a list")

    for k, v in variables.items():
        if not isinstance(v, list):
            raise SpecError(f"{source}.variables.{k}: must be a list")

    when = []
    for i, item in enumerate(when_raw):
        if not isinstance(item, dict):
            raise SpecError(f"{source}.when[{i}]: must be a mapping")
        if_ = item.get("if") or {}
        set_ = item.get("set") or {}
        tag = item.get("tag") or []
        if not isinstance(if_, dict):
            raise SpecError(f"{source}.when[{i}].if: must be a mapping")
        if not isinstance(set_, dict):
            raise SpecError(f"{source}.when[{i}].set: must be a mapping")
        if isinstance(tag, str):
            tag = [tag]
        if not isinstance(tag, list):
            raise SpecError(f"{source}.when[{i}].tag: must be a string or list")
        when.append(WhenRule(if_=dict(if_), set_=dict(set_), tag=tuple(tag), source=f"{source}.when[{i}]"))

    exclude = []
    for i, item in enumerate(exclude_raw):
        if not isinstance(item, dict):
            raise SpecError(f"{source}.exclude[{i}]: must be a mapping")
        exclude.append(ExcludeRule(match=dict(item), source=f"{source}.exclude[{i}]"))

    return Spec(
        static=dict(static),
        variables={k: list(v) for k, v in variables.items()},
        when=when,
        exclude=exclude,
        tags=list(tags),
    )


def _merge(base: Spec, overlay: Spec) -> Spec:
    static = {**base.static, **overlay.static}
    variables = {**base.variables, **overlay.variables}
    when = base.when + overlay.when
    exclude = base.exclude + overlay.exclude
    tags = list(dict.fromkeys(base.tags + overlay.tags))
    return Spec(static=static, variables=variables, when=when, exclude=exclude, tags=tags)
