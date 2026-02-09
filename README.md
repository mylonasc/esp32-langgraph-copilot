# ESP32 MCP Agent Stack (LangGraph + CopilotKit)

This project gives you:

- A Python backend that serves a LangGraph agent.
- An `ESPMCPToolkit` class for MCP servers that bridge to ESP32 devices.
- A CopilotKit-based Next.js UI that chats with the backend agent.

## Project structure

- `backend/` FastAPI + LangGraph agent
- `backend/app/esp_mcp_toolkit.py` MCP integration toolkit for ESP32-facing MCP servers
- `ui/` Next.js + CopilotKit chat interface

## Backend setup

1. Create a virtual environment and install deps:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Configure environment:

```bash
cp .env.example .env
```

3. Start the API:

```bash
uvicorn app.main:app --reload --port 8000
```

Endpoints:

- `GET /health`
- `GET /status` (startup warnings + OpenAI config state)
- `GET /servers`
- `POST /servers`
- `PUT /servers/{server_name}`
- `DELETE /servers/{server_name}`
- `POST /servers/test`
- `GET /discovery/network`
- `POST /discovery/scan`
- `POST /agent/invoke`
- `POST /agent/stream` (SSE token stream)
- `POST /copilotkit` (OpenAI-style response shape for CopilotKit runtime URL, including streaming when `stream: true`)

Backend environment variables:

- `OPENAI_API_KEY` (required for agent calls)
- `OPENAI_MODEL` (default: `gpt-4.1-mini`)
- `AGENT_FAKE_MODE` (`true`/`false`, default `false`; deterministic fast responses for tests)
- `ESP_MCP_SERVERS_JSON` (JSON array/object for startup server registration)
- `CORS_ALLOW_ORIGINS` (`*`, comma-separated list, or JSON array)
- `CORS_ALLOW_CREDENTIALS` (`true`/`false`, default `false`)

## UI setup

1. Install and run:

```bash
cd ui
npm install
cp .env.local.example .env.local
npm run dev
```

2. Open `http://localhost:3000`.

UI notes:

- Chat tab now uses `CopilotKit` + `CopilotChat` against backend `POST /copilotkit`.
- CopilotKit dev console is enabled in the provider for runtime feedback while debugging.
- MCP server management remains in the separate `MCP Configuration` tab.

## Streaming behavior

- CopilotKit can request streamed responses by sending `stream: true` to `POST /copilotkit`.
- The backend returns `text/event-stream` in OpenAI chunk format plus `[DONE]`.
- You can also call `POST /agent/stream` directly for raw SSE events.
- `POST /agent/stream` now emits tool lifecycle events (`tool_start`, `tool_end`) in addition to `token` and `done`.

## Adding MCP servers from UI

- Open the UI and use the "MCP Servers" form.
- Add `name`, `base URL`, timeout, and optional token.
- The list updates immediately and those servers become available to the agent tools.

## Local network discovery from chat

The agent now has a separate local network discovery toolkit with tools for:

- Inspecting local network info and inferred subnet.
- Scanning local subnet ranges for MCP JSON-RPC (`/mcp`) and ESP32 REST (`/api`) servers.
- Optionally saving discovered servers into current runtime config.

Example prompts in chat:

- `scan my local network for MCP servers`
- `scan subnet 192.168.1.0/24 for MCP servers on ports 80,8090`
- `scan and save discovered MCP servers`

Notes:

- Saved discovered servers are stored in current backend runtime (in-memory) and will reset on restart.

## Run with Docker Compose

1. Prepare backend env:

```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_MODEL="gpt-4.1-mini"
export ESP_MCP_SERVERS_JSON='[{"name":"lab-esp32","base_url":"http://host.docker.internal:8090"}]'
```

Notes:

- `ESP_MCP_SERVERS_JSON` must be valid JSON (double quotes inside JSON).
- If this value is invalid, backend now starts with an empty server list and logs a warning instead of crashing.

2. Build and run:

```bash
docker compose up --build
```

3. Open:

- UI: `http://localhost:3000`
- Backend: `http://localhost:8000/health`

## ESP MCP server configuration

The backend reads `ESP_MCP_SERVERS_JSON` from environment.

Example (JSON-RPC MCP server):

```json
[
  {
    "name": "lab-esp32",
    "base_url": "http://localhost:8090",
    "transport": "mcp_jsonrpc",
    "mcp_path": "/mcp",
    "timeout_seconds": 15,
    "auth_token": "optional"
  }
]
```

Example for `esp32-plants.local` firmware observed on LAN (REST mode):

```json
[
  {
    "name": "esp32-plants",
    "base_url": "http://esp32-plants.local",
    "transport": "esp32_rest",
    "timeout_seconds": 15
  }
]
```

For `mcp_jsonrpc`, toolkit calls `<base_url>/<mcp_path>` with:

- `tools/list`
- `tools/call`

For `esp32_rest`, toolkit exposes virtual tools that call HTTP endpoints like:

- `GET /api`
- `GET /api/soil`
- `GET /api/dht`
- `GET /api/relays`
- `GET /api/relays/set?ch=A&state=off`
- `GET /watering_pumps?ch=A&sec=5`

## Robustness to missing/offline servers

- Tool failures are returned as structured `{ "ok": false, "error": "..." }` results instead of crashing the agent runtime.
- The chat backend remains responsive even if one or more configured servers are unreachable.
- You can still chat normally without registered servers; tool calls will just report no server availability.

## Tests

- Backend tests: `backend/.venv/bin/pytest tests/backend`
- E2E tests (Playwright): `cd ui && npm run test:e2e`
- Combined: `make test-all`

For fast, deterministic tests use fake mode:

```bash
export AGENT_FAKE_MODE=true
docker compose up --build -d
```
