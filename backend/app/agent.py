from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
)
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from .esp_mcp_toolkit import ESPMCPToolkit
from .network_discovery_toolkit import LocalNetworkDiscoveryToolkit
from .settings import AppSettings


SYSTEM_PROMPT = (
    "You are an ESP32 operations copilot. "
    "Use available MCP tools to inspect devices and perform actions. "
    "For discovery tasks, use local network discovery tools first, then confirm findings. "
    "When tools fail, explain the failure clearly and suggest the next check."
)


class ESPAgentService:
    def __init__(self, toolkit: ESPMCPToolkit, settings: AppSettings) -> None:
        self.toolkit = toolkit
        self.settings = settings
        self.discovery_toolkit = LocalNetworkDiscoveryToolkit(mcp_toolkit=toolkit)
        self._graph = self._build_graph()

    def _build_graph(self):
        llm = ChatOpenAI(
            model=self.settings.openai_model,
            temperature=0,
            api_key=self.settings.openai_api_key,
        )
        tools = [
            *self.toolkit.as_langchain_tools(),
            *self.discovery_toolkit.as_langchain_tools(),
        ]
        return create_react_agent(model=llm, tools=tools)

    def _ensure_api_key(self) -> None:
        if not self.settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required to run the LangGraph agent")

    @staticmethod
    def _extract_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if isinstance(item, str):
                    chunks.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    chunks.append(item["text"])
            return "".join(chunks)
        return ""

    @staticmethod
    def _extract_thinking_text(chunk: AIMessageChunk) -> str:
        content = getattr(chunk, "content", None)
        thinking_chunks: list[str] = []

        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type", "")).lower()
                if item_type in {
                    "thinking",
                    "thinking_text",
                    "reasoning",
                    "reasoning_text",
                    "reasoning_content",
                }:
                    text = (
                        item.get("text") or item.get("content") or item.get("reasoning")
                    )
                    if isinstance(text, str) and text:
                        thinking_chunks.append(text)

        additional_kwargs = getattr(chunk, "additional_kwargs", None)
        if isinstance(additional_kwargs, dict):
            for key in (
                "reasoning",
                "reasoning_content",
                "thinking",
                "thinking_content",
            ):
                value = additional_kwargs.get(key)
                if isinstance(value, str) and value:
                    thinking_chunks.append(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            text = item.get("text") or item.get("content")
                            if isinstance(text, str) and text:
                                thinking_chunks.append(text)

        return "".join(thinking_chunks)

    async def invoke(self, message: str, thread_id: str = "default") -> dict[str, Any]:
        self._ensure_api_key()

        result = await self._graph.ainvoke(
            {
                "messages": [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=message),
                ]
            },
            config={"configurable": {"thread_id": thread_id}},
        )
        messages = result.get("messages", [])
        response_text = ""
        for item in reversed(messages):
            if isinstance(item, AIMessage):
                response_text = item.text()
                break

        return {
            "response": response_text,
            "messages": [
                m.model_dump() if hasattr(m, "model_dump") else str(m) for m in messages
            ],
        }

    async def stream(
        self, message: str, thread_id: str = "default"
    ) -> AsyncIterator[str]:
        async for event in self.stream_events(message=message, thread_id=thread_id):
            if event.get("type") == "token" and isinstance(event.get("content"), str):
                yield event["content"]

    @staticmethod
    def _compact(value: Any, max_len: int = 500) -> str:
        try:
            raw = (
                value
                if isinstance(value, str)
                else json.dumps(value, ensure_ascii=True)
            )
        except Exception:
            raw = str(value)
        if len(raw) <= max_len:
            return raw
        return f"{raw[:max_len]}..."

    async def stream_events(
        self, message: str, thread_id: str = "default"
    ) -> AsyncIterator[dict[str, Any]]:
        self._ensure_api_key()
        thinking_open = False
        async for event in self._graph.astream_events(
            {
                "messages": [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=message),
                ]
            },
            config={"configurable": {"thread_id": thread_id}},
            version="v2",
        ):
            event_type = str(event.get("event", ""))
            name = str(event.get("name", ""))
            data = event.get("data", {})

            if event_type == "on_chat_model_stream":
                chunk = data.get("chunk") if isinstance(data, dict) else None
                if not isinstance(chunk, AIMessageChunk):
                    continue

                thinking = self._extract_thinking_text(chunk)
                if thinking:
                    if not thinking_open:
                        thinking_open = True
                        yield {"type": "thinking_start", "title": "Reasoning"}
                    yield {"type": "thinking", "content": thinking}

                text = self._extract_text(getattr(chunk, "content", ""))
                if text:
                    if thinking_open:
                        thinking_open = False
                        yield {"type": "thinking_end"}
                    yield {"type": "token", "content": text}

            elif event_type == "on_tool_start":
                if thinking_open:
                    thinking_open = False
                    yield {"type": "thinking_end"}
                payload = data.get("input") if isinstance(data, dict) else None
                yield {
                    "type": "tool_start",
                    "tool_name": name or "tool",
                    "input": self._compact(payload),
                    "tool_call_key": str(event.get("run_id", "")) or None,
                }

            elif event_type == "on_tool_end":
                payload = data.get("output") if isinstance(data, dict) else None
                yield {
                    "type": "tool_end",
                    "tool_name": name or "tool",
                    "output": self._compact(payload),
                    "tool_call_key": str(event.get("run_id", "")) or None,
                }

        if thinking_open:
            yield {"type": "thinking_end"}
