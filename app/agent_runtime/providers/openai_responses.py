from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Protocol, cast

from ...core.metrics import RequestMetrics
from ...core.errors import ApiError
from app.agent_runtime.context.items import ContextItemAppend
from app.agent_runtime.runs.response_text import AppendOnlyAgentResponseProjector, sanitize_agent_response_text
from app.agent_runtime.tools.result import FunctionCallOutput, ToolResult


SdkToolInvoker = Callable[[str], Awaitable[ToolResult]]
SdkTextDeltaHandler = Callable[[str], Awaitable[None]]
SdkContextItemHandler = Callable[[tuple[ContextItemAppend, ...]], Awaitable[None]]


class SdkImageUrlResolver(Protocol):
    async def __call__(
        self,
        *,
        thread_id: str,
        actor_user_id: str,
        asset_id: str,
    ) -> str: ...


@dataclass(frozen=True)
class SdkToolDefinition:
    contract_name: str
    sdk_name: str
    description: str
    params_json_schema: dict[str, Any]
    invoke: SdkToolInvoker
    namespace_name: str = ""
    defer_loading: bool = False


@dataclass(frozen=True)
class SdkToolNamespace:
    name: str
    description: str
    tool_names: tuple[str, ...]
    deferred_tool_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class SdkNodeRequest:
    run_id: str
    thread_id: str
    actor_user_id: str
    instructions: str
    model_input: list[dict[str, Any]]
    tool_names: tuple[str, ...] = ()
    tool_namespaces: tuple[SdkToolNamespace, ...] = ()
    tool_search_enabled: bool = False
    tools: tuple[SdkToolDefinition, ...] = ()
    prompt_version: str = ""
    trace_id: str = ""
    service_skill_id: str = ""
    on_text_delta: SdkTextDeltaHandler | None = None
    on_context_items: SdkContextItemHandler | None = None
    response_text_format: dict[str, Any] | None = None
    web_search_enabled: bool = False
    web_search_required: bool = False
    web_search_allowed_domains: tuple[str, ...] = ()


@dataclass(frozen=True)
class SdkNodeResult:
    final_text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    web_search_used: bool = False
    web_search_citations: list[dict[str, Any]] = field(default_factory=list)


class SdkRunnerBackend(Protocol):
    async def run(self, request: SdkNodeRequest) -> SdkNodeResult: ...


class AgentModelRunner(Protocol):
    async def run_reasoning(self, request: SdkNodeRequest) -> SdkNodeResult: ...

    def supports_tool_namespaces(self) -> bool: ...

    def supports_web_search(self) -> bool: ...


class OpenAIResponsesApiBackend:
    def __init__(
        self,
        *,
        model: str,
        max_turns: int = 10,
        api_key: str = "",
        base_url: str = "",
        reasoning_effort: str = "low",
        text_verbosity: str = "low",
        store_responses: bool = False,
        image_url_resolver: SdkImageUrlResolver | None = None,
    ) -> None:
        self.model = model
        self.max_turns = max_turns
        self.api_key = api_key
        self.base_url = base_url
        self.reasoning_effort = reasoning_effort
        self.text_verbosity = text_verbosity
        self.store_responses = store_responses
        self.image_url_resolver = image_url_resolver

    async def run(self, request: SdkNodeRequest) -> SdkNodeResult:
        try:
            openai_module = importlib.import_module("openai")
        except ImportError as exc:
            raise ApiError(code="dependency_not_configured", message="OpenAI Python SDK is not installed.", status=503) from exc

        async_openai_cls = getattr(openai_module, "AsyncOpenAI", None)
        if async_openai_cls is None:
            raise ApiError(code="dependency_not_configured", message="OpenAI Python SDK AsyncOpenAI is unavailable.", status=503)
        if _is_real_openai_module(openai_module) and not _has_openai_credentials(api_key=self.api_key):
            raise ApiError(
                code="dependency_not_configured",
                message="OpenAI SDK credentials are not configured for Responses tool search.",
                status=503,
            )

        client_kwargs = {"api_key": self.api_key or None}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        client = async_openai_cls(**client_kwargs)
        responses = getattr(client, "responses", None)
        create_response = getattr(responses, "create", None)
        stream_response = getattr(responses, "stream", None)
        if create_response is None:
            raise ApiError(code="dependency_not_configured", message="OpenAI Responses API client is unavailable.", status=503)

        tools_payload = responses_tools_payload(request)
        context: list[Any] = await _responses_input_items(
            request.model_input,
            thread_id=request.thread_id,
            actor_user_id=request.actor_user_id,
            image_url_resolver=self.image_url_resolver,
        )
        tools_by_address = {(tool.namespace_name, tool.sdk_name): tool for tool in request.tools}
        tools_by_name = {tool.sdk_name: tool for tool in request.tools}
        observed_tool_calls: list[dict[str, Any]] = []
        latest_response: Any | None = None

        for _turn_index in range(self.max_turns):
            streamed_text = ""
            if request.on_text_delta is not None:
                latest_response, streamed_text, _ = await _create_response_streamed(
                    create_response=create_response,
                    stream_response=stream_response if callable(stream_response) else None,
                    model=self.model,
                    instructions=request.instructions,
                    context=context,
                    tools_payload=tools_payload,
                    reasoning_effort=self.reasoning_effort,
                    text_verbosity=self.text_verbosity,
                    store_responses=self.store_responses,
                    response_text_format=request.response_text_format,
                    web_search_enabled=request.web_search_enabled,
                    web_search_required=request.web_search_required,
                    on_text_delta=request.on_text_delta,
                )
            else:
                latest_response = await create_response(
                    **_responses_request_kwargs(
                        model=self.model,
                        instructions=request.instructions,
                        context=context,
                        tools_payload=tools_payload,
                        reasoning_effort=self.reasoning_effort,
                        text_verbosity=self.text_verbosity,
                        store_responses=self.store_responses,
                        response_text_format=request.response_text_format,
                        web_search_enabled=request.web_search_enabled,
                        web_search_required=request.web_search_required,
                    )
                )
            output_items = _response_output_items(latest_response)
            function_calls: list[dict[str, str]] = []
            for item in output_items:
                function_call = _response_function_call(item)
                if function_call is not None:
                    function_calls.append(function_call)
            input_output_items = _response_output_items_for_input(output_items)
            await _persist_context_items(
                request=request,
                items=(
                    input_output_items
                    if function_calls
                    else [item for item in input_output_items if item.get("type") != "message"]
                ),
                response_id=_response_id(latest_response),
            )
            if not function_calls:
                final_text = _response_output_text(latest_response, output_items=output_items) or streamed_text
                sanitized_text = final_text.strip() if request.response_text_format is not None else _sanitize_model_text(final_text)
                return SdkNodeResult(
                    final_text=sanitized_text,
                    tool_calls=observed_tool_calls,
                    web_search_used=_response_used_web_search(output_items),
                    web_search_citations=_web_search_citations_from_response(latest_response),
                )

            context.extend(input_output_items)
            for function_call in function_calls:
                tool = tools_by_address.get((function_call["namespace"], function_call["name"]))
                if tool is None and not function_call["namespace"]:
                    tool = tools_by_name.get(function_call["name"])
                if tool is None:
                    raise ApiError(
                        code="sdk_unknown_tool_call",
                        message="Responses API returned a function call for an unavailable tool.",
                        status=502,
                        details={"sdk_tool_name": function_call["name"]},
                    )
                args_json = function_call["arguments"]
                try:
                    invocation = await tool.invoke(args_json)
                except ApiError as exc:
                    if _fatal_tool_error(exc):
                        raise
                    error_output = _tool_error_model_output(exc)
                    error_item = {
                        "type": "function_call_output",
                        "call_id": function_call["call_id"],
                        "output": error_output,
                    }
                    context.append(error_item)
                    await _persist_context_items(request=request, items=[error_item])
                    observed_tool_calls.append(
                        {
                            "tool_name": tool.contract_name,
                            "status": "failed",
                            "args": _json_object_or_raw(args_json),
                            "error_code": exc.code,
                            "safe_output": _json_object_or_raw(error_output),
                        }
                    )
                    continue
                function_call_output, observed_output = _invocation_outputs(invocation)
                result_item = {
                    "type": "function_call_output",
                    "call_id": function_call["call_id"],
                    "output": function_call_output,
                }
                context.append(result_item)
                await _persist_context_items(request=request, items=[result_item])
                observed_tool_calls.append(
                    {
                        "tool_name": tool.contract_name,
                        "status": "completed",
                        "args": _json_object_or_raw(args_json),
                        "safe_output": observed_output,
                    }
                )

        raise ApiError(
            code="sdk_run_max_turns_exceeded",
            message="Responses API run exceeded the configured tool-call turn limit.",
            status=504,
            details={"response_id": _response_id(latest_response)},
        )


class OpenAIResponsesRunner:
    def __init__(
        self,
        *,
        backend: SdkRunnerBackend | None = None,
        metrics: RequestMetrics | None = None,
        model: str = "gpt-5.6-terra",
        max_turns: int = 10,
        timeout_seconds: float = 60,
        api_key: str = "",
        base_url: str = "",
        reasoning_effort: str = "low",
        text_verbosity: str = "low",
        store_responses: bool = False,
        metrics_node_name: str = "openai_responses",
        image_url_resolver: SdkImageUrlResolver | None = None,
    ) -> None:
        self.backend = backend
        self.metrics = metrics
        self.model = model
        self.max_turns = max_turns
        self.timeout_seconds = timeout_seconds
        self.provider = "openai"
        self.api_key = api_key
        self.base_url = base_url
        self.reasoning_effort = reasoning_effort
        self.text_verbosity = text_verbosity
        self.store_responses = store_responses
        self.metrics_node_name = metrics_node_name
        self.image_url_resolver = image_url_resolver

    async def run_reasoning(self, request: SdkNodeRequest) -> SdkNodeResult:
        started_at = perf_counter()
        try:
            backend = self.backend or OpenAIResponsesApiBackend(
                model=self.model,
                max_turns=self.max_turns,
                api_key=self.api_key,
                base_url=self.base_url,
                reasoning_effort=self.reasoning_effort,
                text_verbosity=self.text_verbosity,
                store_responses=self.store_responses,
                image_url_resolver=self.image_url_resolver,
            )
            result = await asyncio.wait_for(backend.run(request), timeout=self.timeout_seconds)
            self._record(outcome="completed", error_code="", started_at=started_at)
            return result
        except TimeoutError as exc:
            self._record(outcome="failed", error_code="sdk_run_timed_out", started_at=started_at)
            raise ApiError(code="sdk_run_timed_out", message="OpenAI Responses run timed out.", status=504) from exc
        except ApiError as exc:
            self._record(outcome="failed", error_code=exc.code, started_at=started_at)
            raise
        except Exception as exc:
            mapped = _provider_error_mapping(exc)
            self._record(outcome="failed", error_code=mapped.code, started_at=started_at)
            raise ApiError(
                code=mapped.code,
                message=mapped.message,
                status=mapped.status,
                details=_provider_error_details(exc),
            ) from exc

    def supports_tool_namespaces(self) -> bool:
        return True

    def supports_web_search(self) -> bool:
        return True

    def _record(self, *, outcome: str, error_code: str, started_at: float) -> None:
        if self.metrics is not None:
            self.metrics.record_agent_sdk(
                node_name=self.metrics_node_name,
                outcome=outcome,
                error_code=error_code,
                duration_ms=(perf_counter() - started_at) * 1000,
            )


def responses_tools_payload(request: SdkNodeRequest) -> list[dict[str, Any]]:
    tools_by_contract = {tool.contract_name: tool for tool in request.tools}
    if not request.tool_namespaces:
        flat_payload = [_responses_function_tool_payload(tool) for tool in request.tools]
        if request.tool_search_enabled:
            flat_payload.append({"type": "tool_search"})
        if request.web_search_enabled:
            flat_payload.append(_responses_web_search_tool_payload(request.web_search_allowed_domains))
        return flat_payload

    payload: list[dict[str, Any]] = []
    namespaced_contracts = {contract_name for namespace in request.tool_namespaces for contract_name in namespace.tool_names}
    for tool in request.tools:
        if tool.contract_name not in namespaced_contracts:
            payload.append(_responses_function_tool_payload(tool))
    for namespace in request.tool_namespaces:
        missing_tool_names = [contract_name for contract_name in namespace.tool_names if contract_name not in tools_by_contract]
        if missing_tool_names:
            raise ApiError(
                code="sdk_tool_namespace_mismatch",
                message="SDK tool namespace references unavailable tool contracts.",
                status=500,
                details={"namespace": namespace.name, "missing_tool_names": missing_tool_names},
            )
        namespace_tools = [_responses_function_tool_payload(tools_by_contract[contract_name]) for contract_name in namespace.tool_names]
        payload.append(
            {
                "type": "namespace",
                "name": namespace.name,
                "description": namespace.description,
                "tools": namespace_tools,
            }
        )
    if request.tool_search_enabled:
        payload.append({"type": "tool_search"})
    if request.web_search_enabled:
        payload.append(_responses_web_search_tool_payload(request.web_search_allowed_domains))
    return payload


def _responses_web_search_tool_payload(allowed_domains: tuple[str, ...]) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": "web_search"}
    if allowed_domains:
        payload["filters"] = {"allowed_domains": list(allowed_domains)}
    return payload


def _responses_function_tool_payload(tool: SdkToolDefinition) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "function",
        "name": tool.sdk_name,
        "description": tool.description,
        "parameters": tool.params_json_schema,
    }
    if tool.defer_loading:
        payload["defer_loading"] = True
    return payload


async def _responses_input_items(
    model_input: list[dict[str, Any]],
    *,
    thread_id: str,
    actor_user_id: str,
    image_url_resolver: SdkImageUrlResolver | None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in model_input:
        item_type = item.get("type")
        if isinstance(item_type, str) and item_type:
            items.append(cast(dict[str, Any], _strip_sdk_only_response_fields(item)))
            continue
        role = str(item.get("role") or "user")
        content = item.get("content")
        item_content: str | list[Any]
        if isinstance(content, str):
            item_content = content
        elif isinstance(content, list):
            item_content = await _responses_message_content(
                content,
                thread_id=thread_id,
                actor_user_id=actor_user_id,
                image_url_resolver=image_url_resolver,
            )
        elif isinstance(content, dict):
            item_content = json.dumps(content, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        else:
            item_content = str(content)
        items.append({"role": role, "content": item_content})
    return items


async def _responses_message_content(
    content: list[Any],
    *,
    thread_id: str,
    actor_user_id: str,
    image_url_resolver: SdkImageUrlResolver | None,
) -> list[Any]:
    materialized: list[Any] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "input_image" or not block.get("asset_id"):
            materialized.append(dict(block) if isinstance(block, dict) else block)
            continue
        if image_url_resolver is None:
            raise ApiError(
                code="agent_image_url_unavailable",
                message="Agent image URL resolver is not configured.",
                status=503,
            )
        asset_id = str(block.get("asset_id") or "")
        image_url = await image_url_resolver(
            thread_id=thread_id,
            actor_user_id=actor_user_id,
            asset_id=asset_id,
        )
        detail = str(block.get("detail") or "auto")
        materialized.append(
            {
                "type": "input_image",
                "image_url": image_url,
                "detail": detail if detail in {"auto", "low", "high", "original"} else "auto",
            }
        )
    return materialized


async def _create_response_streamed(
    *,
    create_response: Callable[..., Awaitable[Any]],
    stream_response: Callable[..., Any] | None,
    model: str,
    instructions: str,
    context: list[Any],
    tools_payload: list[dict[str, Any]],
    reasoning_effort: str,
    text_verbosity: str,
    store_responses: bool,
    response_text_format: dict[str, Any] | None,
    web_search_enabled: bool,
    web_search_required: bool,
    on_text_delta: SdkTextDeltaHandler | None = None,
) -> tuple[Any | None, str, bool]:
    kwargs = _responses_request_kwargs(
        model=model,
        instructions=instructions,
        context=context,
        tools_payload=tools_payload,
        reasoning_effort=reasoning_effort,
        text_verbosity=text_verbosity,
        store_responses=store_responses,
        response_text_format=response_text_format,
        web_search_enabled=web_search_enabled,
        web_search_required=web_search_required,
    )
    stream = (
        await _maybe_await(stream_response(**kwargs))
        if stream_response is not None
        else await create_response(**kwargs, stream=True)
    )
    response, streamed_text, emitted_stream = await _consume_response_stream(stream, on_text_delta=on_text_delta)
    if response is None and not streamed_text:
        response = await create_response(**kwargs)
    return response, streamed_text, emitted_stream


def _responses_request_kwargs(
    *,
    model: str,
    instructions: str,
    context: list[Any],
    tools_payload: list[dict[str, Any]],
    reasoning_effort: str,
    text_verbosity: str,
    store_responses: bool,
    response_text_format: dict[str, Any] | None,
    web_search_enabled: bool = False,
    web_search_required: bool = False,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": list(context),
        "tools": tools_payload,
        "parallel_tool_calls": False,
        "reasoning": {"effort": reasoning_effort},
        "text": {"verbosity": text_verbosity},
        "store": store_responses,
    }
    if not store_responses:
        kwargs["include"] = ["reasoning.encrypted_content"]
    if web_search_enabled:
        kwargs.setdefault("include", []).append("web_search_call.action.sources")
    if web_search_required:
        kwargs["tool_choice"] = {
            "type": "allowed_tools",
            "mode": "required",
            "tools": [{"type": "web_search"}],
        }
    if response_text_format is not None:
        kwargs["text"]["format"] = response_text_format
    return kwargs


async def _consume_response_stream(stream: Any, *, on_text_delta: SdkTextDeltaHandler | None = None) -> tuple[Any | None, str, bool]:
    response: Any | None = None
    raw_text = ""
    emitted_stream = False
    if hasattr(stream, "__aenter__"):
        async with stream as entered_stream:
            response, raw_text, emitted_stream = await _iterate_response_stream(entered_stream, on_text_delta=on_text_delta)
            if response is None:
                response = await _stream_final_response(entered_stream)
        if response is None:
            response = await _stream_final_response(stream)
        return response, raw_text, emitted_stream
    response, raw_text, emitted_stream = await _iterate_response_stream(stream, on_text_delta=on_text_delta)
    if response is None:
        response = await _stream_final_response(stream)
    return response, raw_text, emitted_stream


async def _iterate_response_stream(
    stream: Any,
    *,
    on_text_delta: SdkTextDeltaHandler | None = None,
) -> tuple[Any | None, str, bool]:
    response: Any | None = None
    raw_text = ""
    projector = AppendOnlyAgentResponseProjector()
    emitted_stream = False
    stream_events = stream
    if not hasattr(stream_events, "__aiter__"):
        stream_events_factory = getattr(stream_events, "stream_events", None)
        if callable(stream_events_factory):
            stream_events = stream_events_factory()
    async for event in stream_events:
        delta = _text_delta_from_response_stream_event(event)
        if delta:
            raw_text += delta
            if on_text_delta is not None:
                sanitized_delta = projector.push(delta)
                if sanitized_delta:
                    await on_text_delta(sanitized_delta)
                    emitted_stream = True
        event_response = _response_from_response_stream_event(event)
        if event_response is not None:
            response = event_response
    if on_text_delta is not None:
        final_delta = projector.finalize()
        if final_delta:
            await on_text_delta(final_delta)
            emitted_stream = True
    return response, raw_text, emitted_stream


def _text_delta_from_response_stream_event(event: Any) -> str:
    event_type = str(_item_value(event, "type", "") or "")
    if event_type != "response.output_text.delta":
        return ""
    delta = _item_value(event, "delta", "")
    return delta if isinstance(delta, str) else ""


def _response_from_response_stream_event(event: Any) -> Any | None:
    event_type = str(_item_value(event, "type", "") or "")
    if event_type != "response.completed":
        return None
    response = _item_value(event, "response", None)
    return response if response is not None else None


async def _stream_final_response(stream: Any) -> Any | None:
    for method_name in ("get_final_response", "get_final_output"):
        method = getattr(stream, method_name, None)
        if callable(method):
            return await _maybe_await(method())
    for attr in ("final_response", "response"):
        value = getattr(stream, attr, None)
        if value is not None:
            return value
    return None


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _response_output_items(response: Any) -> list[Any]:
    output = _item_value(response, "output", [])
    if isinstance(output, list | tuple):
        return list(output)
    return []


def _response_output_items_for_input(output_items: list[Any]) -> list[dict[str, Any]]:
    return [_response_output_item_for_input(item) for item in output_items]


def _response_output_item_for_input(item: Any) -> dict[str, Any]:
    if _item_value(item, "type") == "function_call":
        function_call = _response_function_call(item)
        if function_call is None:  # pragma: no cover - guarded by the item type above
            raise ApiError(
                code="sdk_malformed_tool_call",
                message="Responses API returned a malformed function call item.",
                status=502,
            )
        payload: dict[str, Any] = {
            "type": "function_call",
            "call_id": function_call["call_id"],
            "name": function_call["name"],
            "arguments": function_call["arguments"],
        }
        for key in ("id", "status"):
            value = _item_value(item, key)
            if isinstance(value, str) and value:
                payload[key] = value
        if function_call["namespace"]:
            payload["namespace"] = function_call["namespace"]
        return payload

    if isinstance(item, dict):
        payload = item
    else:
        model_dump = getattr(item, "model_dump", None)
        if not callable(model_dump):
            raise ApiError(
                code="sdk_malformed_response_item",
                message="Responses API returned an unsupported output item.",
                status=502,
                details={"item_type": _item_value(item, "type")},
            )
        payload = model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
            exclude=getattr(item, "__api_exclude__", None),
        )
    return cast(dict[str, Any], _strip_sdk_only_response_fields(payload))


def _strip_sdk_only_response_fields(value: Any) -> Any:
    if isinstance(value, dict):
        item_type = str(value.get("type") or "")
        excluded_fields = {
            "function_call": {"parsed_arguments"},
            "output_text": {"parsed"},
        }.get(item_type, set())
        return {key: _strip_sdk_only_response_fields(item) for key, item in value.items() if key not in excluded_fields}
    if isinstance(value, list | tuple):
        return [_strip_sdk_only_response_fields(item) for item in value]
    return value


def _response_function_call(item: Any) -> dict[str, str] | None:
    if _item_value(item, "type") != "function_call":
        return None
    name = str(_item_value(item, "name", "") or "")
    call_id = str(_item_value(item, "call_id", "") or _item_value(item, "id", "") or "")
    raw_arguments = _item_value(item, "arguments", "{}")
    if isinstance(raw_arguments, str):
        arguments = raw_arguments or "{}"
    else:
        arguments = json.dumps(raw_arguments or {}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if not name or not call_id:
        raise ApiError(
            code="sdk_malformed_tool_call",
            message="Responses API returned a malformed function call item.",
            status=502,
            details={"item_type": _item_value(item, "type")},
        )
    namespace = str(_item_value(item, "namespace", "") or "")
    return {"namespace": namespace, "name": name, "call_id": call_id, "arguments": arguments}


def _response_output_text(response: Any, *, output_items: list[Any]) -> str:
    output_text = _item_value(response, "output_text", "")
    if isinstance(output_text, str) and output_text:
        return output_text
    text_parts: list[str] = []
    for item in output_items:
        item_type = _item_value(item, "type", "")
        if item_type == "message":
            text_parts.extend(_response_content_texts(_item_value(item, "content", [])))
        elif item_type in {"output_text", "text"}:
            text = _item_value(item, "text", "")
            if isinstance(text, str):
                text_parts.append(text)
    return "".join(text_parts)


def _response_used_web_search(output_items: list[Any]) -> bool:
    return any(_item_value(item, "type", "") == "web_search_call" for item in output_items)


def _web_search_citations_from_response(response: Any) -> list[dict[str, Any]]:
    output_items = _response_output_items(response)
    citations: list[dict[str, Any]] = []
    for item in output_items:
        if _item_value(item, "type") != "message":
            continue
        content = _item_value(item, "content", [])
        if not isinstance(content, list | tuple):
            continue
        for part in content:
            annotations = _item_value(part, "annotations", [])
            if not isinstance(annotations, list | tuple):
                continue
            for annotation in annotations:
                citation = _web_search_citation(annotation)
                if citation is not None:
                    citations = _merge_web_search_citations(citations, [citation])
    if citations:
        return citations
    for item in output_items:
        action = _item_value(item, "action", None)
        for container in (action, item):
            for key in ("sources", "results"):
                sources = _item_value(container, key, [])
                if not isinstance(sources, list | tuple):
                    continue
                citations = _merge_web_search_citations(
                    citations,
                    [citation for source in sources if (citation := _web_search_citation(source)) is not None],
                )
    return citations


def _web_search_citation(value: Any) -> dict[str, Any] | None:
    url = _item_value(value, "url", "")
    if not isinstance(url, str) or not url.strip().startswith(("https://", "http://")):
        return None
    title = _item_value(value, "title", "")
    return {
        "url": url.strip(),
        "title": str(title or "").strip() or "参考来源",
    }


def _merge_web_search_citations(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for group in groups:
        for item in group:
            url = str(item.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            merged.append(dict(item))
            if len(merged) >= 8:
                return merged
    return merged


def _response_content_texts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list | tuple):
        return []
    text_parts: list[str] = []
    for part in content:
        part_type = _item_value(part, "type", "")
        if part_type in {"output_text", "text"}:
            text = _item_value(part, "text", "")
            if isinstance(text, str):
                text_parts.append(text)
    return text_parts


def _item_value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _json_object_or_raw(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


async def _persist_context_items(
    *,
    request: SdkNodeRequest,
    items: list[dict[str, Any]],
    response_id: str = "",
) -> None:
    if request.on_context_items is None or not items:
        return
    pending = tuple(
        ContextItemAppend(
            item_key=_context_item_key(
                run_id=request.run_id,
                item=item,
                response_id=response_id,
                index=index,
            ),
            item=dict(item),
        )
        for index, item in enumerate(items)
    )
    await request.on_context_items(pending)


def _context_item_key(*, run_id: str, item: dict[str, Any], response_id: str, index: int) -> str:
    prefix = f"run:{run_id}"
    item_type = str(item.get("type") or "message")
    item_id = str(item.get("id") or "")
    if item_id:
        return f"{prefix}:provider:{item_id}"
    call_id = str(item.get("call_id") or "")
    if call_id:
        return f"{prefix}:{item_type}:{call_id}"
    return f"{prefix}:response:{response_id or 'local'}:{index}:{item_type}"


def _invocation_outputs(invocation: ToolResult) -> tuple[FunctionCallOutput, Any]:
    return invocation.to_function_call_output(), invocation.to_observation()


def _response_id(response: Any | None) -> str:
    if response is None:
        return ""
    return str(_item_value(response, "id", "") or "")


def _sanitize_model_text(text: str) -> str:
    return sanitize_agent_response_text(text).text


def sdk_tool_name(contract_name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", contract_name).strip("_")
    return normalized or "tool"


def _fatal_tool_error(exc: ApiError) -> bool:
    return exc.code == "tool_commit_failed" or exc.details.get("fatal") is True


def _tool_error_model_output(exc: ApiError) -> str:
    return json.dumps(
        {"error": {"code": exc.code, "message": "Tool call was rejected by application policy."}},
        sort_keys=True,
    )


def _is_real_openai_module(openai_module: Any) -> bool:
    return bool(getattr(openai_module, "__file__", ""))


def _has_openai_credentials(*, api_key: str) -> bool:
    if api_key:
        return True
    return bool(os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_ADMIN_KEY"))


@dataclass(frozen=True)
class _ProviderErrorMapping:
    code: str
    message: str
    status: int


def _provider_error_mapping(exc: Exception) -> _ProviderErrorMapping:
    status_code = _provider_status_code(exc)
    name = exc.__class__.__name__.lower()
    text = str(exc).lower()
    if status_code == 429 or "rate" in name and "limit" in name or "rate limit" in text:
        return _ProviderErrorMapping(
            code="sdk_rate_limited",
            message="OpenAI Responses provider rate limit was reached.",
            status=429,
        )
    if status_code in {401, 403} or "auth" in name or "permission" in name:
        return _ProviderErrorMapping(
            code="sdk_auth_failed",
            message="OpenAI Responses provider authentication failed.",
            status=503,
        )
    if status_code is not None and status_code >= 500:
        return _ProviderErrorMapping(
            code="sdk_provider_unavailable",
            message="OpenAI Responses provider is unavailable.",
            status=503,
        )
    if status_code in {400, 422}:
        return _ProviderErrorMapping(
            code="sdk_bad_request",
            message="OpenAI Responses provider rejected the request.",
            status=502,
        )
    return _ProviderErrorMapping(
        code="sdk_run_failed",
        message="OpenAI Responses run failed.",
        status=502,
    )


def _provider_status_code(exc: Exception) -> int | None:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    return response_status if isinstance(response_status, int) else None


def _provider_error_details(exc: Exception) -> dict[str, Any]:
    details: dict[str, Any] = {
        "exception_type": exc.__class__.__name__,
    }
    status_code = _provider_status_code(exc)
    if status_code is not None:
        details["provider_status_code"] = status_code
    provider_error = getattr(exc, "body", None) or getattr(exc, "error", None)
    if provider_error is not None:
        details["provider_error_type"] = provider_error.__class__.__name__
    message = _redact_exception_text(str(exc))
    if message:
        details["message_excerpt"] = message[:500]
    return details


def _redact_exception_text(text: str) -> str:
    redacted = re.sub(r"(?i)(api[_-]?key|authorization|bearer|token)(['\"\\s:=]+)[^\\s,'\"]+", r"\1\2[redacted]", text)
    redacted = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "[redacted]", redacted)
    return redacted.strip()
