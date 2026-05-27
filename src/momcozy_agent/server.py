import asyncio
import json
import os
import sys
import threading
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .agents import QUICK_REPLIES_TOOL_NAME, quick_replies_event, run_agent_loop, run_error_event, text_message_semantic
from .config import get_openai_client_options, load_project_env
from .contexts import DEFAULT_LOCALE, DEFAULT_TIMEZONE, ContextState
from .services.paths import ensure_runtime_dirs
from .types import SkillId

ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = ROOT / "web"
SKILLS_ROOT = ROOT / "skills"
HOST = "127.0.0.1"
PORT = 8768
MAX_IMAGE_ATTACHMENTS = 4
STREAM_TIMING_ENV = "MOMCOZY_DEBUG_STREAM_TIMING"
DEFAULT_QUICK_REPLIES: tuple[dict[str, str], ...] = (
    {"text": "继续这个问题", "send_text": "继续这个问题"},
    {"text": "换个说法", "send_text": "请换个说法再解释一遍"},
    {"text": "我想问别的", "send_text": "我想问另一个问题"},
)
STATIC_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".gif": "image/gif",
    ".html": "text/html; charset=utf-8",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".js": "text/javascript; charset=utf-8",
    ".mp4": "video/mp4",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}


@dataclass
class ChatSession:
    conversation_id: str
    previous_response_id: str | None = None
    loaded_skill_ids: list[SkillId] = field(default_factory=list)
    context_state: ContextState = field(default_factory=ContextState)


class ChatRuntime:
    def __init__(self, client: Any, *, model: str = "gpt-5.5", store: bool = True) -> None:
        self.client = client
        self.model = model
        self.store = store
        self.sessions: dict[str, ChatSession] = {}

    def get_session(self, conversation_id: str | None) -> ChatSession:
        if conversation_id and conversation_id in self.sessions:
            return self.sessions[conversation_id]

        new_id = conversation_id or str(uuid.uuid4())
        session = ChatSession(conversation_id=new_id)
        self.sessions[new_id] = session
        return session


def make_runtime() -> ChatRuntime:
    load_project_env(ROOT / ".env")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("The OpenAI SDK is not installed. Install it with: python3 -m pip install openai") from exc

    return ChatRuntime(OpenAI(**get_openai_client_options()))


def create_app(runtime: ChatRuntime | None = None, *, include_websocket_bridge: bool = False) -> Any:
    try:
        from fastapi import FastAPI, Request
        from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:
        raise RuntimeError("FastAPI is not installed. Install the 'server' optional dependencies.") from exc

    load_project_env(ROOT / ".env")
    ensure_runtime_dirs()

    app = FastAPI(title="Momcozy Agent API")
    app.state.runtime = runtime
    app.state.runtime_lock = threading.Lock()

    @app.post("/api/ag-ui")
    async def ag_ui_stream(request: Request) -> Any:
        try:
            payload = await _read_json_payload(request)
            inputs = _runtime_inputs_from_ag_ui(payload)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        stream = stream_ag_ui_events(payload, inputs, runtime_from_app(request.app))
        return StreamingResponse(
            stream_sse_bytes(stream),
            media_type="text/event-stream; charset=utf-8",
            headers={"Cache-Control": "no-cache", "Connection": "close"},
        )

    @app.post("/api/support-ticket-submit")
    async def support_ticket_submit(request: Request) -> Any:
        try:
            payload = await _read_json_payload(request)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        ticket = payload.get("ticket") if isinstance(payload, dict) else None
        if not isinstance(ticket, dict):
            return JSONResponse({"error": "support ticket submit requires a ticket object."}, status_code=400)
        return JSONResponse(_submit_support_ticket(ticket))

    @app.post("/api/client-event")
    async def client_event(request: Request) -> Any:
        try:
            payload = await _read_json_payload(request)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"error": "client event requires a JSON object."}, status_code=400)

        thread_id = str(_field(payload, "thread_id", "threadId") or payload.get("conversation_id") or "").strip()
        if not thread_id:
            return JSONResponse({"error": "client event requires thread_id."}, status_code=400)

        event = _format_client_event(payload)
        session = runtime_from_app(request.app).get_session(thread_id)
        if event not in session.context_state.client_events:
            session.context_state.client_events.append(event)
            session.context_state.client_events = session.context_state.client_events[-10:]

        return JSONResponse(
            {
                "status": "recorded",
                "conversation_id": session.conversation_id,
                "consult_id": _client_event_consult_id(payload),
                "event": event,
                "session_state": _session_state_payload(session),
            }
        )

    @app.api_route("/skill-assets/{skill_id}/{asset_path:path}", methods=["GET", "HEAD"])
    async def skill_asset(skill_id: str, asset_path: str) -> Any:
        asset_full = (SKILLS_ROOT / skill_id / "assets" / asset_path).resolve()
        skill_assets_root = (SKILLS_ROOT / skill_id / "assets").resolve()
        if skill_assets_root not in asset_full.parents or not asset_full.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        content_type = STATIC_CONTENT_TYPES.get(asset_full.suffix.lower())
        if content_type is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(asset_full, media_type=content_type)

    @app.get("/")
    async def web_index() -> Any:
        return _web_file_response(WEB_ROOT / "index.html")

    @app.get("/app.js")
    async def web_app_js() -> Any:
        return _web_file_response(WEB_ROOT / "app.js")

    @app.get("/styles.css")
    async def web_styles_css() -> Any:
        return _web_file_response(WEB_ROOT / "styles.css")

    if include_websocket_bridge:
        from .api.chat_ws_bridge import router as chat_ws_router

        app.include_router(chat_ws_router)

    if WEB_ROOT.exists():
        app.mount("/", StaticFiles(directory=str(WEB_ROOT), html=True), name="web")

    return app


def _web_file_response(path: Path) -> Any:
    from fastapi.responses import FileResponse, JSONResponse

    if not path.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(
        path,
        media_type=STATIC_CONTENT_TYPES.get(path.suffix.lower()),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


def runtime_from_app(app: Any) -> ChatRuntime:
    runtime = getattr(app.state, "runtime", None)
    if isinstance(runtime, ChatRuntime):
        return runtime

    runtime_lock = getattr(app.state, "runtime_lock", None)
    if runtime_lock is None:
        runtime = make_runtime()
        app.state.runtime = runtime
        return runtime

    with runtime_lock:
        runtime = getattr(app.state, "runtime", None)
        if isinstance(runtime, ChatRuntime):
            return runtime
        runtime = make_runtime()
        app.state.runtime = runtime
        return runtime


async def stream_ag_ui_events(
    payload: dict[str, Any],
    inputs: dict[str, Any],
    runtime: ChatRuntime,
) -> AsyncIterator[dict[str, Any]]:
    thread_id = _field(payload, "thread_id", "threadId") or f"thread_{payload.get('conversation_id', 'anonymous')}"
    run_id = _field(payload, "run_id", "runId") or f"run_{date.today().isoformat()}"
    parent_run_id = _field(payload, "parent_run_id", "parentRunId")
    assistant_message_id = f"{run_id}:assistant"

    session = runtime.get_session(str(thread_id))
    if session.previous_response_id and "previous_response_id" not in inputs:
        inputs["previous_response_id"] = session.previous_response_id

    sentinel = object()
    output_queue: asyncio.Queue[Any] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    stream_started_at = time.perf_counter()
    debug_stream_timing = _debug_stream_timing_enabled()

    def push(item: Any) -> None:
        loop.call_soon_threadsafe(output_queue.put_nowait, item)

    def log_timing(label: str, metadata: dict[str, Any] | None = None) -> None:
        if not debug_stream_timing:
            return
        elapsed_ms = (time.perf_counter() - stream_started_at) * 1000
        details = _format_timing_metadata(metadata)
        print(f"[momcozy.stream] +{elapsed_ms:7.1f}ms {run_id} {label}{details}", file=sys.stderr, flush=True)

    def worker() -> None:
        pending_run_finished: dict[str, Any] | None = None
        pending_assistant_followups: list[str] = []
        pending_quick_replies: list[dict[str, str]] | None = None
        text_started = False
        streamed_text_parts: list[str] = []

        def send_event(event: dict[str, Any]) -> None:
            log_timing(f"sse:{event.get('type', 'unknown')}", _ag_ui_timing_metadata(event))
            push(event)

        def send_ag_ui_event(event: dict[str, Any]) -> None:
            nonlocal pending_run_finished, pending_quick_replies
            if event.get("type") == "RUN_FINISHED":
                log_timing("ag_ui:RUN_FINISHED buffered", _ag_ui_timing_metadata(event))
                pending_run_finished = event
                return
            quick_replies = _quick_replies_from_tool_result_event(event)
            if quick_replies is not None:
                pending_quick_replies = quick_replies
            if _is_quick_replies_tool_event(event):
                log_timing("ag_ui:quick_replies_tool_event hidden", _ag_ui_timing_metadata(event))
                return
            followup = _assistant_followup_from_tool_result_event(event)
            if followup and followup not in pending_assistant_followups:
                pending_assistant_followups.append(followup)
            send_event(event)

        def send_text_delta(delta: str) -> None:
            nonlocal text_started
            if not text_started:
                send_event(
                    {
                        "type": "TEXT_MESSAGE_START",
                        "message_id": assistant_message_id,
                        "role": "assistant",
                        "semantic": text_message_semantic("start", assistant_message_id),
                    }
                )
                text_started = True
            streamed_text_parts.append(delta)
            send_event(
                {
                    "type": "TEXT_MESSAGE_CONTENT",
                    "message_id": assistant_message_id,
                    "delta": delta,
                    "semantic": text_message_semantic("content", assistant_message_id),
                }
            )

        response_stream_timing = (
            lambda event_type, metadata: log_timing(f"responses:{event_type}", metadata)
        ) if debug_stream_timing else None

        try:
            agent_options: dict[str, Any] = {
                "model": runtime.model,
                "store": runtime.store,
                "loaded_skill_ids": session.loaded_skill_ids,
                "context_state": session.context_state,
            }
            response = run_agent_loop(
                runtime.client,
                inputs,
                agent_options,
                on_ag_ui_event=send_ag_ui_event,
                ag_ui_thread_id=str(thread_id),
                ag_ui_run_id=str(run_id),
                ag_ui_parent_run_id=str(parent_run_id) if parent_run_id else None,
                on_text_delta=send_text_delta,
                on_response_stream_event=response_stream_timing,
            )
            response_id = _response_id(response)
            if response_id:
                session.previous_response_id = response_id
            loaded_skill_ids = agent_options.get("loaded_skill_ids")
            if isinstance(loaded_skill_ids, list):
                session.loaded_skill_ids = loaded_skill_ids
            text = _response_text(response)
            if not streamed_text_parts and text:
                send_text_delta(text)
            current_text = "".join(streamed_text_parts) or text
            for followup in pending_assistant_followups:
                if _should_send_assistant_followup(followup, current_text):
                    send_text_delta(f"\n\n{followup}")
                    current_text = f"{current_text}\n\n{followup}"
            if text_started:
                send_event(
                    {
                        "type": "TEXT_MESSAGE_END",
                        "message_id": assistant_message_id,
                        "semantic": text_message_semantic("end", assistant_message_id),
                    }
                )
            if pending_run_finished:
                send_event(quick_replies_event(assistant_message_id, pending_quick_replies or _default_quick_replies()))
                send_event(pending_run_finished)
        except Exception as exc:
            send_event(run_error_event(str(exc), type(exc).__name__, thread_id=str(thread_id), run_id=str(run_id)))
        finally:
            push(sentinel)

    threading.Thread(target=worker, daemon=True).start()

    while True:
        item = await output_queue.get()
        if item is sentinel:
            break
        yield item


async def stream_sse_bytes(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[bytes]:
    async for event in events:
        yield _sse_format(event)


def _sse_format(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")


def main() -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("uvicorn is not installed. Install the 'server' optional dependencies.") from exc

    host = os.getenv("CHAT_HOST", "127.0.0.1")
    port = int(os.getenv("CHAT_PORT", "8768"))

    application = create_app(runtime=make_runtime())
    print(f"Momcozy agent test UI: http://{host}:{port}")
    uvicorn.run(application, host=host, port=port, log_level="warning")


async def _read_json_payload(request: Any) -> dict[str, Any]:
    raw = await request.body()
    if not raw:
        return {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"request body must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object.")
    return payload


def _debug_stream_timing_enabled() -> bool:
    return os.environ.get(STREAM_TIMING_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _format_timing_metadata(metadata: dict[str, Any] | None) -> str:
    if not metadata:
        return ""
    parts = []
    for key in sorted(metadata):
        value = metadata[key]
        if value is None:
            continue
        if not isinstance(value, (str, int, float, bool)):
            continue
        value_text = str(value).replace("\n", " ")[:120]
        parts.append(f"{key}={value_text}")
    return f" {' '.join(parts)}" if parts else ""


def _ag_ui_timing_metadata(event: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    event_type = event.get("type")
    if isinstance(event_type, str):
        metadata["type"] = event_type
    if event_type == "TEXT_MESSAGE_CONTENT":
        delta = event.get("delta")
        if isinstance(delta, str):
            metadata["delta_len"] = len(delta)
    if isinstance(event.get("name"), str):
        metadata["name"] = event["name"]
    if isinstance(event.get("tool_call_name"), str):
        metadata["tool_call_name"] = event["tool_call_name"]
    if isinstance(event.get("tool_call_id"), str):
        metadata["tool_call_id"] = event["tool_call_id"]
    content = event.get("content") if isinstance(event.get("content"), dict) else {}
    if isinstance(content.get("phase"), str):
        metadata["phase"] = content["phase"]
    value = event.get("value") if isinstance(event.get("value"), dict) else {}
    if isinstance(value.get("phase"), str):
        metadata["phase"] = value["phase"]
    if isinstance(value.get("status"), str):
        metadata["status"] = value["status"]
    value_metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
    if isinstance(value_metadata.get("source_event"), str):
        metadata["source_event"] = value_metadata["source_event"]
    if isinstance(value_metadata.get("after_output_text"), bool):
        metadata["after_output_text"] = value_metadata["after_output_text"]
    return metadata


def _runtime_inputs_from_ag_ui(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("AG-UI input requires a JSON object.")

    message = _latest_user_message(payload.get("messages", []))
    images = _latest_user_images(payload.get("messages", []))
    if not message:
        message = str(payload.get("message", "")).strip()
    if not message and images:
        message = "请根据我发送的图片提供帮助。"
    if not message:
        raise ValueError("AG-UI input requires a user message.")

    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    forwarded_props = _field(payload, "forwarded_props", "forwardedProps")
    if not isinstance(forwarded_props, dict):
        forwarded_props = {}

    user_id = _string_context_value(payload, state, forwarded_props, "user_id", "userId")
    timezone = forwarded_props.get("timezone") or state.get("timezone") or payload.get("timezone") or DEFAULT_TIMEZONE
    message_sent_at = (
        forwarded_props.get("message_sent_at")
        or state.get("message_sent_at")
        or payload.get("message_sent_at")
        or _now_in_timezone(str(timezone)).isoformat(timespec="seconds")
    )

    inputs: dict[str, Any] = {
        "user_message": message,
        "locale": forwarded_props.get("locale") or state.get("locale") or payload.get("locale", DEFAULT_LOCALE),
        "timezone": timezone,
        "message_sent_at": message_sent_at,
    }
    if user_id:
        inputs["user_id"] = user_id
    if images:
        inputs["images"] = images

    user_profile = _context_value(state, forwarded_props, "user_profile")
    if isinstance(user_profile, dict):
        inputs["user_profile"] = dict(user_profile)
    elif user_id:
        inputs["user_profile"] = {"user_id": user_id}
    if user_id and isinstance(inputs.get("user_profile"), dict):
        inputs["user_profile"].setdefault("user_id", user_id)

    for key in ("baby_profile", "service_state", "retrieved_records", "retrieved_knowledge", "hospital_bag_cart"):
        value = _context_value(state, forwarded_props, key)
        if value is not None:
            inputs[key] = value

    previous_response_id = forwarded_props.get("previous_response_id") or state.get("previous_response_id")
    if previous_response_id:
        inputs["previous_response_id"] = previous_response_id

    return inputs


def _assistant_followup_from_tool_result_event(event: dict[str, Any]) -> str | None:
    if event.get("type") != "TOOL_CALL_RESULT":
        return None
    content = event.get("content")
    if not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    followup = payload.get("assistant_followup")
    if not isinstance(followup, dict):
        return None
    message = followup.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return None


def _is_quick_replies_tool_event(event: dict[str, Any]) -> bool:
    if str(event.get("tool_call_name") or "") == QUICK_REPLIES_TOOL_NAME:
        return True
    if event.get("type") == "CUSTOM":
        value = event.get("value")
        if isinstance(value, dict):
            metadata = value.get("metadata")
            return isinstance(metadata, dict) and str(metadata.get("tool_name") or "") == QUICK_REPLIES_TOOL_NAME
    return False


def _quick_replies_from_tool_result_event(event: dict[str, Any]) -> list[dict[str, str]] | None:
    if event.get("type") != "TOOL_CALL_RESULT" or not _is_quick_replies_tool_event(event):
        return None
    content = event.get("content")
    if not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    replies = payload.get("quick_replies")
    if not isinstance(replies, list) or len(replies) != 3:
        return None
    normalized: list[dict[str, str]] = []
    for item in replies:
        if not isinstance(item, dict):
            return None
        text = str(item.get("text") or "").strip()
        send_text = str(item.get("send_text") or text).strip()
        if not text or not send_text:
            return None
        normalized.append({"text": text, "send_text": send_text})
    return normalized


def _default_quick_replies() -> list[dict[str, str]]:
    return [dict(item) for item in DEFAULT_QUICK_REPLIES]


def _submit_support_ticket(ticket: dict[str, Any]) -> dict[str, Any]:
    ticket_id = f"mock_ticket_{uuid.uuid4().hex[:8]}"
    return {
        "status": "mock_submitted",
        "ticket_id": ticket_id,
        "side_effect_performed": False,
        "mock": True,
        "message": "工单已提交，人工客服会在 24 小时内联系你解决问题。",
        "ticket": ticket,
    }


def _format_client_event(payload: dict[str, Any]) -> str:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    context_text = payload.get("context_text") or metadata.get("context_text")
    if context_text:
        return str(context_text).strip()
    event_type = str(payload.get("event_type") or payload.get("type") or "client_event").strip()
    label = str(payload.get("label") or event_type).strip()
    occurred_at = str(payload.get("occurred_at") or payload.get("message_sent_at") or "").strip()
    detail_parts = []
    for key in ("user_id", "consultant_name", "consultant_credentials", "source", "consult_id"):
        value = metadata.get(key) or payload.get(key)
        if value:
            detail_parts.append(f"{key}: {value}")
    details = f" ({'; '.join(detail_parts)})" if detail_parts else ""
    time_prefix = f"{occurred_at}: " if occurred_at else ""
    return f"{time_prefix}{label} [event_type: {event_type}]{details}"


def _client_event_consult_id(payload: dict[str, Any]) -> str:
    value = payload.get("consult_id") or payload.get("consultId")
    if not value and isinstance(payload.get("metadata"), dict):
        value = payload["metadata"].get("consult_id") or payload["metadata"].get("consultId")
    return str(value or "").strip()


def _session_state_payload(session: ChatSession) -> dict[str, Any]:
    return {
        "conversation_id": session.conversation_id,
        "previous_response_id": session.previous_response_id,
        "loaded_skill_ids": list(session.loaded_skill_ids),
        "context_state": {
            "environment_sent": session.context_state.environment_sent,
            "loaded_references": list(session.context_state.loaded_references),
            "client_events": list(session.context_state.client_events),
            "available_tool_images": list(session.context_state.available_tool_images),
            "last_displayed_tool_image": dict(session.context_state.last_displayed_tool_image or {}),
            "active_device_module": session.context_state.active_device_module,
            "shown_step_image_urls": list(session.context_state.shown_step_image_urls),
        },
    }


def _should_send_assistant_followup(followup: str, current_text: str) -> bool:
    if not followup.strip():
        return False
    if followup in current_text:
        return False
    if "sea.momcozy.com" in followup and "sea.momcozy.com" in current_text:
        return False
    if "/hospital-bag-cart" in followup and "/hospital-bag-cart" in current_text:
        return False
    return True


def _latest_user_message(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(parts).strip()
    return ""


def _latest_user_images(messages: Any) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        return []
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content", [])
        if not isinstance(content, list):
            return []
        images: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict) or item.get("type") not in {"image", "input_image"}:
                continue
            image_url = item.get("image_url") or item.get("url") or item.get("data_url")
            if not isinstance(image_url, str) or not _is_supported_image_url(image_url):
                continue
            image: dict[str, Any] = {"image_url": image_url, "detail": _image_detail(item.get("detail"))}
            for key in ("mime_type", "name", "size"):
                if key in item:
                    image[key] = item[key]
            images.append(image)
            if len(images) >= MAX_IMAGE_ATTACHMENTS:
                break
        return images
    return []


def _is_supported_image_url(value: str) -> bool:
    return value.startswith("data:image/") or value.startswith("https://") or value.startswith("http://")


def _image_detail(value: Any) -> str:
    return str(value) if value in {"low", "high", "auto"} else "auto"


def _now_in_timezone(timezone: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(timezone))
    except ZoneInfoNotFoundError:
        return datetime.now(ZoneInfo(DEFAULT_TIMEZONE))


def _field(payload: dict[str, Any], snake_case: str, camel_case: str) -> Any:
    if snake_case in payload:
        return payload[snake_case]
    return payload.get(camel_case)


def _context_value(state: dict[str, Any], forwarded_props: dict[str, Any], key: str) -> Any:
    if key in state:
        return state[key]
    if key in forwarded_props:
        return forwarded_props[key]
    return None


def _string_context_value(
    payload: dict[str, Any],
    state: dict[str, Any],
    forwarded_props: dict[str, Any],
    snake_case: str,
    camel_case: str,
) -> str:
    value = (
        _field(forwarded_props, snake_case, camel_case)
        or _field(state, snake_case, camel_case)
        or _field(payload, snake_case, camel_case)
    )
    return str(value or "").strip()


def _response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text

    parts: list[str] = []
    for item in _output_items(response):
        if _get(item, "type") != "message":
            continue
        for content in _get(item, "content", []):
            if _get(content, "type") == "output_text":
                text = _get(content, "text")
                if text:
                    parts.append(text)
    return "\n".join(parts).strip()


def _response_id(response: Any) -> str | None:
    if isinstance(response, dict):
        response_id = response.get("id")
    else:
        response_id = getattr(response, "id", None)
    return response_id if isinstance(response_id, str) else None


def _output_items(response: Any) -> list[Any]:
    if isinstance(response, dict):
        return response.get("output", [])
    return getattr(response, "output", []) or []


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)
