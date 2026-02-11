from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence, TypedDict, cast

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.prebuilt import ToolNode, tools_condition


InputSanitizationStrategy = Literal["truncate", "summarize"]


@dataclass(frozen=True)
class InputSanitizerConfig:
    enabled: bool = True
    strategy: InputSanitizationStrategy = "truncate"
    max_messages: int = 24
    preserve_system_messages: bool = True
    summary_max_chars: int = 4000


@dataclass(frozen=True)
class OutputSanitizerConfig:
    enabled: bool = False
    remove_tool_inputs: bool = False
    remove_tool_outputs: bool = False


@dataclass(frozen=True)
class ReactAgentFactoryConfig:
    input_sanitizer: InputSanitizerConfig = field(default_factory=InputSanitizerConfig)
    output_sanitizer: OutputSanitizerConfig = field(
        default_factory=OutputSanitizerConfig
    )


_raw_message_count_ctx: ContextVar[int | None] = ContextVar(
    "raw_message_count", default=None
)
_sanitized_message_count_ctx: ContextVar[int | None] = ContextVar(
    "sanitized_message_count", default=None
)
_thread_message_counts: dict[str, dict[str, int | None]] = {}


class AgentGraphState(MessagesState, total=False):
    thread_id: str


def get_thread_message_counts(thread_id: str) -> dict[str, int | None]:
    counts = _thread_message_counts.get(thread_id)
    if counts is None:
        return {
            "raw_message_count": 0,
            "sanitized_message_count": 0,
        }
    return {
        "raw_message_count": counts.get("raw_message_count"),
        "sanitized_message_count": counts.get("sanitized_message_count"),
    }


def _message_text(message: BaseMessage) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str) and text:
                    parts.append(text)
        return " ".join(parts)
    return str(content)


def _truncate_messages(
    messages: Sequence[BaseMessage], max_messages: int, preserve_system_messages: bool
) -> list[BaseMessage]:
    if len(messages) <= max_messages:
        return cast(list[BaseMessage], list(messages))

    if not preserve_system_messages:
        return list(messages[-max_messages:])

    system_messages: list[BaseMessage] = [
        cast(BaseMessage, m) for m in messages if isinstance(m, SystemMessage)
    ]
    non_system_messages: list[BaseMessage] = [
        cast(BaseMessage, m) for m in messages if not isinstance(m, SystemMessage)
    ]

    if len(system_messages) >= max_messages:
        return system_messages[-max_messages:]

    tail_count = max_messages - len(system_messages)
    return [*system_messages, *non_system_messages[-tail_count:]]


def _summarize_messages(
    messages: Sequence[BaseMessage],
    max_messages: int,
    preserve_system_messages: bool,
    summary_max_chars: int,
) -> list[BaseMessage]:
    if len(messages) <= max_messages:
        return cast(list[BaseMessage], list(messages))

    keep_count = max(1, max_messages - 1)
    recent: list[BaseMessage] = list(messages[-keep_count:])
    older: list[BaseMessage] = list(messages[:-keep_count])

    if preserve_system_messages:
        recent = [
            *[m for m in older if isinstance(m, SystemMessage)],
            *[m for m in recent if not isinstance(m, SystemMessage)],
        ]
        if len(recent) > keep_count:
            recent = recent[-keep_count:]

    summary_lines: list[str] = []
    for item in older:
        role = "assistant"
        if item.type == "human":
            role = "user"
        elif item.type == "system":
            role = "system"
        elif item.type == "tool":
            role = "tool"
        summary_lines.append(f"- {role}: {_message_text(item)[:240]}")

    summary_text = "\n".join(summary_lines)
    if len(summary_text) > summary_max_chars:
        summary_text = f"{summary_text[:summary_max_chars]}..."

    summary_message = SystemMessage(
        content=(
            "Conversation summary for context compression. "
            "Use this only as historical context:\n"
            f"{summary_text}"
        )
    )
    return [summary_message, *recent]


def _strip_tool_inputs(message: BaseMessage) -> BaseMessage:
    if not isinstance(message, AIMessage):
        return message
    additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
    additional_kwargs.pop("tool_calls", None)
    return message.model_copy(
        update={
            "tool_calls": [],
            "invalid_tool_calls": [],
            "additional_kwargs": additional_kwargs,
        }
    )


def create_stateful_react_agent(
    *,
    model: Runnable,
    tools: list[Any],
    config: ReactAgentFactoryConfig | None = None,
):
    effective = config or ReactAgentFactoryConfig()

    async def _agent_message_count() -> str:
        raw_count = _raw_message_count_ctx.get()
        sanitized_count = _sanitized_message_count_ctx.get()
        payload = {
            "raw_message_count": raw_count if isinstance(raw_count, int) else 0,
            "sanitized_message_count": (
                sanitized_count if isinstance(sanitized_count, int) else None
            ),
            "source": "pre_sanitize",
        }
        return json.dumps(payload)

    introspection_tool = StructuredTool.from_function(
        coroutine=_agent_message_count,
        name="agent_message_count",
        description=(
            "Return the current number of messages in agent state before input "
            "sanitization."
        ),
    )
    tools_with_introspection: list[Any] = [*tools, introspection_tool]

    if hasattr(model, "bind_tools") and tools_with_introspection:
        model_with_tools = cast(Any, model).bind_tools(tools_with_introspection)
    else:
        model_with_tools = model

    def sanitize_inputs_node(state: AgentGraphState) -> dict[str, Any]:
        messages = cast(list[BaseMessage], list(state.get("messages", [])))
        cfg = effective.input_sanitizer
        raw_count = len(messages)
        _raw_message_count_ctx.set(raw_count)
        raw_thread = state.get("thread_id")
        thread_id = raw_thread if isinstance(raw_thread, str) and raw_thread else None

        if not cfg.enabled or cfg.max_messages < 1:
            sanitized_count = len(messages)
            _sanitized_message_count_ctx.set(sanitized_count)
            if thread_id:
                _thread_message_counts[thread_id] = {
                    "raw_message_count": raw_count,
                    "sanitized_message_count": sanitized_count,
                }
            return {"messages": messages}

        if cfg.strategy == "summarize":
            sanitized = _summarize_messages(
                messages=messages,
                max_messages=cfg.max_messages,
                preserve_system_messages=cfg.preserve_system_messages,
                summary_max_chars=cfg.summary_max_chars,
            )
        else:
            sanitized = _truncate_messages(
                messages=messages,
                max_messages=cfg.max_messages,
                preserve_system_messages=cfg.preserve_system_messages,
            )
        sanitized_count = len(sanitized)
        _sanitized_message_count_ctx.set(sanitized_count)
        if thread_id:
            _thread_message_counts[thread_id] = {
                "raw_message_count": raw_count,
                "sanitized_message_count": sanitized_count,
            }
        return {"messages": sanitized}

    def call_node(state: AgentGraphState) -> dict[str, Any]:
        response = model_with_tools.invoke(state["messages"])
        return {"messages": [response]}

    def sanitize_outputs_node(state: AgentGraphState) -> dict[str, Any]:
        cfg = effective.output_sanitizer
        messages = cast(list[BaseMessage], list(state.get("messages", [])))
        if not cfg.enabled:
            return {"messages": messages}

        sanitized: list[BaseMessage] = []
        for message in messages:
            if cfg.remove_tool_outputs and isinstance(message, ToolMessage):
                continue
            if cfg.remove_tool_inputs:
                message = _strip_tool_inputs(message)
            sanitized.append(message)
        return {"messages": sanitized}

    graph = StateGraph(AgentGraphState)
    graph.add_node("call", call_node)

    if tools_with_introspection:
        graph.add_node("tools", ToolNode(tools_with_introspection))

    if effective.input_sanitizer.enabled:
        graph.add_node("sanitize_inputs", sanitize_inputs_node)
        graph.add_edge(START, "sanitize_inputs")
        graph.add_edge("sanitize_inputs", "call")
    else:
        graph.add_edge(START, "call")

    output_target = END
    if effective.output_sanitizer.enabled:
        output_target = "sanitize_outputs"
        graph.add_node("sanitize_outputs", sanitize_outputs_node)
        graph.add_edge("sanitize_outputs", END)

    if tools_with_introspection:
        graph.add_conditional_edges(
            "call",
            tools_condition,
            {
                "tools": "tools",
                "__end__": output_target,
            },
        )
        graph.add_edge("tools", "call")
    else:
        graph.add_edge("call", output_target)

    return graph.compile()
