"""Post-load spec checks: empty axes, mixed-type axes (warn), env-var key shape."""
import re
import warnings

from abench_speckz.model import Combo, Spec, SpecError

_ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def validate(spec: Spec) -> None:
    known_axes = set(spec.variables)
    known_keys = known_axes | set(spec.static)

    for axis, values in spec.variables.items():
        if not values:
            raise SpecError(f"variables.{axis}: list is empty")
        types = {type(v).__name__ for v in values}
        if len(types) > 1:
            warnings.warn(
                f"variables.{axis} mixes types {sorted(types)} — values will be stringified for env-file output",
                stacklevel=2,
            )

    for rule in spec.when:
        unknown = set(rule.if_) - known_axes
        if unknown:
            raise SpecError(f"{rule.source}.if: unknown axis keys {sorted(unknown)}")
        for k in rule.set_:
            if k in spec.static:
                raise SpecError(
                    f"{rule.source}.set: key {k!r} also appears in `static` — choose one"
                )

    for exc in spec.exclude:
        unknown = set(exc.match) - known_keys
        if unknown:
            raise SpecError(f"{exc.source}: unknown keys {sorted(unknown)}")


def validate_env_keys(combos: list[Combo]) -> None:
    for combo in combos:
        for k in combo.vars:
            if not _ENV_KEY.fullmatch(k):
                raise SpecError(
                    f"key {k!r} is not a valid env-var identifier "
                    f"(must match [A-Za-z_][A-Za-z0-9_]*)"
                )
