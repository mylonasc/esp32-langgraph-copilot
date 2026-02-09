from __future__ import annotations

import json
import importlib


def _sse_events(raw: str) -> list[dict]:
    chunks = [chunk.strip() for chunk in raw.split("\n\n") if chunk.strip()]
    events: list[dict] = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.startswith("data: "):
                payload = line[6:]
                try:
                    events.append(json.loads(payload))
                except json.JSONDecodeError:
                    continue
    return events


def test_agent_run_single_endpoint_stream(client):
    response = client.post(
        "/copilotkit",
        json={
            "method": "agent/run",
            "body": {
                "threadId": "t-1",
                "runId": "r-1",
                "messages": [{"id": "u1", "role": "user", "content": "test run"}],
            },
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(response.text)
    assert any(event.get("type") == "RUN_STARTED" for event in events)
    assert any(event.get("type") == "TEXT_MESSAGE_START" for event in events)
    assert any(event.get("type") == "TEXT_MESSAGE_CONTENT" for event in events)
    assert any(event.get("type") == "TOOL_CALL_START" for event in events)
    assert any(event.get("type") == "TOOL_CALL_ARGS" for event in events)
    assert any(event.get("type") == "TOOL_CALL_RESULT" for event in events)
    assert any(event.get("type") == "THINKING_START" for event in events)
    assert any(event.get("type") == "THINKING_TEXT_MESSAGE_CONTENT" for event in events)
    assert any(event.get("type") == "THINKING_END" for event in events)
    assert any(event.get("type") == "RUN_FINISHED" for event in events)


def test_agent_connect_single_endpoint(client):
    response = client.post(
        "/copilotkit",
        json={"method": "agent/connect", "params": {"threadId": "thread-connect"}},
    )
    assert response.status_code == 200
    events = _sse_events(response.text)
    assert any(event.get("type") == "RUN_FINISHED" for event in events)


def test_agent_run_repeated_tool_names_keep_distinct_call_ids(client, monkeypatch):
    main_module = importlib.import_module("app.main")

    async def fake_stream_events(_message: str, _thread_id: str):
        yield {
            "type": "tool_start",
            "tool_name": "shared_tool",
            "tool_call_key": "k1",
            "input": '{"call":1}',
        }
        yield {
            "type": "tool_start",
            "tool_name": "shared_tool",
            "tool_call_key": "k2",
            "input": '{"call":2}',
        }
        yield {
            "type": "tool_end",
            "tool_name": "shared_tool",
            "tool_call_key": "k2",
            "output": "result-k2",
        }
        yield {
            "type": "tool_end",
            "tool_name": "shared_tool",
            "tool_call_key": "k1",
            "output": "result-k1",
        }
        yield {"type": "token", "content": "done"}

    monkeypatch.setattr(main_module, "_stream_agent_events", fake_stream_events)

    response = client.post(
        "/copilotkit",
        json={
            "method": "agent/run",
            "body": {
                "threadId": "t-repeat",
                "runId": "r-repeat",
                "messages": [{"id": "u1", "role": "user", "content": "repeat tools"}],
            },
        },
    )
    assert response.status_code == 200
    events = _sse_events(response.text)

    starts = [event for event in events if event.get("type") == "TOOL_CALL_START"]
    assert len(starts) == 2
    start_ids = {event["toolCallId"] for event in starts}
    assert len(start_ids) == 2

    result_for_k1 = next(
        event
        for event in events
        if event.get("type") == "TOOL_CALL_RESULT"
        and "result-k1" in event.get("content", "")
    )
    result_for_k2 = next(
        event
        for event in events
        if event.get("type") == "TOOL_CALL_RESULT"
        and "result-k2" in event.get("content", "")
    )
    assert result_for_k1["toolCallId"] != result_for_k2["toolCallId"]
    assert result_for_k1["toolCallId"] in start_ids
    assert result_for_k2["toolCallId"] in start_ids


def test_agent_run_emits_synthetic_thinking_when_model_thinking_is_absent(
    client, monkeypatch
):
    main_module = importlib.import_module("app.main")

    async def fake_stream_events(_message: str, _thread_id: str):
        yield {
            "type": "tool_start",
            "tool_name": "test_tool",
            "tool_call_key": "k1",
            "input": "{}",
        }
        yield {
            "type": "tool_end",
            "tool_name": "test_tool",
            "tool_call_key": "k1",
            "output": "ok",
        }
        yield {"type": "token", "content": "hello"}

    monkeypatch.setattr(main_module, "_stream_agent_events", fake_stream_events)

    response = client.post(
        "/copilotkit",
        json={
            "method": "agent/run",
            "body": {
                "threadId": "t-synth-think",
                "runId": "r-synth-think",
                "messages": [
                    {"id": "u1", "role": "user", "content": "no model thinking"}
                ],
            },
        },
    )

    assert response.status_code == 200
    events = _sse_events(response.text)

    assert any(event.get("type") == "THINKING_START" for event in events)
    assert any(event.get("type") == "THINKING_TEXT_MESSAGE_CONTENT" for event in events)
    assert any(event.get("type") == "THINKING_TEXT_MESSAGE_END" for event in events)
    assert any(event.get("type") == "THINKING_END" for event in events)
