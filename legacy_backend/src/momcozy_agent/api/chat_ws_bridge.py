from __future__ import annotations

import json
import os
import time
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse

from ..agents import run_error_event
from ..services.timing_log import record_ag_ui_timing_event, record_ag_ui_timing_payload


router = APIRouter()


DEFAULT_UPSTREAM_SSE_URL = "http://127.0.0.1:8768/api/ag-ui"
SSE_BOUNDARY_LF = "\n\n"
SSE_BOUNDARY_CRLF = "\r\n\r\n"


@router.post("/api/ag-ui-timing-log")
async def ag_ui_timing_log(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "expected_object"}, status_code=400)
    record_ag_ui_timing_payload(payload)
    return JSONResponse({"ok": True})


@router.websocket("/api/ag-ui-ws")
async def chat_ws_bridge(websocket: WebSocket) -> None:
    if not _verify_token(websocket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    accepted_at = time.perf_counter()
    try:
        try:
            raw_first = await websocket.receive_text()
        except WebSocketDisconnect:
            return

        try:
            payload = json.loads(raw_first or "{}")
        except json.JSONDecodeError:
            await _send_run_error(websocket, "first frame must be valid JSON", "INVALID_REQUEST")
            return
        if not isinstance(payload, dict):
            await _send_run_error(websocket, "first frame must be a JSON object", "INVALID_REQUEST")
            return

        _record_bridge_timing(
            "bridge.ws_first_frame_received",
            payload,
            accepted_at,
            metadata={"raw_len": len(raw_first or "")},
        )
        try:
            await _bridge_sse_to_ws(websocket, payload, accepted_at=accepted_at)
        except WebSocketDisconnect:
            return
        except Exception as exc:
            await _send_run_error(websocket, f"upstream stream error: {exc}", type(exc).__name__)
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


async def _bridge_sse_to_ws(websocket: WebSocket, payload: dict[str, Any], *, accepted_at: float) -> None:
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError(
            "httpx is not installed; install the 'server' optional dependencies"
        ) from exc

    upstream_url = (os.getenv("MOMCOZY_CHAT_SSE_URL") or "").strip() or DEFAULT_UPSTREAM_SSE_URL
    timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)

    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        try:
            _record_bridge_timing(
                "bridge.upstream_request_start",
                payload,
                accepted_at,
                metadata={"upstream_url": upstream_url},
            )
            async with client.stream(
                "POST",
                upstream_url,
                json=payload,
                headers={
                    "Accept": "text/event-stream",
                    "Content-Type": "application/json",
                },
            ) as resp:
                _record_bridge_timing(
                    "bridge.upstream_response_headers",
                    payload,
                    accepted_at,
                    metadata={"status_code": resp.status_code},
                )
                if resp.status_code != 200:
                    body_preview = ""
                    try:
                        body_bytes = await resp.aread()
                        body_preview = body_bytes.decode("utf-8", errors="replace")[:300]
                    except Exception:
                        pass
                    await _send_run_error(
                        websocket,
                        f"upstream returned status {resp.status_code}: {body_preview}".strip(),
                        f"UPSTREAM_{resp.status_code}",
                    )
                    return

                buffer = ""
                first_chunk = True
                first_event = True
                async for chunk in resp.aiter_text():
                    if not chunk:
                        continue
                    if first_chunk:
                        first_chunk = False
                        _record_bridge_timing(
                            "bridge.upstream_first_chunk",
                            payload,
                            accepted_at,
                            metadata={"chunk_len": len(chunk)},
                        )
                    buffer += chunk
                    while True:
                        boundary_idx, boundary_len = _find_boundary(buffer)
                        if boundary_idx == -1:
                            break
                        raw_event = buffer[:boundary_idx]
                        buffer = buffer[boundary_idx + boundary_len:]
                        await _emit_event(websocket, raw_event, payload=payload, accepted_at=accepted_at, first_event=first_event)
                        first_event = False

                if buffer.strip():
                    await _emit_event(websocket, buffer, payload=payload, accepted_at=accepted_at, first_event=first_event)
                _record_bridge_timing("bridge.upstream_stream_end", payload, accepted_at)
        except httpx.HTTPError as exc:
            _record_bridge_timing(
                "bridge.upstream_error",
                payload,
                accepted_at,
                metadata={"error_type": type(exc).__name__},
            )
            await _send_run_error(
                websocket,
                f"upstream connection failed: {exc}",
                type(exc).__name__,
            )


def _find_boundary(buffer: str) -> tuple[int, int]:
    crlf_idx = buffer.find(SSE_BOUNDARY_CRLF)
    lf_idx = buffer.find(SSE_BOUNDARY_LF)
    if crlf_idx == -1 and lf_idx == -1:
        return -1, 0
    if crlf_idx == -1:
        return lf_idx, len(SSE_BOUNDARY_LF)
    if lf_idx == -1:
        return crlf_idx, len(SSE_BOUNDARY_CRLF)
    if crlf_idx <= lf_idx:
        return crlf_idx, len(SSE_BOUNDARY_CRLF)
    return lf_idx, len(SSE_BOUNDARY_LF)


async def _emit_event(
    websocket: WebSocket,
    raw_event: str,
    *,
    payload: dict[str, Any],
    accepted_at: float,
    first_event: bool = False,
) -> None:
    data_lines: list[str] = []
    for line in raw_event.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if not data_lines:
        return
    data_str = "\n".join(data_lines)
    if not data_str:
        return
    try:
        obj = json.loads(data_str)
    except json.JSONDecodeError:
        return
    if first_event:
        _record_bridge_timing(
            "bridge.first_event_to_ws",
            payload,
            accepted_at,
            metadata={"event_type": obj.get("type") if isinstance(obj, dict) else "", "raw_len": len(data_str)},
        )
    await websocket.send_text(json.dumps(obj, ensure_ascii=False))


async def _send_run_error(websocket: WebSocket, message: str, code: str | None = None) -> None:
    payload = run_error_event(message, code)
    try:
        await websocket.send_text(json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass


async def _safe_close(websocket: WebSocket) -> None:
    try:
        await websocket.close()
    except Exception:
        pass


def _record_bridge_timing(
    stage: str,
    payload: dict[str, Any],
    accepted_at: float,
    metadata: dict[str, Any] | None = None,
) -> None:
    record_ag_ui_timing_event(
        stage=stage,
        source="bridge",
        run_id=str(payload.get("runId") or payload.get("run_id") or "").strip(),
        thread_id=str(payload.get("threadId") or payload.get("thread_id") or "").strip(),
        client_timing_id=_payload_context_value(payload, "client_timing_id", "clientTimingId"),
        user_id=_payload_context_value(payload, "user_id", "userId"),
        elapsed_ms=(time.perf_counter() - accepted_at) * 1000,
        metadata=metadata or {},
    )


def _payload_context_value(payload: dict[str, Any], snake_case: str, camel_case: str) -> str:
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    forwarded = payload.get("forwardedProps") if isinstance(payload.get("forwardedProps"), dict) else {}
    value = (
        forwarded.get(snake_case)
        or forwarded.get(camel_case)
        or state.get(snake_case)
        or state.get(camel_case)
        or payload.get(snake_case)
        or payload.get(camel_case)
    )
    return str(value or "").strip()
