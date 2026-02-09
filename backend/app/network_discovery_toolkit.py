from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import time
from typing import Any

import httpx
from langchain_core.tools import StructuredTool

from .esp_mcp_toolkit import ESPMCPToolkit, MCPServerConfig


class LocalNetworkDiscoveryToolkit:
    """Separate toolkit for local network discovery of MCP-capable servers."""

    def __init__(self, mcp_toolkit: ESPMCPToolkit) -> None:
        self._mcp_toolkit = mcp_toolkit

    @staticmethod
    def _ok(data: Any) -> dict[str, Any]:
        return {"ok": True, "data": data}

    @staticmethod
    def _err(error: str) -> dict[str, Any]:
        return {"ok": False, "error": error}

    @staticmethod
    def _local_ipv4() -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                return str(sock.getsockname()[0])
        except Exception:
            return socket.gethostbyname(socket.gethostname())

    def _default_subnet(self) -> str:
        local_ip = self._local_ipv4()
        octets = local_ip.split(".")
        if len(octets) != 4:
            raise RuntimeError(f"Unable to infer subnet from local IP: {local_ip}")
        return f"{octets[0]}.{octets[1]}.{octets[2]}.0/24"

    @staticmethod
    def _parse_ports(ports_csv: str) -> list[int]:
        values: list[int] = []
        for raw in ports_csv.split(","):
            token = raw.strip()
            if not token:
                continue
            port = int(token)
            if 1 <= port <= 65535:
                values.append(port)
        return sorted(set(values))

    @staticmethod
    def _base_url(host: str, port: int) -> str:
        if port == 80:
            return f"http://{host}"
        return f"http://{host}:{port}"

    async def _probe_mcp_jsonrpc(
        self, host: str, port: int, timeout_seconds: float
    ) -> dict[str, Any] | None:
        endpoint = f"{self._base_url(host, port)}/mcp"
        payload = {
            "jsonrpc": "2.0",
            "id": "discovery",
            "method": "tools/list",
            "params": {},
        }
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(endpoint, json=payload)
                if response.status_code >= 500:
                    return None
                data = response.json()
            if not isinstance(data, dict):
                return None
            if data.get("jsonrpc") != "2.0":
                return None

            result = data.get("result", {})
            tools_count: int | None = None
            if isinstance(result, dict) and isinstance(result.get("tools"), list):
                tools_count = len(result["tools"])

            return {
                "transport": "mcp_jsonrpc",
                "base_url": self._base_url(host, port),
                "mcp_path": "/mcp",
                "timeout_seconds": timeout_seconds,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "details": {"tools_count": tools_count},
            }
        except Exception:
            return None

    async def _probe_esp32_rest(
        self, host: str, port: int, timeout_seconds: float
    ) -> dict[str, Any] | None:
        endpoint = f"{self._base_url(host, port)}/api"
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.get(endpoint)
                if response.status_code != 200:
                    return None
                data = response.json()
            if not isinstance(data, dict):
                return None

            keys = set(data.keys())
            if keys == {"detail"}:
                return None

            known_esp_keys = {
                "soil",
                "dht",
                "relays",
                "modules",
                "status",
                "firmware",
                "uptime",
            }
            if not keys.intersection(known_esp_keys):
                return None

            return {
                "transport": "esp32_rest",
                "base_url": self._base_url(host, port),
                "mcp_path": "/mcp",
                "timeout_seconds": timeout_seconds,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "details": {
                    "keys": sorted(list(keys))[:8],
                },
            }
        except Exception:
            return None

    def _register_discovered(
        self, discovered: list[dict[str, Any]], name_prefix: str
    ) -> int:
        existing = self._mcp_toolkit.list_servers()
        existing_keys = {
            (str(item.get("base_url", "")), str(item.get("transport", "")))
            for item in existing
        }
        existing_names = {str(item.get("name", "")) for item in existing}

        saved = 0
        for item in discovered:
            base_url = str(item.get("base_url", "")).strip()
            transport = str(item.get("transport", "")).strip()
            if not base_url or not transport:
                continue
            if (base_url, transport) in existing_keys:
                continue

            stem = f"{name_prefix}-{transport}-{base_url.split('//')[-1].replace(':', '-')}"
            name = stem
            idx = 2
            while name in existing_names:
                name = f"{stem}-{idx}"
                idx += 1

            self._mcp_toolkit.register_server(
                MCPServerConfig(
                    name=name,
                    base_url=base_url,
                    transport=transport,
                    mcp_path=str(item.get("mcp_path", "/mcp")),
                    timeout_seconds=float(item.get("timeout_seconds", 5.0)),
                )
            )
            existing_names.add(name)
            existing_keys.add((base_url, transport))
            saved += 1

        return saved

    async def network_info(self, subnet_cidr: str = "") -> dict[str, Any]:
        local_ip = self._local_ipv4()
        inferred_subnet = self._default_subnet()
        selected_subnet = subnet_cidr.strip() or inferred_subnet

        network = ipaddress.ip_network(selected_subnet, strict=False)
        host_count = max(network.num_addresses - 2, 0)

        return {
            "local_ip": local_ip,
            "inferred_subnet": inferred_subnet,
            "selected_subnet": selected_subnet,
            "host_count": host_count,
        }

    async def discover_mcp_servers(
        self,
        subnet_cidr: str = "",
        ports_csv: str = "80,8000,8080,8090",
        timeout_seconds: float = 1.2,
        max_hosts: int = 64,
        save: bool = False,
        name_prefix: str = "discovered",
    ) -> dict[str, Any]:
        selected_subnet = subnet_cidr.strip() or self._default_subnet()
        network = ipaddress.ip_network(selected_subnet, strict=False)
        ports = self._parse_ports(ports_csv)
        if not ports:
            raise ValueError("No valid ports to scan. Example: '80,8000,8080,8090'")
        if max_hosts <= 0:
            raise ValueError("max_hosts must be > 0")

        hosts: list[str] = []
        for host in network.hosts():
            hosts.append(str(host))
            if len(hosts) >= max_hosts:
                break

        semaphore = asyncio.Semaphore(32)

        async def scan_host(host: str) -> list[dict[str, Any]]:
            found: list[dict[str, Any]] = []
            async with semaphore:
                for port in ports:
                    mcp = await self._probe_mcp_jsonrpc(host, port, timeout_seconds)
                    if mcp is not None:
                        found.append(mcp)

                    esp = await self._probe_esp32_rest(host, port, timeout_seconds)
                    if esp is not None:
                        found.append(esp)
            return found

        started = time.perf_counter()
        batches = await asyncio.gather(*(scan_host(host) for host in hosts))
        discovered: list[dict[str, Any]] = [item for batch in batches for item in batch]

        dedup: dict[tuple[str, str], dict[str, Any]] = {}
        for item in discovered:
            key = (str(item.get("base_url", "")), str(item.get("transport", "")))
            prev = dedup.get(key)
            if prev is None:
                dedup[key] = item
                continue
            if int(item.get("latency_ms", 10_000)) < int(
                prev.get("latency_ms", 10_000)
            ):
                dedup[key] = item

        final_discovered = list(dedup.values())
        saved_count = (
            self._register_discovered(final_discovered, name_prefix) if save else 0
        )

        return {
            "subnet": selected_subnet,
            "hosts_scanned": len(hosts),
            "ports": ports,
            "timeout_seconds": timeout_seconds,
            "scan_ms": int((time.perf_counter() - started) * 1000),
            "found_count": len(final_discovered),
            "saved_count": saved_count,
            "servers": final_discovered,
        }

    def as_langchain_tools(self) -> list[StructuredTool]:
        async def _network_info(subnet_cidr: str = "") -> str:
            try:
                return json.dumps(
                    self._ok(await self.network_info(subnet_cidr=subnet_cidr))
                )
            except Exception as exc:
                return json.dumps(self._err(str(exc)))

        async def _discover(
            subnet_cidr: str = "",
            ports_csv: str = "80,8000,8080,8090",
            timeout_seconds: float = 1.2,
            max_hosts: int = 64,
            save: bool = False,
            name_prefix: str = "discovered",
        ) -> str:
            try:
                result = await self.discover_mcp_servers(
                    subnet_cidr=subnet_cidr,
                    ports_csv=ports_csv,
                    timeout_seconds=timeout_seconds,
                    max_hosts=max_hosts,
                    save=save,
                    name_prefix=name_prefix,
                )
                return json.dumps(self._ok(result))
            except Exception as exc:
                return json.dumps(self._err(str(exc)))

        return [
            StructuredTool.from_function(
                coroutine=_network_info,
                name="esp_local_network_info",
                description=(
                    "Return local network connection info and inferred subnet. "
                    "Optionally pass subnet_cidr."
                ),
            ),
            StructuredTool.from_function(
                coroutine=_discover,
                name="esp_discover_mcp_servers",
                description=(
                    "Scan a local subnet for MCP/ESP32 servers. "
                    "Arguments: subnet_cidr(optional), ports_csv, timeout_seconds, max_hosts, "
                    "save(boolean to register discovered servers), name_prefix."
                ),
            ),
        ]
