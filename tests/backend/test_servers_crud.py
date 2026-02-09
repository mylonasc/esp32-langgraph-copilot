from __future__ import annotations


def test_servers_crud(client):
    payload = {
        "name": "pytest-server",
        "base_url": "http://127.0.0.1:8090",
        "transport": "mcp_jsonrpc",
        "mcp_path": "/mcp",
        "timeout_seconds": 1.5,
    }

    create = client.post("/servers", json=payload)
    assert create.status_code == 200
    assert create.json()["ok"] is True

    listing = client.get("/servers")
    assert listing.status_code == 200
    servers = listing.json()["servers"]
    assert any(server["name"] == "pytest-server" for server in servers)

    update_payload = {
        **payload,
        "base_url": "http://127.0.0.1:8091",
    }
    update = client.put("/servers/pytest-server", json=update_payload)
    assert update.status_code == 200

    delete = client.delete("/servers/pytest-server")
    assert delete.status_code == 200
    assert delete.json()["ok"] is True
