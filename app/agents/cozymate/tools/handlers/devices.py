from __future__ import annotations

import base64
from typing import Any, cast
from urllib.parse import urlsplit

from app.core.errors import ApiError
from app.infrastructure.object_storage.base import ObjectStorage
from app.agent_runtime.runs.models import AgentWorkflowState
from app.agent_runtime.runs.service import AgentRuntimeService
from app.agent_runtime.tools.result import ToolImageOutput, ToolResult
from app.agent_runtime.tools.executor import ToolHandlerContext
from app.modules.assets.service import ProductAssetService
from app.agents.cozymate.device_guidance import AIR1_UNBOXING_STEPS, DeviceGuidanceReferenceService


from .base import (
    _StandardToolHandler,
)
from ..pump_models import PumpModelsReferenceService
from .shared import (
    _AIR1_PRODUCT_HIGHLIGHTS,
    _air1_flange_recommendation,
    _asset_media_voice_payloads,
    _asset_payload,
    _device_guidance_step_asset_topic,
    _device_unboxing_workflow_payload,
    _exact_guidance_step_assets,
    _filter_guidance_assets,
    _packaged_image_asset_for_url,
    _quick_start_resources,
    _read_product_asset_bytes,
    _require_active_device_unboxing,
    _required_thread_id,
    _string_list,
    _text,
)


class _DeviceGuidanceContentService:
    def __init__(
        self,
        *,
        asset_service: ProductAssetService,
        reference_service: DeviceGuidanceReferenceService | None = None,
    ) -> None:
        self.asset_service = asset_service
        self.reference_service = reference_service or DeviceGuidanceReferenceService()

    async def read(self, args: dict[str, Any]) -> dict[str, Any]:
        model = _text(args, "model")
        topic = _text(args, "topic")
        step = _text(args, "step")
        resource_kind = _text(args, "resource_kind") or "auto"
        if resource_kind not in {"auto", "image", "pdf", "video"}:
            raise ApiError(code="validation_failed", message="Unsupported device guidance resource kind.", status=422)
        reference = self.reference_service.read(
            model=model,
            topic=topic,
            step=step,
        )
        assets = self.asset_service.list_assets(limit=200)
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
            )
        assets = _filter_assets_by_resource_kind(assets, resource_kind=resource_kind)
        bounded_assets = assets[:10]
        asset_payloads = [_asset_payload(asset) for asset in bounded_assets]
        all_asset_payloads = [_asset_payload(asset) for asset in assets]
        guidance = {
            "topic": topic,
            "step": current_step,
            "guide_outline": reference["guide_outline"],
            "assets": asset_payloads,
            "count": len(bounded_assets),
            "available_count": len(assets),
            "product_highlights": list(_AIR1_PRODUCT_HIGHLIGHTS),
            "resources": _quick_start_resources(all_asset_payloads),
            "flange_recommendation": _air1_flange_recommendation(args.get("measured_nipple_mm")),
        }
        media_voice = _asset_media_voice_payloads(asset_payloads)
        if media_voice:
            guidance["media_voice"] = media_voice
        return {
            "device_model": reference["device_model"],
            "document_version": reference["document_version"],
            "guidance": guidance,
        }


class PumpModelsReadToolHandler(_StandardToolHandler):
    def __init__(self, *, service: PumpModelsReferenceService) -> None:
        self.service = service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        del context
        return await self.service.tool_result()


class DeviceGuidanceToolHandler(_StandardToolHandler):
    WORKFLOW_TYPE = "device_unboxing"
    SCHEMA_VERSION = "device-unboxing.v1"
    RESULT_SCHEMA_VERSION = "device-guidance.result.v1"

    def __init__(
        self,
        *,
        runtime_service: AgentRuntimeService,
        asset_service: ProductAssetService,
        reference_service: DeviceGuidanceReferenceService | None = None,
    ) -> None:
        self.runtime_service = runtime_service
        self.reference_service = reference_service or DeviceGuidanceReferenceService()
        self.content_service = _DeviceGuidanceContentService(
            asset_service=asset_service,
            reference_service=self.reference_service,
        )

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        operation = _text(context.args, "operation")
        model = _text(context.args, "model")
        _validate_device_guidance_args(args=context.args, operation=operation, model=model)
        if operation == "read":
            content = await self.content_service.read(context.args)
            return {
                "schema_version": self.RESULT_SCHEMA_VERSION,
                "status": "content_ready",
                "mode": "direct",
                **content,
                "workflow": None,
            }
        if context.thread_id is None:
            raise ApiError(code="missing_thread_context", message="Device walkthrough requires a thread context.", status=409)
        existing = await self.runtime_service.get_latest_workflow_state(
            owner_user_id=context.actor.user_id,
            thread_id=context.thread_id,
            workflow_type=self.WORKFLOW_TYPE,
        )
        if operation == "start_or_resume":
            return await self._start_or_resume(context=context, model=model, existing=existing)
        if operation == "complete_current":
            return await self._complete_current(context=context, model=model, existing=existing)
        if operation == "cancel":
            return await self._finish(context=context, model=model, existing=existing, phase="cancelled")
        raise ApiError(code="validation_failed", message="Unsupported device guidance operation.", status=422)

    async def _start_or_resume(
        self,
        *,
        context: ToolHandlerContext,
        model: str,
        existing: AgentWorkflowState | None,
    ) -> dict[str, Any]:
        thread_id = _required_thread_id(context)
        if existing is not None and existing.status in {"collecting", "ready", "waiting", "paused"}:
            state = dict(existing.state) if isinstance(existing.state, dict) else {}
            current_step = existing.active_step or _text(state, "current_step")
            normalized_model = _text(state, "device_model") or model
            status = "walkthrough_resumed"
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
            status = "walkthrough_started"
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
        return await self._step_result(context=context, workflow=updated, status="walkthrough_step_advanced")

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
            "schema_version": self.RESULT_SCHEMA_VERSION,
            "status": "walkthrough_completed" if phase == "completed" else "walkthrough_cancelled",
            "mode": "walkthrough",
            "device_model": workflow_projection["device_model"],
            "document_version": _text(state, "document_version"),
            "guidance": None,
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
        content = await self.content_service.read(
            {
                "model": workflow_projection["device_model"],
                "step": workflow_projection["current_step"],
                "resource_kind": "auto",
            }
        )
        return {
            "schema_version": self.RESULT_SCHEMA_VERSION,
            "status": status,
            "mode": "walkthrough",
            **content,
            "workflow": workflow_projection,
        }


def _filter_assets_by_resource_kind(assets: list[Any], *, resource_kind: str) -> list[Any]:
    if resource_kind == "image":
        return [asset for asset in assets if asset.content_type.startswith("image/")]
    if resource_kind == "pdf":
        return [asset for asset in assets if asset.content_type == "application/pdf"]
    if resource_kind == "video":
        return [asset for asset in assets if asset.content_type.startswith("video/")]
    return assets


def _validate_device_guidance_args(*, args: dict[str, Any], operation: str, model: str) -> None:
    if operation not in {"read", "start_or_resume", "complete_current", "cancel"}:
        raise ApiError(code="validation_failed", message="Unsupported device guidance operation.", status=422)
    if operation in {"read", "start_or_resume"} and not model:
        raise ApiError(
            code="validation_failed",
            message="Starting or reading device guidance requires a model.",
            status=422,
        )

    topic = _text(args, "topic")
    step = _text(args, "step")
    if operation == "read":
        if not topic and not step:
            raise ApiError(
                code="validation_failed",
                message="Device guidance read requires a topic or step.",
                status=422,
            )
        if topic and step:
            raise ApiError(
                code="validation_failed",
                message="Device guidance read accepts either topic or step, not both.",
                status=422,
            )
        if "measured_nipple_mm" in args and topic != "flange":
            raise ApiError(
                code="validation_failed",
                message="measured_nipple_mm is only supported for the flange topic.",
                status=422,
            )
        return

    workflow_only_extras = {"topic", "step", "resource_kind", "measured_nipple_mm"} & args.keys()
    if workflow_only_extras:
        raise ApiError(
            code="validation_failed",
            message="Walkthrough operations do not accept direct-read parameters.",
            status=422,
        )


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

        canonical_output = {
            "status": "image_context_ready",
            "image_url": image_url,
            "detail": detail,
            "agent_instruction": (
                "这是当前对话历史中由智能体此前展示的目标图片。"
                "请结合当前用户问题，只依据图片中可见内容回答。"
            ),
        }
        if asset_id:
            canonical_output["asset_id"] = asset_id
            canonical_output["content_type"] = content_type
        return ToolResult.json(
            canonical_output,
            supplemental_content=(
                ToolImageOutput(
                    image_url=model_image_url,
                    detail=cast(Any, detail),
                ),
            ),
        )
