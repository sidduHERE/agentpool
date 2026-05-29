from __future__ import annotations

from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def render_stats_panel(stats: dict[str, Any], *, plain: bool = False) -> RenderableType | str:
    if plain:
        return render_stats_plain(stats)

    sections: list[RenderableType] = []
    window = stats.get("window", {})
    header = Text.assemble(
        ("AgentPool stats ", "bold"),
        (window.get("label", ""), "cyan"),
        (f"  scope={stats.get('scope', 'all')}", "dim"),
    )
    sections.append(header)

    sessions = stats.get("sessions", {})
    session_line = (
        f"sessions: {sessions.get('total', 0)} total | "
        f"spawned {sessions.get('spawned', 0)} | "
        f"terminated {sessions.get('terminated', 0)}"
    )
    sections.append(Text(session_line))

    parallelism = stats.get("parallelism", {})
    ratio = parallelism.get("ratio")
    ratio_text = f"{ratio:.2f}" if ratio is not None else "n/a"
    sections.append(
        Text(
            f"parallelism: ratio {ratio_text} | peak {parallelism.get('peak_concurrent', 0)} "
            f"| worker-hours {parallelism.get('sum_worker_hours', 0)}"
        )
    )

    walls = stats.get("walls", {})
    walls_line = f"walls: avoided {walls.get('avoided')} | hit {walls.get('hit')} | confidence {walls.get('confidence')}"
    if walls.get("confidence") == "low":
        walls_line = f"[yellow]{walls_line}[/yellow]"
    sections.append(Text.from_markup(walls_line))

    tokens = stats.get("tokens", {})
    if tokens.get("by_provider"):
        totals = tokens.get("totals", {})
        token_prefix = "tokens (partial: claude-code only): "
        sections.append(
            Text(
                f"{token_prefix}input {totals.get('input')} | output {totals.get('output')}"
            )
        )

    quota = stats.get("quota", {})
    if quota:
        table = Table("Provider", "Current %", "Min", "Max", "Samples", show_header=True, header_style="bold")
        for provider_id, row in sorted(quota.items()):
            table.add_row(
                provider_id,
                _fmt(row.get("current_remaining_percent")),
                _fmt(row.get("min_in_window")),
                _fmt(row.get("max_in_window")),
                str(row.get("samples", 0)),
            )
        sections.append(table)

    data_quality = stats.get("data_quality") or []
    if data_quality:
        dq_lines = [f"- {entry.get('code')}: {entry.get('note') or entry.get('impact')}" for entry in data_quality]
        sections.append(Panel("\n".join(dq_lines), title="data quality", border_style="yellow"))

    return Panel(Group(*sections), title="agentpool stats", border_style="blue")


def render_stats_plain(stats: dict[str, Any]) -> str:
    lines = [
        f"schema_version={stats.get('schema_version')}",
        f"scope={stats.get('scope')}",
        f"window={stats.get('window', {}).get('spec')}",
    ]
    sessions = stats.get("sessions", {})
    lines.append(f"sessions.total={sessions.get('total', 0)}")
    lines.append(f"sessions.spawned={sessions.get('spawned', 0)}")
    parallelism = stats.get("parallelism", {})
    lines.append(f"parallelism.ratio={parallelism.get('ratio')}")
    lines.append(f"parallelism.peak_concurrent={parallelism.get('peak_concurrent', 0)}")
    walls = stats.get("walls", {})
    lines.append(f"walls.avoided={walls.get('avoided')}")
    lines.append(f"walls.hit={walls.get('hit')}")
    lines.append(f"walls.confidence={walls.get('confidence')}")
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)
