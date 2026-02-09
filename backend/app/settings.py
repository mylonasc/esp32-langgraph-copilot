from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MCPServerSettings(BaseModel):
    name: str = Field(..., min_length=1)
    base_url: str = Field(..., min_length=1)
    transport: Literal["mcp_jsonrpc", "esp32_rest"] = "mcp_jsonrpc"
    mcp_path: str = "/mcp"
    timeout_seconds: float = Field(default=15.0, gt=0)
    auth_token: str | None = None


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL")
    esp_mcp_servers_json: str = Field(default="[]", alias="ESP_MCP_SERVERS_JSON")
    cors_allow_origins: str = Field(default="*", alias="CORS_ALLOW_ORIGINS")
    cors_allow_credentials: bool = Field(default=False, alias="CORS_ALLOW_CREDENTIALS")
    agent_fake_mode: bool = Field(default=False, alias="AGENT_FAKE_MODE")

    def parsed_cors_origins(self) -> list[str]:
        raw = self.cors_allow_origins.strip()
        if not raw:
            return ["*"]

        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return ["*"]
            if isinstance(parsed, list):
                values = [str(item).strip() for item in parsed if str(item).strip()]
                return values or ["*"]
            return ["*"]

        values = [item.strip() for item in raw.split(",") if item.strip()]
        return values or ["*"]

    def parsed_server_configs(self) -> tuple[list[MCPServerSettings], list[str]]:
        warnings: list[str] = []
        raw = self.esp_mcp_servers_json.strip()
        if raw.startswith("'") and raw.endswith("'"):
            raw = raw[1:-1]

        try:
            parsed = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            warnings.append(
                "ESP_MCP_SERVERS_JSON is not valid JSON. Starting with no servers. "
                'Expected a JSON array like: [{"name":"esp32-plants","base_url":"http://esp32-plants.local"}]'
            )
            return [], warnings

        if isinstance(parsed, dict):
            entries: list[object] = [parsed]
        elif isinstance(parsed, list):
            entries = parsed
        else:
            warnings.append(
                "ESP_MCP_SERVERS_JSON must be a JSON object or array. "
                f"Got {type(parsed).__name__}. Starting with no servers."
            )
            return [], warnings

        configs: list[MCPServerSettings] = []
        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                warnings.append(
                    f"Skipping ESP_MCP_SERVERS_JSON[{idx}]: expected object, got {type(entry).__name__}"
                )
                continue

            try:
                configs.append(MCPServerSettings.model_validate(entry))
            except Exception as exc:
                warnings.append(f"Skipping ESP_MCP_SERVERS_JSON[{idx}]: {exc}")

        return configs, warnings
