from __future__ import annotations

import json
import os
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import certifi

from agentpool.models import CapacitySnapshot, Confidence, TmuxSessionRef, UsageStatus, UsageWindow, UsageWindowKind
from agentpool.runtimes.tmux import TmuxRuntime
from agentpool.utils import run_capture, terminate_process_group


class ProbeError(Exception):
    pass


def unavailable(provider_id: str, warning: str) -> CapacitySnapshot:
    return CapacitySnapshot(
        provider_id=provider_id,
        status=UsageStatus.UNAVAILABLE,
        confidence=Confidence.UNKNOWN,
        warnings=[warning],
    )


def unknown(provider_id: str, warning: str, source: str) -> CapacitySnapshot:
    return CapacitySnapshot(
        provider_id=provider_id,
        status=UsageStatus.UNKNOWN,
        confidence=Confidence.UNKNOWN,
        warnings=[warning],
        raw={"source": source},
    )


def _extract_json_payload(text: str) -> Any:
    decoder = json.JSONDecoder()
    errors: list[str] = []
    for index, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
            return payload
        except json.JSONDecodeError as exc:
            errors.append(str(exc))
    raise ProbeError("No JSON payload found." if not errors else f"No parseable JSON payload found: {errors[-1]}")


def _urlopen(
    request: urllib.request.Request,
    *,
    timeout: float = 10,
) -> Any:
    context = ssl.create_default_context(cafile=certifi.where())
    return urllib.request.urlopen(request, timeout=timeout, context=context)


def _run_probe_command(
    command: list[str],
    *,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    return run_capture(command, timeout=timeout, terminal_dumb=True)


def _request_json(request: urllib.request.Request) -> dict[str, Any]:
    try:
        with _urlopen(request, timeout=10) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise ProbeError(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ProbeError(str(exc.reason)) from exc
    try:
        payload = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ProbeError(f"Invalid JSON response: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProbeError("JSON response was not an object.")
    return payload


def _tmux_slash_usage_probe(
    provider_id: str,
    command: list[str],
    slash_command: str,
    parser: Callable[[str, str], CapacitySnapshot | None],
    source: str,
    startup_delay: float,
    timeout: float,
    pre_keys: list[list[str]] | None = None,
    extra_keys_after_match: list[list[str]] | None = None,
    prefer_text: str | None = None,
) -> CapacitySnapshot:
    runtime = TmuxRuntime()
    session_name = f"agentpool-usage-{provider_id.replace('-', '')}-{os.getpid()}-{int(time.time() * 1000) % 100000}"
    with tempfile.TemporaryDirectory(prefix="agentpool-usage-") as tmp:
        ref: TmuxSessionRef | None = None
        try:
            ref = runtime.spawn(command, Path(tmp), {}, session_name)
            time.sleep(startup_delay)
            for keys in pre_keys or []:
                runtime.send_keys(ref, keys)
                time.sleep(0.5)
            runtime.send_message(ref, slash_command, submit=True)
            deadline = time.monotonic() + timeout
            latest = ""
            captures: list[str] = []
            fallback_snapshot: CapacitySnapshot | None = None
            while time.monotonic() < deadline:
                latest = runtime.capture(ref, 260)
                captures.append(latest)
                joined = "\n".join(captures)
                snapshot = parser(provider_id, joined)
                if snapshot:
                    if prefer_text and prefer_text not in joined:
                        fallback_snapshot = snapshot
                        time.sleep(0.75)
                        continue
                    for keys in extra_keys_after_match or []:
                        time.sleep(1.0)
                        runtime.send_keys(ref, keys)
                        time.sleep(0.8)
                        captures.append(runtime.capture(ref, 260))
                    if extra_keys_after_match:
                        enriched = parser(provider_id, "\n".join(captures))
                        if enriched:
                            snapshot = enriched
                    snapshot.raw["source"] = source
                    return snapshot
                time.sleep(0.75)
            if fallback_snapshot:
                fallback_snapshot.raw["source"] = source
                fallback_snapshot.warnings.append(f"Returned fallback before seeing `{prefer_text}`.")
                return fallback_snapshot
            return unknown(
                provider_id,
                f"{slash_command} did not yield parseable usage within {int(timeout)}s.",
                source=source,
            )
        except Exception as exc:
            return unknown(provider_id, f"Interactive usage probe failed: {exc}", source=source)
        finally:
            if ref and runtime.exists(ref):
                runtime.terminate(ref)


def _duration_window_kind(duration_mins: int | None) -> UsageWindowKind:
    if duration_mins == 300:
        return UsageWindowKind.FIVE_HOUR
    if duration_mins == 1440:
        return UsageWindowKind.DAILY
    if duration_mins == 10080:
        return UsageWindowKind.WEEKLY
    if duration_mins and 27 * 1440 <= duration_mins <= 32 * 1440:
        return UsageWindowKind.MONTHLY
    return UsageWindowKind.UNKNOWN


def _status_from_windows(windows: list[UsageWindow]) -> UsageStatus:
    remaining_values = [w.remaining_percent for w in windows if w.remaining_percent is not None]
    if not remaining_values:
        return UsageStatus.UNKNOWN
    remaining = min(remaining_values)
    if remaining <= 0:
        return UsageStatus.LIMIT_REACHED
    if remaining <= 15:
        return UsageStatus.NEAR_LIMIT
    return UsageStatus.AVAILABLE


def _clamp_percent(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _int_number(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _epoch_seconds(value: object) -> datetime | None:
    seconds = _int_number(value)
    if not seconds:
        return None
    return datetime.fromtimestamp(seconds, tz=UTC)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clean_optional_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    terminate_process_group(proc)


def _safe_read_pipe(pipe: Any) -> str:
    if pipe is None:
        return ""
    try:
        return pipe.read()[:1000]
    except Exception:
        return ""
