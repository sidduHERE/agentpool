from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from agentpool.models import Confidence, UsageStatus
from agentpool.utils import expand_user_path


DEFAULT_CONFIG_PATH = Path("~/.agentpool/config.yaml").expanduser()
DEFAULT_MODEL_CATALOG_PATH = Path(__file__).with_name("provider_model_catalog.json")
FAKE_AGENT_DIR = Path(__file__).with_name("fixtures") / "fake_agents"
DEPRECATED_PROVIDER_IDS = {
    "gemini-cli": (
        "Gemini CLI has transitioned to Antigravity CLI; "
        "AgentPool no longer exposes it as a supported provider."
    ),
}
FAKE_PROVIDER_SCRIPTS = {
    "fake-question": "fake_question_agent.py",
    "fake-approval": "fake_approval_agent.py",
    "fake-completed": "fake_completed_agent.py",
    "fake-idle": "fake_idle_agent.py",
    "fake-limit": "fake_limit_agent.py",
    "fake-patch": "fake_patch_agent.py",
}
CATALOG_METADATA_REFRESH_KEYS = {
    "catalog_completeness",
    "default_initial_prompt_mode",
    "forward_separator",
    "model_arg",
    "model_selection",
    "quirks",
    "read_only_mode_arg",
    "reasoning_effort_arg",
    "reasoning_effort_config_key",
    "service_tier_config_key",
    "submit_keys",
}


class StorageConfig(BaseModel):
    db_path: str = "~/.agentpool/agentpool.sqlite"
    artifact_root: str = "~/.agentpool/artifacts"

    @property
    def db(self) -> Path:
        return expand_user_path(self.db_path)

    @property
    def artifacts(self) -> Path:
        return expand_user_path(self.artifact_root)


class TmuxConfig(BaseModel):
    session_prefix: str = "agentpool"
    capture_lines: int = 300
    idle_seconds: int = 30


class TerminalControlArtifactsConfig(BaseModel):
    save_text: bool = False
    save_json: bool = True
    save_svg_on_failure: bool = True
    save_png_on_failure: bool = False
    record: bool = False


class TerminalControlConfig(BaseModel):
    enabled: bool = False
    binary: str = "termctrl"
    session_prefix: str = "agentpool"
    capture_lines: int = 300
    cols: int = 120
    rows: int = 36
    settle_ms: int = 100
    deadline_ms: int = 5000
    host: str | None = None
    artifacts: TerminalControlArtifactsConfig = Field(default_factory=TerminalControlArtifactsConfig)


class RuntimeConfig(BaseModel):
    default: str = "tmux"
    tmux: TmuxConfig = Field(default_factory=TmuxConfig)
    terminal_control: TerminalControlConfig = Field(default_factory=TerminalControlConfig)


class PolicyConfig(BaseModel):
    require_explicit_provider: bool = True
    allow_auto_routing: bool = False
    max_parallel_sessions: int = 4
    never_allow_overage: bool = True
    require_human_approval_for_overage: bool = True
    allow_raw_keys: bool = False
    require_worktree_for_edits: bool = False
    allow_shared_repo_edits: bool = False
    default_isolation: str = "read_only"
    min_remaining_percent: int = 10
    usage_stale_after_seconds: int = 1800
    usage_auto_refresh_after_seconds: int | None = None
    allowed_providers: list[str] = Field(default_factory=list)
    denied_providers: list[str] = Field(default_factory=list)
    block_on_usage_statuses: list[str] = Field(
        default_factory=lambda: [UsageStatus.LIMIT_REACHED.value, UsageStatus.OVERAGE_POSSIBLE.value]
    )


class ProviderConfig(BaseModel):
    enabled: bool = True
    binary_candidates: list[str] = Field(default_factory=list)
    command: list[str] | None = None
    models: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentPoolConfig(BaseModel):
    version: int = 1
    storage: StorageConfig = Field(default_factory=StorageConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    model_catalog_paths: list[str] = Field(default_factory=list)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    custom_providers: list[dict[str, Any]] = Field(default_factory=list)


def default_provider_config(model_catalog_paths: list[str] | None = None) -> dict[str, ProviderConfig]:
    providers = {
        "fake-question": ProviderConfig(
            binary_candidates=[sys.executable],
            command=[sys.executable, str(FAKE_AGENT_DIR / FAKE_PROVIDER_SCRIPTS["fake-question"])],
            models=[{"id": "fake", "source": "config", "confidence": Confidence.LOCAL_CONFIG.value}],
            metadata={"fake": True},
        ),
        "fake-approval": ProviderConfig(
            binary_candidates=[sys.executable],
            command=[sys.executable, str(FAKE_AGENT_DIR / FAKE_PROVIDER_SCRIPTS["fake-approval"])],
            models=[{"id": "fake", "source": "config", "confidence": Confidence.LOCAL_CONFIG.value}],
            metadata={"fake": True},
        ),
        "fake-completed": ProviderConfig(
            binary_candidates=[sys.executable],
            command=[sys.executable, str(FAKE_AGENT_DIR / FAKE_PROVIDER_SCRIPTS["fake-completed"])],
            models=[{"id": "fake", "source": "config", "confidence": Confidence.LOCAL_CONFIG.value}],
            metadata={"fake": True},
        ),
        "fake-idle": ProviderConfig(
            binary_candidates=[sys.executable],
            command=[sys.executable, str(FAKE_AGENT_DIR / FAKE_PROVIDER_SCRIPTS["fake-idle"])],
            models=[{"id": "fake", "source": "config", "confidence": Confidence.LOCAL_CONFIG.value}],
            metadata={"fake": True},
        ),
        "fake-limit": ProviderConfig(
            binary_candidates=[sys.executable],
            command=[sys.executable, str(FAKE_AGENT_DIR / FAKE_PROVIDER_SCRIPTS["fake-limit"])],
            models=[{"id": "fake", "source": "config", "confidence": Confidence.LOCAL_CONFIG.value}],
            metadata={"fake": True},
        ),
        "fake-patch": ProviderConfig(
            binary_candidates=[sys.executable],
            command=[sys.executable, str(FAKE_AGENT_DIR / FAKE_PROVIDER_SCRIPTS["fake-patch"])],
            models=[{"id": "fake", "source": "config", "confidence": Confidence.LOCAL_CONFIG.value}],
            metadata={"fake": True},
        ),
        "claude-code": ProviderConfig(
            binary_candidates=["claude"],
        ),
        "codex-cli": ProviderConfig(
            binary_candidates=["codex"],
        ),
        "cursor-cli": ProviderConfig(
            binary_candidates=["agent", "cursor-agent"],
        ),
        "opencode": ProviderConfig(binary_candidates=["opencode"]),
        "copilot-cli": ProviderConfig(
            binary_candidates=["gh"],
            command=["gh", "copilot"],
        ),
        "droid-cli": ProviderConfig(
            binary_candidates=["droid"],
        ),
        "devin-cli": ProviderConfig(
            binary_candidates=["devin"],
        ),
    }
    return apply_model_catalog(providers, load_model_catalog(model_catalog_paths))


def load_model_catalog(paths: list[str] | None = None) -> dict[str, Any]:
    catalog = _load_json_catalog(DEFAULT_MODEL_CATALOG_PATH)
    for raw_path in paths or []:
        path = expand_user_path(raw_path)
        if not path.exists():
            raise FileNotFoundError(f"Model catalog path does not exist: {path}")
        catalog = deep_merge(catalog, _load_json_catalog(path))
    return catalog


def validate_model_catalog_path(
    path: Path,
    known_provider_ids: set[str] | None = None,
) -> dict[str, Any]:
    expanded = expand_user_path(str(path))
    errors: list[str] = []
    warnings: list[str] = []
    try:
        catalog = _load_json_catalog(expanded)
    except Exception as exc:
        return {"ok": False, "path": str(expanded), "errors": [str(exc)], "warnings": warnings}
    _validate_model_catalog(catalog, errors, warnings, known_provider_ids)
    return {"ok": not errors, "path": str(expanded), "errors": errors, "warnings": warnings}


def _load_json_catalog(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    raise ValueError(f"Model catalog paths must be JSON files: {path}")


def _validate_model_catalog(
    catalog: Any,
    errors: list[str],
    warnings: list[str],
    known_provider_ids: set[str] | None,
) -> None:
    if not isinstance(catalog, dict):
        errors.append("catalog must be a JSON object")
        return
    providers = catalog.get("providers")
    if providers is None:
        errors.append("catalog.providers is required")
        return
    if not isinstance(providers, dict):
        errors.append("catalog.providers must be an object")
        return
    for provider_id, entry in providers.items():
        if not isinstance(provider_id, str) or not provider_id:
            errors.append("provider ids must be non-empty strings")
            continue
        if known_provider_ids is not None and provider_id not in known_provider_ids:
            warnings.append(f"unknown provider id: {provider_id}")
        if not isinstance(entry, dict):
            errors.append(f"providers.{provider_id} must be an object")
            continue
        models = entry.get("models", [])
        if models is None:
            continue
        if not isinstance(models, list):
            errors.append(f"providers.{provider_id}.models must be an array")
            continue
        for index, model in enumerate(models):
            prefix = f"providers.{provider_id}.models[{index}]"
            if not isinstance(model, dict):
                errors.append(f"{prefix} must be an object")
                continue
            _validate_model_descriptor(prefix, model, errors)


def _validate_model_descriptor(prefix: str, model: dict[str, Any], errors: list[str]) -> None:
    model_id = model.get("id")
    if not isinstance(model_id, str) or not model_id:
        errors.append(f"{prefix}.id must be a non-empty string")
    source = model.get("source", "config")
    if source not in {"cli_detected", "config", "default", "observed", "unknown"}:
        errors.append(f"{prefix}.source has invalid value: {source}")
    confidence = model.get("confidence", Confidence.UNKNOWN.value)
    if confidence not in {item.value for item in Confidence}:
        errors.append(f"{prefix}.confidence has invalid value: {confidence}")
    aliases = model.get("aliases", [])
    if aliases is not None and not isinstance(aliases, list):
        errors.append(f"{prefix}.aliases must be an array")
    metadata = model.get("metadata", {})
    if metadata is not None and not isinstance(metadata, dict):
        errors.append(f"{prefix}.metadata must be an object")
        return
    reasoning = (metadata or {}).get("reasoning")
    if reasoning is not None:
        _validate_reasoning(prefix, reasoning, errors)


def _validate_reasoning(prefix: str, reasoning: Any, errors: list[str]) -> None:
    if not isinstance(reasoning, dict):
        errors.append(f"{prefix}.metadata.reasoning must be an object")
        return
    supported = reasoning.get("supported", [])
    if supported is not None and not isinstance(supported, list):
        errors.append(f"{prefix}.metadata.reasoning.supported must be an array")
        return
    for option in supported or []:
        if not isinstance(option, str):
            errors.append(f"{prefix}.metadata.reasoning.supported values must be strings")
    default = reasoning.get("default")
    if default is not None and not isinstance(default, str):
        errors.append(f"{prefix}.metadata.reasoning.default must be a string")


def apply_model_catalog(
    providers: dict[str, ProviderConfig],
    catalog: dict[str, Any],
) -> dict[str, ProviderConfig]:
    for provider_id, entry in (catalog.get("providers") or {}).items():
        if provider_id not in providers:
            continue
        provider = providers[provider_id]
        metadata = {key: value for key, value in entry.items() if key != "models"}
        if "models" in entry:
            provider.models = list(entry["models"] or [])
        provider.metadata = deep_merge(provider.metadata, metadata)
    return providers


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Path | None = None) -> AgentPoolConfig:
    raw: dict[str, Any] = {}
    env_path = os.environ.get("AGENTPOOL_CONFIG")
    config_path = path or (Path(env_path).expanduser() if env_path else DEFAULT_CONFIG_PATH)
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text()) or {}

    model_catalog_paths = _list_paths(raw.get("model_catalog_paths"))
    base = AgentPoolConfig(
        model_catalog_paths=model_catalog_paths,
        providers=default_provider_config(model_catalog_paths),
    ).model_dump(mode="json")
    merged = deep_merge(base, raw)
    merged_config = AgentPoolConfig.model_validate(merged)
    _refresh_provider_catalog_fields(merged_config.providers, load_model_catalog(model_catalog_paths))
    merged = merged_config.model_dump(mode="json")
    _repair_packaged_fake_provider_paths(merged)
    _drop_deprecated_providers(merged)
    return AgentPoolConfig.model_validate(merged)


def _refresh_provider_catalog_fields(
    providers: dict[str, ProviderConfig],
    catalog: dict[str, Any],
) -> None:
    for provider_id, entry in (catalog.get("providers") or {}).items():
        if provider_id not in providers or not isinstance(entry, dict):
            continue
        provider = providers[provider_id]
        if "models" in entry:
            provider.models = list(entry["models"] or [])
        for key in CATALOG_METADATA_REFRESH_KEYS:
            if key in entry:
                provider.metadata[key] = entry[key]
            else:
                provider.metadata.pop(key, None)


def validate_config(config: AgentPoolConfig) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if config.policy.allow_auto_routing:
        errors.append("policy.allow_auto_routing must stay false in AgentPool v0.1")
    if "auto" in config.providers:
        errors.append("providers.auto is not allowed")
    if config.policy.usage_stale_after_seconds < 0:
        errors.append("policy.usage_stale_after_seconds must be non-negative")
    if (
        config.policy.usage_auto_refresh_after_seconds is not None
        and config.policy.usage_auto_refresh_after_seconds < 0
    ):
        errors.append("policy.usage_auto_refresh_after_seconds must be non-negative or null")
    if "factory-droid" in config.providers:
        warnings.append("factory-droid is a PRD compatibility name; use droid-cli for the droid binary")
    for provider_id, reason in DEPRECATED_PROVIDER_IDS.items():
        if provider_id in config.providers:
            warnings.append(f"{provider_id} is deprecated and ignored by load_config: {reason}")
    for provider_id, provider in config.providers.items():
        if provider.command is not None and not provider.command:
            errors.append(f"providers.{provider_id}.command must not be empty")
        for model in provider.models:
            _validate_model_descriptor(f"providers.{provider_id}.models[]", model, errors)
    for index, custom in enumerate(config.custom_providers):
        prefix = f"custom_providers[{index}]"
        custom_id = custom.get("id")
        if not isinstance(custom_id, str) or not custom_id:
            errors.append(f"{prefix}.id must be a non-empty string")
        if custom_id == "auto":
            errors.append(f"{prefix}.id cannot be auto")
        command = custom.get("command")
        candidates = custom.get("binary_candidates", [])
        if command is not None and not isinstance(command, list):
            errors.append(f"{prefix}.command must be an array")
        if not command and not candidates:
            warnings.append(f"{prefix} has no command or binary_candidates")
    for path in config.model_catalog_paths:
        result = validate_model_catalog_path(Path(path), known_provider_ids=set(config.providers))
        errors.extend(result["errors"])
        warnings.extend(result["warnings"])
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def _list_paths(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(path) for path in value]


def _repair_packaged_fake_provider_paths(config: dict[str, Any]) -> None:
    providers = config.get("providers")
    if not isinstance(providers, dict):
        return
    for provider_id, script in FAKE_PROVIDER_SCRIPTS.items():
        provider = providers.get(provider_id)
        if not isinstance(provider, dict):
            continue
        metadata = provider.get("metadata") or {}
        if not isinstance(metadata, dict) or not metadata.get("fake"):
            continue
        command = provider.get("command")
        script_path = Path(str(command[1])).expanduser() if isinstance(command, list) and len(command) > 1 else None
        if script_path and script_path.exists():
            continue
        provider["binary_candidates"] = [sys.executable]
        provider["command"] = [sys.executable, str(FAKE_AGENT_DIR / script)]


def _drop_deprecated_providers(config: dict[str, Any]) -> None:
    providers = config.get("providers")
    if not isinstance(providers, dict):
        return
    for provider_id in DEPRECATED_PROVIDER_IDS:
        providers.pop(provider_id, None)
