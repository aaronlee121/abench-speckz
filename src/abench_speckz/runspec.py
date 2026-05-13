"""Dataclasses for tool YAML, per-run I/O, and per-combo aggregate rows."""
from dataclasses import dataclass, field


@dataclass
class ToolSpec:
    name: str
    command: str
    timeout_seconds: int = 600
    version_command: str | None = None
    capture: dict[str, str] = field(default_factory=dict)
    parser: str | None = None
    output_file: str | None = None
    output_format: str = "json"
    pretty_names: dict[str, str] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    higher_is_better: dict[str, bool] = field(default_factory=dict)
    plots: list[dict] = field(default_factory=list)
    env_probes: dict[str, str] = field(default_factory=dict)
    setup: list[str] = field(default_factory=list)
    teardown: list[str] = field(default_factory=list)
    post_run: list[str] = field(default_factory=list)
    setup_timeout_seconds: int = 120
    setup_per_sweep: list[str] = field(default_factory=list)
    teardown_per_sweep: list[str] = field(default_factory=list)
    per_sweep_var: str | None = None


@dataclass
class RunRequest:
    config_hash: str
    vars: dict[str, str]
    tags: list[str]
    tool: str
    tool_version: str | None
    command: str
    env: dict[str, str]
    timeout_seconds: int


@dataclass
class RunResult:
    exit_code: int
    duration_ms: int
    started_at: str
    finished_at: str
    stdout: str
    stderr: str
    failure_reason: str | None = None


@dataclass
class AggregateRow:
    config_hash: str
    metric: str
    n: int
    n_failed: int
    mean: float
    stddev: float
    p50: float
    p95: float
    p99: float
    ci95_low: float | None
    ci95_high: float | None
    vars: dict[str, str] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
