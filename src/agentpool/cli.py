from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.console import Console
from rich.table import Table

from agentpool import __version__
from agentpool.agent_io import collect_payload, observe_payload, parse_detail, read_stdin_text
from agentpool.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_MODEL_CATALOG_PATH,
    load_config,
    validate_config,
    validate_model_catalog_path,
)
from agentpool.mcp_server import run_mcp_server
from agentpool.models import SpawnWorkerRequest, ToolError
from agentpool.onboarding import (
    command_path,
    deep_doctor,
    default_onboarding_nudges,
    format_mcp_install,
    init_config,
    mcp_client_config,
    mcp_host_config,
    privacy_doctor,
    run_fake_smoke,
    run_real_read_only_smoke,
    setup_all_providers,
    setup_provider,
)
from agentpool.mcp import tools as mcp_tools
from agentpool.session_manager import SessionManager
from agentpool.stats.card import render_stats_card
from agentpool.stats.render import render_stats_panel, render_stats_plain
from agentpool.usage.probes import detect_codexbar


app = typer.Typer(
    help="AgentPool local coding-agent control plane.",
    invoke_without_command=True,
    no_args_is_help=True,
)
config_app = typer.Typer(help="Inspect AgentPool config.")
leases_app = typer.Typer(help="Manage advisory file leases.")
worktrees_app = typer.Typer(help="Inspect and clean AgentPool-created worktrees.")
app.add_typer(config_app, name="config")
app.add_typer(leases_app, name="leases")
app.add_typer(worktrees_app, name="worktrees")
console = Console()


@app.callback()
def root(
    version: Annotated[bool, typer.Option("--version", help="Show AgentPool version.")] = False,
) -> None:
    if version:
        console.print(f"agentpool {__version__}")
        raise typer.Exit()


def print_data(data: object, json_output: bool) -> None:
    if json_output:
        console.print_json(json.dumps(data, default=str))
    else:
        console.print(data)


def manager() -> SessionManager:
    return SessionManager(load_config())


def handle_tool_error(exc: ToolError, json_output: bool = False) -> None:
    data = {"error": exc.error.model_dump(mode="json")}
    next_command = _next_command_for_error(exc)
    if next_command:
        data["error"]["details"] = {**(data["error"].get("details") or {}), "example": next_command}
    if json_output:
        console.print_json(json.dumps(data))
    else:
        console.print(f"[red]{exc.error.code}[/red]: {exc.error.message}")
        if next_command:
            console.print(f"try: {next_command}")
    raise typer.Exit(1)


def _next_command_for_error(exc: ToolError) -> str | None:
    code = exc.error.code
    details = exc.error.details or {}
    if code == "PROVIDER_NOT_FOUND":
        return "agentpool inventory --json"
    if code == "PROVIDER_NOT_INSTALLED":
        provider_id = details.get("provider_id") or "<provider-id>"
        return f"agentpool setup {provider_id}"
    if code == "POLICY_BLOCKED" and details.get("policy") in {
        "require_explicit_provider",
        "denied_providers",
        "allowed_providers",
    }:
        return "agentpool inventory --json"
    if code == "POLICY_BLOCKED" and "max_parallel_sessions" in details:
        return "agentpool sessions --json"
    if code == "USAGE_POLICY_BLOCKED":
        provider_id = details.get("provider_id") or "<provider-id>"
        return f"agentpool usage-summary --provider {provider_id} --refresh --json"
    if code in {"INVALID_REQUEST", "INVALID_STDIN"}:
        return str(details.get("example") or "agentpool spawn --provider <provider-id> --repo . --task \"Inspect this repo.\"")
    if code == "INVALID_DETAIL":
        return "agentpool observe <session-id> --detail excerpt"
    if code == "INVALID_SESSION_PAGE":
        return "agentpool sessions --limit 50 --offset 0 --json"
    return None


@app.command()
def doctor(
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
    deep: Annotated[bool, typer.Option("--deep", help="Run tmux/sqlite/artifact/cache checks.")] = False,
    privacy: Annotated[
        bool,
        typer.Option("--privacy", help="Show local storage and usage-probe privacy posture."),
    ] = False,
) -> None:
    mgr = manager()
    tmux_path = shutil.which("tmux")
    inventory = mgr.inventory(include_usage=True)
    data = {
        "tmux": {"installed": bool(tmux_path), "path": tmux_path},
        "config_path": str(DEFAULT_CONFIG_PATH),
        "db_path": str(mgr.config.storage.db),
        "artifact_root": str(mgr.config.storage.artifacts),
        "inventory": inventory,
    }
    if deep:
        data["deep"] = deep_doctor(mgr)
    if privacy:
        data["privacy"] = privacy_doctor(mgr)
    if json_output:
        console.print_json(json.dumps(data, default=str))
        return
    table = Table("Provider", "Installed", "Auth", "Usage")
    for provider in inventory["providers"]:
        table.add_row(
            provider["id"],
            "yes" if provider["installed"] else "no",
            provider["auth"]["status"],
            provider["usage"]["status"] if provider.get("usage") else "unknown",
        )
    console.print(f"tmux: {tmux_path or 'missing'}")
    if deep:
        deep_data = data["deep"]
        console.print(f"deep checks: {'ok' if deep_data['ok'] else 'failed'}")
        for check in deep_data["checks"]:
            console.print(f"  {check['name']}: {'ok' if check['ok'] else 'failed'}")
    if privacy:
        privacy_data = data["privacy"]
        console.print("privacy:")
        console.print(
            "  credential storage: "
            f"{'yes' if privacy_data['credential_storage']['agentpool_stores_provider_credentials'] else 'no'}"
        )
        console.print(
            "  browser scraping by default: "
            f"{'yes' if privacy_data['credential_storage']['browser_scraping_enabled_by_default'] else 'no'}"
        )
        console.print(f"  sqlite db: {privacy_data['local_storage']['sqlite_db']}")
        console.print(f"  artifacts: {privacy_data['local_storage']['artifact_root']}")
        console.print("  live usage probes run only on explicit refresh: yes")
    console.print(table)
    console.print("\nWire AgentPool into Cursor:")
    for command in default_onboarding_nudges():
        console.print(f"  {command}")


@app.command("init")
def init_command(
    path: Annotated[Path, typer.Option("--path", help="Config path to initialize.")] = DEFAULT_CONFIG_PATH,
    force: Annotated[bool, typer.Option("--force", help="Back up and overwrite existing config.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    data = init_config(path, force=force)
    if json_output:
        console.print_json(json.dumps(data, default=str))
        return
    status = "wrote" if data["changed"] else "exists"
    console.print(f"config {status}: {data['config_path']}")
    if data.get("backup_path"):
        console.print(f"backup: {data['backup_path']}")
    console.print("next:")
    for command in data.get("next_commands") or default_onboarding_nudges():
        console.print(f"  {command}")


@app.command("mcp-config")
def mcp_config(
    client: Annotated[
        str,
        typer.Option(
            "--client",
            help=(
                "MCP host: generic, claude-code, claude-desktop, codex, cursor, or copilot-cli."
            ),
        ),
    ] = "generic",
    absolute_command: Annotated[
        bool, typer.Option("--absolute-command", help="Use the current resolved agentpool command.")
    ] = False,
    install: Annotated[
        bool,
        typer.Option(
            "--install",
            help="Print one-click install helpers (deeplink, shell command, or Copilot CLI steps).",
        ),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Print MCP host configuration.

    Examples:
      agentpool mcp-config --client codex --absolute-command --install
      agentpool mcp-config --client claude-code --json
      agentpool mcp-config --client generic
    """
    command = command_path(absolute=absolute_command) if absolute_command else "agentpool"
    data = mcp_client_config(client, command)
    if json_output:
        console.print_json(json.dumps(data, default=str))
        if not data.get("ok", True):
            raise typer.Exit(1)
        return
    if not data.get("ok", True):
        console.print(f"[red]{data['error']}[/red]")
        console.print(f"supported: {', '.join(data['supported_clients'])}")
        raise typer.Exit(1)
    if install:
        console.print(format_mcp_install(data), markup=False)
        return
    if data.get("format") == "toml":
        console.print(data["config"], end="", markup=False)
    else:
        console.print_json(json.dumps(data["config"]))


@app.command()
def inventory(json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False) -> None:
    data = manager().inventory(include_usage=True)
    if json_output:
        console.print_json(json.dumps(data, default=str))
    else:
        table = Table("Provider", "Installed", "Binary", "Auth", "Usage")
        for provider in data["providers"]:
            table.add_row(
                provider["id"],
                "yes" if provider["installed"] else "no",
                provider.get("binary_path") or "",
                provider["auth"]["status"],
                provider["usage"]["status"] if provider.get("usage") else "unknown",
            )
        console.print(table)


@app.command()
def usage(
    provider: Annotated[str | None, typer.Option("--provider", help="Provider id.")] = None,
    backend: Annotated[
        str,
        typer.Option("--backend", help="Usage backend: native, codexbar, ccusage, or combined."),
    ] = "combined",
    cached: Annotated[bool, typer.Option("--cached", help="Read latest persisted snapshot without probing.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    try:
        data = manager().cached_usage_snapshot(provider) if cached else manager().usage_snapshot(provider, backend=backend)
        print_data(data, json_output)
    except ToolError as exc:
        handle_tool_error(exc, json_output)


@app.command("usage-summary")
def usage_summary(
    provider: Annotated[str | None, typer.Option("--provider", help="Provider id.")] = None,
    refresh: Annotated[bool, typer.Option("--refresh", help="Run live probes before summarizing.")] = False,
    backend: Annotated[
        str,
        typer.Option("--backend", help="Live usage backend: native, codexbar, ccusage, or combined."),
    ] = "combined",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Summarize provider usage.

    Examples:
      agentpool usage-summary --json
      agentpool usage-summary --provider codex-cli --refresh --json
      agentpool usage-summary --backend codexbar --json
    """
    try:
        data = manager().usage_summary(provider_id=provider, refresh=refresh, backend=backend)
        if json_output:
            console.print_json(json.dumps(data, default=str))
            return
        table = Table("Provider", "Status", "Confidence", "Summary", "Checked")
        for row in data["providers"].values():
            table.add_row(
                row["provider_id"],
                row["status"],
                row["confidence"],
                row["summary"],
                row["checked_at"],
            )
        console.print(f"source: {data['source']}")
        console.print(table)
    except ToolError as exc:
        handle_tool_error(exc, json_output)


@app.command("capacity-summary")
def capacity_summary(
    provider: Annotated[str | None, typer.Option("--provider", help="Provider id.")] = None,
    refresh: Annotated[bool, typer.Option("--refresh", help="Run live probes before summarizing.")] = False,
    backend: Annotated[
        str,
        typer.Option("--backend", help="Live usage backend: native, codexbar, ccusage, or combined."),
    ] = "combined",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    usage_summary(provider=provider, refresh=refresh, backend=backend, json_output=json_output)


@app.command("setup")
def setup_command(
    target: Annotated[
        str,
        typer.Argument(help="Setup target, for example: cursor, codex, claude-code, droid-cli."),
    ],
    backend: Annotated[
        str | None,
        typer.Option("--backend", help="Usage backend override: native, codexbar, ccusage, or combined."),
    ] = None,
    skip_usage: Annotated[
        bool,
        typer.Option("--skip-usage", help="Do not run live usage probes during setup."),
    ] = False,
    relative_command: Annotated[
        bool,
        typer.Option("--relative-command", help="Use 'agentpool' instead of an absolute path in MCP config."),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    if target.strip().lower() == "all":
        data = setup_all_providers(
            manager(),
            backend=backend,
            run_usage=not skip_usage,
            absolute_command=not relative_command,
        )
        if json_output:
            console.print_json(json.dumps(data, default=str))
            return
        console.print("[bold]AgentPool setup: all providers[/bold]")
        table = Table("Provider", "Installed", "Usage", "MCP", "Details", "Next action")
        for row in data["rows"]:
            installed = "yes" if row["installed"] else "no"
            if row["installed"] is None:
                installed = "n/a"
            usage = "skip" if row["usage_ok"] is None else ("ok" if row["usage_ok"] else "needs action")
            table.add_row(
                row["provider_id"],
                installed,
                usage,
                row["mcp_config"],
                str(row.get("usage_message") or ""),
                str(row.get("action") or ""),
            )
        console.print(table)
        console.print("\nRun a focused setup for details:")
        for target_name in data["targets"]:
            console.print(f"  agentpool setup {target_name}")
        return

    data = setup_provider(
        manager(),
        target,
        backend=backend,
        run_usage=not skip_usage,
        absolute_command=not relative_command,
    )
    if json_output:
        console.print_json(json.dumps(data, default=str))
        if not data["ok"]:
            raise typer.Exit(1)
        return
    if data.get("error"):
        console.print(f"[red]{data['error']}[/red]")
        console.print(f"supported: {', '.join(data.get('supported_targets', []))}")
        if data.get("action"):
            console.print(f"next: {data['action']}")
        raise typer.Exit(1)
    console.print(f"[bold]AgentPool setup: {data['display_name']}[/bold]")
    table = Table("Check", "Status", "Details", "Next action")
    for check in data["checks"]:
        status = "skip" if check["ok"] is None else ("ok" if check["ok"] else "needs action")
        table.add_row(check["name"], status, str(check.get("message") or ""), str(check.get("action") or ""))
    console.print(table)
    if data.get("mcp_config"):
        config = data["mcp_config"]
        install_text = format_mcp_install(config)
        if install_text:
            console.print(f"\n{install_text}")
        console.print(f"\nMCP config for {config['path']}:")
        if config.get("format") == "toml":
            console.print(config["config"], end="", markup=False)
        else:
            console.print_json(json.dumps(config["config"], default=str))
    console.print("\nManual steps:")
    for step in data["manual_steps"]:
        console.print(f"  - {step}")
    if data.get("actions"):
        console.print("\nActions to resolve setup issues:")
        for action in data["actions"]:
            console.print(f"  - {action}")
    if data.get("setup_doc"):
        console.print(f"\nGuide: {data['setup_doc']}")
    console.print("\nUseful next commands:")
    for command in data["next_commands"]:
        console.print(f"  {command}")
    if not data["ok"]:
        raise typer.Exit(1)


@app.command()
def onboard(json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False) -> None:
    mgr = manager()
    data = {
        "config_path": str(DEFAULT_CONFIG_PATH),
        "db_path": str(mgr.config.storage.db),
        "artifact_root": str(mgr.config.storage.artifacts),
        "usage_backends": {
            "default": "combined",
            "available": ["native", "codexbar", "ccusage", "combined"],
            "codexbar": detect_codexbar(),
            "web_sources_enabled_by_default": False,
        },
        "first_commands": [
            "agentpool init",
            "agentpool doctor --deep",
            "agentpool usage-summary --refresh",
            "agentpool usage-summary --refresh --backend codexbar",
            "agentpool providers",
            "agentpool models",
            "agentpool smoke --provider fake-question --repo .",
        ],
        "mcp_resources": [
            "agentpool://onboarding",
            "agentpool://skill.md",
            "agentpool://sessions/{session_id}/transcript",
            "agentpool://sessions/{session_id}/events",
            "agentpool://artifacts/{session_id}",
        ],
        "mcp_host_config": {
            "mcpServers": {"agentpool": {"command": "agentpool", "args": ["mcp"]}},
        },
        "rules": [
            "Select providers explicitly; provider=auto is rejected.",
            "Use usage-summary before delegating when possible.",
            "Use read_only for exploration; choose worktree isolation explicitly when AgentPool should create one.",
            "Observe and collect workers deliberately; terminate when done.",
        ],
    }
    if json_output:
        console.print_json(json.dumps(data, default=str))
        return
    console.print("[bold]AgentPool Onboarding[/bold]")
    console.print(f"config: {data['config_path']}")
    console.print(f"db: {data['db_path']}")
    console.print(f"artifacts: {data['artifact_root']}")
    codexbar = data["usage_backends"]["codexbar"]
    console.print(f"codexbar: {'installed' if codexbar['installed'] else 'not installed'}")
    console.print("\nFirst commands:")
    for command in data["first_commands"]:
        console.print(f"  {command}")
    console.print("\nMCP resources agents may read on demand:")
    for resource in data["mcp_resources"]:
        console.print(f"  {resource}")
    console.print("\nHost config:")
    console.print_json(json.dumps(data["mcp_host_config"]))


@app.command()
def smoke(
    provider: Annotated[str, typer.Option("--provider", help="Provider id to smoke test.")] = "fake-question",
    repo: Annotated[Path, typer.Option("--repo", help="Repository path.")] = Path("."),
    model: Annotated[
        str | None,
        typer.Option("--model", help="Explicit model id for real-provider smoke. Defaults to provider smoke_model."),
    ] = None,
    real_read_only: Annotated[
        bool,
        typer.Option("--real-read-only", help="Allow a guarded read-only smoke for a real provider."),
    ] = False,
    timeout: Annotated[int, typer.Option("--timeout", help="Real-provider observe timeout in seconds.")] = 60,
    no_accept_startup_trust: Annotated[
        bool,
        typer.Option("--no-accept-startup-trust", help="Do not answer known startup trust prompts."),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    try:
        if provider.startswith("fake-"):
            data = run_fake_smoke(manager(), repo=repo.expanduser().resolve(), provider_id=provider)
        elif real_read_only:
            data = run_real_read_only_smoke(
                manager(),
                repo=repo.expanduser().resolve(),
                provider_id=provider,
                model=model,
                timeout_seconds=timeout,
                accept_startup_trust=not no_accept_startup_trust,
            )
        else:
            raise ToolError(
                "POLICY_BLOCKED",
                "Real-provider smoke requires --real-read-only.",
                {"provider_id": provider, "isolation": "read_only"},
            )
        if json_output:
            console.print_json(json.dumps(data, default=str))
            return
        console.print(f"smoke {'ok' if data['ok'] else 'failed'}: {data['session_id']}")
        console.print(f"artifacts: {data.get('artifact_dir')}")
    except ToolError as exc:
        handle_tool_error(exc, json_output)


@app.command()
def providers(json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False) -> None:
    data = manager().inventory(include_usage=False)
    if json_output:
        console.print_json(json.dumps({"providers": data["providers"]}, default=str))
    else:
        for provider in data["providers"]:
            console.print(provider["id"])


@app.command("models")
def models_command(
    action: Annotated[
        str | None,
        typer.Argument(help="Use 'validate' to validate a JSON model catalog."),
    ] = None,
    provider: Annotated[str | None, typer.Option("--provider", help="Provider id.")] = None,
    path: Annotated[
        Path | None,
        typer.Option("--path", help="JSON model catalog path. Defaults to the embedded catalog."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    mgr = manager()
    if action:
        if action != "validate":
            raise typer.BadParameter("Only supported models action is 'validate'.")
        data = validate_model_catalog_path(
            path or DEFAULT_MODEL_CATALOG_PATH,
            known_provider_ids=set(mgr.config.providers),
        )
        if json_output:
            console.print_json(json.dumps(data, default=str))
        else:
            console.print(f"catalog {'ok' if data['ok'] else 'failed'}: {data['path']}")
            for warning in data["warnings"]:
                console.print(f"[yellow]warning[/yellow]: {warning}")
            for error in data["errors"]:
                console.print(f"[red]error[/red]: {error}")
        if not data["ok"]:
            raise typer.Exit(1)
        return
    rows = mgr.provider_models(provider)["providers"]
    if json_output:
        console.print_json(json.dumps({"providers": rows}, default=str))
        return
    if provider:
        row = rows[0]
        console.print(f"[bold]{row['provider_id']}[/bold]")
        console.print(f"default: {row['default_model'] or ''}")
        console.print(f"smoke: {row['smoke_model'] or ''}")
        console.print(f"selection: {row['model_selection'] or ''}")
        console.print(f"catalog: {row['catalog_completeness'] or ''}")
        if row["quirks"]:
            console.print("quirks:")
            for quirk in row["quirks"]:
                console.print(f"  {quirk}")
        table = Table("Model", "Display", "Confidence", "Reasoning")
        for model in row["models"]:
            metadata = model.get("metadata") or {}
            reasoning = metadata.get("reasoning") or {}
            supported = ", ".join(reasoning.get("supported") or [])
            default = reasoning.get("default")
            reasoning_text = f"{supported}; default {default}" if supported and default else supported
            table.add_row(
                model["id"],
                model.get("display_name") or "",
                model.get("confidence") or "",
                reasoning_text,
            )
        console.print(table)
        return
    table = Table("Provider", "Default", "Smoke", "Selection", "Models", "Catalog")
    for row in rows:
        table.add_row(
            row["provider_id"],
            str(row["default_model"] or ""),
            str(row["smoke_model"] or ""),
            str(row["model_selection"] or ""),
            str(len(row["models"])),
            str(row["catalog_completeness"] or ""),
        )
    console.print(table)


@app.command()
def stats(
    since: Annotated[
        str | None,
        typer.Option("--since", help="Window spec: 7d, 30d, 12h, 1w, ISO date, or all."),
    ] = None,
    window_from: Annotated[
        str | None,
        typer.Option("--from", help="Window start ISO timestamp. Mutually exclusive with --since."),
    ] = None,
    window_to: Annotated[
        str | None,
        typer.Option("--to", help="Window end ISO timestamp. Requires --from."),
    ] = None,
    provider: Annotated[str | None, typer.Option("--provider", help="Filter by provider id.")] = None,
    scope: Annotated[
        str,
        typer.Option("--scope", help="Session scope: mine or all."),
    ] = "all",
    sections: Annotated[
        list[str] | None,
        typer.Option("--sections", help="Limit output sections. Repeat for multiple."),
    ] = None,
    share: Annotated[
        Path | None,
        typer.Option(
            "--share",
            help="Render a PNG share card. Optional output path.",
            file_okay=True,
            dir_okay=False,
        ),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
    plain: Annotated[bool, typer.Option("--plain", help="Emit grep-friendly key=value lines.")] = False,
) -> None:
    """Report pool stats for a time window. Defaults to the last 7 days."""
    if json_output and plain:
        handle_tool_error(
            ToolError("INVALID_OUTPUT", "Choose either --json or --plain, not both."),
            json_output,
        )
    if window_from or window_to:
        if since is not None:
            handle_tool_error(
                ToolError("INVALID_WINDOW", "Use either --since or --from/--to, not both."),
                json_output,
            )
        if not window_from or not window_to:
            handle_tool_error(
                ToolError("INVALID_WINDOW", "--from and --to must be provided together."),
                json_output,
            )
        window_spec = f"{window_from}/{window_to}"
    else:
        window_spec = since or "7d"

    try:
        data = mcp_tools.get_stats(
            manager(),
            window=window_spec,
            provider_id=provider,
            sections=sections,
            scope=scope,
        )
        if share is not None:
            card = render_stats_card(data, str(share))
            if json_output:
                data = {**data, "share_card": card}
            elif not plain:
                console.print(f"share card: {card['path']} ({card['bytes']} bytes)")
        if json_output:
            console.print_json(json.dumps(data, default=str))
            return
        if plain:
            console.print(render_stats_plain(data))
            return
        console.print(render_stats_panel(data))
    except ToolError as exc:
        handle_tool_error(exc, json_output)


@app.command()
def sessions(
    state: Annotated[
        str | None,
        typer.Option("--state", help="Comma-separated states such as running,completed."),
    ] = None,
    provider: Annotated[str | None, typer.Option("--provider", help="Filter by provider id.")] = None,
    limit: Annotated[int, typer.Option("--limit", help="Maximum sessions to return.")] = 50,
    offset: Annotated[int, typer.Option("--offset", help="Zero-based session page offset.")] = 0,
    recent: Annotated[int | None, typer.Option("--recent", help="Return the N most recent sessions.")] = None,
    all_rows: Annotated[bool, typer.Option("--all", help="Return all matching sessions.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """List sessions with bounded output by default.

    Examples:
      agentpool sessions --json
      agentpool sessions --limit 25 --offset 25 --json
      agentpool sessions --state running,awaiting_user_input --json
      agentpool sessions --recent 10 --json
    """
    try:
        if recent is not None and all_rows:
            raise ToolError(
                "INVALID_SESSION_PAGE",
                "Use either --recent or --all, not both.",
                {"example": "agentpool sessions --recent 10 --json"},
            )
        page_limit: int | None = None if all_rows else (recent if recent is not None else limit)
        page_offset = 0 if recent is not None else offset
        states = [part.strip() for part in state.split(",") if part.strip()] if state else None
        data = manager().list_sessions(
            states=states,
            provider_id=provider,
            limit=page_limit,
            offset=page_offset,
        )
    except ToolError as exc:
        handle_tool_error(exc, json_output)
        return
    print_data(data, json_output)


@app.command()
def spawn(
    provider: Annotated[str, typer.Option("--provider", help="Explicit provider id.")],
    task: Annotated[str | None, typer.Option("--task", help="Worker task.")] = None,
    task_stdin: Annotated[bool, typer.Option("--task-stdin", help="Read worker task from stdin.")] = False,
    repo: Annotated[Path, typer.Option("--repo", help="Repository path.")] = Path("."),
    role: Annotated[
        str,
        typer.Option("--role", help="Worker role: explorer, reviewer, implementer, tester, or custom."),
    ] = "explorer",
    runtime: Annotated[str, typer.Option("--runtime", help="Runtime. v0.1 supports tmux only.")] = "tmux",
    isolation: Annotated[
        str,
        typer.Option(
            "--isolation",
            help="Isolation: read_only, shared, or worktree. Worktree is explicit, not the default.",
        ),
    ] = "read_only",
    model: Annotated[
        str | None,
        typer.Option("--model", help="Explicit model id. Defaults to the selected provider's configured default_model."),
    ] = None,
    account: Annotated[
        str | None,
        typer.Option("--account", help="Optional account label/id to persist with the session."),
    ] = None,
    allowed_file: Annotated[
        list[str] | None,
        typer.Option("--allowed-file", help="Advisory allowed file path. Repeat for multiple paths."),
    ] = None,
    max_runtime_seconds: Annotated[
        int | None,
        typer.Option("--max-runtime-seconds", help="Terminate on the next control operation after this runtime."),
    ] = None,
    max_turns: Annotated[
        int | None,
        typer.Option("--max-turns", help="Maximum number of send-message turns AgentPool will allow."),
    ] = None,
    supervision: Annotated[
        str,
        typer.Option("--supervision", help="Supervision: interactive, autonomous, or human_visible."),
    ] = "interactive",
    initial_prompt_mode: Annotated[
        str,
        typer.Option("--initial-prompt-mode", help="Initial prompt mode: provider_default, send_after_launch, arg, or stdin."),
    ] = "provider_default",
    reasoning_effort: Annotated[
        str | None,
        typer.Option("--reasoning-effort", help="Provider reasoning effort override when supported, for example high."),
    ] = None,
    service_tier: Annotated[
        str | None,
        typer.Option("--service-tier", help="Provider service tier override when supported, for example fast."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Spawn one explicitly selected worker.

    Examples:
      agentpool spawn --provider codex-cli --repo . --task "Review the auth module read-only." --isolation read_only
      cat task.md | agentpool spawn --provider fake-question --repo . --task-stdin --json
      agentpool spawn --provider codex-cli --repo . --task "Make the narrow patch." --isolation worktree
    """
    try:
        if task_stdin and task:
            raise ToolError(
                "INVALID_REQUEST",
                "Use either --task or --task-stdin, not both.",
                {"example": "cat task.md | agentpool spawn --provider <provider-id> --repo . --task-stdin"},
            )
        if task_stdin:
            task = read_stdin_text(
                sys.stdin.read(),
                "task",
                "cat task.md | agentpool spawn --provider <provider-id> --repo . --task-stdin",
            )
        if not task:
            raise ToolError(
                "INVALID_REQUEST",
                "Missing worker task.",
                {"example": "agentpool spawn --provider <provider-id> --repo . --task \"Inspect this repo read-only.\""},
            )
        data = manager().spawn_worker(
            SpawnWorkerRequest(
                provider_id=provider,
                task=task,
                repo_path=str(repo),
                role=role,  # type: ignore[arg-type]
                runtime=runtime,  # type: ignore[arg-type]
                isolation=isolation,  # type: ignore[arg-type]
                model=model,
                account=account,
                allowed_files=allowed_file or [],
                max_runtime_seconds=max_runtime_seconds,
                max_turns=max_turns,
                supervision=supervision,  # type: ignore[arg-type]
                initial_prompt_mode=initial_prompt_mode,  # type: ignore[arg-type]
                reasoning_effort=reasoning_effort,
                service_tier=service_tier,
            )
        )
        if json_output:
            console.print_json(json.dumps(data, default=str))
        else:
            console.print(data["session"]["id"])
            console.print(data["attach_command"])
    except ToolError as exc:
        handle_tool_error(exc, json_output)


@app.command()
def observe(
    session_id: str,
    wait_for: Annotated[str | None, typer.Option("--wait-for", help="Comma-separated events.")] = None,
    timeout: Annotated[int, typer.Option("--timeout")] = 0,
    detail: Annotated[str, typer.Option("--detail", help="Output detail: summary, excerpt, or full.")] = "summary",
    max_lines: Annotated[int | None, typer.Option("--max-lines", help="tmux capture line limit.")] = None,
    output: Annotated[Path | None, typer.Option("--output", help="Write JSON observe payload to this path.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Observe worker state without dumping large transcripts by default.

    Examples:
      agentpool observe <session-id> --wait-for completed,error,question,approval_prompt --timeout 60 --json
      agentpool observe <session-id> --detail excerpt --json
      agentpool observe <session-id> --detail full --output /tmp/observe.json
    """
    try:
        parsed_detail = parse_detail(detail)
        mgr = manager()
        observed = mgr.observe_worker(
            session_id,
            wait_for=wait_for.split(",") if wait_for else None,
            timeout_seconds=timeout,
            include_screen=parsed_detail != "summary",
            include_recent_log=False,
            max_lines=max_lines,
        ).model_dump(mode="json")
        data = observe_payload(observed, mgr.artifact_manifest(session_id), parsed_detail)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            if json_output:
                console.print_json(json.dumps({"output_path": str(output), **data}, default=str))
            else:
                console.print(str(output))
            return
        print_data(data, json_output)
    except ToolError as exc:
        handle_tool_error(exc, json_output)


@app.command()
def send(
    session_id: str,
    message: Annotated[str | None, typer.Argument(help="Message to send. Omit with --stdin.")] = None,
    stdin: Annotated[bool, typer.Option("--stdin", help="Read message from stdin.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Send one message to a worker.

    Examples:
      agentpool send <session-id> "Continue with the review." --json
      cat reply.md | agentpool send <session-id> --stdin --json
      agentpool send <session-id> "" --json
    """
    try:
        if stdin and message:
            raise ToolError(
                "INVALID_REQUEST",
                "Use either a message argument or --stdin, not both.",
                {"example": "cat reply.md | agentpool send <session-id> --stdin"},
            )
        if stdin:
            message = read_stdin_text(
                sys.stdin.read(),
                "message",
                "cat reply.md | agentpool send <session-id> --stdin",
            )
        if message is None:
            raise ToolError(
                "INVALID_REQUEST",
                "Missing message.",
                {"example": "agentpool send <session-id> \"Continue.\""},
            )
        print_data(manager().send_worker_message(session_id, message), json_output)
    except ToolError as exc:
        handle_tool_error(exc, json_output)


@app.command()
def keys(
    session_id: str,
    key: list[str],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Send raw keys when policy allows it.

    Examples:
      agentpool keys <session-id> C-c --json
      agentpool keys <session-id> Enter --json
    """
    try:
        print_data(manager().send_worker_keys(session_id, key), json_output)
    except ToolError as exc:
        handle_tool_error(exc, json_output)


@app.command()
def interrupt(
    session_id: str,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Interrupt a worker.

    Examples:
      agentpool interrupt <session-id> --json
    """
    try:
        print_data(manager().interrupt_worker(session_id), json_output)
    except ToolError as exc:
        handle_tool_error(exc, json_output)


@app.command()
def attach(
    session_id: str,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Print attach information for a worker.

    Examples:
      agentpool attach <session-id>
      agentpool attach <session-id> --json
    """
    try:
        data = manager().attach_info(session_id)
        if json_output:
            console.print_json(json.dumps(data, default=str))
        else:
            console.print(data["attach_command"])
    except ToolError as exc:
        handle_tool_error(exc, json_output)


@app.command()
def collect(
    session_id: str,
    detail: Annotated[str, typer.Option("--detail", help="Output detail: summary, excerpt, or full.")] = "summary",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Collect worker artifacts and return paths by default.

    Examples:
      agentpool collect <session-id> --json
      agentpool collect <session-id> --detail excerpt --json
      agentpool collect <session-id> --detail full
    """
    try:
        parsed_detail = parse_detail(detail)
        print_data(collect_payload(manager().collect_worker_artifacts(session_id), parsed_detail), json_output)
    except ToolError as exc:
        handle_tool_error(exc, json_output)


@app.command("artifacts")
def artifacts_command(
    session_id: str,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """List artifact paths for a worker.

    Examples:
      agentpool artifacts <session-id> --json
      agentpool artifacts <session-id>
    """
    try:
        data = manager().artifact_manifest(session_id)
        print_data(data, json_output)
    except ToolError as exc:
        handle_tool_error(exc, json_output)


@app.command()
def transcript(
    session_id: str,
    offset: Annotated[int, typer.Option("--offset", help="Zero-based byte offset.")] = 0,
    limit: Annotated[int, typer.Option("--limit", help="Maximum bytes to read.")] = 4000,
    tail_lines: Annotated[int | None, typer.Option("--tail-lines", help="Read the last N transcript lines.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Read a bounded transcript page or tail.

    Examples:
      agentpool transcript <session-id> --offset 0 --limit 4000 --json
      agentpool transcript <session-id> --offset 4000 --limit 4000 --json
      agentpool transcript <session-id> --tail-lines 80
    """
    try:
        data = manager().read_transcript(session_id, offset=offset, limit=limit, tail_lines=tail_lines)
        if json_output:
            console.print_json(json.dumps(data, default=str))
        else:
            console.print(data["text"], end="")
    except ToolError as exc:
        handle_tool_error(exc, json_output)


@app.command()
def terminate(
    session_id: str,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Terminate a worker.

    Examples:
      agentpool terminate <session-id> --json
    """
    try:
        print_data(manager().terminate_worker(session_id), json_output)
    except ToolError as exc:
        handle_tool_error(exc, json_output)


@app.command()
def mcp(
    toolsets: Annotated[
        str | None,
        typer.Option("--toolsets", help="Comma-separated MCP toolsets. Defaults to env or default."),
    ] = None,
    tools: Annotated[
        str | None,
        typer.Option("--tools", help="Comma-separated extra MCP tool names to expose."),
    ] = None,
    lockdown: Annotated[bool, typer.Option("--lockdown", help="Suppress inline untrusted worker output.")] = False,
) -> None:
    """Start the AgentPool MCP server.

    Examples:
      agentpool mcp
      agentpool mcp --toolsets default,stats
      AGENTPOOL_MCP_LOCKDOWN=1 agentpool mcp --toolsets default
    """
    run_mcp_server(toolsets=toolsets, tools=tools, lockdown=lockdown)


@config_app.command("path")
def config_path() -> None:
    console.print(str(DEFAULT_CONFIG_PATH))


@config_app.command("print")
def config_print() -> None:
    console.print(yaml.safe_dump(load_config().model_dump(mode="json"), sort_keys=False))


@config_app.command("validate")
def config_validate(
    path: Annotated[Path | None, typer.Option("--path", help="Config path. Defaults to ~/.agentpool/config.yaml.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    try:
        config = load_config(path)
        data = validate_config(config)
        if json_output:
            console.print_json(json.dumps(data, default=str))
            return
        console.print(f"config {'ok' if data['ok'] else 'failed'}")
        for warning in data["warnings"]:
            console.print(f"[yellow]warning[/yellow]: {warning}")
        for error in data["errors"]:
            console.print(f"[red]error[/red]: {error}")
        if not data["ok"]:
            raise typer.Exit(1)
    except Exception as exc:
        if json_output:
            console.print_json(json.dumps({"ok": False, "errors": [str(exc)], "warnings": []}, default=str))
        else:
            console.print(f"[red]error[/red]: {exc}")
        raise typer.Exit(1)


@leases_app.command("list")
def leases_list(
    session_id: Annotated[str | None, typer.Option("--session-id", help="Filter by session id.")] = None,
    repo: Annotated[Path | None, typer.Option("--repo", help="Filter by repository path.")] = None,
    all_leases: Annotated[bool, typer.Option("--all", help="Include released and expired leases.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    try:
        data = manager().list_file_leases(
            session_id=session_id,
            repo_path=str(repo) if repo else None,
            active_only=not all_leases,
        )
        print_data(data, json_output)
    except ToolError as exc:
        handle_tool_error(exc, json_output)


@leases_app.command("acquire")
def leases_acquire(
    session_id: Annotated[str, typer.Option("--session-id", help="Owning session id.")],
    file_path: Annotated[str, typer.Option("--file", help="File path to lease.")],
    mode: Annotated[str, typer.Option("--mode", help="Lease mode: read or write.")] = "write",
    ttl_seconds: Annotated[int | None, typer.Option("--ttl-seconds", help="Optional lease TTL.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    try:
        print_data(manager().acquire_file_lease(session_id, file_path, mode=mode, ttl_seconds=ttl_seconds), json_output)
    except ToolError as exc:
        handle_tool_error(exc, json_output)


@leases_app.command("release")
def leases_release(
    lease_id: Annotated[int | None, typer.Option("--lease-id", help="Lease id to release.")] = None,
    session_id: Annotated[str | None, typer.Option("--session-id", help="Release leases for this session.")] = None,
    file_path: Annotated[str | None, typer.Option("--file", help="Optional file path filter with --session-id.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    try:
        print_data(manager().release_file_lease(lease_id=lease_id, session_id=session_id, file_path=file_path), json_output)
    except ToolError as exc:
        handle_tool_error(exc, json_output)
    except ValueError as exc:
        handle_tool_error(ToolError("INVALID_LEASE_RELEASE", str(exc)), json_output)


@worktrees_app.command("list")
def worktrees_list(
    repo: Annotated[Path, typer.Option("--repo", help="Repository path.")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    try:
        print_data(manager().list_worktrees(str(repo)), json_output)
    except ToolError as exc:
        handle_tool_error(exc, json_output)


@worktrees_app.command("cleanup")
def worktrees_cleanup(
    session_id: Annotated[str, typer.Option("--session-id", help="Session whose AgentPool worktree should be removed.")],
    force: Annotated[bool, typer.Option("--force", help="Remove even if active or dirty.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    try:
        print_data(manager().cleanup_worktree(session_id, force=force), json_output)
    except ToolError as exc:
        handle_tool_error(exc, json_output)
