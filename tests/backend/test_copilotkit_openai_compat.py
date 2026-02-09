from __future__ import annotations


def test_copilotkit_info_method(client):
    response = client.post("/copilotkit", json={"method": "info"})
    assert response.status_code == 200
    data = response.json()
    assert "agents" in data
    assert "default" in data["agents"]


def test_copilotkit_openai_non_stream(client):
    response = client.post(
        "/copilotkit",
        json={
            "messages": [{"role": "user", "content": "hello"}],
            "thread_id": "pytest-thread",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["role"] == "assistant"
