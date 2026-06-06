from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Confidence(StrEnum):
    OFFICIAL = "official"
    LOCAL_CLI = "local_cli"
    LOCAL_CONFIG = "local_config"
    PROVIDER_WARNING = "provider_warning"
    OBSERVED = "observed"
    USER_CONFIGURED = "user_configured"
    UNKNOWN = "unknown"


LEGACY_CONFIDENCE_MAP = {
    "high": Confidence.OBSERVED,
    "medium": Confidence.OBSERVED,
    "low": Confidence.UNKNOWN,
}


PLACEHOLDER_TASK_PATTERNS = [
    re.compile(r"@filename\b", re.I),
    re.compile(r"<\s*filename\s*>", re.I),
    re.compile(r"\{\s*filename\s*\}", re.I),
    re.compile(r"<\s*task\s*>", re.I),
    re.compile(r"\b(your task here|replace this|todo task)\b", re.I),
]


def normalize_confidence(value: Any) -> Any:
    if isinstance(value, str):
        return LEGACY_CONFIDENCE_MAP.get(value.lower(), value)
    return value


class RuntimeKind(StrEnum):
    TMUX = "tmux"
    TERMINAL_CONTROL = "terminal-control"
    PTY = "pty"
    ACP = "acp"


class Capability(StrEnum):
    LIVE_STEERING = "live_steering"
    READ_ONLY_ADVISORY = "read_only_advisory"
    WORKTREE_EDITS = "worktree_edits"
    CAN_RUN_TESTS = "can_run_tests"
    SUPPORTS_MODEL_ARG = "supports_model_arg"
    SUPPORTS_RESUME = "supports_resume"
    SUPPORTS_MCP_INJECTION = "supports_mcp_injection"
    SUPPORTS_ACP = "supports_acp"
    SUPPORTS_USAGE_PROBE = "supports_usage_probe"
    SUPPORTS_APPROVAL_DETECTION = "supports_approval_detection"
    SUPPORTS_ONE_SHOT_MODE = "supports_one_shot_mode"
    SUPPORTS_INTERACTIVE_MODE = "supports_interactive_mode"


class UsageStatus(StrEnum):
    AVAILABLE = "available"
    NEAR_LIMIT = "near_limit"
    LIMIT_REACHED = "limit_reached"
    OVERAGE_POSSIBLE = "overage_possible"
    UNAUTHENTICATED = "unauthenticated"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class UsageWindowKind(StrEnum):
    DAILY = "daily"
    FIVE_HOUR = "5h"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    SESSION = "session"
    MODEL = "model"
    CREDITS = "credits"
    ON_DEMAND = "on_demand"
    UNKNOWN = "unknown"


class SessionState(StrEnum):
    STARTING = "STARTING"
    READY = "READY"
    RUNNING = "RUNNING"
    AWAITING_USER_INPUT = "AWAITING_USER_INPUT"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    IDLE = "IDLE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class ObserveEvent(StrEnum):
    NONE = "none"
    QUESTION = "question"
    APPROVAL_PROMPT = "approval_prompt"
    IDLE = "idle"
    COMPLETED = "completed"
    ERROR = "error"
    LIMIT_WARNING = "limit_warning"
    OVERAGE_PROMPT = "overage_prompt"
    SCREEN_CHANGED = "screen_changed"
    TIMEOUT = "timeout"


class AuthStatus(BaseModel):
    status: Literal["authenticated", "unauthenticated", "unknown", "unavailable"]
    confidence: Confidence
    reason: str | None = None
    checked_at: datetime = Field(default_factory=now_utc)

    _normalize_confidence = field_validator("confidence", mode="before")(normalize_confidence)


class ModelDescriptor(BaseModel):
    id: str
    display_name: str | None = None
    source: Literal["cli_detected", "config", "default", "observed", "unknown"]
    confidence: Confidence
    aliases: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _normalize_confidence = field_validator("confidence", mode="before")(normalize_confidence)


class UsageWindow(BaseModel):
    name: str
    kind: UsageWindowKind = UsageWindowKind.UNKNOWN
    status: str | None = None
    remaining_percent: float | None = None
    used_percent: float | None = None
    remaining_units: float | None = None
    used_units: float | None = None
    reset_at: datetime | None = None
    confidence: Confidence
    raw_text: str | None = None

    _normalize_confidence = field_validator("confidence", mode="before")(normalize_confidence)


class CapacitySnapshot(BaseModel):
    provider_id: str
    status: UsageStatus
    confidence: Confidence
    checked_at: datetime = Field(default_factory=now_utc)
    windows: list[UsageWindow] = Field(default_factory=list)
    premium_requests_remaining: int | None = None
    credits_remaining: float | None = None
    reset_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    _normalize_confidence = field_validator("confidence", mode="before")(normalize_confidence)


class AccountDescriptor(BaseModel):
    id: str
    display_name: str | None = None
    confidence: Confidence = Confidence.UNKNOWN
    metadata: dict[str, Any] = Field(default_factory=dict)

    _normalize_confidence = field_validator("confidence", mode="before")(normalize_confidence)


class ProviderDescriptor(BaseModel):
    id: str
    display_name: str
    vendor: str | None = None
    harness: str
    installed: bool
    binary_path: str | None = None
    version: str | None = None
    auth: AuthStatus
    models: list[ModelDescriptor] = Field(default_factory=list)
    runtimes: list[RuntimeKind] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)
    usage: CapacitySnapshot | None = None
    accounts: list[AccountDescriptor] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentTarget(BaseModel):
    provider_id: str
    model: str | None = None
    harness: str | None = None
    runtime: RuntimeKind = RuntimeKind.TMUX
    account: str | None = None


class SpawnWorkerRequest(BaseModel):
    provider_id: str
    task: str
    repo_path: str
    role: Literal["explorer", "reviewer", "implementer", "tester", "custom"] = "explorer"
    model: str | None = None
    account: str | None = None
    runtime: RuntimeKind | None = None
    isolation: Literal["read_only", "worktree", "shared"] = "read_only"
    allowed_files: list[str] = Field(default_factory=list)
    max_runtime_seconds: int | None = None
    max_turns: int | None = None
    supervision: Literal["interactive", "autonomous", "human_visible"] = "interactive"
    initial_prompt_mode: Literal["provider_default", "send_after_launch", "arg", "stdin"] = "provider_default"
    reasoning_effort: str | None = None
    service_tier: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("task")
    @classmethod
    def task_must_be_concrete(cls, value: str) -> str:
        task = value.strip()
        if not task:
            raise ValueError("task must be a concrete non-empty instruction")
        for pattern in PLACEHOLDER_TASK_PATTERNS:
            if pattern.search(task):
                raise ValueError("task contains placeholder text; pass the actual delegated task")
        return task

    @field_validator("runtime", mode="before")
    @classmethod
    def normalize_runtime(cls, value: Any) -> Any:
        if value in {None, ""}:
            return None
        if isinstance(value, str):
            normalized = value.strip().lower().replace("_", "-")
            if normalized in {"termctrl", "terminal"}:
                return RuntimeKind.TERMINAL_CONTROL
            return normalized
        return value


class TmuxSessionRef(BaseModel):
    session_name: str
    window: str = "0"
    pane: str = "0"

    @property
    def target(self) -> str:
        return f"{self.session_name}:{self.window}.{self.pane}"


class TerminalControlSessionRef(BaseModel):
    session_name: str

    @property
    def target(self) -> str:
        return self.session_name


class AgentSession(BaseModel):
    id: str
    provider_id: str
    model: str | None = None
    harness: str
    account: str | None = None
    role: str
    task: str
    repo_path: str
    worktree_path: str | None = None
    runtime: RuntimeKind
    state: SessionState
    created_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None
    tmux: TmuxSessionRef | None = None
    artifact_dir: str
    transcript_path: str
    events_path: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObserveWorkerResponse(BaseModel):
    session_id: str
    state: SessionState
    event: ObserveEvent
    screen_excerpt: str | None = None
    recent_log: str | None = None
    parsed_question: str | None = None
    suggested_next_action: str | None = None
    confidence: Confidence
    observed_at: datetime = Field(default_factory=now_utc)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _normalize_confidence = field_validator("confidence", mode="before")(normalize_confidence)


class ArtifactRecord(BaseModel):
    kind: str
    path: str
    sha256: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FileLease(BaseModel):
    id: int | None = None
    session_id: str
    repo_path: str
    file_path: str
    mode: Literal["read", "write"] = "write"
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=now_utc)
    released_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentPoolError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ToolError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.error = AgentPoolError(code=code, message=message, details=details or {})


class JsonModel(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
