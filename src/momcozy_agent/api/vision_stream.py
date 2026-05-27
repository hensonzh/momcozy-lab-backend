from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from ..services import data_store


router = APIRouter()


VISION_SYSTEM_PROMPT = """
你是一个图片解析助手。从图片中提取每一条与喂养相关的事件，按 NDJSON（Newline-Delimited JSON）输出：
- 每行一个独立 JSON 对象。
- 对象的键固定为：time, event, event_type。
- time: 24 小时制 HH:MM 格式，例如 08:30、14:05。
- event: 简短的中文事件描述（不要超过 20 个字）。
- event_type: 严格三选一：pump（吸奶/吸乳/排奶）、breastfeed（亲喂/母乳亲喂）、custom（其他自定义事件）。

严格要求：
- 只输出 NDJSON，每行一个对象。
- 不要输出任何解释性文字，不要包裹在 ```json ... ``` 代码块里。
- 不要输出 JSON 数组的 `[`、`]`，也不要在对象之间加逗号。
- 一行只允许一个完整对象，不要跨行。
- 没有任何可识别事件时，不输出任何内容。
""".strip()

VISION_USER_PROMPT = "请从这张图片中提取所有的喂养时间事件，按上述 NDJSON 规则输出。"


_TIME_PATTERN = re.compile(r"^\d{1,2}:\d{2}$")
_ALLOWED_EVENT_TYPES = {"pump", "breastfeed", "custom"}
_EVENT_LABEL_MAP = {"pump": "吸奶", "breastfeed": "亲喂", "custom": "自定义"}
_CHINESE_FALLBACK_KEYWORDS: list[tuple[str, str]] = [
    ("吸奶", "pump"),
    ("吸乳", "pump"),
    ("排奶", "pump"),
    ("挤奶", "pump"),
    ("亲喂", "breastfeed"),
    ("母乳亲喂", "breastfeed"),
    ("哺乳", "breastfeed"),
]


@router.websocket("/v1/vision/events/stream")
async def vision_events_stream(websocket: WebSocket) -> None:
    if not _verify_token(websocket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    request_id = uuid.uuid4().hex

    try:
        try:
            raw_first = await websocket.receive_text()
        except WebSocketDisconnect:
            return

        try:
            payload = json.loads(raw_first or "{}")
        except json.JSONDecodeError:
            await _send_error(websocket, "invalid_request", "first frame must be valid JSON")
            return

        if not isinstance(payload, dict):
            await _send_error(websocket, "invalid_request", "first frame must be a JSON object")
            return

        user_id = str(payload.get("user_id") or "").strip()
        file_id = str(payload.get("file_id") or "").strip()
        if not user_id or not file_id:
            await _send_error(websocket, "invalid_request", "user_id and file_id are required")
            return

        metadata = data_store.get_uploaded_file(file_id)
        if not metadata:
            await _send_error(websocket, "file_not_found", f"file not found for file_id={file_id}")
            return

        file_path = Path(str(metadata.get("path") or ""))
        if not file_path.exists() or not file_path.is_file():
            await _send_error(websocket, "file_not_found", f"file is missing on disk for file_id={file_id}")
            return

        try:
            data_url = _build_image_data_url(file_path, metadata)
        except Exception as exc:
            await _send_error(websocket, "internal_error", f"failed to load image: {exc}")
            return

        await websocket.send_json(
            {
                "type": "started",
                "request_id": request_id,
                "user_id": user_id,
                "file_id": file_id,
            }
        )

        try:
            count = await _stream_vision_events(websocket, data_url=data_url)
        except WebSocketDisconnect:
            return
        except _VisionModelError as exc:
            await _send_error(websocket, "model_error", str(exc))
            return
        except Exception as exc:
            await _send_error(websocket, "internal_error", f"unexpected error: {exc}")
            return

        try:
            await websocket.send_json(
                {
                    "type": "done",
                    "count": count,
                    "request_id": request_id,
                }
            )
        except WebSocketDisconnect:
            return
    finally:
        await _safe_close(websocket)


def _verify_token(websocket: WebSocket) -> bool:
    expected = (os.getenv("ENTRY_API_KEY") or "").strip()
    if not expected:
        return False

    query_token = (websocket.query_params.get("token") or "").strip()
    if query_token and query_token == expected:
        return True

    auth_header = websocket.headers.get("authorization") or ""
    if auth_header.startswith("Bearer "):
        header_token = auth_header[7:].strip()
        if header_token and header_token == expected:
            return True

    protocol_header = websocket.headers.get("sec-websocket-protocol") or ""
    for fragment in protocol_header.split(","):
        token = fragment.strip()
        if token and token == expected:
            return True

    return False


def _build_image_data_url(file_path: Path, metadata: dict[str, Any]) -> str:
    mime = str(metadata.get("mime_type") or "").strip()
    if not mime:
        guessed, _ = mimetypes.guess_type(file_path.name)
        mime = guessed or "image/png"
    body = file_path.read_bytes()
    encoded = base64.b64encode(body).decode("ascii")
    return f"data:{mime};base64,{encoded}"


class _VisionModelError(RuntimeError):
    pass


async def _stream_vision_events(websocket: WebSocket, *, data_url: str) -> int:
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:
        raise _VisionModelError("openai SDK is not installed") from exc

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise _VisionModelError("OPENAI_API_KEY is not set")

    model = (os.getenv("MOMCOZY_VISION_MODEL") or "gpt-5.5").strip()
    client = AsyncOpenAI(api_key=api_key)

    try:
        stream = await client.chat.completions.create(
            model=model,
            stream=True,
            messages=[
                {"role": "system", "content": VISION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VISION_USER_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        )
    except Exception as exc:
        raise _VisionModelError(f"failed to call vision model: {exc}") from exc

    buffer = ""
    index = 0

    try:
        async for chunk in stream:
            delta = _extract_delta_content(chunk)
            if not delta:
                continue
            buffer += delta
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                event = _parse_event_line(line)
                if event is None:
                    continue
                event["type"] = "event"
                event["index"] = index
                index += 1
                await websocket.send_json(event)
    except WebSocketDisconnect:
        raise
    except Exception as exc:
        raise _VisionModelError(f"vision stream interrupted: {exc}") from exc
    finally:
        await _safe_close_stream(stream)
        await _safe_close_client(client)

    if buffer.strip():
        event = _parse_event_line(buffer)
        if event is not None:
            event["type"] = "event"
            event["index"] = index
            index += 1
            await websocket.send_json(event)

    return index


def _extract_delta_content(chunk: Any) -> str:
    choices = getattr(chunk, "choices", None) or (chunk.get("choices") if isinstance(chunk, dict) else None)
    if not choices:
        return ""
    first = choices[0]
    delta = getattr(first, "delta", None)
    if delta is None and isinstance(first, dict):
        delta = first.get("delta")
    if delta is None:
        return ""
    content = getattr(delta, "content", None)
    if content is None and isinstance(delta, dict):
        content = delta.get("content")
    if isinstance(content, str):
        return content
    return ""


def _parse_event_line(line: str) -> dict[str, Any] | None:
    text = (line or "").strip()
    if not text:
        return None
    text = text.strip().strip(",").strip()
    if text in {"[", "]", "{", "}"}:
        return None
    if text.startswith("```") or text.endswith("```"):
        return None

    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None

    time_value = str(obj.get("time") or "").strip()
    event_value = str(obj.get("event") or "").strip()
    if not time_value or not event_value:
        return None
    if not _TIME_PATTERN.match(time_value):
        return None
    hour_str, minute_str = time_value.split(":", 1)
    try:
        hour = int(hour_str)
        minute = int(minute_str)
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    normalized_time = f"{hour:02d}:{minute:02d}"

    raw_type = str(obj.get("event_type") or "").strip().lower()
    event_type = raw_type if raw_type in _ALLOWED_EVENT_TYPES else _fallback_event_type(event_value)

    return {
        "time": normalized_time,
        "event": event_value,
        "event_type": event_type,
        "event_label": _EVENT_LABEL_MAP[event_type],
    }


def _fallback_event_type(event_text: str) -> str:
    text = (event_text or "").strip()
    for keyword, mapped in _CHINESE_FALLBACK_KEYWORDS:
        if keyword in text:
            return mapped
    return "custom"


async def _safe_close_stream(stream: Any) -> None:
    close = getattr(stream, "close", None)
    if close is None:
        return
    try:
        result = close()
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        pass


async def _safe_close_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if close is None:
        return
    try:
        result = close()
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        pass


async def _send_error(websocket: WebSocket, code: str, message: str) -> None:
    try:
        await websocket.send_json({"type": "error", "code": code, "message": message})
    except Exception:
        pass


async def _safe_close(websocket: WebSocket) -> None:
    try:
        await websocket.close()
    except Exception:
        pass
