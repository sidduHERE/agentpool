from __future__ import annotations

from agentpool.config import AgentPoolConfig
from agentpool.models import SessionState, ToolError


MUTATING_ROLES = {"implementer", "tester"}


def enforce_spawn_policy(config: AgentPoolConfig, provider_id: str, role: str, isolation: str) -> None:
    policy = config.policy
    if policy.require_explicit_provider and (not provider_id or provider_id == "auto"):
        raise ToolError(
            "POLICY_BLOCKED",
            "AgentPool v0.1 requires explicit provider selection; provider=auto is disabled.",
            {"provider_id": provider_id, "policy": "require_explicit_provider"},
        )
    if provider_id in policy.denied_providers:
        raise ToolError(
            "POLICY_BLOCKED",
            f"Provider {provider_id} is denied by policy.",
            {"provider_id": provider_id, "policy": "denied_providers"},
        )
    if policy.allowed_providers and provider_id not in policy.allowed_providers:
        raise ToolError(
            "POLICY_BLOCKED",
            f"Provider {provider_id} is not in the allowed provider list.",
            {"provider_id": provider_id, "policy": "allowed_providers"},
        )
    if role in MUTATING_ROLES and policy.require_worktree_for_edits and isolation != "worktree":
        raise ToolError(
            "POLICY_BLOCKED",
            "Mutating roles require worktree isolation by policy.",
            {"role": role, "isolation": isolation, "policy": "require_worktree_for_edits"},
        )
    if isolation == "shared" and not policy.allow_shared_repo_edits and role in MUTATING_ROLES:
        raise ToolError(
            "POLICY_BLOCKED",
            "Shared-repo mutations are disabled by policy.",
            {"role": role, "isolation": isolation, "policy": "allow_shared_repo_edits"},
        )


def enforce_raw_keys_policy(config: AgentPoolConfig, keys: list[str]) -> None:
    if config.policy.allow_raw_keys:
        return
    raise ToolError(
        "POLICY_BLOCKED",
        "Raw key sending is disabled by policy; use interrupt_worker for Ctrl-C.",
        {"keys": keys, "policy": "allow_raw_keys"},
    )


def active_state(state: SessionState | str) -> bool:
    return str(state) in {
        SessionState.STARTING.value,
        SessionState.READY.value,
        SessionState.RUNNING.value,
        SessionState.AWAITING_USER_INPUT.value,
        SessionState.AWAITING_APPROVAL.value,
        SessionState.IDLE.value,
        SessionState.UNKNOWN.value,
    }
