"""Public re-exports of the dataclasses and exceptions used across the package."""

from abench_speckz.model import Combo, ExcludeRule, RunError, Spec, SpecError, WhenRule
from abench_speckz.runspec import AggregateRow, RunRequest, RunResult, ToolSpec

__version__ = "0.12.2"

__all__ = [
    "AggregateRow",
    "Combo",
    "ExcludeRule",
    "RunError",
    "RunRequest",
    "RunResult",
    "Spec",
    "SpecError",
    "ToolSpec",
    "WhenRule",
    "__version__",
]
