from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .agent import ESPAgentService
from .esp_mcp_toolkit import ESPMCPToolkit, MCPServerConfig

load_dotenv()
logger = logging.getLogger(__name__)
startup_warnings: list[str] = []

app = FastAPI(title="ESP32 LangGraph Agent API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

toolkit = ESPMCPToolkit()
service = ESPAgentService(toolkit=toolkit)


class AgentInvokeRequest(BaseModel):
    message: str = Field(..., min_length=1)
    thread_id: str = Field(default="default")


class CopilotKitRequest(BaseModel):
    messages: list[dict[str, Any]] = Field(default_factory=list)
    thread_id: str | None = None
    stream: bool = False


class ServerUpsertRequest(BaseModel):
    name: str = Field(..., min_length=1)
    base_url: str = Field(..., min_length=1)
    transport: str = Field(default="mcp_jsonrpc")
    mcp_path: str = Field(default="/mcp")
    timeout_seconds: float = Field(default=15.0, gt=0)
    auth_token: str | None = None


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
    return f"data: {data}\\n\\n"


def _extract_latest_user_message(messages: list[dict[str, Any]]) -> str | None:
    user_messages = [m for m in messages if m.get("role") == "user"]
    if not user_messages:
        return None

    latest = user_messages[-1].get("content")
    if isinstance(latest, list):
        text_parts = [part.get("text", "") for part in latest if isinstance(part, dict)]
        return " ".join(x for x in text_parts if x)
    return str(latest)


@app.on_event("startup")
async def startup() -> None:
    raw = os.getenv("ESP_MCP_SERVERS_JSON", "[]").strip()
    if raw.startswith("'") and raw.endswith("'"):
        raw = raw[1:-1]

    try:
        parsed = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        _warn(
            "ESP_MCP_SERVERS_JSON is not valid JSON. Starting with no servers. "
            'Expected a JSON array like: [{"name":"esp32-plants","base_url":"http://esp32-plants.local"}]'
        )
        parsed = []

    servers = parsed if isinstance(parsed, list) else [parsed]
    if not isinstance(parsed, (list, dict)):
        _warn(
            "ESP_MCP_SERVERS_JSON must be a JSON object or array. "
            f"Got {type(parsed).__name__}. Starting with no servers."
        )
        servers = []

    for idx, server in enumerate(servers):
        if not isinstance(server, dict):
            _warn(
                "Skipping ESP_MCP_SERVERS_JSON"
                f"[{idx}]: expected object, got {type(server).__name__}"
            )
            continue

        name = server.get("name")
        base_url = server.get("base_url")
        if not name or not base_url:
            _warn(f"Skipping ESP_MCP_SERVERS_JSON[{idx}]: missing 'name' or 'base_url'")
            continue

        toolkit.register_server(
            MCPServerConfig(
                name=str(name),
                base_url=str(base_url),
                transport=str(server.get("transport", "mcp_jsonrpc")),
                mcp_path=str(server.get("mcp_path", "/mcp")),
                timeout_seconds=float(server.get("timeout_seconds", 15.0)),
                auth_token=server.get("auth_token"),
            )
        )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status")
async def status() -> dict[str, Any]:
    return {
        "status": "ok",
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
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
        return await service.invoke(payload.message, payload.thread_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/agent/stream")
async def agent_stream(payload: AgentInvokeRequest) -> StreamingResponse:
    async def event_generator() -> AsyncIterator[str]:
        try:
            async for token in service.stream(payload.message, payload.thread_id):
                yield _sse(json.dumps({"type": "token", "content": token}))
            yield _sse(json.dumps({"type": "done"}))
        except Exception as exc:
            yield _sse(json.dumps({"type": "error", "error": str(exc)}))

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/copilotkit")
async def copilotkit_invoke(payload: dict[str, Any]) -> dict[str, Any]:
    method = payload.get("method")
    if method == "info":
        return _runtime_info()

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
                    "message": {
                        "role": "assistant",
                        "content": "",
                    },
                    "finish_reason": "stop",
                }
            ],
            "thread_id": thread_id,
        }

    if parsed.stream:
        stream_id = f"chatcmpl-{uuid4()}"

        async def stream_generator() -> AsyncIterator[str]:
            try:
                header = {
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
                yield _sse(json.dumps(header))

                async for token in service.stream(latest_message, thread_id=thread_id):
                    chunk = {
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
                    yield _sse(json.dumps(chunk))

                end = {
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
                yield _sse(json.dumps(end))
                yield _sse("[DONE]")
            except Exception as exc:
                err = {
                    "error": {
                        "message": str(exc),
                        "type": "server_error",
                    }
                }
                yield _sse(json.dumps(err))
                yield _sse("[DONE]")

        return StreamingResponse(stream_generator(), media_type="text/event-stream")

    result = await service.invoke(latest_message, thread_id=thread_id)

    return {
        "id": f"chatcmpl-{uuid4()}",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": result["response"],
                },
                "finish_reason": "stop",
            }
        ],
        "thread_id": thread_id,
    }
