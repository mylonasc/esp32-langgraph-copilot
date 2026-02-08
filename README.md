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
- `DELETE /servers/{server_name}`
- `POST /agent/invoke`
- `POST /agent/stream` (SSE token stream)
- `POST /copilotkit` (OpenAI-style response shape for CopilotKit runtime URL, including streaming when `stream: true`)

## UI setup

1. Install and run:

```bash
cd ui
npm install
cp .env.local.example .env.local
npm run dev
```

2. Open `http://localhost:3000`.

## Streaming behavior

- CopilotKit can request streamed responses by sending `stream: true` to `POST /copilotkit`.
- The backend returns `text/event-stream` in OpenAI chunk format plus `[DONE]`.
- You can also call `POST /agent/stream` directly for raw SSE token events.

## Adding MCP servers from UI

- Open the UI and use the "MCP Servers" form.
- Add `name`, `base URL`, timeout, and optional token.
- The list updates immediately and those servers become available to the agent tools.

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
