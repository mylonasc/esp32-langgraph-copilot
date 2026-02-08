from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any
from uuid import uuid4

import httpx
from langchain_core.tools import StructuredTool

ServerTransport = str


@dataclass(slots=True)
class MCPServerConfig:
    name: str
    base_url: str
    transport: ServerTransport = "mcp_jsonrpc"
    mcp_path: str = "/mcp"
    timeout_seconds: float = 15.0
    auth_token: str | None = None


class ESPMCPToolkit:
    """Toolkit for MCP servers connected to ESP32 fleets."""

    def __init__(self) -> None:
        self._servers: dict[str, MCPServerConfig] = {}

    def register_server(self, config: MCPServerConfig) -> None:
        self._servers[config.name] = config

    def remove_server(self, server_name: str) -> bool:
        return self._servers.pop(server_name, None) is not None

    def list_servers(self) -> list[dict[str, Any]]:
        return [asdict(cfg) for cfg in self._servers.values()]

    def _get_server(self, server_name: str) -> MCPServerConfig:
        server = self._servers.get(server_name)
        if server is None:
            known = ", ".join(sorted(self._servers)) if self._servers else "none"
            raise ValueError(f"Unknown server '{server_name}'. Known servers: {known}")
        return server

    @staticmethod
    def _ok(data: Any) -> dict[str, Any]:
        return {"ok": True, "data": data}

    @staticmethod
    def _err(error: str) -> dict[str, Any]:
        return {"ok": False, "error": error}

    async def _http_json(
        self,
        server_name: str,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        server = self._get_server(server_name)
        headers: dict[str, str] = {}
        if server.auth_token:
            headers["Authorization"] = f"Bearer {server.auth_token}"
        if body is not None:
            headers["Content-Type"] = "application/json"

        endpoint = f"{server.base_url.rstrip('/')}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=server.timeout_seconds) as client:
            response = await client.request(
                method.upper(), endpoint, params=query or {}, json=body, headers=headers
            )
            response.raise_for_status()
            if "application/json" in response.headers.get("Content-Type", ""):
                return response.json()
            return {"text": response.text}

    async def _rpc(
        self, server_name: str, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        server = self._get_server(server_name)
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid4()),
            "method": method,
            "params": params or {},
        }
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if server.auth_token:
            headers["Authorization"] = f"Bearer {server.auth_token}"

        endpoint = f"{server.base_url.rstrip('/')}/{server.mcp_path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=server.timeout_seconds) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        if "error" in data:
            raise RuntimeError(f"MCP error from {server_name}: {data['error']}")

        return data.get("result", {})

    async def list_server_tools(self, server_name: str) -> dict[str, Any]:
        server = self._get_server(server_name)
        if server.transport == "esp32_rest":
            return {
                "tools": [
                    {
                        "name": "api_overview",
                        "description": "GET /api summary payload for device modules and status",
                    },
                    {
                        "name": "api_soil",
                        "description": "GET /api/soil soil moisture data",
                    },
                    {
                        "name": "api_dht",
                        "description": "GET /api/dht temperature and humidity data",
                    },
                    {
                        "name": "api_relays",
                        "description": "GET /api/relays relay states",
                    },
                    {
                        "name": "relay_set",
                        "description": "GET /api/relays/set with query keys: ch and state(on/off)",
                    },
                    {
                        "name": "pump_control",
                        "description": "GET /watering_pumps with query keys ch(A/B/C), sec, off(1)",
                    },
                    {
                        "name": "http_request",
                        "description": "Generic HTTP request. Arguments: method, path, query(optional), body(optional)",
                    },
                ]
            }
        return await self._rpc(server_name, "tools/list")

    async def call_server_tool(
        self, server_name: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        server = self._get_server(server_name)
        if server.transport == "esp32_rest":
            if tool_name == "api_overview":
                return await self._http_json(server_name, "GET", "/api")
            if tool_name == "api_soil":
                return await self._http_json(server_name, "GET", "/api/soil")
            if tool_name == "api_dht":
                return await self._http_json(server_name, "GET", "/api/dht")
            if tool_name == "api_relays":
                return await self._http_json(server_name, "GET", "/api/relays")
            if tool_name == "relay_set":
                return await self._http_json(
                    server_name,
                    "GET",
                    "/api/relays/set",
                    query={
                        "ch": arguments.get("ch", "A"),
                        "state": arguments.get("state", "off"),
                    },
                )
            if tool_name == "pump_control":
                query: dict[str, Any] = {}
                if arguments.get("off"):
                    query["off"] = 1
                else:
                    query["ch"] = arguments.get("ch", "A")
                    query["sec"] = int(arguments.get("sec", 5))
                return await self._http_json(
                    server_name, "GET", "/watering_pumps", query=query
                )
            if tool_name == "http_request":
                return await self._http_json(
                    server_name,
                    str(arguments.get("method", "GET")),
                    str(arguments.get("path", "/api")),
                    query=arguments.get("query")
                    if isinstance(arguments.get("query"), dict)
                    else None,
                    body=arguments.get("body")
                    if isinstance(arguments.get("body"), dict)
                    else None,
                )
            raise ValueError(f"Unknown esp32_rest tool '{tool_name}'")

        return await self._rpc(
            server_name,
            "tools/call",
            {
                "name": tool_name,
                "arguments": arguments,
            },
        )

    def as_langchain_tools(self) -> list[StructuredTool]:
        async def _list_servers() -> str:
            try:
                return json.dumps(self._ok(self.list_servers()))
            except Exception as exc:
                return json.dumps(self._err(str(exc)))

        async def _list_tools(server_name: str) -> str:
            try:
                return json.dumps(self._ok(await self.list_server_tools(server_name)))
            except Exception as exc:
                return json.dumps(self._err(str(exc)))

        async def _call_tool(
            server_name: str, tool_name: str, arguments_json: str = "{}"
        ) -> str:
            try:
                arguments = json.loads(arguments_json)
            except json.JSONDecodeError as exc:
                return json.dumps(
                    self._err(f"arguments_json must be valid JSON: {exc}")
                )

            try:
                result = await self.call_server_tool(server_name, tool_name, arguments)
                return json.dumps(self._ok(result))
            except Exception as exc:
                return json.dumps(self._err(str(exc)))

        return [
            StructuredTool.from_function(
                coroutine=_list_servers,
                name="esp_list_mcp_servers",
                description="List configured ESP32 MCP servers.",
            ),
            StructuredTool.from_function(
                coroutine=_list_tools,
                name="esp_list_server_tools",
                description="List tools exposed by one MCP server by server_name.",
            ),
            StructuredTool.from_function(
                coroutine=_call_tool,
                name="esp_call_server_tool",
                description=(
                    "Call a tool on an ESP32 MCP server. Provide server_name, tool_name, and "
                    "arguments_json (JSON object string)."
                ),
            ),
        ]
