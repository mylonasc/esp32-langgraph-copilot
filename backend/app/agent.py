from __future__ import annotations

import os
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


SYSTEM_PROMPT = (
    "You are an ESP32 operations copilot. "
    "Use available MCP tools to inspect devices and perform actions. "
    "When tools fail, explain the failure clearly and suggest the next check."
)


class ESPAgentService:
    def __init__(self, toolkit: ESPMCPToolkit) -> None:
        self.toolkit = toolkit
        self._graph = self._build_graph()

    def _build_graph(self):
        model_name = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        llm = ChatOpenAI(model=model_name, temperature=0)
        return create_react_agent(model=llm, tools=self.toolkit.as_langchain_tools())

    def _ensure_api_key(self) -> None:
        if not os.getenv("OPENAI_API_KEY"):
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
        self._ensure_api_key()
        async for chunk, metadata in self._graph.astream(
            {
                "messages": [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=message),
                ]
            },
            config={"configurable": {"thread_id": thread_id}},
            stream_mode="messages",
        ):
            if metadata.get("langgraph_node") != "agent":
                continue
            if not isinstance(chunk, AIMessageChunk):
                continue
            text = self._extract_text(chunk.content)
            if text:
                yield text
