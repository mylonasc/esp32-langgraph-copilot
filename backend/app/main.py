from __future__ import annotations

import json
import logging
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .agent import ESPAgentService
from .esp_mcp_toolkit import ESPMCPToolkit, MCPServerConfig
from .network_discovery_toolkit import LocalNetworkDiscoveryToolkit
from .react_agent_factory import get_thread_message_counts
from .settings import AppSettings

logger = logging.getLogger(__name__)
startup_warnings: list[str] = []
settings = AppSettings()

app = FastAPI(title="ESP32 LangGraph Agent API", version="0.1.0")

cors_allow_origins = settings.parsed_cors_origins()
allow_credentials = settings.cors_allow_credentials and cors_allow_origins != ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

toolkit = ESPMCPToolkit()
discovery_toolkit = LocalNetworkDiscoveryToolkit(mcp_toolkit=toolkit)
service = ESPAgentService(toolkit=toolkit, settings=settings)


class AgentInvokeRequest(BaseModel):
    message: str = Field(..., min_length=1)
    thread_id: str = Field(default="default")


class CopilotKitRequest(BaseModel):
    messages: list[dict[str, Any]] = Field(default_factory=list)
    thread_id: str | None = None
    stream: bool = False


class AGUIEnvelope(BaseModel):
    method: str
    params: dict[str, Any] = Field(default_factory=dict)
    body: dict[str, Any] = Field(default_factory=dict)


class ServerUpsertRequest(BaseModel):
    name: str = Field(..., min_length=1)
    base_url: str = Field(..., min_length=1)
    transport: str = Field(default="mcp_jsonrpc", pattern="^(mcp_jsonrpc|esp32_rest)$")
    mcp_path: str = Field(default="/mcp")
    timeout_seconds: float = Field(default=15.0, gt=0)
    auth_token: str | None = None


class ServerTestRequest(BaseModel):
    base_url: str = Field(..., min_length=1)
    transport: str = Field(default="mcp_jsonrpc", pattern="^(mcp_jsonrpc|esp32_rest)$")
    mcp_path: str = Field(default="/mcp")
    timeout_seconds: float = Field(default=15.0, gt=0)
    auth_token: str | None = None


class DiscoveryScanRequest(BaseModel):
    subnet_cidr: str = ""
    ports_csv: str = "80,8000,8080,8090"
    timeout_seconds: float = Field(default=1.2, gt=0)
    max_hosts: int = Field(default=64, gt=0)
    save: bool = False
    name_prefix: str = "discovered"


def _runtime_info() -> dict[str, Any]:
    return {
        "version": "0.1.0",
        "audioFileTranscriptionEnabled": False,
        "agents": {
            "default": {
                "name": "default",
                "className": "ESPLangGraphAgent",
                "description": "LangGraph ESP32 MCP assistant",
            }
        },
    }


def _warn(message: str) -> None:
    startup_warnings.append(message)
    logger.warning(message)


def _sse(data: str) -> str:
    return f"data: {data}\n\n"


def _extract_latest_user_message(messages: list[dict[str, Any]]) -> str | None:
    user_messages = [m for m in messages if m.get("role") == "user"]
    if not user_messages:
        return None

    latest = user_messages[-1].get("content")
    if isinstance(latest, list):
        text_parts = [part.get("text", "") for part in latest if isinstance(part, dict)]
        return " ".join(x for x in text_parts if x)
    return str(latest)


def _extract_latest_user_message_agui(messages: list[dict[str, Any]]) -> str | None:
    users = [message for message in messages if message.get("role") == "user"]
    if not users:
        return None

    latest = users[-1].get("content", "")
    if isinstance(latest, str):
        return latest
    if isinstance(latest, list):
        text_parts: list[str] = []
        for part in latest:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text")
                if isinstance(text, str) and text:
                    text_parts.append(text)
        return " ".join(text_parts).strip() or None
    return str(latest)


def _fake_response_text(message: str) -> str:
    cleaned = message.strip() or "(empty message)"
    return (
        "Fake mode response\n\n"
        f"You said: `{cleaned}`\n\n"
        "```text\n"
        "tool: esp_fake_tool\n"
        "status: ok\n"
        "```"
    )


async def _stream_agent_events(
    message: str,
    thread_id: str,
    messages: list[dict[str, Any]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    if settings.agent_fake_mode:
        fallback_message = message
        if not fallback_message and isinstance(messages, list):
            fallback_message = _extract_latest_user_message(messages) or ""
        yield {
            "type": "thinking_start",
            "title": "Planning",
        }
        yield {
            "type": "thinking",
            "content": "Checking available tools and preparing an execution plan...",
        }
        yield {
            "type": "thinking_end",
        }
        yield {
            "type": "tool_start",
            "tool_name": "esp_fake_tool",
            "input": json.dumps({"query": fallback_message}),
            "tool_call_key": "fake_tool_call_1",
        }
        yield {
            "type": "tool_end",
            "tool_name": "esp_fake_tool",
            "output": json.dumps({"ok": True, "thread_id": thread_id}),
            "tool_call_key": "fake_tool_call_1",
        }
        for token in _fake_response_text(fallback_message).split(" "):
            yield {"type": "token", "content": f"{token} "}
        return

    async for event in service.stream_events(
        message=message,
        thread_id=thread_id,
        messages=messages,
    ):
        yield event


def _agui_event(event_type: str, **kwargs: Any) -> dict[str, Any]:
    return {"type": event_type, **kwargs}


def _normalize_agui_input(
    input_payload: dict[str, Any], thread_id: str, run_id: str
) -> dict[str, Any]:
    messages = input_payload.get("messages")
    tools = input_payload.get("tools")
    context = input_payload.get("context")
    state = input_payload.get("state")
    forwarded_props = input_payload.get("forwardedProps")

    return {
        "threadId": thread_id,
        "runId": run_id,
        "messages": messages if isinstance(messages, list) else [],
        "tools": tools if isinstance(tools, list) else [],
        "context": context if isinstance(context, list) else [],
        "state": state if isinstance(state, dict) else {},
        "forwardedProps": forwarded_props if isinstance(forwarded_props, dict) else {},
    }


async def _agui_run_stream(
    latest_message: str,
    thread_id: str,
    run_id: str,
    input_payload: dict[str, Any],
    messages: list[dict[str, Any]] | None = None,
) -> AsyncIterator[str]:
    message_id = f"msg_{uuid4().hex}"
    tool_counter = 0
    tool_call_ids_by_key: dict[str, str] = {}
    pending_tool_call_ids_by_name: dict[str, deque[str]] = defaultdict(deque)
    real_thinking_seen = False
    synthetic_thinking_open = False
    thinking_text_open = False

    def _new_tool_call_id() -> str:
        nonlocal tool_counter
        tool_counter += 1
        return f"tool_{tool_counter}_{uuid4().hex[:8]}"

    async def _emit_synthetic_thinking_start() -> AsyncIterator[str]:
        nonlocal synthetic_thinking_open, thinking_text_open
        if synthetic_thinking_open or real_thinking_seen:
            return
        synthetic_thinking_open = True
        thinking_text_open = True
        yield _sse(json.dumps(_agui_event("THINKING_START", title="Reasoning")))
        yield _sse(json.dumps(_agui_event("THINKING_TEXT_MESSAGE_START")))
        yield _sse(
            json.dumps(
                _agui_event(
                    "THINKING_TEXT_MESSAGE_CONTENT",
                    delta="Analyzing your request and planning tool usage...",
                )
            )
        )

    async def _emit_thinking_end_if_open() -> AsyncIterator[str]:
        nonlocal synthetic_thinking_open, thinking_text_open
        if thinking_text_open:
            yield _sse(json.dumps(_agui_event("THINKING_TEXT_MESSAGE_END")))
            thinking_text_open = False
        if synthetic_thinking_open:
            yield _sse(json.dumps(_agui_event("THINKING_END")))
            synthetic_thinking_open = False

    async def _resolve_or_create_tool_call_id(
        tool_name: str, tool_call_key: str | None = None
    ) -> tuple[str, bool]:
        if tool_call_key:
            existing = tool_call_ids_by_key.get(tool_call_key)
            if existing:
                return existing, False

        tool_call_id = _new_tool_call_id()
        if tool_call_key:
            tool_call_ids_by_key[tool_call_key] = tool_call_id
        pending_tool_call_ids_by_name[tool_name].append(tool_call_id)
        return tool_call_id, True

    async def _consume_tool_call_id(
        tool_name: str, tool_call_key: str | None = None
    ) -> str:
        if tool_call_key and tool_call_key in tool_call_ids_by_key:
            tool_call_id = tool_call_ids_by_key.pop(tool_call_key)
            pending = pending_tool_call_ids_by_name.get(tool_name)
            if pending:
                try:
                    pending.remove(tool_call_id)
                except ValueError:
                    pass
            return tool_call_id

        pending = pending_tool_call_ids_by_name.get(tool_name)
        if pending:
            return pending.popleft()

        return _new_tool_call_id()

    normalized_input = _normalize_agui_input(input_payload, thread_id, run_id)
    yield _sse(
        json.dumps(
            _agui_event(
                "RUN_STARTED",
                threadId=thread_id,
                runId=run_id,
                input=normalized_input,
            )
        )
    )

    yield _sse(
        json.dumps(
            _agui_event(
                "TEXT_MESSAGE_START",
                messageId=message_id,
                role="assistant",
            )
        )
    )

    try:
        async for synthetic_event in _emit_synthetic_thinking_start():
            yield synthetic_event

        async for event in _stream_agent_events(
            latest_message,
            thread_id,
            messages=messages,
        ):
            kind = event.get("type")
            if kind in {"thinking_start", "thinking", "thinking_end"}:
                real_thinking_seen = True
                if synthetic_thinking_open:
                    async for synthetic_end in _emit_thinking_end_if_open():
                        yield synthetic_end

                if kind == "thinking_start":
                    title = event.get("title")
                    yield _sse(json.dumps(_agui_event("THINKING_START", title=title)))
                    if not thinking_text_open:
                        yield _sse(
                            json.dumps(_agui_event("THINKING_TEXT_MESSAGE_START"))
                        )
                        thinking_text_open = True
                elif kind == "thinking":
                    content = event.get("content")
                    if isinstance(content, str) and content:
                        if not thinking_text_open:
                            yield _sse(
                                json.dumps(_agui_event("THINKING_TEXT_MESSAGE_START"))
                            )
                            thinking_text_open = True
                        yield _sse(
                            json.dumps(
                                _agui_event(
                                    "THINKING_TEXT_MESSAGE_CONTENT", delta=content
                                )
                            )
                        )
                elif kind == "thinking_end":
                    if thinking_text_open:
                        yield _sse(json.dumps(_agui_event("THINKING_TEXT_MESSAGE_END")))
                        thinking_text_open = False
                    yield _sse(json.dumps(_agui_event("THINKING_END")))

            elif kind == "token":
                if thinking_text_open or synthetic_thinking_open:
                    async for synthetic_end in _emit_thinking_end_if_open():
                        yield synthetic_end

                content = event.get("content")
                if isinstance(content, str) and content:
                    yield _sse(
                        json.dumps(
                            _agui_event(
                                "TEXT_MESSAGE_CONTENT",
                                messageId=message_id,
                                delta=content,
                            )
                        )
                    )
            elif kind == "tool_start":
                if thinking_text_open or synthetic_thinking_open:
                    async for synthetic_end in _emit_thinking_end_if_open():
                        yield synthetic_end

                tool_name = str(event.get("tool_name", "tool"))
                tool_input = str(event.get("input", "{}"))
                tool_call_key = event.get("tool_call_key")
                tool_call_id, is_new = await _resolve_or_create_tool_call_id(
                    tool_name=tool_name,
                    tool_call_key=str(tool_call_key) if tool_call_key else None,
                )
                if is_new:
                    yield _sse(
                        json.dumps(
                            _agui_event(
                                "TOOL_CALL_START",
                                toolCallId=tool_call_id,
                                toolCallName=tool_name,
                                parentMessageId=message_id,
                            )
                        )
                    )
                yield _sse(
                    json.dumps(
                        _agui_event(
                            "TOOL_CALL_ARGS",
                            toolCallId=tool_call_id,
                            delta=tool_input,
                        )
                    )
                )
                yield _sse(
                    json.dumps(_agui_event("TOOL_CALL_END", toolCallId=tool_call_id))
                )
            elif kind == "tool_end":
                if thinking_text_open or synthetic_thinking_open:
                    async for synthetic_end in _emit_thinking_end_if_open():
                        yield synthetic_end

                tool_name = str(event.get("tool_name", "tool"))
                tool_output = str(event.get("output", ""))
                tool_call_key = event.get("tool_call_key")
                tool_call_id = await _consume_tool_call_id(
                    tool_name=tool_name,
                    tool_call_key=str(tool_call_key) if tool_call_key else None,
                )
                yield _sse(
                    json.dumps(
                        _agui_event(
                            "TOOL_CALL_RESULT",
                            messageId=f"toolmsg_{uuid4().hex[:8]}",
                            toolCallId=tool_call_id,
                            role="tool",
                            toolName=tool_name,
                            content=f"[{tool_name}] {tool_output}",
                        )
                    )
                )

        if thinking_text_open or synthetic_thinking_open:
            async for synthetic_end in _emit_thinking_end_if_open():
                yield synthetic_end

        yield _sse(json.dumps(_agui_event("TEXT_MESSAGE_END", messageId=message_id)))
        yield _sse(
            json.dumps(
                _agui_event(
                    "RUN_FINISHED",
                    threadId=thread_id,
                    runId=run_id,
                )
            )
        )
    except Exception as exc:
        yield _sse(
            json.dumps(_agui_event("RUN_ERROR", message=str(exc), code="server_error"))
        )


async def _test_server_connectivity(payload: ServerTestRequest) -> dict[str, Any]:
    headers: dict[str, str] = {}
    if payload.auth_token:
        headers["Authorization"] = f"Bearer {payload.auth_token}"

    if payload.transport == "mcp_jsonrpc":
        endpoint = f"{payload.base_url.rstrip('/')}/{payload.mcp_path.lstrip('/')}"
        body = {
            "jsonrpc": "2.0",
            "id": "connectivity-check",
            "method": "tools/list",
            "params": {},
        }
        try:
            async with httpx.AsyncClient(timeout=payload.timeout_seconds) as client:
                response = await client.post(endpoint, json=body, headers=headers)
                response.raise_for_status()
                data = response.json()

            if isinstance(data, dict) and data.get("error"):
                return {
                    "ok": False,
                    "transport": payload.transport,
                    "endpoint": endpoint,
                    "error": f"MCP responded with error: {data.get('error')}",
                }

            tools = []
            if isinstance(data, dict):
                result = data.get("result", {})
                if isinstance(result, dict):
                    tools = result.get("tools", [])

            return {
                "ok": True,
                "transport": payload.transport,
                "endpoint": endpoint,
                "details": {
                    "tools_count": len(tools) if isinstance(tools, list) else None
                },
            }
        except httpx.TimeoutException as exc:
            return {
                "ok": False,
                "transport": payload.transport,
                "endpoint": endpoint,
                "error": f"Timeout while connecting: {exc}",
            }
        except httpx.ConnectError as exc:
            return {
                "ok": False,
                "transport": payload.transport,
                "endpoint": endpoint,
                "error": f"Connection failed (possible DNS/host issue): {exc}",
            }
        except Exception as exc:
            return {
                "ok": False,
                "transport": payload.transport,
                "endpoint": endpoint,
                "error": str(exc),
            }

    endpoint = f"{payload.base_url.rstrip('/')}/api"
    try:
        async with httpx.AsyncClient(timeout=payload.timeout_seconds) as client:
            response = await client.get(endpoint, headers=headers)
            response.raise_for_status()
        return {
            "ok": True,
            "transport": payload.transport,
            "endpoint": endpoint,
            "details": {"status_code": response.status_code},
        }
    except httpx.TimeoutException as exc:
        return {
            "ok": False,
            "transport": payload.transport,
            "endpoint": endpoint,
            "error": f"Timeout while connecting: {exc}",
        }
    except httpx.ConnectError as exc:
        return {
            "ok": False,
            "transport": payload.transport,
            "endpoint": endpoint,
            "error": f"Connection failed (possible DNS/host issue): {exc}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "transport": payload.transport,
            "endpoint": endpoint,
            "error": str(exc),
        }


@app.on_event("startup")
async def startup() -> None:
    servers, warnings = settings.parsed_server_configs()
    for warning in warnings:
        _warn(warning)

    for server in servers:
        toolkit.register_server(
            MCPServerConfig(
                name=server.name,
                base_url=server.base_url,
                transport=server.transport,
                mcp_path=server.mcp_path,
                timeout_seconds=server.timeout_seconds,
                auth_token=server.auth_token,
            )
        )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status")
async def status() -> dict[str, Any]:
    return {
        "status": "ok",
        "openai_configured": bool(settings.openai_api_key),
        "openai_model": settings.openai_model,
        "cors_allow_origins": cors_allow_origins,
        "cors_allow_credentials": allow_credentials,
        "server_count": len(toolkit.list_servers()),
        "warnings": startup_warnings,
    }


@app.get("/servers")
async def list_servers() -> dict[str, Any]:
    return {"servers": toolkit.list_servers()}


@app.post("/servers")
async def add_server(payload: ServerUpsertRequest) -> dict[str, Any]:
    toolkit.register_server(
        MCPServerConfig(
            name=payload.name,
            base_url=payload.base_url,
            transport=payload.transport,
            mcp_path=payload.mcp_path,
            timeout_seconds=payload.timeout_seconds,
            auth_token=payload.auth_token,
        )
    )
    return {"ok": True, "servers": toolkit.list_servers()}


@app.put("/servers/{server_name}")
async def update_server(
    server_name: str, payload: ServerUpsertRequest
) -> dict[str, Any]:
    if server_name not in {server["name"] for server in toolkit.list_servers()}:
        raise HTTPException(status_code=404, detail=f"Server '{server_name}' not found")

    if payload.name != server_name:
        toolkit.remove_server(server_name)

    toolkit.register_server(
        MCPServerConfig(
            name=payload.name,
            base_url=payload.base_url,
            transport=payload.transport,
            mcp_path=payload.mcp_path,
            timeout_seconds=payload.timeout_seconds,
            auth_token=payload.auth_token,
        )
    )
    return {"ok": True, "servers": toolkit.list_servers()}


@app.post("/servers/test")
async def test_server(payload: ServerTestRequest) -> dict[str, Any]:
    return await _test_server_connectivity(payload)


@app.get("/discovery/network")
async def discovery_network(subnet_cidr: str = "") -> dict[str, Any]:
    return await discovery_toolkit.network_info(subnet_cidr=subnet_cidr)


@app.post("/discovery/scan")
async def discovery_scan(payload: DiscoveryScanRequest) -> dict[str, Any]:
    return await discovery_toolkit.discover_mcp_servers(
        subnet_cidr=payload.subnet_cidr,
        ports_csv=payload.ports_csv,
        timeout_seconds=payload.timeout_seconds,
        max_hosts=payload.max_hosts,
        save=payload.save,
        name_prefix=payload.name_prefix,
    )


@app.delete("/servers/{server_name}")
async def delete_server(server_name: str) -> dict[str, Any]:
    deleted = toolkit.remove_server(server_name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Server '{server_name}' not found")
    return {"ok": True, "servers": toolkit.list_servers()}


@app.get("/copilotkit/info")
async def copilotkit_info() -> dict[str, Any]:
    return _runtime_info()


@app.post("/agent/invoke")
async def agent_invoke(payload: AgentInvokeRequest) -> dict[str, Any]:
    try:
        if settings.agent_fake_mode:
            return {
                "response": _fake_response_text(payload.message),
                "thread_id": payload.thread_id,
                "messages": [],
            }
        return await service.invoke(
            message=payload.message, thread_id=payload.thread_id
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/agent/stream")
async def agent_stream(payload: AgentInvokeRequest) -> StreamingResponse:
    async def event_generator() -> AsyncIterator[str]:
        try:
            async for event in _stream_agent_events(
                payload.message,
                payload.thread_id,
            ):
                yield _sse(json.dumps(event))
            yield _sse(json.dumps({"type": "done"}))
        except Exception as exc:
            yield _sse(json.dumps({"type": "error", "error": str(exc)}))

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/agent/message-count")
async def agent_message_count(thread_id: str) -> dict[str, Any]:
    counts = get_thread_message_counts(thread_id)
    return {
        "thread_id": thread_id,
        "raw_message_count": counts.get("raw_message_count"),
        "sanitized_message_count": counts.get("sanitized_message_count"),
    }


@app.post("/copilotkit")
async def copilotkit_invoke(payload: dict[str, Any]) -> Any:
    method = payload.get("method")
    if method is not None:
        envelope = AGUIEnvelope.model_validate(payload)
        if envelope.method == "info":
            return _runtime_info()

        if envelope.method == "agent/stop":
            return {"ok": True}

        if envelope.method == "agent/connect":
            connect_thread = str(envelope.params.get("threadId") or uuid4())
            connect_run = str(uuid4())
            connect_input = _normalize_agui_input({}, connect_thread, connect_run)

            async def connect_stream() -> AsyncIterator[str]:
                yield _sse(
                    json.dumps(
                        _agui_event(
                            "RUN_STARTED",
                            threadId=connect_thread,
                            runId=connect_run,
                            input=connect_input,
                        )
                    )
                )
                yield _sse(
                    json.dumps(
                        _agui_event(
                            "RUN_FINISHED",
                            threadId=connect_thread,
                            runId=connect_run,
                        )
                    )
                )

            return StreamingResponse(connect_stream(), media_type="text/event-stream")

        if envelope.method == "agent/run":
            body = envelope.body if isinstance(envelope.body, dict) else {}
            messages = body.get("messages")
            parsed_messages = messages if isinstance(messages, list) else []
            latest_message = _extract_latest_user_message_agui(parsed_messages)
            thread_id = str(
                body.get("threadId") or envelope.params.get("threadId") or uuid4()
            )
            run_id = str(body.get("runId") or uuid4())

            if not latest_message:
                latest_message = ""

            return StreamingResponse(
                _agui_run_stream(
                    latest_message=latest_message,
                    thread_id=thread_id,
                    run_id=run_id,
                    input_payload=body,
                    messages=parsed_messages,
                ),
                media_type="text/event-stream",
            )

        raise HTTPException(
            status_code=400, detail=f"Unsupported method: {envelope.method}"
        )

    parsed = CopilotKitRequest.model_validate(payload)
    latest_message = _extract_latest_user_message(parsed.messages)
    thread_id = parsed.thread_id or str(uuid4())

    if not latest_message:
        empty_id = f"chatcmpl-{uuid4()}"
        if parsed.stream:

            async def empty_stream_generator() -> AsyncIterator[str]:
                header = {
                    "id": empty_id,
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant"},
                            "finish_reason": None,
                        }
                    ],
                }
                end = {
                    "id": empty_id,
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "stop",
                        }
                    ],
                }
                yield _sse(json.dumps(header))
                yield _sse(json.dumps(end))
                yield _sse("[DONE]")

            return StreamingResponse(
                empty_stream_generator(), media_type="text/event-stream"
            )

        return {
            "id": empty_id,
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": "stop",
                }
            ],
            "thread_id": thread_id,
        }

    if parsed.stream:
        stream_id = f"chatcmpl-{uuid4()}"

        async def stream_generator() -> AsyncIterator[str]:
            try:
                yield _sse(
                    json.dumps(
                        {
                            "id": stream_id,
                            "object": "chat.completion.chunk",
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"role": "assistant"},
                                    "finish_reason": None,
                                }
                            ],
                        }
                    )
                )

                async for event in _stream_agent_events(
                    latest_message,
                    thread_id=thread_id,
                    messages=parsed.messages,
                ):
                    if event.get("type") != "token":
                        continue
                    token = event.get("content")
                    if not isinstance(token, str):
                        continue
                    yield _sse(
                        json.dumps(
                            {
                                "id": stream_id,
                                "object": "chat.completion.chunk",
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {"content": token},
                                        "finish_reason": None,
                                    }
                                ],
                            }
                        )
                    )

                yield _sse(
                    json.dumps(
                        {
                            "id": stream_id,
                            "object": "chat.completion.chunk",
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {},
                                    "finish_reason": "stop",
                                }
                            ],
                        }
                    )
                )
                yield _sse("[DONE]")
            except Exception as exc:
                yield _sse(
                    json.dumps(
                        {
                            "error": {
                                "message": str(exc),
                                "type": "server_error",
                            }
                        }
                    )
                )
                yield _sse("[DONE]")

        return StreamingResponse(stream_generator(), media_type="text/event-stream")

    if settings.agent_fake_mode:
        response_text = _fake_response_text(latest_message)
    else:
        result = await service.invoke(
            message=latest_message,
            thread_id=thread_id,
            messages=parsed.messages,
        )
        response_text = str(result["response"])

    return {
        "id": f"chatcmpl-{uuid4()}",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": response_text},
                "finish_reason": "stop",
            }
        ],
        "thread_id": thread_id,
    }
