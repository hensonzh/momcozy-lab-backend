from __future__ import annotations

import base64
from typing import Any, cast
from urllib.parse import urlsplit

from app.core.errors import ApiError
from app.infrastructure.object_storage.base import ObjectStorage
from app.agent_runtime.runs.models import AgentWorkflowState
from app.agent_runtime.runs.service import AgentRuntimeService
from app.agent_runtime.tools.result import ToolImageOutput, ToolResult, ToolTextOutput
from app.agent_runtime.tools.executor import ToolHandlerContext
from app.modules.assets.service import ProductAssetService
from app.modules.devices.service import DevicesService
from app.agents.cozymate.device_guidance import AIR1_UNBOXING_STEPS, DeviceGuidanceReferenceService


from .base import (
    _StandardToolHandler,
)
from .shared import (
    _AIR1_PRODUCT_HIGHLIGHTS,
    _air1_flange_recommendation,
    _asset_media_voice_payloads,
    _asset_payload,
    _device_guidance_step_asset_topic,
    _device_payload,
    _device_unboxing_workflow_payload,
    _exact_guidance_step_assets,
    _filter_guidance_assets,
    _limit,
    _packaged_image_asset_for_url,
    _quick_start_resources,
    _read_product_asset_bytes,
    _require_active_device_unboxing,
    _required_thread_id,
    _string_list,
    _telemetry_payload,
    _text,
)


class DevicesPumpStatusReadToolHandler(_StandardToolHandler):
    def __init__(self, *, devices_service: DevicesService) -> None:
        self.devices_service = devices_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        owner_user_id = context.actor.user_id
        limit = _limit(context.args.get("limit"), default=5, max_limit=20)
        devices = await self.devices_service.list_devices(owner_user_id=owner_user_id)
        telemetry = await self.devices_service.list_telemetry_events(owner_user_id=owner_user_id, limit=limit)
        bounded_devices = devices[:limit]
        output: dict[str, Any] = {
            "pumps": [_device_payload(device) for device in bounded_devices],
            "telemetry": [_telemetry_payload(event) for event in telemetry],
            "counts": {
                "pumps": len(bounded_devices),
                "telemetry": len(telemetry),
            },
        }
        return output


class DeviceGuidanceReadToolHandler(_StandardToolHandler):
    def __init__(
        self,
        *,
        asset_service: ProductAssetService,
        reference_service: DeviceGuidanceReferenceService | None = None,
    ) -> None:
        self.asset_service = asset_service
        self.reference_service = reference_service or DeviceGuidanceReferenceService()

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        limit = _limit(context.args.get("limit"), default=10, max_limit=20)
        content_type = _text(context.args, "content_type")
        model = _text(context.args, "model")
        topic = _text(context.args, "topic")
        step = _text(context.args, "step")
        query = _text(context.args, "query")
        reference = self.reference_service.read(
            model=model,
            topic=topic,
            step=step,
            query=query,
            limit=limit,
        )
        assets = self.asset_service.list_assets(limit=200)
        if content_type:
            assets = [asset for asset in assets if asset.content_type == content_type]
        current_step = reference.get("current_step")
        resolved_step = _text(current_step, "id") if isinstance(current_step, dict) else ""
        if step and resolved_step:
            assets = _exact_guidance_step_assets(
                assets=assets,
                image_urls=self.reference_service.image_urls_for_step(resolved_step),
            )
        else:
            assets = _filter_guidance_assets(
                assets=assets,
                model=model,
                topic=topic or _device_guidance_step_asset_topic(step),
                query=query,
            )
        bounded_assets = assets[:limit]
        asset_payloads = [_asset_payload(asset) for asset in bounded_assets]
        all_asset_payloads = [_asset_payload(asset) for asset in assets]
        result = {
            **reference,
            "assets": asset_payloads,
            "count": len(bounded_assets),
            "available_count": len(assets),
            "product_highlights": list(_AIR1_PRODUCT_HIGHLIGHTS),
            "quick_start_resources": _quick_start_resources(all_asset_payloads),
            "flange_recommendation": _air1_flange_recommendation(context.args.get("measured_nipple_mm")),
            "query_context": {
                "model": model,
                "topic": topic,
                "step": step,
                "query": query,
                "measured_nipple_mm": context.args.get("measured_nipple_mm"),
            },
        }
        media_voice = _asset_media_voice_payloads(asset_payloads)
        if media_voice:
            result["media_voice"] = media_voice
        return result


class DeviceUnboxingAdvanceToolHandler(_StandardToolHandler):
    WORKFLOW_TYPE = "device_unboxing"
    SCHEMA_VERSION = "device-unboxing.v1"

    def __init__(
        self,
        *,
        runtime_service: AgentRuntimeService,
        asset_service: ProductAssetService,
        reference_service: DeviceGuidanceReferenceService | None = None,
    ) -> None:
        self.runtime_service = runtime_service
        self.reference_service = reference_service or DeviceGuidanceReferenceService()
        self.guidance_reader = DeviceGuidanceReadToolHandler(
            asset_service=asset_service,
            reference_service=self.reference_service,
        )

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        if context.thread_id is None:
            raise ApiError(code="missing_thread_context", message="Device unboxing requires a thread context.", status=409)
        action = _text(context.args, "action")
        model = _text(context.args, "model")
        existing = await self.runtime_service.get_latest_workflow_state(
            owner_user_id=context.actor.user_id,
            thread_id=context.thread_id,
            workflow_type=self.WORKFLOW_TYPE,
        )
        if action == "start":
            return await self._start_or_resume(context=context, model=model, existing=existing, started=True)
        if action == "resume":
            return await self._start_or_resume(context=context, model=model, existing=existing, started=False)
        if action == "complete_current":
            return await self._complete_current(context=context, model=model, existing=existing)
        if action == "cancel":
            return await self._finish(context=context, model=model, existing=existing, phase="cancelled")
        raise ApiError(code="validation_failed", message="Unsupported device unboxing action.", status=422)

    async def _start_or_resume(
        self,
        *,
        context: ToolHandlerContext,
        model: str,
        existing: AgentWorkflowState | None,
        started: bool,
    ) -> dict[str, Any]:
        thread_id = _required_thread_id(context)
        if existing is not None and existing.status in {"collecting", "ready", "waiting", "paused"}:
            state = dict(existing.state) if isinstance(existing.state, dict) else {}
            current_step = existing.active_step or _text(state, "current_step")
            normalized_model = _text(state, "device_model") or model
            status = "unboxing_resumed"
        else:
            current_step = AIR1_UNBOXING_STEPS[0]
            initial_reference = self.reference_service.read(model=model, step=current_step)
            normalized_model = _text(initial_reference, "device_model")
            state = {
                "phase": "guiding",
                "device_model": normalized_model,
                "completed_steps": [],
                "document_version": _text(initial_reference, "document_version"),
            }
            status = "unboxing_started" if started else "unboxing_resumed"
        workflow = await self.runtime_service.upsert_workflow_state(
            owner_user_id=context.actor.user_id,
            thread_id=thread_id,
            run_id=context.run_id,
            workflow_type=self.WORKFLOW_TYPE,
            status="waiting",
            schema_version=self.SCHEMA_VERSION,
            state=state,
            active_step=current_step,
        )
        return await self._step_result(context=context, workflow=workflow, status=status)

    async def _complete_current(
        self,
        *,
        context: ToolHandlerContext,
        model: str,
        existing: AgentWorkflowState | None,
    ) -> dict[str, Any]:
        thread_id = _required_thread_id(context)
        workflow = _require_active_device_unboxing(existing)
        state = dict(workflow.state) if isinstance(workflow.state, dict) else {}
        current_step = workflow.active_step
        if current_step not in AIR1_UNBOXING_STEPS:
            raise ApiError(code="invalid_device_unboxing_step", message="The current device unboxing step is invalid.", status=409)
        normalized_model = _text(state, "device_model") or model
        self.reference_service.read(model=normalized_model, step=current_step)
        completed_steps = [step for step in state.get("completed_steps", []) if isinstance(step, str) and step in AIR1_UNBOXING_STEPS]
        if current_step not in completed_steps:
            completed_steps.append(current_step)
        current_index = AIR1_UNBOXING_STEPS.index(current_step)
        if current_index + 1 >= len(AIR1_UNBOXING_STEPS):
            return await self._finish(context=context, model=normalized_model, existing=workflow, phase="completed")
        next_step = AIR1_UNBOXING_STEPS[current_index + 1]
        state.update(
            {
                "phase": "guiding",
                "device_model": normalized_model,
                "completed_steps": completed_steps,
            }
        )
        updated = await self.runtime_service.upsert_workflow_state(
            owner_user_id=context.actor.user_id,
            thread_id=thread_id,
            run_id=context.run_id,
            workflow_type=self.WORKFLOW_TYPE,
            status="waiting",
            schema_version=self.SCHEMA_VERSION,
            state=state,
            active_step=next_step,
        )
        return await self._step_result(context=context, workflow=updated, status="unboxing_step_advanced")

    async def _finish(
        self,
        *,
        context: ToolHandlerContext,
        model: str,
        existing: AgentWorkflowState | None,
        phase: str,
    ) -> dict[str, Any]:
        thread_id = _required_thread_id(context)
        workflow = _require_active_device_unboxing(existing)
        state = dict(workflow.state) if isinstance(workflow.state, dict) else {}
        current_step = workflow.active_step
        completed_steps = [step for step in state.get("completed_steps", []) if isinstance(step, str) and step in AIR1_UNBOXING_STEPS]
        if phase == "completed" and current_step in AIR1_UNBOXING_STEPS and current_step not in completed_steps:
            completed_steps.append(current_step)
        state.update(
            {
                "phase": phase,
                "device_model": _text(state, "device_model") or model,
                "completed_steps": completed_steps,
            }
        )
        updated = await self.runtime_service.upsert_workflow_state(
            owner_user_id=context.actor.user_id,
            thread_id=thread_id,
            run_id=context.run_id,
            workflow_type=self.WORKFLOW_TYPE,
            status="completed",
            schema_version=self.SCHEMA_VERSION,
            state=state,
            active_step="",
        )
        workflow_projection = _device_unboxing_workflow_payload(updated)
        return {
            "status": "unboxing_completed" if phase == "completed" else "unboxing_cancelled",
            "workflow": workflow_projection,
        }

    async def _step_result(
        self,
        *,
        context: ToolHandlerContext,
        workflow: AgentWorkflowState,
        status: str,
    ) -> dict[str, Any]:
        workflow_projection = _device_unboxing_workflow_payload(workflow)
        guidance_result = await self.guidance_reader.execute(
            ToolHandlerContext(
                actor=context.actor,
                run_id=context.run_id,
                tool_name="devices.guidance.read",
                call_id=context.call_id,
                args={
                    "model": workflow_projection["device_model"],
                    "step": workflow_projection["current_step"],
                    "limit": 10,
                },
                thread_id=context.thread_id,
            )
        )
        output = {
            "status": status,
            "workflow": workflow_projection,
            "guidance": guidance_result,
        }
        return output


class ConversationHistoryImageLoadToolHandler(_StandardToolHandler):
    def __init__(self, *, asset_service: ProductAssetService, object_storage: ObjectStorage | None) -> None:
        self.asset_service = asset_service
        self.object_storage = object_storage

    async def execute(self, context: ToolHandlerContext) -> ToolResult:
        image_url = _text(context.args, "image_url")
        visible_image_urls = _string_list(context.args.get("visible_image_urls"))
        if image_url not in visible_image_urls:
            raise ApiError(
                code="image_reference_not_visible",
                message="The selected image URL is not visible in the current conversation context.",
                status=422,
            )
        detail = _text(context.args, "detail") or "low"
        if detail not in {"low", "high"}:
            raise ApiError(code="validation_failed", message="detail must be low or high.", status=422)

        asset = _packaged_image_asset_for_url(asset_service=self.asset_service, image_url=image_url)
        if asset is None:
            parsed = urlsplit(image_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ApiError(code="image_input_unavailable", message="The selected image cannot be loaded.", status=422)
            model_image_url = image_url
            asset_id = ""
            content_type = ""
        else:
            if not asset.content_type.startswith("image/"):
                raise ApiError(code="image_input_unavailable", message="The selected asset is not an image.", status=422)
            body = await _read_product_asset_bytes(asset=asset, object_storage=self.object_storage)
            model_image_url = f"data:{asset.content_type};base64,{base64.b64encode(body).decode('ascii')}"
            asset_id = asset.id
            content_type = asset.content_type

        safe_output = {
            "status": "image_context_ready",
            "image_url": image_url,
            "detail": detail,
        }
        if asset_id:
            safe_output["asset_id"] = asset_id
            safe_output["content_type"] = content_type
        return ToolResult(
            output=(
                *ToolResult.json(safe_output).output,
                ToolTextOutput(
                    text="这是当前对话历史中由智能体此前展示的目标图片。请结合当前用户问题，只依据图片中可见内容回答。"
                ),
                ToolImageOutput(
                    image_url=model_image_url,
                    detail=cast(Any, detail),
                ),
            ),
            audit_output=safe_output,
        )
