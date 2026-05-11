"""Public re-exports of the dataclasses and exceptions used across the package."""
from benchspec.model import Combo, ExcludeRule, RunError, Spec, SpecError, WhenRule
from benchspec.runspec import AggregateRow, RunRequest, RunResult, ToolSpec

__version__ = "0.1.0"

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
