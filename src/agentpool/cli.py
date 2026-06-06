from __future__ import annotations

import json
import os
import shutil
import sys
from importlib import resources
from pathlib import Path
from typing import Annotated, Any, Callable

import click
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
    privacy_doctor,
    run_fake_smoke,
    run_real_read_only_smoke,
    setup_all_providers,
    setup_provider,
)
from agentpool.preferences import PREFERENCES_PATH, ensure_preferences_file, preferences_payload
from agentpool.mcp import tools as mcp_tools
from agentpool.session_manager import SessionManager
from agentpool.stats.card import render_stats_card
from agentpool.stats.render import render_stats_panel, render_stats_plain
from agentpool.usage.probes import detect_codexbar


class AgentPoolCommand(typer.core.TyperCommand):
    def main(
        self,
        args: list[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **extra: Any,
    ) -> Any:
        return _main_with_recovery(
            lambda **kwargs: super(AgentPoolCommand, self).main(**kwargs),
            self,
            args=args,
            prog_name=prog_name,
            complete_var=complete_var,
            standalone_mode=standalone_mode,
            windows_expand_args=windows_expand_args,
            **extra,
        )


class AgentPoolGroup(typer.core.TyperGroup):
    def main(
        self,
        args: list[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **extra: Any,
    ) -> Any:
        return _main_with_recovery(
            lambda **kwargs: super(AgentPoolGroup, self).main(**kwargs),
            self,
            args=args,
            prog_name=prog_name,
            complete_var=complete_var,
            standalone_mode=standalone_mode,
            windows_expand_args=windows_expand_args,
            **extra,
        )


class AgentPoolTyper(typer.Typer):
    def command(self, *args: Any, cls: type[typer.core.TyperCommand] | None = None, **kwargs: Any) -> Any:
        return super().command(*args, cls=cls or AgentPoolCommand, **kwargs)


def _main_with_recovery(
    call_main: Callable[..., Any],
    command: click.Command,
    *,
    args: list[str] | None,
    prog_name: str | None,
    complete_var: str | None,
    standalone_mode: bool,
    windows_expand_args: bool,
    **extra: Any,
) -> Any:
    try:
        result = call_main(
            args=args,
            prog_name=prog_name,
            complete_var=complete_var,
            standalone_mode=False,
            windows_expand_args=windows_expand_args,
            **extra,
        )
        if standalone_mode:
            sys.exit(result if isinstance(result, int) else 0)
        return result
    except click.ClickException as exc:
        if not standalone_mode:
            raise
        _format_click_error(exc, getattr(command, "rich_markup_mode", None))
        example = _missing_parameter_example(exc)
        if example:
            click.echo(f"try: {example}", err=True)
        sys.exit(exc.exit_code)


def _format_click_error(exc: click.ClickException, rich_markup_mode: str | None) -> None:
    if typer.core.HAS_RICH and rich_markup_mode is not None:
        from typer import rich_utils

        rich_utils.rich_format_error(exc)
    else:
        exc.show()


def _missing_parameter_example(exc: click.ClickException) -> str | None:
    if not isinstance(exc, click.MissingParameter) or exc.ctx is None:
        return None
    command_path = _normalize_command_path(tuple((exc.ctx.command_path or "").split()))
    param = exc.param
    param_name = param.name if param is not None else ""
    option_names = set(getattr(param, "opts", []) or [])
    examples: dict[tuple[tuple[str, ...], str], str] = {
        (("spawn",), "provider"): "cat task.md | agentpool spawn --provider <provider-id> --repo . --task-stdin",
        (("setup",), "target"): "agentpool setup codex",
        (("observe",), "session_id"): "agentpool observe <session-id> --detail excerpt --json",
        (("send",), "session_id"): 'agentpool send <session-id> "Continue."',
        (("keys",), "session_id"): "agentpool keys <session-id> Enter --json",
        (("interrupt",), "session_id"): "agentpool interrupt <session-id> --json",
        (("attach",), "session_id"): "agentpool attach <session-id>",
        (("collect",), "session_id"): "agentpool collect <session-id> --json",
        (("artifacts",), "session_id"): "agentpool artifacts <session-id> --json",
        (("transcript",), "session_id"): "agentpool transcript <session-id> --tail-lines 80",
        (("terminate",), "session_id"): "agentpool terminate <session-id> --dry-run --json",
        (("session", "show"), "session_id"): "agentpool session show <session-id> --json",
        (("leases", "acquire"), "session_id"): "agentpool leases acquire --session-id <session-id> --file <path> --json",
        (("leases", "acquire"), "file_path"): "agentpool leases acquire --session-id <session-id> --file <path> --json",
        (("worktrees", "cleanup"), "session_id"): "agentpool worktrees cleanup --session-id <session-id> --dry-run --json",
    }
    if "--provider" in option_names:
        return examples.get((command_path, "provider"))
    if "--session-id" in option_names:
        return examples.get((command_path, "session_id"))
    if "--file" in option_names:
        return examples.get((command_path, "file_path"))
    return examples.get((command_path, param_name))


def _normalize_command_path(tokens: tuple[str, ...]) -> tuple[str, ...]:
    known = {
        "artifacts",
        "attach",
        "collect",
        "interrupt",
        "keys",
        "leases",
        "observe",
        "send",
        "session",
        "setup",
        "spawn",
        "terminate",
        "transcript",
        "worktrees",
    }
    for index, token in enumerate(tokens):
        if token in known:
            return tokens[index:]
    return tokens[1:]


app = AgentPoolTyper(
    cls=AgentPoolGroup,
    help=(
        "Use every coding-agent subscription you pay for: see live usage limits and offload work to providers with headroom.\n\n"
        "Start here (for AI agents):\n"
        "  agentpool skills get agentpool\n\n"
        "  Skills ship with the CLI, stay version-matched, and include workflow patterns, safety boundaries, "
        "and copy-paste examples. Prefer this over guessing from flag docs alone.\n\n"
        "  skills \\[list]              List available skills\n"
        "  skills get agentpool       Core CLI + MCP delegation guide\n"
        "  skills get core --full     Include quickstart and examples\n"
        "  skills path \\[name]         Print bundled skill/docs path\n\n"
        "Examples:\n"
        "  agentpool inventory --json\n"
        "  cat task.md | agentpool spawn --provider codex-cli --repo . --task-stdin\n"
        "  agentpool observe <session-id> --detail excerpt --json\n\n"
        "More examples live in docs/examples.md. Lifecycle commands stay flat (`spawn`, `observe`, `terminate`); "
        "resource commands are grouped (`session show`, `leases *`, `worktrees *`)."
    ),
    invoke_without_command=True,
    no_args_is_help=True,
)
skills_app = AgentPoolTyper(
    cls=AgentPoolGroup,
    help=(
        "List and retrieve bundled AgentPool skill content.\n\n"
        "Examples:\n"
        "  agentpool skills\n"
        "  agentpool skills get agentpool\n"
        "  agentpool skills get core --full\n"
        "  agentpool skills path agentpool"
    ),
    invoke_without_command=True,
    no_args_is_help=False,
)
config_app = AgentPoolTyper(
    cls=AgentPoolGroup,
    help=(
        "Inspect AgentPool config.\n\n"
        "Examples:\n"
        "  agentpool config path --json\n"
        "  agentpool config validate --json"
    ),
)
leases_app = AgentPoolTyper(
    cls=AgentPoolGroup,
    help=(
        "Manage advisory file leases.\n\n"
        "Examples:\n"
        "  agentpool leases list --json\n"
        "  agentpool leases release --session-id <session-id> --dry-run --json"
    ),
)
session_app = AgentPoolTyper(
    cls=AgentPoolGroup,
    help=(
        "Inspect individual sessions.\n\n"
        "Examples:\n"
        "  agentpool session show <session-id> --json\n"
        "  agentpool session show <session-id> --plain"
    ),
)
worktrees_app = AgentPoolTyper(
    cls=AgentPoolGroup,
    help=(
        "Inspect and clean AgentPool-created worktrees.\n\n"
        "Examples:\n"
        "  agentpool worktrees list --repo . --json\n"
        "  agentpool worktrees cleanup --session-id <session-id> --dry-run --json"
    ),
)
app.add_typer(skills_app, name="skills")
app.add_typer(config_app, name="config")
app.add_typer(leases_app, name="leases")
app.add_typer(session_app, name="session")
app.add_typer(worktrees_app, name="worktrees")
console = Console()

SKILL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "agentpool": {
        "aliases": ["core"],
        "filename": "agentpool-skill.md",
        "description": "Core AgentPool CLI + MCP delegation guide. Read this before spawning workers.",
        "references": ["quickstart.md", "examples.md"],
    }
}
SKILL_ALIASES = {
    alias: name for name, definition in SKILL_DEFINITIONS.items() for alias in [name, *definition["aliases"]]
}
PROJECT_ROOT = Path(__file__).resolve().parents[2]


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


def _docs_resource(filename: str | None = None) -> Any:
    docs = resources.files("agentpool").joinpath("docs")
    return docs.joinpath(filename) if filename else docs


def _read_packaged_doc(filename: str) -> str:
    packaged = _docs_resource(filename)
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    return (PROJECT_ROOT / "docs" / filename).read_text(encoding="utf-8")


def _resource_display_path(filename: str | None = None) -> str:
    packaged = _docs_resource(filename)
    if packaged.is_file() or (filename is None and packaged.is_dir()):
        return str(packaged)
    fallback_docs = PROJECT_ROOT / "docs"
    return str(fallback_docs / filename) if filename else str(fallback_docs)


def _skill_entry(name: str) -> dict[str, Any]:
    definition = SKILL_DEFINITIONS[name]
    return {
        "name": name,
        "aliases": definition["aliases"],
        "description": definition["description"],
        "full_available": bool(definition.get("references")),
    }


def _resolve_skill_name(name: str) -> str:
    normalized = name.strip().lower()
    if normalized in SKILL_ALIASES:
        return SKILL_ALIASES[normalized]
    raise ToolError(
        "INVALID_REQUEST",
        f"Unknown skill {name!r}.",
        {"example": "agentpool skills list"},
    )


def _skill_text(name: str, *, full: bool = False) -> str:
    definition = SKILL_DEFINITIONS[name]
    chunks = [_read_packaged_doc(definition["filename"])]
    if full:
        for filename in definition.get("references") or []:
            title = filename.removesuffix(".md").replace("-", " ").title()
            chunks.append(f"\n\n# Reference: {title}\n\n{_read_packaged_doc(filename)}")
    return "".join(chunks)


def _print_skills_list(json_output: bool) -> None:
    data = {"skills": [_skill_entry(name) for name in sorted(SKILL_DEFINITIONS)]}
    if json_output:
        console.print_json(json.dumps(data, default=str))
        return
    for skill in data["skills"]:
        aliases = f" (aliases: {', '.join(skill['aliases'])})" if skill["aliases"] else ""
        console.print(f"  {skill['name']:<12} {skill['description']}{aliases}")


@skills_app.callback(invoke_without_command=True)
def skills_root(
    ctx: typer.Context,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """List bundled skills when no subcommand is provided."""
    if ctx.invoked_subcommand is None:
        _print_skills_list(json_output)
        raise typer.Exit()


@skills_app.command("list")
def skills_list(json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False) -> None:
    """List bundled skills.

    Examples:
      agentpool skills list
      agentpool skills list --json
    """
    _print_skills_list(json_output)


@skills_app.command("get")
def skills_get(
    name: Annotated[str | None, typer.Argument(help="Skill name, for example: agentpool or core.")] = None,
    full: Annotated[bool, typer.Option("--full", help="Include quickstart and examples with the core skill.")] = False,
    all_skills: Annotated[bool, typer.Option("--all", help="Output every bundled skill.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Output bundled skill content.

    Examples:
      agentpool skills get agentpool
      agentpool skills get core --full
      agentpool skills get --all --json
    """
    try:
        if all_skills:
            names = sorted(SKILL_DEFINITIONS)
        elif name:
            names = [_resolve_skill_name(name)]
        else:
            raise ToolError(
                "INVALID_REQUEST",
                "Provide a skill name or --all.",
                {"example": "agentpool skills get agentpool"},
            )
        skills = [
            {
                **_skill_entry(skill_name),
                "full": full,
                "path": _resource_display_path(SKILL_DEFINITIONS[skill_name]["filename"]),
                "text": _skill_text(skill_name, full=full),
            }
            for skill_name in names
        ]
        if json_output:
            console.print_json(json.dumps({"skills": skills}, default=str))
            return
        console.print("\n\n".join(skill["text"] for skill in skills), markup=False)
    except ToolError as exc:
        handle_tool_error(exc, json_output)


@skills_app.command("path")
def skills_path(
    name: Annotated[str | None, typer.Argument(help="Optional skill name, for example: agentpool or core.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Print bundled skill/docs path.

    Examples:
      agentpool skills path
      agentpool skills path agentpool
      agentpool skills path core --json
    """
    try:
        if name:
            skill_name = _resolve_skill_name(name)
            path = _resource_display_path(SKILL_DEFINITIONS[skill_name]["filename"])
            data = {"name": skill_name, "path": path}
        else:
            data = {"path": _resource_display_path()}
        if json_output:
            console.print_json(json.dumps(data, default=str))
            return
        console.print(data["path"], markup=False)
    except ToolError as exc:
        handle_tool_error(exc, json_output)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _usage_allow_interactive(no_interactive: bool) -> bool:
    return not (no_interactive or _env_flag("AGENTPOOL_NO_INTERACTIVE_USAGE"))


def _init_plan(path: Path, *, force: bool = False) -> dict[str, Any]:
    expanded = path.expanduser()
    preferences_path = expanded.parent / PREFERENCES_PATH.name
    config_exists = expanded.exists()
    would_write_config = force or not config_exists
    backup_path = expanded.with_suffix(expanded.suffix + ".bak") if config_exists and force else None
    would_write_preferences = force or not preferences_path.exists()
    return {
        "dry_run": True,
        "changed": would_write_config,
        "config_path": str(expanded),
        "would_write_config": would_write_config,
        "would_backup_config": backup_path is not None,
        "backup_path": str(backup_path) if backup_path else None,
        "preferences": {
            "path": str(preferences_path),
            "would_write": would_write_preferences,
            "exists": preferences_path.exists(),
        },
        "next_commands": default_onboarding_nudges(),
    }


def _preferences_init_plan(path: Path, *, force: bool = False) -> dict[str, Any]:
    expanded = path.expanduser()
    existed = expanded.exists()
    backup_path = expanded.with_suffix(expanded.suffix + ".bak") if existed and force else None
    return {
        "dry_run": True,
        "changed": force or not existed,
        "path": str(expanded),
        "exists": existed,
        "would_write": force or not existed,
        "would_backup": backup_path is not None,
        "backup_path": str(backup_path) if backup_path else None,
        "resource_uri": "agentpool://preferences.md",
    }


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
    if code == "POLICY_BLOCKED" and details.get("policy") == "require_worktree_for_edits":
        return "cat task.md | agentpool spawn --provider <provider-id> --repo . --task-stdin --role implementer --isolation worktree"
    if code == "POLICY_BLOCKED" and details.get("policy") == "allow_shared_repo_edits":
        return "agentpool spawn --provider <provider-id> --repo . --task-stdin --role implementer --isolation worktree"
    if code == "POLICY_BLOCKED" and details.get("policy") == "allow_raw_keys":
        return "agentpool interrupt <session-id> --json"
    if code == "POLICY_BLOCKED" and details.get("provider_id"):
        return "agentpool inventory --json"
    if code == "USAGE_POLICY_BLOCKED":
        provider_id = details.get("provider_id") or "<provider-id>"
        return f"agentpool usage-summary --provider {provider_id} --refresh --json"
    if code == "INVALID_OUTPUT":
        return "agentpool stats --since 7d --json"
    if code in {"INVALID_REQUEST", "INVALID_STDIN"}:
        return str(details.get("example") or "cat task.md | agentpool spawn --provider <provider-id> --repo . --task-stdin")
    if code == "INVALID_DETAIL":
        return "agentpool observe <session-id> --detail excerpt"
    if code == "INVALID_SESSION_PAGE":
        return "agentpool sessions --limit 50 --offset 0 --json"
    if code == "TMUX_SESSION_NOT_FOUND":
        return "agentpool sessions --state running,ready,awaiting_user_input,awaiting_approval --json"
    if code == "WORKTREE_ACTIVE":
        session_id = details.get("session_id") or "<session-id>"
        return f"agentpool terminate {session_id} --json"
    if code == "WORKTREE_DIRTY":
        return "git -C <worktree-path> status --short"
    if code == "WORKTREE_FAILED":
        return "git status --short"
    if code == "GIT_NOT_REPO":
        return "agentpool spawn --provider <provider-id> --repo <git-repo-path> --task-stdin --isolation read_only"
    if code == "TMUX_NOT_FOUND":
        return "brew install tmux"
    if code == "TERMINAL_CONTROL_NOT_FOUND":
        return "install termctrl, then run agentpool doctor --deep --json"
    if code == "INVALID_WINDOW":
        return "agentpool stats --since 7d --json"
    if code == "INVALID_TRANSCRIPT_RANGE":
        return "agentpool transcript <session-id> --offset 0 --limit 4000 --json"
    if code == "INVALID_LEASE_RELEASE":
        return str(details.get("example") or "agentpool leases list --json")
    if code in {"INVALID_LEASE_MODE", "LEASE_CONFLICT"}:
        return "agentpool leases list --json"
    return None


@app.command()
def doctor(
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
    deep: Annotated[bool, typer.Option("--deep", help="Run runtime/sqlite/artifact/cache checks.")] = False,
    privacy: Annotated[
        bool,
        typer.Option("--privacy", help="Show local storage and usage-probe privacy posture."),
    ] = False,
) -> None:
    """Check environment, runtime, and provider health.

    Examples:
      agentpool doctor
      agentpool doctor --deep --privacy --json
    """
    mgr = manager()
    tmux_path = shutil.which("tmux")
    termctrl_path = shutil.which(mgr.config.runtime.terminal_control.binary)
    inventory = mgr.inventory(include_usage=True)
    data = {
        "tmux": {"installed": bool(tmux_path), "path": tmux_path},
        "terminal_control": {
            "enabled": mgr.config.runtime.terminal_control.enabled,
            "installed": bool(termctrl_path),
            "path": termctrl_path,
        },
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
    console.print(f"terminal-control: {termctrl_path or 'missing'}")
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
    force: Annotated[
        bool,
        typer.Option("--force", "--yes", help="Back up and overwrite existing config."),
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview planned writes without changing files.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Initialize AgentPool config and local state.

    Examples:
      agentpool init
      agentpool init --path ~/.agentpool/config.yaml --dry-run --json
      agentpool init --force --json
    """
    data = _init_plan(path, force=force) if dry_run else init_config(path, force=force)
    if json_output:
        console.print_json(json.dumps(data, default=str))
        return
    if dry_run:
        console.print(f"dry run: config {'would write' if data['would_write_config'] else 'unchanged'}: {data['config_path']}")
        if data.get("backup_path"):
            console.print(f"backup would be written: {data['backup_path']}")
        console.print(
            f"preferences {'would write' if data['preferences']['would_write'] else 'unchanged'}: "
            f"{data['preferences']['path']}"
        )
        return
    status = "wrote" if data["changed"] else "exists"
    console.print(f"config {status}: {data['config_path']}")
    preferences = data.get("preferences") or {}
    if preferences:
        preferences_status = "wrote" if preferences.get("changed") else "exists"
        console.print(f"preferences {preferences_status}: {preferences['path']}")
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
    """List providers with install, auth, model, and usage state.

    Examples:
      agentpool inventory
      agentpool inventory --json
    """
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
        console.print(f"preferences: {data['preferences']['path']}")


@app.command()
def usage(
    provider: Annotated[str | None, typer.Option("--provider", help="Provider id.")] = None,
    backend: Annotated[
        str,
        typer.Option("--backend", help="Usage backend: native, codexbar, ccusage, or combined."),
    ] = "combined",
    cached: Annotated[bool, typer.Option("--cached", help="Read latest persisted snapshot without probing.")] = False,
    no_interactive: Annotated[
        bool,
        typer.Option(
            "--no-interactive",
            "--no-interactive-usage",
            help="Disable provider TUI fallback probes for this command.",
        ),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Show a provider usage snapshot.

    Examples:
      agentpool usage --provider codex-cli --json
      AGENTPOOL_NO_INTERACTIVE_USAGE=1 agentpool usage --provider claude-code --json
      agentpool usage --provider cursor-cli --backend codexbar --json
      agentpool usage --cached --json
    """
    try:
        data = (
            manager().cached_usage_snapshot(provider)
            if cached
            else manager().usage_snapshot(
                provider,
                backend=backend,
                allow_interactive=_usage_allow_interactive(no_interactive),
            )
        )
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
    no_interactive: Annotated[
        bool,
        typer.Option(
            "--no-interactive",
            "--no-interactive-usage",
            help="Disable provider TUI fallback probes during refresh.",
        ),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Summarize provider usage.

    Examples:
      agentpool usage-summary --json
      agentpool usage-summary --provider codex-cli --refresh --json
      agentpool usage-summary --provider claude-code --refresh --no-interactive --json
      agentpool usage-summary --backend codexbar --json
    """
    try:
        data = manager().usage_summary(
            provider_id=provider,
            refresh=refresh,
            backend=backend,
            allow_interactive=_usage_allow_interactive(no_interactive),
        )
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
        console.print(f"preferences: {data['preferences']['path']}")
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
    no_interactive: Annotated[
        bool,
        typer.Option(
            "--no-interactive",
            "--no-interactive-usage",
            help="Disable provider TUI fallback probes during refresh.",
        ),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Human convenience alias for usage-summary.

    Examples:
      agentpool capacity-summary --json
      agentpool capacity-summary --refresh --no-interactive --json
    """
    usage_summary(
        provider=provider,
        refresh=refresh,
        backend=backend,
        no_interactive=no_interactive,
        json_output=json_output,
    )


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
    no_interactive_usage: Annotated[
        bool,
        typer.Option(
            "--no-interactive-usage",
            "--no-interactive",
            help="Disable provider TUI fallback probes during setup usage checks.",
        ),
    ] = False,
    relative_command: Annotated[
        bool,
        typer.Option("--relative-command", help="Use 'agentpool' instead of an absolute path in MCP config."),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Wire AgentPool into an MCP host or provider and report next steps.

    Examples:
      agentpool setup codex
      agentpool setup cursor --json
      agentpool setup claude-code --no-interactive-usage --json
      agentpool setup all --skip-usage
    """
    if target.strip().lower() == "all":
        data = setup_all_providers(
            manager(),
            backend=backend,
            run_usage=not skip_usage,
            absolute_command=not relative_command,
            allow_interactive=_usage_allow_interactive(no_interactive_usage),
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
        allow_interactive=_usage_allow_interactive(no_interactive_usage),
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
    """Print onboarding paths, first commands, and MCP host config.

    Examples:
      agentpool onboard
      agentpool onboard --json
    """
    mgr = manager()
    data = {
        "config_path": str(DEFAULT_CONFIG_PATH),
        "db_path": str(mgr.config.storage.db),
        "artifact_root": str(mgr.config.storage.artifacts),
        "preferences": preferences_payload(include_text=False),
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
            "agentpool://preferences.md",
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
    console.print(f"preferences: {data['preferences']['path']}")
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


@app.command("preferences")
def preferences_command(
    action: Annotated[
        str,
        typer.Argument(help="Action: show, init, or path."),
    ] = "show",
    path: Annotated[
        Path,
        typer.Option("--path", help="Preferences path. Defaults to ~/.agentpool/preferences.md."),
    ] = PREFERENCES_PATH,
    force: Annotated[
        bool,
        typer.Option("--force", "--yes", help="Back up and overwrite during init."),
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview preferences init without writing files.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Show or create the Markdown preferences agents should read before delegation.

    Examples:
      agentpool preferences
      agentpool preferences init
      agentpool preferences init --dry-run --json
      agentpool preferences --json
      agentpool preferences path
    """
    action = action.strip().lower()
    if dry_run and action != "init":
        handle_tool_error(
            ToolError(
                "INVALID_REQUEST",
                "--dry-run is only supported for preferences init.",
                {"example": "agentpool preferences init --dry-run --json"},
            ),
            json_output,
        )
        return
    if action == "path":
        data = {"path": str(path.expanduser()), "resource_uri": "agentpool://preferences.md"}
        if json_output:
            console.print_json(json.dumps(data, default=str))
        else:
            console.print(data["path"])
        return
    if action == "init":
        data = _preferences_init_plan(path, force=force) if dry_run else ensure_preferences_file(path, force=force)
    elif action == "show":
        data = preferences_payload(path, include_text=True)
    else:
        handle_tool_error(
            ToolError(
                "INVALID_REQUEST",
                "Preferences action must be one of: show, init, path.",
                {"example": "agentpool preferences init"},
            ),
            json_output,
        )
        return
    if json_output:
        console.print_json(json.dumps(data, default=str))
        return
    if action == "init":
        if dry_run:
            console.print(f"dry run: preferences {'would write' if data['would_write'] else 'unchanged'}: {data['path']}")
            if data.get("backup_path"):
                console.print(f"backup would be written: {data['backup_path']}")
            return
        status = "wrote" if data["changed"] else "exists"
        console.print(f"preferences {status}: {data['path']}")
        if data.get("backup_path"):
            console.print(f"backup: {data['backup_path']}")
        return
    console.print(data["text"], end="" if str(data["text"]).endswith("\n") else "\n")


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
    """Run a fake-provider smoke or guarded real-provider smoke.

    Examples:
      agentpool smoke --provider fake-question --repo . --json
      agentpool smoke --provider codex-cli --repo . --real-read-only --timeout 60
    """
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
    """List configured provider ids.

    Examples:
      agentpool providers
      agentpool providers --json
    """
    data = manager().inventory(include_usage=False)
    if json_output:
        console.print_json(
            json.dumps(
                {"providers": data["providers"], "preferences": data["preferences"]},
                default=str,
            )
        )
    else:
        for provider in data["providers"]:
            console.print(provider["id"])
        console.print(f"preferences: {data['preferences']['path']}")


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
    """List the model catalog, or validate it with 'models validate'.

    Examples:
      agentpool models --json
      agentpool models --provider codex-cli
      agentpool models validate --path src/agentpool/provider_model_catalog.json --json
    """
    if action:
        if action != "validate":
            handle_tool_error(
                ToolError(
                    "INVALID_REQUEST",
                    "Only supported models action is 'validate'.",
                    {"example": "agentpool models validate --json"},
                ),
                json_output,
            )
            return
        mgr = manager()
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
    mgr = manager()
    data = mgr.provider_models(provider)
    rows = data["providers"]
    if json_output:
        console.print_json(json.dumps(data, default=str))
        return
    if provider:
        row = rows[0]
        console.print(f"[bold]{row['provider_id']}[/bold]")
        console.print(f"default: {row['default_model'] or ''}")
        console.print(f"smoke: {row['smoke_model'] or ''}")
        console.print(f"selection: {row['model_selection'] or ''}")
        if row.get("reasoning_effort_arg"):
            console.print(f"reasoning: {row['reasoning_effort_arg']}")
        elif row.get("reasoning_effort_config_key"):
            console.print(f"reasoning config: {row['reasoning_effort_config_key']}")
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
        console.print(f"preferences: {data['preferences']['path']}")
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
    console.print(f"preferences: {data['preferences']['path']}")


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
    """Report pool stats for a time window. Defaults to the last 7 days.

    Examples:
      agentpool stats
      agentpool stats --since 30d --plain
      agentpool stats --from 2026-01-01T00:00:00Z --to 2026-01-08T00:00:00Z --json
    """
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
    plain: Annotated[bool, typer.Option("--plain", help="Emit grep-friendly key=value lines.")] = False,
) -> None:
    """List sessions with bounded output by default.

    Examples:
      agentpool sessions --json
      agentpool sessions --limit 25 --offset 25 --json
      agentpool sessions --state running,awaiting_user_input --json
      agentpool sessions --recent 10 --json
      agentpool sessions --plain
    """
    if json_output and plain:
        handle_tool_error(ToolError("INVALID_OUTPUT", "Choose either --json or --plain, not both."), json_output)
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
    if json_output:
        print_data(data, json_output)
        return
    if plain:
        console.print(_sessions_plain(data), markup=False)
        return
    _print_sessions_table(data)


def _print_sessions_table(data: dict[str, Any]) -> None:
    table = Table("Session", "Provider", "State", "Role", "Created", "Repo")
    for session in data.get("sessions", []):
        table.add_row(
            str(session.get("id") or ""),
            str(session.get("provider_id") or ""),
            str(session.get("state") or ""),
            str(session.get("role") or ""),
            str(session.get("created_at") or ""),
            str(session.get("repo_path") or ""),
        )
    console.print(table)
    pagination = data.get("pagination") or {}
    console.print(
        f"showing {pagination.get('count', 0)} of {pagination.get('total', 0)}"
        + (f"; next offset {pagination['next_offset']}" if pagination.get("next_offset") is not None else "")
    )


def _sessions_plain(data: dict[str, Any]) -> str:
    lines: list[str] = []
    for index, session in enumerate(data.get("sessions", [])):
        prefix = f"sessions.{index}"
        for key in ("id", "provider_id", "state", "role", "repo_path", "created_at"):
            lines.append(f"{prefix}.{key}={session.get(key) or ''}")
    pagination = data.get("pagination") or {}
    for key in ("count", "total", "offset", "limit", "has_more", "next_offset"):
        lines.append(f"pagination.{key}={pagination.get(key) if pagination.get(key) is not None else ''}")
    return "\n".join(lines)


def _session_plain(data: dict[str, Any]) -> str:
    session = data.get("session") or {}
    lines = []
    for key in ("id", "provider_id", "model", "state", "role", "repo_path", "worktree_path", "created_at", "ended_at"):
        lines.append(f"session.{key}={session.get(key) or ''}")
    return "\n".join(lines)


def _yes_no(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _print_status_payload(data: dict[str, Any]) -> None:
    preferred = (
        "session_id",
        "ok",
        "state",
        "current_state",
        "dry_run",
        "would_interrupt",
        "would_terminate_runtime",
        "would_terminate_tmux",
        "already_terminated",
        "released",
        "lease_id",
        "file_path",
    )
    labels = {"session_id": "session"}
    printed = set()
    for key in preferred:
        if key in data and data[key] is not None:
            console.print(f"{labels.get(key, key)}: {_yes_no(data[key])}")
            printed.add(key)
    for key, value in data.items():
        if key in printed or isinstance(value, (dict, list)) or value is None:
            continue
        console.print(f"{key}: {_yes_no(value)}")


def _print_artifact_manifest(data: dict[str, Any]) -> None:
    console.print(f"session: {data.get('session_id') or ''}")
    console.print(f"artifact_dir: {data.get('artifact_dir') or ''}", markup=False, soft_wrap=True)
    files = data.get("files") or []
    if not files:
        console.print("files: none")
        return
    table = Table("Kind", "Path", "Bytes")
    for artifact in files:
        table.add_row(
            str(artifact.get("kind") or ""),
            str(artifact.get("path") or ""),
            str(artifact.get("bytes") or artifact.get("size_bytes") or ""),
        )
    console.print(table)


def _print_collect_payload(data: dict[str, Any]) -> None:
    console.print(f"session: {data.get('session_id') or ''}")
    console.print(f"state: {data.get('state') or ''}")
    console.print(f"artifact_dir: {data.get('artifact_dir') or ''}", markup=False, soft_wrap=True)
    artifacts = data.get("artifacts") or []
    console.print(f"artifacts: {len(artifacts)}")
    worker_output = data.get("worker_output") or {}
    if worker_output.get("included"):
        console.print(f"worker_output: included ({worker_output.get('chars', 0)} chars)")
    else:
        console.print(f"worker_output: omitted ({worker_output.get('reason') or worker_output.get('detail') or 'summary'})")
    git = data.get("git") or {}
    if "dirty" in git:
        console.print(f"git_dirty: {_yes_no(git['dirty'])}")


@session_app.command("show")
def session_show(
    session_id: str,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
    plain: Annotated[bool, typer.Option("--plain", help="Emit grep-friendly key=value lines.")] = False,
) -> None:
    """Show one session by id.

    Examples:
      agentpool session show <session-id>
      agentpool session show <session-id> --json
      agentpool session show <session-id> --plain
    """
    if json_output and plain:
        handle_tool_error(ToolError("INVALID_OUTPUT", "Choose either --json or --plain, not both."), json_output)
    try:
        data = manager().get_session(session_id)
    except ToolError as exc:
        handle_tool_error(exc, json_output)
        return
    if json_output:
        print_data(data, json_output)
        return
    if plain:
        console.print(_session_plain(data), markup=False)
        return
    session = data["session"]
    table = Table("Field", "Value")
    for key in ("id", "provider_id", "model", "state", "role", "repo_path", "worktree_path", "created_at", "ended_at"):
        table.add_row(key, str(session.get(key) or ""))
    console.print(table)


@app.command()
def spawn(
    provider: Annotated[str, typer.Option("--provider", help="Explicit provider id.")],
    task: Annotated[str | None, typer.Option("--task", help="Worker task.")] = None,
    task_stdin: Annotated[
        bool,
        typer.Option("--task-stdin", "--stdin", help="Read worker task from stdin."),
    ] = False,
    repo: Annotated[Path, typer.Option("--repo", help="Repository path.")] = Path("."),
    role: Annotated[
        str,
        typer.Option("--role", help="Worker role: explorer, reviewer, implementer, tester, or custom."),
    ] = "explorer",
    runtime: Annotated[
        str | None,
        typer.Option("--runtime", help="Runtime override: tmux or terminal-control. Defaults to config."),
    ] = None,
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
        typer.Option("--service-tier", help="Provider service tier override when supported, for example priority."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Spawn one explicitly selected worker.

    Examples:
      agentpool spawn --provider codex-cli --repo . --task "Review the auth module read-only." --isolation read_only
      cat task.md | agentpool spawn --provider fake-question --repo . --task-stdin --json
      agentpool spawn --provider codex-cli --repo . --task "Make the narrow patch." --isolation worktree

    Notes:
      Each invocation creates a new session. Use your own idempotency key outside AgentPool if you need dedupe.
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
            _print_spawn_success(data)
    except ToolError as exc:
        handle_tool_error(exc, json_output)


def _print_spawn_success(data: dict[str, Any]) -> None:
    session = data.get("session") or {}
    console.print(f"session: {session.get('id')}")
    console.print(f"provider: {session.get('provider_id')}")
    if session.get("model"):
        console.print(f"model: {session.get('model')}")
    console.print(f"state: {session.get('state')}")
    console.print(f"attach: {data.get('attach_command')}")
    if data.get("artifact_dir") or session.get("artifact_dir"):
        console.print(f"artifacts: {data.get('artifact_dir') or session.get('artifact_dir')}")
    if data.get("worktree_path") or session.get("worktree_path"):
        console.print(f"worktree: {data.get('worktree_path') or session.get('worktree_path')}")
    live_control = data.get("live_control") or {}
    if live_control:
        console.print(
            "control: "
            + ", ".join(f"{key}={value}" for key, value in live_control.items() if value is not None)
        )
    preferences = data.get("preferences") or {}
    if preferences.get("path"):
        console.print(f"preferences: {preferences['path']}")


@app.command()
def observe(
    session_id: str,
    wait_for: Annotated[str | None, typer.Option("--wait-for", help="Comma-separated events.")] = None,
    timeout: Annotated[int, typer.Option("--timeout")] = 0,
    detail: Annotated[str, typer.Option("--detail", help="Output detail: summary, excerpt, or full.")] = "summary",
    max_lines: Annotated[int | None, typer.Option("--max-lines", help="Runtime capture line limit.")] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "--output-file", help="Write JSON observe payload to this file path."),
    ] = None,
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
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview interrupt without sending Ctrl-C.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Interrupt a worker.

    Examples:
      agentpool interrupt <session-id> --dry-run --json
      agentpool interrupt <session-id> --json
    """
    try:
        data = {"ok": True, "session_id": session_id, "dry_run": True, "would_interrupt": True} if dry_run else manager().interrupt_worker(session_id)
        if json_output:
            console.print_json(json.dumps(data, default=str))
            return
        _print_status_payload(data)
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
        data = collect_payload(manager().collect_worker_artifacts(session_id), parsed_detail)
        if json_output:
            console.print_json(json.dumps(data, default=str))
            return
        _print_collect_payload(data)
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
        if json_output:
            console.print_json(json.dumps(data, default=str))
            return
        _print_artifact_manifest(data)
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
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview termination without killing the runtime session or updating state."),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Terminate a worker.

    Examples:
      agentpool terminate <session-id> --dry-run --json
      agentpool terminate <session-id> --json
    """
    try:
        data = manager().terminate_worker(session_id, dry_run=dry_run)
        if json_output:
            console.print_json(json.dumps(data, default=str))
            return
        _print_status_payload(data)
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
def config_path(json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False) -> None:
    """Print the resolved config path.

    Examples:
      agentpool config path
      agentpool config path --json
    """
    data = {"path": str(DEFAULT_CONFIG_PATH)}
    if json_output:
        console.print_json(json.dumps(data, default=str))
    else:
        console.print(data["path"])


@config_app.command("print")
def config_print(json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False) -> None:
    """Print the merged config.

    Examples:
      agentpool config print
      agentpool config print --json
    """
    data = load_config().model_dump(mode="json")
    if json_output:
        console.print_json(json.dumps(data, default=str))
    else:
        console.print(yaml.safe_dump(data, sort_keys=False))


@config_app.command("validate")
def config_validate(
    path: Annotated[Path | None, typer.Option("--path", help="Config path. Defaults to ~/.agentpool/config.yaml.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Validate AgentPool config.

    Examples:
      agentpool config validate
      agentpool config validate --path ~/.agentpool/config.yaml --json
    """
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
    """List advisory file leases.

    Examples:
      agentpool leases list --json
      agentpool leases list --session-id <session-id>
      agentpool leases list --repo .
    """
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
    """Acquire an advisory file lease.

    Examples:
      agentpool leases acquire --session-id <session-id> --file src/app.py --json
      agentpool leases acquire --session-id <session-id> --file src/app.py --mode read --ttl-seconds 600
    """
    try:
        print_data(manager().acquire_file_lease(session_id, file_path, mode=mode, ttl_seconds=ttl_seconds), json_output)
    except ToolError as exc:
        handle_tool_error(exc, json_output)


@leases_app.command("release")
def leases_release(
    lease_id: Annotated[int | None, typer.Option("--lease-id", help="Lease id to release.")] = None,
    session_id: Annotated[str | None, typer.Option("--session-id", help="Release leases for this session.")] = None,
    file_path: Annotated[str | None, typer.Option("--file", help="Optional file path filter with --session-id.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview release without updating lease state.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Release advisory file leases.

    Examples:
      agentpool leases release --lease-id 1 --dry-run --json
      agentpool leases release --lease-id 1 --json
      agentpool leases release --session-id <session-id> --file src/app.py
    """
    try:
        if dry_run:
            if lease_id is None and not session_id:
                raise ToolError(
                    "INVALID_LEASE_RELEASE",
                    "Provide --lease-id or --session-id.",
                    {"example": "agentpool leases release --lease-id <lease-id> --dry-run --json"},
                )
            data = {
                "ok": True,
                "dry_run": True,
                "would_release": True,
                "lease_id": lease_id,
                "session_id": session_id,
                "file_path": file_path,
            }
        else:
            data = manager().release_file_lease(lease_id=lease_id, session_id=session_id, file_path=file_path)
        if json_output:
            console.print_json(json.dumps(data, default=str))
            return
        _print_status_payload(data)
    except ToolError as exc:
        handle_tool_error(exc, json_output)
    except ValueError as exc:
        handle_tool_error(ToolError("INVALID_LEASE_RELEASE", str(exc)), json_output)


@worktrees_app.command("list")
def worktrees_list(
    repo: Annotated[Path, typer.Option("--repo", help="Repository path.")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """List AgentPool-created worktrees.

    Examples:
      agentpool worktrees list --repo . --json
      agentpool worktrees list --repo .
    """
    try:
        print_data(manager().list_worktrees(str(repo)), json_output)
    except ToolError as exc:
        handle_tool_error(exc, json_output)


@worktrees_app.command("cleanup")
def worktrees_cleanup(
    session_id: Annotated[str, typer.Option("--session-id", help="Session whose AgentPool worktree should be removed.")],
    force: Annotated[
        bool,
        typer.Option("--force", "--yes", help="Remove even if active or dirty."),
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview cleanup without removing the worktree.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Clean up one AgentPool-created worktree.

    Examples:
      agentpool worktrees cleanup --session-id <session-id> --dry-run --json
      agentpool worktrees cleanup --session-id <session-id> --force --json
    """
    try:
        print_data(manager().cleanup_worktree(session_id, force=force, dry_run=dry_run), json_output)
    except ToolError as exc:
        handle_tool_error(exc, json_output)
