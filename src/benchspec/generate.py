from itertools import product
from typing import Any, Iterable

from benchspec import interpolate
from benchspec.model import Combo, ExcludeRule, Spec, SpecError, WhenRule


def expand(spec: Spec) -> list[Combo]:
    axes = list(spec.variables.keys())
    if not axes:
        vars_ = dict(spec.static)
        return [Combo(vars=vars_, varying_keys=())]

    value_lists = [spec.variables[k] for k in axes]
    combos: list[Combo] = []
    for assignment in product(*value_lists):
        vars_ = dict(spec.static)
        for k, v in zip(axes, assignment):
            vars_[k] = v
        combos.append(Combo(vars=vars_, varying_keys=tuple(axes)))
    return combos


def apply_excludes(combos: list[Combo], rules: list[ExcludeRule]) -> list[Combo]:
    if not rules:
        return combos
    return [c for c in combos if not any(_matches(c.vars, r.match) for r in rules)]


def apply_when(combos: list[Combo], rules: list[WhenRule]) -> list[Combo]:
    if not rules:
        return combos

    out: list[Combo] = []
    for combo in combos:
        pins: dict[str, tuple[Any, str]] = {}
        new_tags: set[str] = set(combo.tags)
        for rule in rules:
            if not _matches(combo.vars, rule.if_):
                continue
            for k, v in rule.set_.items():
                if k in pins and pins[k][0] != v:
                    raise SpecError(
                        f"conflicting `when` rules pin {k!r}: "
                        f"{pins[k][1]} -> {pins[k][0]!r}, {rule.source} -> {v!r}"
                    )
                pins[k] = (v, rule.source)
            new_tags.update(rule.tag)
        new_vars = dict(combo.vars)
        for k, (v, _) in pins.items():
            new_vars[k] = v
        out.append(Combo(vars=new_vars, tags=new_tags, varying_keys=combo.varying_keys))
    return out


def dedupe(combos: list[Combo]) -> list[Combo]:
    seen: dict[tuple, Combo] = {}
    for combo in combos:
        key = tuple(sorted((k, _hashable(v)) for k, v in combo.vars.items()))
        existing = seen.get(key)
        if existing is None:
            seen[key] = combo
        else:
            existing.tags.update(combo.tags)
    return list(seen.values())


def interpolate_combos(combos: list[Combo]) -> list[Combo]:
    out = []
    for combo in combos:
        resolved = interpolate.resolve(combo.vars)
        out.append(Combo(vars=dict(resolved), tags=combo.tags, varying_keys=combo.varying_keys))
    return out


def assign_universal_tags(combos: list[Combo], universal: Iterable[str]) -> list[Combo]:
    universal_set = set(universal)
    if not universal_set:
        return combos
    for combo in combos:
        combo.tags.update(universal_set)
    return combos


def filter_tags(
    combos: list[Combo],
    require: Iterable[str] = (),
    exclude: Iterable[str] = (),
) -> list[Combo]:
    require_set = set(require)
    exclude_set = set(exclude)
    out = []
    for combo in combos:
        if require_set and not require_set.issubset(combo.tags):
            continue
        if exclude_set and (exclude_set & combo.tags):
            continue
        out.append(combo)
    return out


def run_pipeline(
    spec: Spec,
    require_tags: Iterable[str] = (),
    exclude_tags: Iterable[str] = (),
) -> list[Combo]:
    combos = expand(spec)
    combos = apply_excludes(combos, spec.exclude)
    combos = apply_when(combos, spec.when)
    combos = dedupe(combos)
    combos = interpolate_combos(combos)
    combos = assign_universal_tags(combos, spec.tags)
    combos = filter_tags(combos, require_tags, exclude_tags)
    return combos


def _matches(vars_: dict[str, Any], partial: dict[str, Any]) -> bool:
    return all(k in vars_ and vars_[k] == v for k, v in partial.items())


def _hashable(v: Any) -> Any:
    if isinstance(v, list):
        return ("__list__", tuple(_hashable(x) for x in v))
    if isinstance(v, dict):
        return ("__dict__", tuple(sorted((k, _hashable(x)) for k, x in v.items())))
    return v
