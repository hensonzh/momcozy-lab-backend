from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
from typing import Any

from .paths import LOG_ROOT, ensure_runtime_dirs

_LOCK = threading.Lock()
_DEFAULT_LOG_NAME = "ag-ui-timing.jsonl"
_MAX_METADATA_KEYS = 40
_MAX_LIST_ITEMS = 20
_MAX_STRING_CHARS = 240


def ag_ui_timing_log_path() -> Path:
    configured = os.getenv("MOMCOZY_AG_UI_TIMING_LOG_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return LOG_ROOT / _DEFAULT_LOG_NAME


def ag_ui_timing_log_enabled() -> bool:
    value = os.getenv("MOMCOZY_AG_UI_TIMING_LOG", "1").strip().lower()
    return value not in {"0", "false", "off", "no"}


def record_ag_ui_timing_payload(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        return
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    record_ag_ui_timing_event(
        stage=str(payload.get("stage") or "").strip() or "client.event",
        source=str(payload.get("source") or "client").strip() or "client",
        run_id=str(payload.get("run_id") or payload.get("runId") or "").strip(),
        thread_id=str(payload.get("thread_id") or payload.get("threadId") or "").strip(),
        client_timing_id=str(payload.get("client_timing_id") or payload.get("clientTimingId") or "").strip(),
        user_id=str(payload.get("user_id") or payload.get("userId") or "").strip(),
        elapsed_ms=_optional_float(payload.get("elapsed_ms") or payload.get("elapsedMs")),
        client_ts_ms=_optional_float(payload.get("client_ts_ms") or payload.get("clientTsMs")),
        metadata=metadata,
    )


def record_ag_ui_timing_event(
    *,
    stage: str,
    source: str = "backend",
    run_id: str = "",
    thread_id: str = "",
    client_timing_id: str = "",
    user_id: str = "",
    elapsed_ms: float | None = None,
    client_ts_ms: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not ag_ui_timing_log_enabled():
        return
    safe_stage = str(stage or "").strip()
    if not safe_stage:
        return
    event: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "perf_counter_ms": round(time.perf_counter() * 1000, 3),
        "source": str(source or "backend").strip() or "backend",
        "stage": safe_stage,
    }
    for key, value in (
        ("run_id", run_id),
        ("thread_id", thread_id),
        ("client_timing_id", client_timing_id),
        ("user_id", user_id),
    ):
        text = str(value or "").strip()
        if text:
            event[key] = text
    if elapsed_ms is not None:
        event["elapsed_ms"] = round(float(elapsed_ms), 3)
    if client_ts_ms is not None:
        event["client_ts_ms"] = round(float(client_ts_ms), 3)
    safe_metadata = _safe_metadata(metadata or {})
    if safe_metadata:
        event["metadata"] = safe_metadata
    _append_jsonl(event)


def _append_jsonl(event: dict[str, Any]) -> None:
    try:
        ensure_runtime_dirs()
        path = ag_ui_timing_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with _LOCK:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        return


def _safe_metadata(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for index, (key, item) in enumerate(value.items()):
        if index >= _MAX_METADATA_KEYS:
            result["truncated"] = True
            break
        safe_key = str(key or "").strip()[:80]
        if not safe_key:
            continue
        safe_value = _safe_value(item)
        if safe_value is not None:
            result[safe_key] = safe_value
    return result


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if len(text) > _MAX_STRING_CHARS:
            return text[: _MAX_STRING_CHARS - 1].rstrip() + "…"
        return text
    if isinstance(value, list):
        return [_safe_value(item) for item in value[:_MAX_LIST_ITEMS]]
    if isinstance(value, tuple):
        return [_safe_value(item) for item in value[:_MAX_LIST_ITEMS]]
    if isinstance(value, dict):
        return _safe_metadata(value)
    return str(value)[:_MAX_STRING_CHARS]


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None
