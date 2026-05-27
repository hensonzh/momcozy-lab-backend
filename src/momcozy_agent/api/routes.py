from __future__ import annotations

import base64
import asyncio
import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse

from .auth import verify_api_key
from .responses import (
    basic_sync_response,
    pump_health_response,
    pump_reply_response,
    pump_threshold_response,
)
from ..services import data_store
from ..services.milk_process.breast_pump_FSM_v3 import LogDrivenSessionManager
from ..services.milk_management.daily_summary import create_daily_summary
from ..services.milk_management.feeding import assess_feeding_demand_reference
from ..services.milk_management.status_advice import evaluate_status_advice_normality, generate_status_advice
from ..services.paths import MILK_PROCESS_CONFIG_ROOT, MILK_PROCESS_LOG_ROOT, UPLOAD_ROOT, ensure_runtime_dirs
from ..services.pump_session_summary import build_pump_session_summary
from ..services.pump_process import (
    PROCESS_ENERGY_LOWER_VALUE,
    PROCESS_ENERGY_UPPER_VALUE,
    PROCESS_REST_THRESHOLD,
    append_pump_process_points,
    build_pump_process_reply,
    get_pump_process_reply_state,
    validate_pump_process_payload,
)
from ..services.pump_workstate import (
    build_pump_workstate_reply,
    get_latest_pump_workstate_event,
    get_pump_workstate_reply_state,
    record_pump_workstate_update,
    validate_pump_workstate_payload,
)
from ..tool_handlers.cards import update_hospital_bag_cart as execute_hospital_bag_cart_update


router = APIRouter()
_pump_process_data_lock = asyncio.Lock()
_pump_process_data_session_manager = LogDrivenSessionManager(
    json_dir=MILK_PROCESS_CONFIG_ROOT,
    log_dir=MILK_PROCESS_LOG_ROOT,
)


async def _upload_image_to_openai(*, filename: str, body: bytes, mime_type: str) -> str:
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:
        raise HTTPException(
            status_code=501,
            detail={"code": "openai_sdk_missing", "message": "OpenAI SDK is not installed", "status": 501},
        ) from exc

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail={"code": "openai_config_missing", "message": "OPENAI_API_KEY is not set", "status": 400},
        )

    try:
        async with AsyncOpenAI(api_key=api_key) as client:
            uploaded = await client.files.create(
                file=(filename, BytesIO(body), mime_type),
                purpose="vision",
            )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "openai_file_upload_failed", "message": f"failed to upload file to OpenAI: {exc}", "status": 502},
        ) from exc

    file_id = str(getattr(uploaded, "id", "") or "").strip()
    if not file_id:
        raise HTTPException(
            status_code=502,
            detail={"code": "openai_file_upload_failed", "message": "OpenAI file upload did not return a file id", "status": 502},
        )
    return file_id


@router.post("/v1/device/info")
async def upload_device_info(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    body = await _json_body_or_error(request, basic=True)
    if not isinstance(body, dict):
        return basic_sync_response(status=400, message="invalid request body", error=-1)
    try:
        user_id, left, right = _validate_device_info_payload(body)
        data_store.save_device_info(user_id, left, right, body)
    except ValueError as exc:
        return basic_sync_response(status=400, message=str(exc), error=-1)
    except Exception:
        return basic_sync_response(status=400, message="failed to save device info", error=-1)
    return basic_sync_response(status=200, message="success", error=0)


@router.post("/v1/pump/workstate")
async def upload_pump_workstate(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    body = await _json_body_or_error(request, pump=True)
    if not isinstance(body, dict):
        return pump_reply_response(status=400, message="invalid request body", error=-1)
    try:
        is_valid, error_message, normalized = validate_pump_workstate_payload(body)
        if not is_valid or normalized is None:
            return pump_reply_response(status=400, message=error_message, error=-1)
        previous_event = get_latest_pump_workstate_event(normalized["user_id"])
        previous_reply_state = get_pump_workstate_reply_state(normalized["user_id"])
        reply = build_pump_workstate_reply(
            current_payload=normalized,
            previous_event=previous_event,
            previous_reply_state=previous_reply_state,
        )
        record_pump_workstate_update(
            normalized,
            reply_state=reply.get("updated_reply_state") if isinstance(reply.get("updated_reply_state"), dict) else {},
            job_operation=reply.get("job_operation") if isinstance(reply.get("job_operation"), dict) else None,
        )
    except ValueError as exc:
        return pump_reply_response(status=400, message=str(exc), error=-1)
    except Exception:
        return pump_reply_response(status=400, message="failed to process workstate", error=-1)
    return pump_reply_response(
        status=200,
        message="success",
        error=0,
        need_reply=bool(reply.get("need_reply")),
        output=str(reply.get("output", "") or ""),
        reply_code=str(reply.get("reply_code", "") or ""),
        reply_side=str(reply.get("reply_side", "") or ""),
        direct_rich_text=reply.get("direct_rich_text") if isinstance(reply.get("direct_rich_text"), dict) else None,
    )


@router.post("/v1/pump/workstate/pending-replies")
async def pull_pump_workstate_pending_replies(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    body = await _json_body_or_error(request, basic=True)
    if not isinstance(body, dict):
        return {"status": 400, "message": "invalid request body", "data": {"error": -1, "replies": []}}
    user_id = str(body.get("user_id") or "").strip()
    if not user_id:
        return {"status": 400, "message": "user_id is required", "data": {"error": -1, "replies": []}}
    try:
        limit = max(1, min(int(body.get("limit", 10) or 10), 20))
    except Exception:
        limit = 10
    try:
        replies = data_store.pull_pending_replies(user_id, limit=limit)
    except Exception:
        return {"status": 400, "message": "failed to pull pending replies", "data": {"error": -1, "replies": []}}
    return {"status": 200, "message": "success", "data": {"error": 0, "replies": replies}}


@router.post("/v1/pump/process")
async def upload_pump_process(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    body = await _json_body_or_error(request, pump=True)
    if not isinstance(body, dict):
        return pump_reply_response(status=400, message="invalid request body", error=-1)
    try:
        is_valid, error_message, normalized = validate_pump_process_payload(body)
        if not is_valid or normalized is None:
            return pump_reply_response(status=400, message=error_message, error=-1)
        previous_reply_state = get_pump_process_reply_state(normalized["user_id"])
        latest_workstate = get_latest_pump_workstate_event(normalized["user_id"])
        reply = build_pump_process_reply(
            current_payload=normalized,
            previous_reply_state=previous_reply_state,
            current_workstate_event=latest_workstate,
        )
        append_pump_process_points(
            normalized,
            reply_state=reply.get("updated_reply_state") if isinstance(reply.get("updated_reply_state"), dict) else {},
        )
    except ValueError as exc:
        return pump_reply_response(status=400, message=str(exc), error=-1)
    except Exception:
        return pump_reply_response(status=400, message="failed to process pump data", error=-1)
    return pump_reply_response(
        status=200,
        message="success",
        error=0,
        need_reply=bool(reply.get("need_reply")),
        output=str(reply.get("output", "") or ""),
        reply_code=str(reply.get("reply_code", "") or ""),
        reply_side=str(reply.get("reply_side", "") or ""),
        direct_rich_text=reply.get("direct_rich_text") if isinstance(reply.get("direct_rich_text"), dict) else None,
    )


@router.post("/v1/pump/process/data")
async def calculate_pump_process_data(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    try:
        body = await request.json()
    except Exception:
        return _pump_process_data_response(status=400, message="invalid request body", error=-1, text="invalid request body")
    try:
        _validate_process_data_payload(body)
        async with _pump_process_data_lock:
            result = await _pump_process_data_session_manager.process(body)
        if int(result.get("error", 0) or 0) == -1:
            text = str(result.get("text", "") or "")
            return _pump_process_data_response(status=400, message=text, error=-1, text=text)
        process_l = int(result.get("process_l", 0) or 0)
        process_r = int(result.get("process_r", 0) or 0)
        process_all = int(result.get("process_all", 0) or 0)
    except ValueError as exc:
        return _pump_process_data_response(status=400, message=str(exc), error=-1, text=str(exc))
    except Exception:
        return _pump_process_data_response(status=400, message="failed to calculate pump process", error=-1, text="failed to calculate pump process")
    return _pump_process_data_response(status=200, message="success", error=0, process_l=process_l, process_r=process_r, process_all=process_all)


@router.websocket("/v1/pump/session-summary")
async def create_pump_session_summary_ws(websocket: WebSocket) -> None:
    if not _verify_websocket_api_key(websocket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    try:
        try:
            raw_first = await websocket.receive_text()
        except WebSocketDisconnect:
            return

        try:
            body = json.loads(raw_first or "{}")
        except json.JSONDecodeError:
            await websocket.send_json(_pump_session_summary_error("invalid request body"))
            return

        response = await _create_pump_session_summary_response(websocket, body)
        await websocket.send_json(response)
    except WebSocketDisconnect:
        return
    finally:
        await _safe_close_websocket(websocket)


@router.post("/v1/pump/threshold/upload")
async def upload_pump_threshold(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    body = await _json_body_or_error(request, basic=True)
    if not isinstance(body, dict):
        return pump_threshold_response(status=400, message="invalid request body", error=-1)
    user_id = str(body.get("user_id") or "").strip()
    if not user_id:
        return pump_threshold_response(status=400, message="user_id is required", error=-1)
    try:
        values = {name: _positive_int(body.get(name), name) for name in ("stimulate_level_l", "deep_level_l", "stimulate_level_r", "deep_level_r")}
        data_store.upsert_pump_threshold(user_id, values)
    except ValueError as exc:
        return pump_threshold_response(status=400, message=str(exc), error=-1)
    except Exception:
        return pump_threshold_response(status=400, message="failed to save pump threshold", error=-1)
    return pump_threshold_response(status=200, message="success", error=0)


@router.get("/v1/pump/threshold/get")
async def get_pump_threshold(request: Request, user_id: str = "") -> dict[str, Any]:
    verify_api_key(request)
    uid = str(user_id or "").strip()
    if not uid:
        return pump_threshold_response(status=400, message="user_id is required", error=-1)
    threshold = data_store.get_pump_threshold(uid)
    if not isinstance(threshold, dict):
        return pump_threshold_response(status=400, message="pump threshold not found", error=-1)
    return pump_threshold_response(
        status=200,
        message="success",
        error=0,
        stimulate_level_l=int(threshold["stimulate_level_l"]),
        deep_level_l=int(threshold["deep_level_l"]),
        stimulate_level_r=int(threshold["stimulate_level_r"]),
        deep_level_r=int(threshold["deep_level_r"]),
    )


@router.get("/v1/pump/energy/get")
async def get_pump_energy_target(request: Request, user_id: str = "") -> dict[str, Any]:
    verify_api_key(request)
    if not str(user_id or "").strip():
        return {"status": 400, "message": "user_id is required", "data": {"error": -1, "upper_value": PROCESS_ENERGY_UPPER_VALUE, "lower_value": PROCESS_ENERGY_LOWER_VALUE}}
    return {"status": 200, "message": "success", "data": {"error": 0, "upper_value": PROCESS_ENERGY_UPPER_VALUE, "lower_value": PROCESS_ENERGY_LOWER_VALUE}}


@router.post("/v1/pump/health/upload")
async def upload_pump_health(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    try:
        body = await request.json()
        user_id = str(body.get("user_id") or "").strip()
        if not user_id:
            return pump_health_response(error=-1)
        health_l = _enum_int(body.get("health_l"), "health_l", {0, 1, 2})
        health_r = _enum_int(body.get("health_r"), "health_r", {0, 1, 2})
        data_store.upsert_pump_health(user_id, health_l, health_r)
    except Exception:
        return pump_health_response(error=-1)
    return pump_health_response(error=0)


@router.get("/v1/pump/health/get")
async def get_pump_health_endpoint(request: Request, user_id: str = "") -> dict[str, Any]:
    verify_api_key(request)
    uid = str(user_id or "").strip()
    if not uid:
        return pump_health_response(error=-1)
    result = data_store.get_pump_health(uid)
    if not isinstance(result, dict):
        return pump_health_response(error=-1)
    return pump_health_response(error=0, health_l=result.get("health_l"), health_r=result.get("health_r"))


@router.post("/v1/files/upload")
async def upload_file(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    verify_api_key(request)
    ensure_runtime_dirs()
    filename = file.filename or "upload"
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    allowed_extensions = {"png", "jpeg", "jpg", "webp", "gif"}
    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_file_type", "message": f"Only {', '.join(sorted(allowed_extensions))} files are allowed", "status": 400},
        )
    body = await file.read()
    mime_type = file.content_type or f"image/{extension}"
    file_id = await _upload_image_to_openai(filename=filename, body=body, mime_type=mime_type)
    target = UPLOAD_ROOT / f"{file_id}.{extension}"
    target.write_bytes(body)
    created_at = int(time.time())
    metadata = {
        "id": file_id,
        "name": filename,
        "size": len(body),
        "extension": extension,
        "mime_type": mime_type,
        "created_by": 123,
        "created_at": created_at,
        "path": str(target),
    }
    data_store.save_uploaded_file(metadata)
    return {key: metadata[key] for key in ("id", "name", "size", "extension", "mime_type", "created_by", "created_at")}


@router.post("/v1/tts")
async def tts_endpoint(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    body = await request.json()
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail={"code": "invalid_text", "message": "text is required", "status": 400})
    if not _xfyun_tts_configured():
        raise HTTPException(status_code=400, detail={"code": "tts_config_missing", "message": "XFYUN_TTS_APP_ID/API_KEY/API_SECRET not configured", "status": 400})
    raise HTTPException(status_code=501, detail={"code": "tts_adapter_missing", "message": "TTS adapter package is not installed in momcozy-agent.", "status": 501})


@router.get("/v1/tts-stream")
async def tts_stream_endpoint(request: Request, text: str) -> StreamingResponse:
    verify_api_key(request)
    if not str(text or "").strip():
        raise HTTPException(status_code=400, detail={"code": "invalid_text", "message": "text is required", "status": 400})
    if not _xfyun_tts_configured():
        raise HTTPException(status_code=400, detail={"code": "tts_config_missing", "message": "XFYUN_TTS_APP_ID/API_KEY/API_SECRET not configured", "status": 400})
    return StreamingResponse(iter(()), media_type="audio/mpeg")


@router.post("/v1/asr")
async def asr_endpoint(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    body = await request.json()
    input_text = body.get("text")
    if input_text is not None:
        text = _normalize_text(str(input_text))
        if not text:
            raise HTTPException(status_code=400, detail={"code": "invalid_text", "message": "text is required", "status": 400})
        return {"text": text}
    audio_b64 = str(body.get("audio_base64") or "")
    if not audio_b64:
        raise HTTPException(status_code=400, detail={"code": "invalid_audio", "message": "audio_base64 is required", "status": 400})
    try:
        base64.b64decode(audio_b64)
    except Exception:
        raise HTTPException(status_code=400, detail={"code": "invalid_audio", "message": "audio_base64 decode failed", "status": 400}) from None
    if not _xfyun_asr_configured():
        raise HTTPException(status_code=400, detail={"code": "asr_config_missing", "message": "XFYUN_ASR_APP_ID/API_KEY/API_SECRET not configured", "status": 400})
    raise HTTPException(status_code=501, detail={"code": "asr_adapter_missing", "message": "ASR adapter package is not installed in momcozy-agent.", "status": 501})


@router.post("/v1/asr/upload")
async def asr_upload_endpoint(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    verify_api_key(request)
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail={"code": "invalid_audio", "message": "empty audio file", "status": 400})
    if not _xfyun_asr_configured():
        raise HTTPException(status_code=400, detail={"code": "asr_config_missing", "message": "XFYUN_ASR_APP_ID/API_KEY/API_SECRET not configured", "status": 400})
    raise HTTPException(status_code=501, detail={"code": "asr_adapter_missing", "message": "ASR adapter package is not installed in momcozy-agent.", "status": 501})


@router.get("/v1/notify/query")
async def query_notify(request: Request, user_id: str = "", timestamp: str = "") -> dict[str, Any]:
    verify_api_key(request)
    normalized_user_id = str(user_id or "").strip()
    normalized_timestamp = str(timestamp or "").strip()
    if not normalized_user_id:
        return _build_notify_query_response(status=400, message="missing user_id", error=-1, notify_list=[])
    if not normalized_timestamp:
        return _build_notify_query_response(status=400, message="missing timestamp", error=-1, notify_list=[])
    try:
        notify_list = mock_notify_list(normalized_user_id, normalized_timestamp)
        return _build_notify_query_response(status=200, message="success", error=0, notify_list=notify_list)
    except Exception as exc:
        return _build_notify_query_response(status=500, message=f"Internal server error: {exc}", error=-1, notify_list=[])


@router.get("/v1/mom-baby/info/query")
async def query_mom_baby_info_endpoint(request: Request, user_id: str = "") -> dict[str, Any]:
    verify_api_key(request)
    uid = str(user_id or "").strip()
    if not uid:
        return _mom_baby_info_response(error=-1)
    info = data_store.get_mom_baby_info(uid)
    if not info:
        return _mom_baby_info_response(error=-1)
    delivery_date = str(info.get("delivery_date") or "")
    return _mom_baby_info_response(
        error=0,
        delivery_date=delivery_date,
        lactation_advice=info.get("lactation_advice"),
        feeding_advice=info.get("feeding_advice"),
    )


@router.post("/v1/status/create")
async def create_status_endpoint(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    body = await _json_body_or_error(request, basic=True)
    if not isinstance(body, dict):
        return _basic_error_response()
    uid = str(body.get("user_id") or "").strip()
    if not uid:
        return _basic_error_response()
    advice = generate_status_advice(user_id=uid)
    if not advice:
        return _basic_error_response()
    if not data_store.update_user_profile_advice(
        user_id=uid,
        lactation_advice=advice["lactation_advice"],
        feeding_advice=advice["feeding_advice"],
    ):
        return _basic_error_response()
    return _basic_error_response(error=0)



@router.post("/v1/analysis/create")
async def create_analysis_endpoint(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    body = await _json_body_or_error(request, basic=True)
    if not _valid_analysis_payload(body):
        return _analysis_create_response(error=-1, result=False, message="invalid request body")
    uid = str(body.get("user_id") or "").strip()
    analysis_type = str(body.get("type") or "").strip()
    if not uid:
        return _analysis_create_response(error=-1, result=False, message="user_id is required")
    if analysis_type == "mom_baby":
        normality = evaluate_status_advice_normality(user_id=uid)
        advice = generate_status_advice(user_id=uid, normality=normality)
        if not advice:
            return _analysis_create_response(error=-1, result=False, message="failed to generate lactation advice")
        lactation_advice = str(advice.get("lactation_advice") or "").strip()
        feeding_advice = str(advice.get("feeding_advice") or "").strip()
        summary = str(advice.get("summary") or "").strip()
        status_prompt = str(advice.get("status_prompt") or "").strip()
        today_advice = str(advice.get("today_advice") or lactation_advice).strip()
        followup = str(advice.get("followup") or "现在方便吗？我们聊一下奶量的问题。").strip()
        if not data_store.update_user_profile_advice(
            user_id=uid,
            lactation_advice=lactation_advice,
            feeding_advice=feeding_advice,
        ):
            return _analysis_create_response(error=-1, result=False, message="failed to update lactation advice")
        is_normal = bool(normality.get("lactation_normal") is True or normality.get("result") is True)
        return _analysis_create_response(
            error=0,
            result=is_normal,
            message=followup,
            analysis_card=_lactation_advice_analysis_card(
                summary=summary,
                status_prompt=status_prompt,
                today_advice=today_advice,
                followup=followup,
                normality=normality,
                is_normal=is_normal,
            ),
        )
    if analysis_type == "daily_summary":
        summary = create_daily_summary(user_id=uid)
        if not summary.get("ok"):
            return _analysis_create_response(error=-1, message=str(summary.get("summary") or "failed to generate daily summary"))
        data = summary.get("data") if isinstance(summary.get("data"), dict) else {}
        message = data.get("message") if isinstance(data.get("message"), list) else str(summary.get("summary") or "")
        daily_summary_text = "\n".join(str(item) for item in message) if isinstance(message, list) else str(message)
        response_message = "  \r".join(str(item) for item in message) if isinstance(message, list) else str(message)
        if not data_store.update_user_profile_daily_summary(user_id=uid, daily_summary=daily_summary_text):
            return _analysis_create_response(error=-1, message="failed to update daily summary")
        return _analysis_create_response(
            error=0,
            message=response_message,
            analysis_card=_daily_summary_analysis_card(message, summary_data=data),
        )
    return _analysis_create_response(error=-1, result=False, message="unsupported type")


@router.get("/v1/mom-baby/today/query")
async def query_mom_baby_today_endpoint(request: Request, user_id: str = "") -> dict[str, Any]:
    verify_api_key(request)
    uid = str(user_id or "").strip()
    if not uid:
        return _mom_baby_today_response(error=-1)
    summary = data_store.get_mom_baby_today_summary(uid)
    if not summary:
        return _mom_baby_today_response(error=-1)
    return _mom_baby_today_response(
        error=0,
        pump_milk_volum=float(summary.get("pump_milk_volum") or 0),
        feeding_volum=float(summary.get("feeding_volum") or 0),
        feeding_forecast_volum=float(_feeding_forecast_p50(uid)),
    )


@router.post("/v1/feeding/add")
async def add_feeding_endpoint(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    body = await _json_body_or_error(request, basic=True)
    if not isinstance(body, dict):
        return _feeding_add_response(error=-1)
    uid = str(body.get("user_id") or "").strip()
    if not uid:
        return _feeding_add_response(error=-1)
    try:
        feed_type = _enum_int(body.get("feed_type"), "feed_type", {0, 1, 2})
        feed_action = _enum_int(body.get("feed_action", 0), "feed_action", {0, 1})
        feed_time = _normalize_hhmm_to_today(body.get("feed_time"), "feed_time")
        feed_milk_volum = _positive_int(body.get("feed_milk_volum"), "feed_milk_volum")
    except ValueError:
        return _feeding_add_response(error=-1)
    feeding_title = str(body.get("feeding_title") or "").strip()
    infant_id = data_store.resolve_infant_id_for_user(uid)
    if infant_id <= 0:
        return _feeding_add_response(error=-1)
    feeding_id = data_store.add_feeding_record(
        user_id=uid,
        infant_id=infant_id,
        feed_time=feed_time,
        feed_type_code=feed_type,
        feed_milk_volum=feed_milk_volum,
        feed_action=feed_action,
        feeding_title=feeding_title,
    )
    if feed_type == 0:
        data_store.add_pumping_record(
            user_id=uid,
            pump_time=feed_time,
            pump_type=2,
            pump_milk_volum=None,
            pump_milk_duration=feed_milk_volum,
            pump_title=feeding_title,
        )
    return _feeding_add_response(error=0, feeding_id=feeding_id)


@router.post("/v1/feeding/delete")
async def delete_feeding_endpoint(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    body = await _json_body_or_error(request, basic=True)
    if not isinstance(body, dict):
        return _basic_error_response()
    uid = str(body.get("user_id") or "").strip()
    try:
        feeding_id = _positive_int(body.get("feeding_id"), "feeding_id")
    except ValueError:
        return _basic_error_response()
    if not uid or not data_store.delete_feeding_record(user_id=uid, feeding_id=feeding_id):
        return _basic_error_response()
    return _basic_error_response(error=0)


@router.get("/v1/feeding/query")
async def query_today_feeding_endpoint(request: Request, user_id: str = "", timestamp: str = "") -> dict[str, Any]:
    verify_api_key(request)
    uid = str(user_id or "").strip()
    if not uid:
        return _feeding_query_response(error=-1)
    target_date = _normalize_date(timestamp) or datetime.now().strftime("%Y-%m-%d")
    records = data_store.list_feeding_records(user_id=uid, start_at=f"{target_date} 00:00:00", end_at=f"{target_date} 23:59:59")
    feed_list = []
    for record in records:
        code = data_store.FEED_TYPE_TEXT_TO_CODE.get(str(record.get("feed_type")), 0)
        quantity = float(record.get("feed_milk_volum") or 0)
        # 提取 feed_time 的 HH:MM 格式
        feed_time_str = str(record.get("feed_time") or "")
        feed_time_hhmm = feed_time_str[11:16] if len(feed_time_str) >= 16 else ""
        row: dict[str, Any] = {
            "infant_id": int(record.get("infant_id") or 0),
            "feeding_id": int(record.get("feeding_id") or 0),
            "feed_type": code,
            "feed_action": int(record.get("feed_action") or 0),
            "feed_time": feed_time_hhmm,
            "feeding_title": str(record.get("feeding_title") or ""),
        }
        if code == 0:
            row["feed_duration"] = quantity
        else:
            row["feed_milk_volum"] = quantity
        feed_list.append(row)
    return _feeding_query_response(error=0, feed_list=feed_list)


@router.post("/v1/growth/add")
async def add_growth_endpoint(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    body = await _json_body_or_error(request, basic=True)
    if not isinstance(body, dict):
        return _growth_add_response(error=-1)
    user_id = str(body.get("user_id") or "").strip()
    if not user_id:
        return _growth_add_response(error=-1)
    try:
        infant_id = _optional_positive_int(body.get("infant_id"), "infant_id")
        height_cm = _positive_float(body.get("height_cm"), "height_cm")
        weight_kg = _positive_float(body.get("weight_kg"), "weight_kg")
        head_cm = _positive_float(body.get("head_cm"), "head_cm")
    except ValueError:
        return _growth_add_response(error=-1)
    resolved_infant_id = data_store.resolve_infant_id_for_user(user_id, infant_id)
    if infant_id is not None and resolved_infant_id <= 0:
        return _growth_add_response(error=-1)
    growth_id = data_store.add_growth_record(
        user_id=user_id,
        infant_id=resolved_infant_id,
        height_cm=height_cm,
        weight_kg=weight_kg,
        head_cm=head_cm,
    )
    return _growth_add_response(error=0, growth_id=growth_id)


@router.post("/v1/growth/revise")
async def revise_growth_endpoint(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    body = await _json_body_or_error(request, basic=True)
    if not isinstance(body, dict):
        return _growth_add_response(error=-1)
    user_id = str(body.get("user_id") or "").strip()
    if not user_id:
        return _growth_add_response(error=-1, growth_id=int(body.get("growth_id") or 0))
    try:
        infant_id = _optional_positive_int(body.get("infant_id"), "infant_id")
        growth_id = _positive_int(body.get("growth_id"), "growth_id")
        height_cm = _optional_positive_float(body.get("height_cm"), "height_cm")
        weight_kg = _optional_positive_float(body.get("weight_kg"), "weight_kg")
        head_cm = _optional_positive_float(body.get("head_cm"), "head_cm")
    except ValueError:
        return _growth_add_response(error=-1, growth_id=int(body.get("growth_id") or 0))
    if all(value is None for value in (height_cm, weight_kg, head_cm)):
        return _growth_add_response(error=-1, growth_id=growth_id)
    resolved_infant_id = None
    if infant_id is not None:
        resolved_infant_id = data_store.resolve_infant_id_for_user(user_id, infant_id)
        if resolved_infant_id <= 0:
            return _growth_add_response(error=-1, growth_id=growth_id)
    ok = data_store.update_growth_record(
        user_id=user_id,
        infant_id=resolved_infant_id,
        growth_id=growth_id,
        height_cm=height_cm,
        weight_kg=weight_kg,
        head_cm=head_cm,
    )
    if not ok:
        return _growth_add_response(error=-1, growth_id=growth_id)
    return _growth_add_response(error=0, growth_id=growth_id)


@router.get("/v1/growth/query")
async def query_latest_growth_endpoint(request: Request, user_id: str = "") -> dict[str, Any]:
    verify_api_key(request)
    uid = str(user_id or "").strip()
    if not uid:
        return _growth_query_response(error=-1)
    latest = data_store.latest_growth_record_for_user(uid)
    if not latest:
        return _growth_query_response(error=-1)
    return _growth_query_response(
        error=0,
        growth_id=int(latest.get("growth_id") or 0),
        height_mes_time=str(latest.get("height_measured_at") or latest.get("created_at") or ""),
        height_cm=float(latest.get("height_cm") or 0),
        weight_mes_time=str(latest.get("weight_measured_at") or latest.get("created_at") or ""),
        weight_kg=float(latest.get("weight_kg") or 0),
        head_mes_time=str(latest.get("head_measured_at") or latest.get("created_at") or ""),
        head_cm=float(latest.get("head_cm") or 0),
    )


@router.get("/v1/growth/history")
async def query_growth_history_endpoint(request: Request, user_id: str = "") -> dict[str, Any]:
    verify_api_key(request)
    uid = str(user_id or "").strip()
    if not uid:
        return _growth_history_response(error=-1)
    records = data_store.growth_records_for_user(uid)
    growth_data = [
        {
            "growth_id": int(r.get("growth_id") or 0),
            "date": str(r.get("created_at") or "")[:10],
            "height_cm": float(r.get("height_cm") or 0),
            "weight_kg": float(r.get("weight_kg") or 0),
            "head_cm": float(r.get("head_cm") or 0),
        }
        for r in records
    ]
    return _growth_history_response(error=0, growth_data=growth_data)


@router.get("/v1/plan/query-task")
async def query_plan_task_endpoint(request: Request, user_id: str = "", timestamp: str = "") -> dict[str, Any]:
    verify_api_key(request)
    uid = str(user_id or "").strip()
    target_date = _normalize_date(timestamp)
    if not uid or not target_date:
        return _plan_task_response(error=-1)
    result = data_store.query_plan_tasks(user_id=uid, target_date=target_date)
    if result is None:
        return _plan_task_response(error=-1)
    return _plan_task_response(
        error=0,
        plan_type=str(result.get("plan_type") or "None"),
        task_list=result.get("task_list") if isinstance(result.get("task_list"), list) else [],
    )


@router.post("/v1/plan/add-task")
async def add_plan_task_endpoint(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    body = await _json_body_or_error(request, basic=True)
    if not isinstance(body, dict):
        return _plan_task_mutation_response(error=-1)
    uid = str(body.get("user_id") or "").strip()
    target_date = _normalize_date(body.get("timestamp"))
    task_list = body.get("task_list")
    if not uid or not target_date or not isinstance(task_list, list):
        return _plan_task_mutation_response(error=-1)
    inserted = data_store.add_plan_tasks(user_id=uid, target_date=target_date, task_list=task_list)
    if not inserted and task_list:
        return _plan_task_mutation_response(error=-1)
    result = data_store.query_plan_tasks(user_id=uid, target_date=target_date)
    return _plan_task_mutation_response(
        error=0,
        task_list=(result.get("task_list") if isinstance(result, dict) and isinstance(result.get("task_list"), list) else inserted),
    )


@router.post("/v1/plan/delete-task")
async def delete_plan_task_endpoint(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    body = await _json_body_or_error(request, basic=True)
    if not isinstance(body, dict):
        return _plan_task_mutation_response(error=-1)
    uid = str(body.get("user_id") or "").strip()
    target_date = _normalize_date(body.get("timestamp"))
    try:
        task_id = _positive_int(body.get("task_id"), "task_id")
    except ValueError:
        return _plan_task_mutation_response(error=-1)
    if not uid or not target_date:
        return _plan_task_mutation_response(error=-1)
    deleted = data_store.delete_plan_task(user_id=uid, target_date=target_date, task_id=task_id)
    if not deleted:
        return _plan_task_mutation_response(error=-1)
    result = data_store.query_plan_tasks(user_id=uid, target_date=target_date)
    return _plan_task_mutation_response(
        error=0,
        task_list=(result.get("task_list") if isinstance(result, dict) and isinstance(result.get("task_list"), list) else []),
    )


@router.post("/v1/plan/revise-task")
async def revise_plan_task_endpoint(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    body = await _json_body_or_error(request, basic=True)
    if not isinstance(body, dict):
        return _plan_task_mutation_response(error=-1)
    uid = str(body.get("user_id") or "").strip()
    target_date = _normalize_date(body.get("timestamp"))
    try:
        task_id = _positive_int(body.get("task_id"), "task_id")
    except ValueError:
        return _plan_task_mutation_response(error=-1)
    task_time = str(body.get("task_time") or "").strip()
    task_content = str(body.get("task_content") or "").strip()
    task_done = body.get("task_done")
    if not uid or not target_date or not task_time or not task_content:
        return _plan_task_mutation_response(error=-1)
    revised = data_store.revise_plan_task(
        user_id=uid,
        target_date=target_date,
        task_id=task_id,
        task_time=task_time,
        task_content=task_content,
        task_done=task_done,
    )
    if not revised:
        return _plan_task_mutation_response(error=-1)
    result = data_store.query_plan_tasks(user_id=uid, target_date=target_date)
    return _plan_task_mutation_response(
        error=0,
        task_list=(result.get("task_list") if isinstance(result, dict) and isinstance(result.get("task_list"), list) else []),
    )


@router.post("/v1/pump-milk/upload")
async def upload_pump_milk_endpoint(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    body = await _json_body_or_error(request, basic=True)
    if not isinstance(body, dict):
        return _pump_upload_response(error=-1)
    uid = str(body.get("user_id") or "").strip()
    if not uid:
        return _pump_upload_response(error=-1)
    try:
        pump_type = _enum_int(body.get("pump_type"), "pump_type", {0, 1})
        pump_source = _enum_int(body.get("pump_source"), "pump_source", {0, 1, 2})
        pump_time = _normalize_hhmm_to_today(body.get("pump_time"), "pump_time")
        volume = _positive_float(body.get("pump_milk_volum"), "pump_milk_volum")
    except ValueError:
        return _pump_upload_response(error=-1)
    pump_title = str(body.get("pump_title") or "").strip()
    pump_id = data_store.add_pumping_record(
        user_id=uid,
        pump_time=pump_time,
        pump_type=pump_type,
        pump_milk_volum=volume,
        pump_milk_duration=None,
        pump_source=pump_source,
        pump_title=pump_title,
    )
    return _pump_upload_response(error=0, pump_id=pump_id)


@router.get("/v1/pump-milk/query")
async def query_pump_milk_endpoint(request: Request, user_id: str = "", timestamp: str = "") -> dict[str, Any]:
    verify_api_key(request)
    uid = str(user_id or "").strip()
    if not uid:
        return _pump_query_response(error=-1)
    target_date = _normalize_date(timestamp) or datetime.now().strftime("%Y-%m-%d")
    records = data_store.list_pumping_records(user_id=uid, start_at=f"{target_date} 00:00:00", end_at=f"{target_date} 23:59:59")
    pump_milk_list = [
        {
            "pump_id": int(r.get("pumping_id") or 0),
            "pump_type": int(r.get("pump_type") or 0),
            "pump_source": int(r.get("pump_source") if r.get("pump_source") is not None else 1),
            "pump_time": _hhmm_from_datetime(r.get("pump_start_time")),
            "pump_milk_volum": float(r.get("pump_milk_volum") or 0),
            "pump_title": str(r.get("pump_title") or ""),
        }
        for r in records
        if int(r.get("pump_type") or 0) in {0, 1}
    ]
    return _pump_query_response(error=0, pump_milk_list=pump_milk_list)


@router.post("/v1/pump-milk/delete")
async def delete_pump_milk_endpoint(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    body = await _json_body_or_error(request, basic=True)
    if not isinstance(body, dict):
        return _basic_error_response()
    uid = str(body.get("user_id") or "").strip()
    if not uid:
        return _basic_error_response()
    try:
        pump_id = _positive_int(body.get("pump_id"), "pump_id")
    except ValueError:
        return _basic_error_response()
    if not data_store.delete_pumping_record(user_id=uid, pump_id=pump_id):
        return _basic_error_response()
    return _basic_error_response(error=0)


@router.get("/v1/pump/info/get")
async def get_pump_info(request: Request, user_id: str = "") -> dict[str, Any]:
    verify_api_key(request)
    uid = str(user_id or "").strip()
    if not uid:
        return {"error": -1, "lactation_info_list": []}
    return data_store.pump_info(uid)


@router.post("/api/client-event")
async def record_client_event(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    body = await _json_body_or_error(request, basic=True)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="client event requires a JSON object")
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    thread_id = str(
        body.get("thread_id")
        or body.get("threadId")
        or body.get("conversation_id")
        or body.get("conversationId")
        or ""
    ).strip()
    if not thread_id:
        raise HTTPException(status_code=400, detail="client event requires thread_id")

    url = (os.getenv("MOMCOZY_AGENT_CLIENT_EVENT_URL") or "http://127.0.0.1:8768/api/client-event").strip()
    if not url or url.lower() in {"none", "disabled", "off"}:
        return {
            "status": "local_completed",
            "conversation_id": thread_id,
            "consult_id": str(body.get("consult_id") or body.get("consultId") or metadata.get("consult_id") or ""),
            "event_type": str(body.get("event_type") or body.get("type") or "client_event"),
        }

    try:
        import httpx
    except ImportError as exc:
        raise HTTPException(status_code=501, detail="httpx is not installed") from exc

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=1.0, read=3.0, write=1.0, pool=1.0), trust_env=False) as client:
            response = await client.post(url, json=body)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"failed to forward client event: {exc}") from exc

    if response.status_code < 200 or response.status_code >= 300:
        raise HTTPException(status_code=502, detail=f"client event forward failed: {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {"status": "recorded", "conversation_id": thread_id}


@router.post("/api/hospital-bag/cart-update")
async def update_hospital_bag_cart_direct(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    body = await _json_body_or_error(request)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid request body")

    raw_args = body.get("args")
    args = raw_args if isinstance(raw_args, dict) else body
    raw_cart = body.get("hospital_bag_cart")
    current_cart = raw_cart if isinstance(raw_cart, dict) else {"groups": body.get("groups")}
    user_message = str(body.get("user_message") or body.get("userMessage") or "").strip()
    locale = str(body.get("locale") or "zh-CN").strip() or "zh-CN"
    timezone_name = str(body.get("timezone") or "Asia/Shanghai").strip() or "Asia/Shanghai"
    inputs = {
        "user_message": user_message,
        "locale": locale,
        "timezone": timezone_name,
        "message_sent_at": datetime.now(timezone.utc).isoformat(),
        "hospital_bag_cart": current_cart,
    }
    return execute_hospital_bag_cart_update(dict(args), inputs)


async def _json_body_or_error(request: Request, *, basic: bool = False, pump: bool = False) -> Any:
    try:
        return await request.json()
    except Exception:
        return {} if basic or pump else None


def _validate_device_info_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    user_id = str(payload.get("user_id") or "").strip()
    if not user_id:
        raise ValueError("user_id is required")
    left = _object(payload.get("device_left"), "device_left")
    right = _object(payload.get("device_right"), "device_right")
    for name, device in (("device_left", left), ("device_right", right)):
        state = str(device.get("state") or "").strip().lower()
        if state not in {"online", "offline", "unbind"}:
            raise ValueError(f"{name}.state must be one of online, offline, unbind")
        battery = _int(device.get("battery"), f"{name}.battery")
        if battery < 0 or battery > 100:
            raise ValueError(f"{name}.battery must be between 0 and 100")
        _int(device.get("rssi"), f"{name}.rssi")
    return user_id, left, right


def _validate_workstate_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    user_id = str(payload.get("user_id") or "").strip()
    if not user_id:
        raise ValueError("user_id is required")
    normalized = dict(payload)
    for side in ("device_left", "device_right"):
        device = _object(payload.get(side), side)
        state = _enum_int(device.get("state"), f"{side}.state", {0, 1, 2, 3, 4})
        device["state"] = state
        mode = str(device.get("mode") or "").strip().lower()
        if mode and mode not in {"stimulate", "deep", "mix"}:
            raise ValueError(f"{side}.mode must be one of stimulate, deep, mix")
        step = str(device.get("step") or "").strip().lower()
        device["step"] = step if step in {"start", "running", "stop"} else ("running" if state == 1 and mode else "stop")
        normalized[side] = device
    normalized["user_id"] = user_id
    return user_id, normalized


def _build_workstate_reply(payload: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    _ = previous
    modes = {str((payload.get(side) or {}).get("mode") or "").lower() for side in ("device_left", "device_right")}
    active_modes = [mode for mode in modes if mode in {"stimulate", "deep"}]
    if len(active_modes) == 1:
        mode = active_modes[0]
        output = "Pump mode is active. Keep the current rhythm if it feels comfortable."
        data_store.add_pending_reply(payload["user_id"], mode, output, {"mode": mode})
    return {"need_reply": False, "output": "", "reply_code": "", "reply_side": "", "direct_rich_text": None}


def _validate_process_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    user_id = str(payload.get("user_id") or "").strip()
    if not user_id:
        raise ValueError("user_id is required")
    normalized = dict(payload)
    for side in ("process_left", "process_right"):
        item = _object(payload.get(side), side)
        process = _int(item.get("process"), f"{side}.process")
        if process < 0 or process > 100:
            raise ValueError(f"{side}.process must be between 0 and 100")
        item["process"] = process
        item["milk_reel"] = _int(item.get("milk_reel"), f"{side}.milk_reel")
        item["has_milk"] = bool(item["milk_reel"] & 0b1)
        item["has_letdown"] = bool(item["milk_reel"] & 0b10)
        item["time"] = _normalize_utc_time(item.get("time"), f"{side}.time")
        normalized[side] = item
    normalized["user_id"] = user_id
    return user_id, normalized


def _build_process_reply(payload: dict[str, Any], latest_workstate: dict[str, Any] | None) -> dict[str, Any]:
    _ = latest_workstate
    left = payload.get("process_left") or {}
    right = payload.get("process_right") or {}
    max_process = max(int(left.get("process", 0)), int(right.get("process", 0)))
    if max_process >= PROCESS_REST_THRESHOLD:
        return {"need_reply": True, "output": "You have reached the target level. Consider resting if this feels sufficient.", "reply_code": "process_rest_after_20s_at_100", "reply_side": "global", "direct_rich_text": None}
    if max_process >= PROCESS_ENERGY_LOWER_VALUE:
        return {"need_reply": True, "output": "This session is close to the target. You can prepare to wrap up if comfortable.", "reply_code": "process_target_80", "reply_side": "global", "direct_rich_text": None}
    if left.get("has_letdown") or right.get("has_letdown"):
        return {"need_reply": True, "output": "Letdown has been detected. Keep the current rhythm if it feels comfortable.", "reply_code": "letdown_detected", "reply_side": "both" if left.get("has_letdown") and right.get("has_letdown") else ("left" if left.get("has_letdown") else "right"), "direct_rich_text": None}
    return {"need_reply": False, "output": "", "reply_code": "", "reply_side": "", "direct_rich_text": None}


def _validate_process_data_payload(payload: Any) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    user_id = str(payload.get("user_id") or "").strip()
    if not user_id:
        raise ValueError("user_id is required")
    left = _validate_process_data_device("device_left", payload.get("device_left"))
    right = _validate_process_data_device("device_right", payload.get("device_right"))
    return user_id, left, right


def _validate_process_data_device(field: str, value: Any) -> dict[str, Any]:
    item = _object(value, field)
    step = str(item.get("step") or "").strip().lower()
    if step not in {"start", "running", "stop", "pause", "offline"}:
        raise ValueError(f"{field}.step must be one of start, running, stop")
    item["step"] = step
    cap_data = item.get("cap_data")
    if not isinstance(cap_data, list):
        raise ValueError(f"{field}.cap_data must be an array of integers")
    item["cap_data"] = [_int(raw, f"{field}.cap_data[{index}]") for index, raw in enumerate(cap_data)]
    item["time"] = _normalize_utc_time(item.get("time"), f"{field}.time")
    item["milk_reel"] = _int(item.get("milk_reel"), f"{field}.milk_reel")
    item["bandpower"] = _int(item.get("bandpower"), f"{field}.bandpower")
    item["milk"] = _int(item.get("milk"), f"{field}.milk")
    return item


def _calculate_sensor_process(device: dict[str, Any]) -> int:
    milk_reel = int(device.get("milk_reel", 0) or 0)
    return PROCESS_REST_THRESHOLD if (milk_reel & 0b1 or milk_reel & 0b10) else 0


def _pump_process_data_response(*, status: int, message: str, error: int, text: str = "", process_l: int = 0, process_r: int = 0, process_all: int = 0) -> dict[str, Any]:
    return {"status": status, "message": message, "data": {"error": error, "text": text, "process_l": int(process_l), "process_r": int(process_r), "process_all": int(process_all)}}


async def _create_pump_session_summary_response(connection: Any, body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        return _pump_session_summary_error("invalid request body")

    conversation_id = _pump_session_conversation_id(body)
    if not conversation_id:
        return _pump_session_summary_error("conversation_id is required")

    result = build_pump_session_summary(body)
    data = result.get("data") if isinstance(result.get("data"), dict) else {"error": -1}
    if not result.get("ok"):
        return {
            "status": 400,
            "message": str(data.get("message") or result.get("status") or "failed"),
            "data": data,
        }

    context_status = await _append_pump_session_summary_context(
        connection,
        body,
        data,
        conversation_id=conversation_id,
        context_text=str(result.get("context_text") or ""),
    )
    data["context"] = context_status
    return {"status": 200, "message": "success", "data": data}


def _pump_session_summary_error(message: str) -> dict[str, Any]:
    return {"status": 400, "message": message, "data": {"error": -1, "message": message}}


async def _append_pump_session_summary_context(
    connection: Any,
    body: dict[str, Any],
    data: dict[str, Any],
    *,
    conversation_id: str,
    context_text: str,
) -> dict[str, Any]:
    conversation_id = conversation_id.strip()
    context_text = context_text.strip()
    if not context_text:
        return {"appended": False, "reason": "missing_context_text"}
    if not conversation_id:
        return {"appended": False, "reason": "missing_conversation_id"}

    if _append_local_agent_context(connection, conversation_id=conversation_id, context_text=context_text):
        return {"appended": True, "target": "local_runtime"}

    forwarded = await _forward_client_event_context(
        conversation_id=conversation_id,
        context_text=context_text,
        user_id=str(body.get("user_id") or ""),
        ended_at=str((data.get("session") or {}).get("ended_at") or ""),
    )
    if forwarded.get("appended"):
        return forwarded
    return forwarded


def _pump_session_conversation_id(body: dict[str, Any]) -> str:
    return str(
        body.get("thread_id")
        or body.get("threadId")
        or body.get("conversation_id")
        or body.get("conversationId")
        or ""
    ).strip()


def _append_local_agent_context(connection: Any, *, conversation_id: str, context_text: str) -> bool:
    app = getattr(connection, "app", None)
    runtime = getattr(getattr(app, "state", None), "runtime", None)
    get_session = getattr(runtime, "get_session", None)
    if not callable(get_session):
        return False
    try:
        session = get_session(conversation_id)
        context_state = getattr(session, "context_state", None)
        events = getattr(context_state, "client_events", None)
        if not isinstance(events, list):
            return False
        if context_text not in events:
            events.append(context_text)
            context_state.client_events = events[-10:]
        return True
    except Exception:
        return False


def _verify_websocket_api_key(websocket: WebSocket) -> bool:
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


async def _safe_close_websocket(websocket: WebSocket) -> None:
    try:
        await websocket.close()
    except Exception:
        pass


async def _forward_client_event_context(
    *,
    conversation_id: str,
    context_text: str,
    user_id: str,
    ended_at: str,
) -> dict[str, Any]:
    url = (os.getenv("MOMCOZY_AGENT_CLIENT_EVENT_URL") or "http://127.0.0.1:8768/api/client-event").strip()
    if not url or url.lower() in {"none", "disabled", "off"}:
        return {"appended": False, "reason": "context_forwarding_disabled"}
    try:
        import httpx
    except ImportError:
        return {"appended": False, "reason": "httpx_unavailable"}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=0.5, read=1.0, write=0.5, pool=0.5)) as client:
            response = await client.post(
                url,
                json={
                    "thread_id": conversation_id,
                    "conversation_id": conversation_id,
                    "event_type": "pump_session_ended",
                    "label": "本次吸奶小结",
                    "occurred_at": ended_at,
                    "metadata": {
                        "user_id": user_id,
                        "source": "pump_session_summary",
                        "context_text": context_text,
                    },
                },
            )
        if 200 <= response.status_code < 300:
            return {"appended": True, "target": "client_event_forward", "status_code": response.status_code}
        return {"appended": False, "reason": "client_event_forward_failed", "status_code": response.status_code}
    except Exception as exc:
        return {"appended": False, "reason": "client_event_forward_error", "error": type(exc).__name__}


def _build_notify_query_response(*, status: int, message: str, error: int, notify_list: Any = None) -> dict[str, Any]:
    return {
        "status": int(status),
        "message": str(message or ""),
        "data": {
            "error": int(error),
            "notify_list": notify_list if isinstance(notify_list, list) else [],
        },
    }


def mock_notify_list(user_id: str, timestamp: str) -> list[dict[str, Any]]:
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        return []

    now = _parse_notify_timestamp(timestamp)
    notify_list: list[dict[str, Any]] = []
    now_text = now.strftime("%Y-%m-%d %H:%M:%S")
    tasks = data_store.list_calendar_tasks_for_notify(user_id=normalized_user_id, target_date=now.date().isoformat())
    for task in tasks:
        if not _is_notify_care_task(task):
            continue
        task_start = _parse_optional_datetime(task.get("start_time"))
        if task_start is None:
            continue
        completed = data_store.has_task_result(user_id=normalized_user_id, task=task, as_of_time=now_text)
        if not completed:
            notify_list.extend(_pump_reminders_for_task(task_start=task_start, now=now))
        if _is_task_finish_false(task):
            notify_list.extend(_warning_reminders_for_task(task_start=task_start, now=now))

    growth_notice = _growth_notify(normalized_user_id, now)
    if growth_notice:
        notify_list.append(growth_notice)

    summary_notice = _summary_notify(normalized_user_id, now)
    if summary_notice:
        notify_list.append(summary_notice)

    priority = {"warning": 0, "pump": 1, "grown": 2, "summary": 3}
    notify_list.sort(key=lambda item: (priority.get(str(item.get("event") or ""), 99), str(item.get("time") or "")))
    return notify_list


def _parse_notify_timestamp(raw: Any) -> datetime:
    token = str(raw or "").strip()
    if not token:
        raise ValueError("timestamp is required")
    normalized = token[:-1] + "+00:00" if token.endswith("Z") else token
    try:
        parsed = datetime.fromisoformat(normalized)
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
            try:
                parsed = datetime.strptime(token, fmt)
                break
            except Exception:
                parsed = None  # type: ignore[assignment]
        if parsed is None:
            raise ValueError("timestamp must be a valid datetime string")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _parse_optional_datetime(raw: Any) -> datetime | None:
    token = str(raw or "").strip()
    if not token:
        return None
    try:
        return _parse_notify_timestamp(token)
    except ValueError:
        return None


def _is_notify_care_task(task: dict[str, Any]) -> bool:
    task_type = str(task.get("type") or "").strip()
    is_milk_pump = str(task.get("is_milk_pump") or "").strip().lower() in {"1", "true"}
    return is_milk_pump or task_type in {"吸奶", "亲喂", "喂养", "瓶喂", "配方奶"}


def _is_task_finish_false(task: dict[str, Any]) -> bool:
    return str(task.get("finish") or "").strip().lower() == "false"


def _in_notify_window(now: datetime, trigger_at: datetime, *, window_minutes: int = 15) -> bool:
    elapsed = (now - trigger_at).total_seconds()
    return 0 <= elapsed <= window_minutes * 60


def _pump_reminders_for_task(*, task_start: datetime, now: datetime) -> list[dict[str, Any]]:
    reminders: list[dict[str, Any]] = []
    rules = [
        (35, "妈妈，吸奶/喂养时间快到了，可以提前准备一下哦~"),
        (20, "妈妈，该吸奶/喂养了！建议准备开始"),
    ]
    for minutes_before, message in rules:
        trigger_at = task_start - timedelta(minutes=minutes_before)
        if _in_notify_window(now, trigger_at):
            reminders.append({"event": "pump", "time": task_start.strftime("%H:%M"), "message": message})
    return reminders


def _warning_reminders_for_task(*, task_start: datetime, now: datetime) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    rules = [
        (25, "已延迟25分钟，建议尽快安排吸奶/喂养，避免乳房不适"),
        (75, "延迟已超过75分钟，可能引起涨奶或不适，建议尽快安排一次吸奶/亲喂"),
        (135, "长时间未吸奶或喂养，可能增加乳房不适风险，建议优先安排排空乳房"),
    ]
    for overdue_minutes, message in rules:
        trigger_at = task_start + timedelta(minutes=overdue_minutes)
        if _in_notify_window(now, trigger_at):
            warnings.append({"event": "warning", "time": task_start.strftime("%H:%M"), "message": message})
    return warnings


def _growth_notify(user_id: str, now: datetime) -> dict[str, Any] | None:
    trigger_at = now.replace(hour=8, minute=45, second=0, microsecond=0)
    if now.weekday() < 5 or not _in_notify_window(now, trigger_at):
        return None
    infant = data_store.latest_infant_profile_for_user(user_id)
    birth_date = _date_from_datetime_text((infant or {}).get("birth_date"))
    if birth_date is None:
        return None
    latest_growth = data_store.latest_growth_record_for_user(user_id)
    latest_growth_date = _latest_growth_date(latest_growth)
    if latest_growth_date is not None:
        if _baby_age_months(birth_date, now.date()) <= 3:
            week_start = now.date() - timedelta(days=now.weekday())
            if latest_growth_date >= week_start:
                return None
        elif latest_growth_date.year == now.year and latest_growth_date.month == now.month:
            return None
    return {
        "event": "grown",
        "time": "09:00",
        "message": "建议更新一下宝宝生长数据哦～这样能更好地帮你进行奶量管理",
    }


def _summary_notify(user_id: str, now: datetime) -> dict[str, Any] | None:
    trigger_at = now.replace(hour=20, minute=45, second=0, microsecond=0)
    if not _in_notify_window(now, trigger_at):
        return None
    target_date = now.date().isoformat()
    summary = data_store.get_mom_baby_today_summary(user_id, target_date=target_date)
    if summary is None:
        return None
    start_at = f"{target_date} 00:00:00"
    end_at = f"{target_date} 23:59:59"
    pumping_count = len(data_store.list_pumping_records(user_id=user_id, start_at=start_at, end_at=end_at))
    feeding_count = len(data_store.list_feeding_records(user_id=user_id, start_at=start_at, end_at=end_at))
    pump_total = float(summary.get("pump_milk_volum") or 0)
    feeding_total = float(summary.get("feeding_volum") or 0)
    forecast_total = float(summary.get("feeding_forecast_volum") or 0)
    message = f"今日母乳{pumping_count}次，喂养{feeding_count}次，吸奶量{pump_total:g}ml，宝宝摄入{feeding_total:g}ml。"
    if forecast_total > 0:
        message += f" 亲喂预估{forecast_total:g}ml，整体节奏正常。"
    else:
        message += " 整体节奏正常。"
    return {"event": "summary", "time": "21:00", "message": message}


def _date_from_datetime_text(raw: Any) -> date | None:
    parsed = _parse_optional_datetime(raw)
    return parsed.date() if parsed is not None else None


def _latest_growth_date(growth: dict[str, Any] | None) -> date | None:
    if not growth:
        return None
    dates = [
        _date_from_datetime_text(growth.get("height_measured_at")),
        _date_from_datetime_text(growth.get("weight_measured_at")),
        _date_from_datetime_text(growth.get("head_measured_at")),
        _date_from_datetime_text(growth.get("created_at")),
    ]
    dates = [item for item in dates if item is not None]
    return max(dates) if dates else None


def _baby_age_months(birth_date: date, current_date: date) -> int:
    months = (current_date.year - birth_date.year) * 12 + current_date.month - birth_date.month
    if current_date.day < birth_date.day:
        months -= 1
    return max(0, months)


def _mom_baby_info_response(
    *,
    error: int,
    delivery_date: str = "",
    lactation_advice: Any = None,
    feeding_advice: Any = None,
) -> dict[str, Any]:
    return {
        "error": int(error),
        "delivery_date": str(delivery_date or ""),
        "lactation_advice": str(lactation_advice) if lactation_advice is not None else None,
        "feeding_advice": str(feeding_advice) if feeding_advice is not None else None,
    }


def _mom_baby_today_response(
    *,
    error: int,
    pump_milk_volum: float = 0,
    feeding_volum: float = 0,
    feeding_forecast_volum: float = 0,
) -> dict[str, Any]:
    return {
        "error": int(error),
        "pump_milk_volum": float(pump_milk_volum or 0),
        "feeding_volum": float(feeding_volum or 0),
        "feeding_forecast_volum": float(feeding_forecast_volum or 0),
    }


def _growth_add_response(*, error: int, growth_id: int = 0) -> dict[str, Any]:
    return {"error": int(error), "growth_id": int(growth_id or 0)}


def _feeding_add_response(*, error: int, feeding_id: int = 0) -> dict[str, Any]:
    return {"error": int(error), "feeding_id": int(feeding_id or 0)}


def _feeding_query_response(*, error: int, feed_list: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"error": int(error), "feed_list": feed_list if isinstance(feed_list, list) else []}


def _pump_upload_response(*, error: int, pump_id: int = 0) -> dict[str, Any]:
    return {"error": int(error), "pump_id": int(pump_id or 0)}


def _pump_query_response(*, error: int, pump_milk_list: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"error": int(error), "pump_milk_list": pump_milk_list if isinstance(pump_milk_list, list) else []}


def _basic_error_response(*, error: int = -1) -> dict[str, Any]:
    return {"error": int(error)}


def _valid_analysis_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and set(payload.keys()) == {"user_id", "type"}


def _analysis_create_response(
    *,
    error: int,
    message: Any,
    result: bool | None = None,
    analysis_card: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {"error": int(error), "data": {}}
    if result is not None:
        response["data"]["result"] = bool(result)
    response["data"]["message"] = message
    if analysis_card is not None:
        response["data"]["analysis_card"] = analysis_card
    return response


def _daily_summary_analysis_card(message: Any, *, summary_data: dict[str, Any] | None = None) -> dict[str, Any]:
    lines = [str(item).strip() for item in message] if isinstance(message, list) else [str(message or "").strip()]
    lines = [line for line in lines if line]
    data = summary_data if isinstance(summary_data, dict) else {}
    used_latest_available_record = bool(data.get("used_latest_available_record"))
    titles = [
        "最近记录概览" if used_latest_available_record else "今日概览",
        "奶量数据",
        "节律观察",
        "需要留意",
        "明天小建议",
    ]
    tones = ["overview", "milk", "rhythm", "attention", "next"]
    sections = [
        {"id": f"daily_{index}", "title": titles[index], "tone": tones[index], "items": [line]}
        for index, line in enumerate(lines[: len(titles)])
    ]
    card = {
        "kind": "daily_summary",
        "title": "每日奶量总结",
        "sections": sections,
    }
    if used_latest_available_record:
        card["status_label"] = "最近记录"
        card["status_tone"] = "insufficient"
        label = str(data.get("record_date_label") or "").strip()
        if label:
            card["subtitle"] = f"按 {label} 的记录生成"
    return card


def _lactation_advice_analysis_card(
    *,
    summary: str,
    status_prompt: str,
    today_advice: str,
    followup: str,
    normality: dict[str, Any],
    is_normal: bool,
) -> dict[str, Any]:
    reason = str(normality.get("reason") or "").strip()
    status_label, status_tone = _lactation_status_badge(
        normality=normality,
        is_normal=is_normal,
        status_prompt=status_prompt,
    )
    return {
        "kind": "mom_baby",
        "title": "每日泌乳建议",
        "status": "normal" if is_normal else "attention",
        "status_label": status_label,
        "status_tone": status_tone,
        "reason": reason,
        "followup": followup,
        "sections": [
            {"id": "summary", "title": "简单总结", "tone": "overview", "items": [summary]},
            {"id": "status", "title": "状态提示", "tone": "attention" if not is_normal else "milk", "items": [status_prompt]},
            {"id": "today", "title": "今日建议", "tone": "next", "items": [today_advice]},
        ],
    }


def _lactation_status_badge(*, normality: dict[str, Any], is_normal: bool, status_prompt: str = "") -> tuple[str, str]:
    label = status_prompt.strip()
    if is_normal:
        return label or "节奏稳定", "normal"

    reason = str(normality.get("reason") or "").strip()
    if reason in {"insufficient_minimum_valid_days", "assessment_failed", "missing_user_id"}:
        return label or "记录补充中", "insufficient"

    failed_metrics = normality.get("failed_metrics") if isinstance(normality.get("failed_metrics"), list) else []
    failed_types = {
        str(item.get("type") or "").strip()
        for item in failed_metrics
        if isinstance(item, dict) and str(item.get("type") or "").strip()
    }
    metrics = normality.get("metrics") if isinstance(normality.get("metrics"), list) else []
    low_days = [
        item
        for item in metrics
        if isinstance(item, dict)
        and (str(item.get("status") or "") == "low" or str(item.get("rule_hit") or "") == "percentile_below_p15")
    ]
    if len(low_days) >= max(3, min(5, len(metrics) or 0)):
        return label or "密切关注奶量中", "attention"

    if len(failed_types) > 1:
        return label or "留意奶量变化", "attention"

    if "lactation" in failed_types or reason == "lactation_out_of_range":
        return label or "留意奶量变化", "attention"

    if "feeding" in failed_types or reason == "feeding_out_of_range":
        return label or "继续观察中", "insufficient"

    return label or "继续观察中", "attention"


def _failed_day_statuses(failed_metrics: list[Any], *, metric_type: str) -> set[str]:
    statuses: set[str] = set()
    for metric in failed_metrics:
        if not isinstance(metric, dict) or str(metric.get("type") or "") != metric_type:
            continue
        days = metric.get("days") if isinstance(metric.get("days"), list) else []
        for day in days:
            if not isinstance(day, dict):
                continue
            status = str(day.get("status") or "").strip()
            if status:
                statuses.add(status)
    return statuses


def _failed_feeding_directions(failed_metrics: list[Any]) -> set[str]:
    directions: set[str] = set()
    for metric in failed_metrics:
        if not isinstance(metric, dict) or str(metric.get("type") or "") != "feeding":
            continue
        days = metric.get("days") if isinstance(metric.get("days"), list) else []
        for day in days:
            if not isinstance(day, dict):
                continue
            feeding_count = _safe_float(day.get("feeding_count_total"))
            p25 = _safe_float(day.get("feeding_frequency_p25"))
            p75 = _safe_float(day.get("feeding_frequency_p75"))
            if p25 > 0 and feeding_count < p25:
                directions.add("low")
            if p75 > 0 and feeding_count > p75:
                directions.add("high")
    return directions


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _growth_query_response(
    *,
    error: int,
    growth_id: int = 0,
    height_mes_time: str = "",
    height_cm: float = 0,
    weight_mes_time: str = "",
    weight_kg: float = 0,
    head_mes_time: str = "",
    head_cm: float = 0,
) -> dict[str, Any]:
    return {
        "error": int(error),
        "growth_id": int(growth_id or 0),
        "height_mes_time": str(height_mes_time or ""),
        "height_cm": float(height_cm or 0),
        "weight_mes_time": str(weight_mes_time or ""),
        "weight_kg": float(weight_kg or 0),
        "head_mes_time": str(head_mes_time or ""),
        "head_cm": float(head_cm or 0),
    }


def _growth_history_response(*, error: int, growth_data: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"error": int(error), "growth_data": growth_data if isinstance(growth_data, list) else []}


def _plan_task_response(*, error: int, plan_type: str = "None", task_list: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "error": int(error),
        "plan_type": str(plan_type or "None"),
        "task_list": task_list if isinstance(task_list, list) else [],
    }


def _plan_task_mutation_response(*, error: int, task_list: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "error": int(error),
        "task_list": task_list if isinstance(task_list, list) else [],
    }


def _feeding_forecast_p50(user_id: str) -> float:
    reference = assess_feeding_demand_reference(user_id=user_id)
    if not reference.get("ok"):
        return 0.0
    return float(reference.get("p50_value") or 0)


def _normalize_date(raw: Any) -> str:
    token = str(raw or "").strip()
    if not token:
        return ""
    token = token[:-1] + "+00:00" if token.endswith("Z") else token
    for parser in (datetime.fromisoformat,):
        try:
            return parser(token).date().isoformat()
        except Exception:
            pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(str(raw), fmt).date().isoformat()
        except Exception:
            pass
    return ""


def _normalize_hhmm_to_today(raw: Any, field: str) -> str:
    return f"{datetime.now().strftime('%Y-%m-%d')} {_normalize_hhmm(raw, field)}:00"


def _normalize_hhmm(raw: Any, field: str) -> str:
    token = str(raw or "").strip()
    if not token:
        raise ValueError(f"{field} is required")
    try:
        return datetime.strptime(token, "%H:%M").strftime("%H:%M")
    except Exception:
        raise ValueError(f"{field} must be in HH:MM format") from None


def _normalize_utc_time(raw: Any, field: str) -> str:
    token = str(raw or "").strip()
    if not token:
        raise ValueError(f"{field} is required")
    normalized = token[:-1] + "+00:00" if token.endswith("Z") else token
    try:
        dt = datetime.fromisoformat(normalized)
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                dt = datetime.strptime(token, fmt)
                break
            except Exception:
                dt = None  # type: ignore[assignment]
        if dt is None:
            raise ValueError(f"{field} must be a valid UTC time string")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _hhmm_from_datetime(raw: Any) -> str:
    token = str(raw or "").strip()
    if not token:
        return ""
    try:
        return datetime.fromisoformat(token).strftime("%H:%M")
    except Exception:
        pass
    if " " in token:
        token = token.split(" ")[-1]
    return token[:5] if len(token) >= 5 else token


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return dict(value)


def _int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        return int(value)
    except Exception:
        raise ValueError(f"{field} must be an integer") from None


def _positive_int(value: Any, field: str) -> int:
    parsed = _int(value, field)
    if parsed <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def _optional_positive_int(value: Any, field: str) -> int | None:
    if value in (None, ""):
        return None
    return _positive_int(value, field)


def _enum_int(value: Any, field: str, allowed: set[int]) -> int:
    parsed = _int(value, field)
    if parsed not in allowed:
        allowed_text = ", ".join(str(item) for item in sorted(allowed))
        raise ValueError(f"{field} must be one of {allowed_text}")
    return parsed


def _positive_float(value: Any, field: str) -> float:
    try:
        parsed = float(value)
    except Exception:
        raise ValueError(f"{field} must be a number") from None
    if parsed <= 0:
        raise ValueError(f"{field} must be a positive number")
    return parsed


def _optional_positive_float(value: Any, field: str) -> float | None:
    if value in (None, ""):
        return None
    return _positive_float(value, field)


def _clamp_process(value: float) -> int:
    return max(0, min(PROCESS_REST_THRESHOLD, int(round(value))))


def _normalize_text(text: str) -> str:
    normalized = text.replace("\r", " ").replace("\n", " ").strip()
    return re.sub(r"\s+", " ", normalized)


def _xfyun_tts_configured() -> bool:
    return bool(os.getenv("XFYUN_TTS_APP_ID") and os.getenv("XFYUN_TTS_API_KEY") and os.getenv("XFYUN_TTS_API_SECRET"))


def _xfyun_asr_configured() -> bool:
    return bool((os.getenv("XFYUN_ASR_APP_ID") or os.getenv("XFYUN_TTS_APP_ID")) and (os.getenv("XFYUN_ASR_API_KEY") or os.getenv("XFYUN_TTS_API_KEY")) and (os.getenv("XFYUN_ASR_API_SECRET") or os.getenv("XFYUN_TTS_API_SECRET")))
