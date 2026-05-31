from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from agentpool.config import AgentPoolConfig, ProviderConfig
from agentpool.models import CapacitySnapshot, Confidence, ProviderDescriptor, ToolError, UsageStatus
from agentpool.providers.base import (
    ClaudeCodeAdapter,
    CodexCliAdapter,
    CommandProviderAdapter,
    CopilotCliAdapter,
    CursorCliAdapter,
    DevinCliAdapter,
    FactoryDroidAdapter,
    FakeProviderAdapter,
    OpenCodeAdapter,
    ProviderAdapter,
)
from agentpool.usage.probes import ccusage_usage_snapshot, codexbar_usage_snapshot, combine_usage_snapshots


ADAPTERS = {
    "claude-code": ClaudeCodeAdapter,
    "codex-cli": CodexCliAdapter,
    "cursor-cli": CursorCliAdapter,
    "opencode": OpenCodeAdapter,
    "copilot-cli": CopilotCliAdapter,
    "droid-cli": FactoryDroidAdapter,
    "devin-cli": DevinCliAdapter,
}

DISPLAY_NAMES = {
    "claude-code": "Claude Code",
    "codex-cli": "Codex CLI",
    "cursor-cli": "Cursor Agent CLI",
    "opencode": "OpenCode",
    "copilot-cli": "GitHub Copilot CLI",
    "droid-cli": "Droid CLI",
    "devin-cli": "Devin CLI",
}


class ProviderRegistry:
    def __init__(self, adapters: dict[str, ProviderAdapter]):
        self.adapters = adapters

    def get(self, provider_id: str) -> ProviderAdapter:
        if provider_id == "auto":
            raise ToolError(
                "POLICY_BLOCKED",
                "provider=auto is not supported in AgentPool v0.1.",
                {"provider_id": provider_id},
            )
        try:
            return self.adapters[provider_id]
        except KeyError as exc:
            raise ToolError(
                "PROVIDER_NOT_FOUND",
                f"Provider {provider_id} is not configured.",
                {"provider_id": provider_id},
            ) from exc

    def descriptors(self, include_usage: bool = True) -> list[ProviderDescriptor]:
        descriptors = []
        for adapter in self.adapters.values():
            descriptor = adapter.detect()
            if not include_usage:
                descriptor.usage = None
            descriptors.append(descriptor)
        return descriptors

    def usage(
        self,
        provider_id: str | None = None,
        backend: str = "native",
        allow_interactive: bool = True,
    ) -> list[CapacitySnapshot]:
        if backend not in {"native", "codexbar", "ccusage", "combined"}:
            raise ToolError(
                "INVALID_USAGE_BACKEND",
                "Usage backend must be one of: native, codexbar, ccusage, combined.",
                {"backend": backend},
            )
        adapters = [self.get(provider_id)] if provider_id else list(self.adapters.values())
        if len(adapters) <= 1:
            if not adapters:
                return []
            return [_usage_for_adapter(adapters[0], backend, allow_interactive=allow_interactive)]
        with ThreadPoolExecutor(max_workers=min(8, len(adapters))) as executor:
            return list(
                executor.map(
                    lambda adapter: _usage_for_adapter(
                        adapter,
                        backend,
                        allow_interactive=allow_interactive,
                    ),
                    adapters,
                )
            )


def _usage_for_adapter(
    adapter: ProviderAdapter,
    backend: str,
    allow_interactive: bool = True,
) -> CapacitySnapshot:
    descriptor = adapter.detect()
    if not descriptor.installed:
        return CapacitySnapshot(
            provider_id=adapter.id,
            status=UsageStatus.UNAVAILABLE,
            confidence=Confidence.UNKNOWN,
            warnings=["Provider binary is not installed."],
        )
    if backend == "codexbar":
        return codexbar_usage_snapshot(adapter.id)
    if backend == "ccusage":
        return ccusage_usage_snapshot(adapter.id)
    native = adapter.usage_snapshot(allow_interactive=allow_interactive)
    if backend == "combined":
        codexbar = codexbar_usage_snapshot(adapter.id)
        ccusage = ccusage_usage_snapshot(adapter.id) if adapter.id == "claude-code" else None
        return combine_usage_snapshots(native, codexbar, ccusage=ccusage)
    return native


def build_registry(config: AgentPoolConfig) -> ProviderRegistry:
    adapters: dict[str, ProviderAdapter] = {}
    for provider_id, provider_config in config.providers.items():
        if not provider_config.enabled:
            continue
        if provider_id.startswith("fake-"):
            adapters[provider_id] = FakeProviderAdapter(
                provider_id, provider_id.replace("-", " ").title(), provider_id, provider_config
            )
            continue
        cls = ADAPTERS.get(provider_id, CommandProviderAdapter)
        adapters[provider_id] = cls(
            provider_id,
            DISPLAY_NAMES.get(provider_id, provider_id.replace("-", " ").title()),
            provider_id,
            provider_config,
        )
    for custom in config.custom_providers:
        provider_id = custom["id"]
        provider_config = ProviderConfig(
            enabled=custom.get("enabled", True),
            binary_candidates=custom.get("binary_candidates", []),
            command=custom.get("command"),
            models=custom.get("models", []),
            metadata={**custom.get("metadata", {}), "custom": True},
        )
        adapters[provider_id] = CommandProviderAdapter(
            provider_id,
            custom.get("display_name", provider_id),
            custom.get("harness", provider_id),
            provider_config,
        )
    return ProviderRegistry(adapters)
