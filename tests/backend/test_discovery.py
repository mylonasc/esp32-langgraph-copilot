from __future__ import annotations


def test_discovery_network(client):
    response = client.get("/discovery/network")
    assert response.status_code == 200
    data = response.json()
    assert "local_ip" in data
    assert "selected_subnet" in data


def test_discovery_scan_small(client):
    response = client.post(
        "/discovery/scan",
        json={"max_hosts": 2, "timeout_seconds": 0.05, "ports_csv": "80"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "hosts_scanned" in data
    assert "servers" in data
