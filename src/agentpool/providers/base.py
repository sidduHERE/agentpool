from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Protocol

from agentpool.config import ProviderConfig
from agentpool.models import (
    AuthStatus,
    Capability,
    CapacitySnapshot,
    Confidence,
    ModelDescriptor,
    ProviderDescriptor,
    RuntimeKind,
    SpawnWorkerRequest,
    UsageStatus,
)
from agentpool.utils import run_capture
from agentpool.usage.probes import (
    claude_code_usage_snapshot,
    codex_cli_usage_snapshot,
    copilot_cli_usage_snapshot,
    devin_cli_usage_snapshot,
    unknown,
)


class ProviderEventPatterns:
    question: list[str] = []
    approval: list[str] = []
    error: list[str] = []


class ProviderAdapter(Protocol):
    id: str
    display_name: str
    harness: str
    config: ProviderConfig

    def detect(self, config: ProviderConfig) -> ProviderDescriptor:
        ...

    def auth_status(self) -> AuthStatus:
        ...

    def usage_snapshot(self, *, allow_interactive: bool = True) -> CapacitySnapshot:
        ...

    def inventory_usage_snapshot(self) -> CapacitySnapshot:
        ...

    def build_launch_command(self, request: SpawnWorkerRequest, workdir: Path) -> list[str]:
        ...

    def build_initial_prompt(self, request: SpawnWorkerRequest, session_id: str, workdir: Path) -> str:
        ...

    def event_patterns(self) -> ProviderEventPatterns:
        ...

    def capabilities(self) -> list[Capability]:
        ...

    def submit_keys(self) -> list[str] | None:
        ...


class CommandProviderAdapter:
    vendor: str | None = None

    def __init__(self, provider_id: str, display_name: str, harness: str, config: ProviderConfig):
        self.id = provider_id
        self.display_name = display_name
        self.harness = harness
        self.config = config

    def detect(self, config: ProviderConfig | None = None) -> ProviderDescriptor:
        cfg = config or self.config
        binary_path = self._binary_path(cfg)
        installed = bool(binary_path)
        return ProviderDescriptor(
            id=self.id,
            display_name=self.display_name,
            vendor=self.vendor,
            harness=self.harness,
            installed=installed,
            binary_path=binary_path,
            version=self._version(binary_path),
            auth=self.auth_status() if installed else AuthStatus(
                status="unavailable",
                confidence=Confidence.UNKNOWN,
                reason="No binary candidate found.",
            ),
            models=[
                ModelDescriptor(
                    id=str(model["id"]),
                    display_name=model.get("display_name"),
                    source=model.get("source", "config"),
                    confidence=Confidence(model.get("confidence", Confidence.UNKNOWN.value)),
                    aliases=model.get("aliases", []),
                    metadata=model.get("metadata", {}),
                )
                for model in cfg.models
            ],
            runtimes=[RuntimeKind.TMUX] if installed else [],
            capabilities=self.capabilities() if installed else [],
            usage=self.inventory_usage_snapshot() if installed else CapacitySnapshot(
                provider_id=self.id,
                status=UsageStatus.UNAVAILABLE,
                confidence=Confidence.UNKNOWN,
                warnings=["Provider binary is not installed."],
            ),
            warnings=[] if installed else ["Provider binary is not installed."],
            metadata=cfg.metadata,
        )

    def auth_status(self) -> AuthStatus:
        return AuthStatus(
            status="unknown",
            confidence=Confidence.UNKNOWN,
            reason="No safe auth probe implemented for this provider.",
        )

    def usage_snapshot(self, *, allow_interactive: bool = True) -> CapacitySnapshot:
        return CapacitySnapshot(
            provider_id=self.id,
            status=UsageStatus.UNKNOWN,
            confidence=Confidence.UNKNOWN,
            warnings=["No safe usage probe available for this provider."],
        )

    def inventory_usage_snapshot(self) -> CapacitySnapshot:
        return self.usage_snapshot(allow_interactive=False)

    def build_launch_command(self, request: SpawnWorkerRequest, workdir: Path) -> list[str]:
        if self.config.command:
            command = [str(Path(part).expanduser()) if part.startswith("~/") else part for part in self.config.command]
        else:
            binary = self._binary_path(self.config)
            if not binary:
                raise FileNotFoundError(f"No binary found for {self.id}")
            command = [binary]
        if request.model and Capability.SUPPORTS_MODEL_ARG in self.capabilities():
            model_arg = str(self.config.metadata.get("model_arg") or "--model")
            command.extend([model_arg, request.model])
        return command

    def build_initial_prompt(self, request: SpawnWorkerRequest, session_id: str, workdir: Path) -> str:
        return f"""You are running as a delegated worker session under AgentPool.

Role: {request.role}
Isolation: {request.isolation}
Repo: {workdir}
Session: {session_id}

Instructions:
- Follow the task from the primary agent.
- If you are blocked, ask a concise question and wait.
- If you need approval for a risky action, ask before proceeding.
- Do not modify files unless the task explicitly allows edits.
- If isolation is read_only, inspect only and do not edit files.
- Keep notes of files inspected, commands run, and findings.
- When finished, print:

AGENTPOOL_RESULT_START
Summary:
Findings:
Files inspected:
Files changed:
Commands run:
Tests run:
Blockers:
Confidence:
AGENTPOOL_RESULT_END

Task:
{request.task}
"""

    def event_patterns(self) -> ProviderEventPatterns:
        return ProviderEventPatterns()

    def capabilities(self) -> list[Capability]:
        return [
            Capability.LIVE_STEERING,
            Capability.READ_ONLY_ADVISORY,
            Capability.CAN_RUN_TESTS,
            Capability.SUPPORTS_INTERACTIVE_MODE,
        ]

    def submit_keys(self) -> list[str] | None:
        keys = self.config.metadata.get("submit_keys")
        return list(keys) if isinstance(keys, list) else None

    def _binary_path(self, config: ProviderConfig) -> str | None:
        if config.command:
            first = config.command[0]
            return first if Path(first).exists() else shutil.which(first)
        for candidate in config.binary_candidates:
            if Path(candidate).exists():
                return str(Path(candidate))
            found = shutil.which(candidate)
            if found:
                return found
        return None

    def _version(self, binary_path: str | None) -> str | None:
        if not binary_path:
            return None
        proc = run_capture([binary_path, "--version"], timeout=1)
        if proc.returncode == 0:
            return (proc.stdout or proc.stderr).strip().splitlines()[0][:200]
        return None


class FakeProviderAdapter(CommandProviderAdapter):
    vendor = "AgentPool"

    def auth_status(self) -> AuthStatus:
        return AuthStatus(status="authenticated", confidence=Confidence.LOCAL_CONFIG, reason="Fake local fixture.")

    def usage_snapshot(self, *, allow_interactive: bool = True) -> CapacitySnapshot:
        return CapacitySnapshot(provider_id=self.id, status=UsageStatus.AVAILABLE, confidence=Confidence.LOCAL_CONFIG)

    def capabilities(self) -> list[Capability]:
        return super().capabilities() + [
            Capability.WORKTREE_EDITS,
            Capability.SUPPORTS_APPROVAL_DETECTION,
            Capability.SUPPORTS_ONE_SHOT_MODE,
        ]


class ClaudeCodeAdapter(CommandProviderAdapter):
    vendor = "Anthropic"

    def usage_snapshot(self, *, allow_interactive: bool = True) -> CapacitySnapshot:
        if not allow_interactive:
            return unknown(
                self.id,
                "Claude native usage refresh launches an interactive Claude /usage probe; "
                "it is disabled for MCP callers to avoid interfering with the host Claude Code session. "
                "Use cached usage from MCP, backend=ccusage/codexbar where available, or run "
                "`agentpool usage-summary --provider claude-code --refresh --json` from a normal shell.",
                source="interactive_probe_disabled",
            )
        return claude_code_usage_snapshot(self.id, self._binary_path(self.config))

    def inventory_usage_snapshot(self) -> CapacitySnapshot:
        return unknown(
            self.id,
            "Explicit `agentpool usage --provider claude-code` runs a temporary Claude CLI /usage probe.",
            source="claude_pty_usage",
        )

    def capabilities(self) -> list[Capability]:
        return super().capabilities() + [Capability.SUPPORTS_MODEL_ARG, Capability.SUPPORTS_USAGE_PROBE]


class CodexCliAdapter(CommandProviderAdapter):
    vendor = "OpenAI"

    def build_launch_command(self, request: SpawnWorkerRequest, workdir: Path) -> list[str]:
        command = super().build_launch_command(request, workdir)
        if request.reasoning_effort:
            command.extend(["-c", f"model_reasoning_effort={json.dumps(request.reasoning_effort)}"])
        if request.service_tier:
            command.extend(["-c", f"service_tier={json.dumps(request.service_tier)}"])
        return command

    def usage_snapshot(self, *, allow_interactive: bool = True) -> CapacitySnapshot:
        return codex_cli_usage_snapshot(self.id, self._binary_path(self.config))

    def inventory_usage_snapshot(self) -> CapacitySnapshot:
        return unknown(
            self.id,
            "Explicit `agentpool usage --provider codex-cli` runs the Codex app-server usage probe.",
            source="codex_app_server",
        )

    def capabilities(self) -> list[Capability]:
        return super().capabilities() + [Capability.SUPPORTS_MODEL_ARG, Capability.SUPPORTS_USAGE_PROBE]


class OpenCodeAdapter(CommandProviderAdapter):
    vendor = "OpenCode"


class CursorCliAdapter(CommandProviderAdapter):
    vendor = "Cursor"

    def auth_status(self) -> AuthStatus:
        binary = self._binary_path(self.config)
        if not binary:
            return AuthStatus(
                status="unavailable",
                confidence=Confidence.UNKNOWN,
                reason="No Cursor Agent binary found.",
            )
        proc = run_capture([binary, "status", "--format", "json"], timeout=5)
        text = "\n".join(part for part in [proc.stdout, proc.stderr] if part)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            if proc.returncode == 0 and "logged in" in text.lower():
                return AuthStatus(status="authenticated", confidence=Confidence.LOCAL_CLI, reason=text.strip()[:300])
            return AuthStatus(status="unknown", confidence=Confidence.UNKNOWN, reason=text.strip()[:300] or None)
        if payload.get("isAuthenticated") or payload.get("status") == "authenticated":
            reason = payload.get("message")
            return AuthStatus(
                status="authenticated",
                confidence=Confidence.LOCAL_CLI,
                reason=str(reason) if reason else None,
            )
        return AuthStatus(
            status="unauthenticated",
            confidence=Confidence.LOCAL_CLI,
            reason=str(payload.get("message") or "Cursor Agent CLI is not authenticated."),
        )

    def build_launch_command(self, request: SpawnWorkerRequest, workdir: Path) -> list[str]:
        command = super().build_launch_command(request, workdir)
        if request.isolation == "read_only":
            read_only_mode = str(self.config.metadata.get("read_only_mode_arg") or "ask")
            command.extend(["--mode", read_only_mode])
        command.extend(["--workspace", str(workdir)])
        return command

    def usage_snapshot(self, *, allow_interactive: bool = True) -> CapacitySnapshot:
        return unknown(
            self.id,
            "Cursor Agent CLI exposes usage via interactive /usage, but no stable non-interactive native usage probe is confirmed.",
            source="cursor_cli_interactive_usage_only",
        )

    def inventory_usage_snapshot(self) -> CapacitySnapshot:
        return unknown(
            self.id,
            "Use `agentpool usage --provider cursor-cli --backend codexbar` for the optional CodexBar Cursor usage path.",
            source="cursor_cli_usage_unknown",
        )

    def capabilities(self) -> list[Capability]:
        return super().capabilities() + [Capability.SUPPORTS_MODEL_ARG, Capability.SUPPORTS_USAGE_PROBE]


class CopilotCliAdapter(CommandProviderAdapter):
    vendor = "GitHub"

    def build_launch_command(self, request: SpawnWorkerRequest, workdir: Path) -> list[str]:
        command = [str(Path(part).expanduser()) if part.startswith("~/") else part for part in self.config.command or []]
        if not command:
            binary = self._binary_path(self.config)
            if not binary:
                raise FileNotFoundError(f"No binary found for {self.id}")
            command = [binary]
        copilot_args: list[str] = []
        if request.model:
            model_arg = str(self.config.metadata.get("model_arg") or "--model")
            copilot_args.extend([model_arg, request.model])
        if request.isolation == "read_only":
            read_only_mode = str(self.config.metadata.get("read_only_mode_arg") or "plan")
            copilot_args.extend(["--mode", read_only_mode])
        separator = self.config.metadata.get("forward_separator")
        if copilot_args and command[:2] == ["gh", "copilot"] and separator:
            command.append(str(separator))
        command.extend(copilot_args)
        return command

    def usage_snapshot(self, *, allow_interactive: bool = True) -> CapacitySnapshot:
        return copilot_cli_usage_snapshot(self.id, self._binary_path(self.config))

    def inventory_usage_snapshot(self) -> CapacitySnapshot:
        return unknown(
            self.id,
            "Explicit `agentpool usage --provider copilot-cli` uses an ambient GitHub token.",
            source="github_copilot_internal_api",
        )

    def capabilities(self) -> list[Capability]:
        return super().capabilities() + [Capability.SUPPORTS_MODEL_ARG, Capability.SUPPORTS_USAGE_PROBE]


class FactoryDroidAdapter(CommandProviderAdapter):
    vendor = "Factory"

    def build_launch_command(self, request: SpawnWorkerRequest, workdir: Path) -> list[str]:
        command = super().build_launch_command(request, workdir)
        if request.model:
            command.extend(["--settings", str(_droid_runtime_settings_path(request.model))])
        return command


class DevinCliAdapter(CommandProviderAdapter):
    vendor = "Devin"

    def usage_snapshot(self, *, allow_interactive: bool = True) -> CapacitySnapshot:
        return devin_cli_usage_snapshot(
            self.id,
            self._binary_path(self.config),
            allow_interactive_fallback=allow_interactive,
        )

    def inventory_usage_snapshot(self) -> CapacitySnapshot:
        return unknown(
            self.id,
            "Explicit `agentpool usage --provider devin-cli` runs the Devin plan-status usage probe.",
            source="devin_plan_status_api",
        )

    def capabilities(self) -> list[Capability]:
        return super().capabilities() + [Capability.SUPPORTS_MODEL_ARG, Capability.SUPPORTS_USAGE_PROBE]


def _droid_runtime_settings_path(model: str) -> Path:
    safe_model = re.sub(r"[^A-Za-z0-9_.-]+", "_", model)
    root = Path("~/.agentpool/runtime-settings").expanduser()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"droid-{safe_model}.json"
    payload = {"sessionDefaultSettings": {"model": model}}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
