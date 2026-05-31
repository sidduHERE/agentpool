from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed
from typing import TypeVar

from agentpool.config import AgentPoolConfig, ProviderConfig
from agentpool.models import AuthStatus, CapacitySnapshot, Confidence, ProviderDescriptor, ToolError, UsageStatus
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

    def descriptors(
        self,
        include_usage: bool = True,
        timeout_seconds: float | None = None,
    ) -> list[ProviderDescriptor]:
        adapters = list(self.adapters.values())
        if timeout_seconds is not None:
            descriptors = _bounded_adapter_calls(
                adapters,
                lambda adapter: adapter.detect(),
                timeout_seconds,
                lambda adapter, exc: _descriptor_failure(adapter, timeout_seconds, exc),
            )
        else:
            descriptors = [adapter.detect() for adapter in adapters]
        for descriptor in descriptors:
            if not include_usage:
                descriptor.usage = None
        return descriptors

    def usage(
        self,
        provider_id: str | None = None,
        backend: str = "native",
        allow_interactive: bool = True,
        timeout_seconds: float | None = None,
    ) -> list[CapacitySnapshot]:
        if backend not in {"native", "codexbar", "ccusage", "combined"}:
            raise ToolError(
                "INVALID_USAGE_BACKEND",
                "Usage backend must be one of: native, codexbar, ccusage, combined.",
                {"backend": backend},
            )
        adapters = [self.get(provider_id)] if provider_id else list(self.adapters.values())
        if timeout_seconds is not None:
            return _bounded_adapter_calls(
                adapters,
                lambda adapter: _usage_for_adapter(adapter, backend, allow_interactive=allow_interactive),
                timeout_seconds,
                lambda adapter, exc: _usage_failure_snapshot(adapter, backend, timeout_seconds, exc),
            )
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


T = TypeVar("T")


def _bounded_adapter_calls(
    adapters: list[ProviderAdapter],
    call: Callable[[ProviderAdapter], T],
    timeout_seconds: float,
    failure_value: Callable[[ProviderAdapter, BaseException | None], T],
) -> list[T]:
    if not adapters:
        return []
    timeout = max(float(timeout_seconds), 0.001)
    executor = ThreadPoolExecutor(max_workers=min(8, len(adapters)))
    futures = {executor.submit(call, adapter): adapter for adapter in adapters}
    results: dict[str, T] = {}
    try:
        for future in as_completed(futures, timeout=timeout):
            adapter = futures[future]
            try:
                results[adapter.id] = future.result()
            except Exception as exc:
                results[adapter.id] = failure_value(adapter, exc)
    except FutureTimeoutError:
        pass
    finally:
        for future, adapter in futures.items():
            if adapter.id in results:
                continue
            if future.done():
                try:
                    results[adapter.id] = future.result()
                except Exception as exc:
                    results[adapter.id] = failure_value(adapter, exc)
                continue
            future.cancel()
            results[adapter.id] = failure_value(adapter, None)
        executor.shutdown(wait=False, cancel_futures=True)
    return [results[adapter.id] for adapter in adapters]


def _descriptor_failure(
    adapter: ProviderAdapter,
    timeout_seconds: float,
    exc: BaseException | None,
) -> ProviderDescriptor:
    reason = (
        f"Provider detect exceeded the {timeout_seconds:g}s MCP refresh budget."
        if exc is None
        else f"Provider detect failed: {exc}"
    )
    return ProviderDescriptor(
        id=adapter.id,
        display_name=adapter.display_name,
        harness=adapter.harness,
        installed=False,
        auth=AuthStatus(status="unknown", confidence=Confidence.UNKNOWN, reason=reason),
        usage=CapacitySnapshot(
            provider_id=adapter.id,
            status=UsageStatus.UNKNOWN,
            confidence=Confidence.UNKNOWN,
            warnings=[reason],
            raw={"source": "agentpool_descriptor_timeout" if exc is None else "agentpool_descriptor_error"},
        ),
        warnings=[reason],
        metadata={"agentpool_partial": True},
    )


def _usage_failure_snapshot(
    adapter: ProviderAdapter,
    backend: str,
    timeout_seconds: float,
    exc: BaseException | None,
) -> CapacitySnapshot:
    if exc is None:
        warning = f"Usage refresh exceeded the {timeout_seconds:g}s MCP refresh budget."
        source = "agentpool_usage_timeout"
    else:
        warning = f"Usage refresh failed: {exc}"
        source = "agentpool_usage_error"
    return CapacitySnapshot(
        provider_id=adapter.id,
        status=UsageStatus.UNKNOWN,
        confidence=Confidence.UNKNOWN,
        warnings=[warning],
        raw={"source": source, "backend": backend, "timeout_seconds": timeout_seconds},
    )


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
