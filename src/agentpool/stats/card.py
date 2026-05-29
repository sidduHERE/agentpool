from __future__ import annotations

from pathlib import Path
from typing import Any

from agentpool.models import ToolError
from agentpool.utils import utc_now_iso

CARD_WIDTH = 1200
CARD_HEIGHT = 630


def render_stats_card(stats: dict[str, Any], output_path: str | Path | None = None) -> dict[str, Any]:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise ToolError(
            "MISSING_OPTIONAL_DEPENDENCY",
            "PNG share cards require the optional `card` extra: pip install 'agentpool[card]'.",
            {"dependency": "pillow"},
        ) from exc

    path = Path(output_path or _default_card_path())
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        image = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), color=(15, 23, 42))
        draw = ImageDraw.Draw(image)
        try:
            title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 48)
            body_font = ImageFont.truetype("DejaVuSans.ttf", 32)
        except OSError:
            title_font = ImageFont.load_default()
            body_font = ImageFont.load_default()

        window = stats.get("window", {})
        title = f"AgentPool stats — {window.get('label', 'window')}"
        draw.text((60, 60), title, fill=(248, 250, 252), font=title_font)

        sessions = stats.get("sessions", {})
        parallelism = stats.get("parallelism", {})
        walls = stats.get("walls", {})
        lines = [
            f"Sessions: {sessions.get('total', 0)} total | spawned {sessions.get('spawned', 0)}",
            f"Parallelism ratio: {parallelism.get('ratio', 'n/a')} | peak {parallelism.get('peak_concurrent', 0)}",
            f"Walls avoided: {walls.get('avoided')} | hit {walls.get('hit')} | confidence {walls.get('confidence')}",
            f"Scope: {stats.get('scope')} | schema {stats.get('schema_version')}",
        ]
        y = 160
        for line in lines:
            draw.text((60, y), line, fill=(226, 232, 240), font=body_font)
            y += 56

        image.save(path, format="PNG")
    except Exception as exc:
        raise ToolError(
            "CARD_RENDER_FAILED",
            "Failed to render stats share card.",
            {"reason": str(exc), "path": str(path)},
        ) from exc

    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "width": CARD_WIDTH,
        "height": CARD_HEIGHT,
        "stats_window": window.get("spec"),
        "generated_at": utc_now_iso(),
    }


def _default_card_path() -> Path:
    stamp = utc_now_iso().replace(":", "").replace("-", "")
    return Path.home() / ".agentpool" / "cards" / f"stats-{stamp}.png"
