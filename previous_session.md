# ESP32 LangGraph Copilot - Session Handoff

## 1) System architecture (high-level)

### Backend
- Entry point: `backend/app/main.py`
- Agent implementation: `backend/app/agent.py`
- Main API surfaces:
  - `POST /copilotkit` (primary CopilotKit endpoint)
  - `GET /copilotkit/info`
  - `POST /agent/invoke` and `POST /agent/stream` (legacy/helper endpoints)
  - MCP server CRUD/test endpoints (`/servers`, `/servers/test`, etc.)
- AG-UI envelope path in `/copilotkit` handles methods:
  - `info`
  - `agent/connect`
  - `agent/run`
  - `agent/stop`
- `agent/run` streams SSE AG-UI events from `_agui_run_stream(...)`.

### UI
- Main app page: `ui/app/page.tsx`
- Uses CopilotKit React components:
  - `CopilotKit` provider
  - `CopilotChat`
- Uses custom renderers for tool events:
  - `ToolCallMessageRenderer`
  - `ToolResultMessageRenderer`
- Side panel intended for lineage:
  - `ToolLineagePanel` with "Tool Call Info" count/list
- Runtime client libraries:
  - `@copilotkit/react-core`
  - `@copilotkit/react-ui`
  - `@copilotkit/runtime-client-gql`

## 2) Original reported issues

User-reported:
- Tool calls not visibly streamed
- Thinking tokens not shown
- "Tool Calls Info" not populated

## 3) Fixes implemented in this session

### Backend event streaming fixes

#### 3.1 Thinking events added (real + synthetic fallback)
- File: `backend/app/main.py`
- `_agui_run_stream(...)` now emits AG-UI thinking events:
  - `THINKING_START`
  - `THINKING_TEXT_MESSAGE_START`
  - `THINKING_TEXT_MESSAGE_CONTENT`
  - `THINKING_TEXT_MESSAGE_END`
  - `THINKING_END`
- Synthetic default behavior added when provider does not emit thinking deltas.

#### 3.2 Agent-level thinking extraction
- File: `backend/app/agent.py`
- Added `_extract_thinking_text(...)` and wired into `stream_events(...)`.
- Emits internal events:
  - `thinking_start`, `thinking`, `thinking_end`
- Also ensures thinking is closed before tool/token emission.

#### 3.3 Tool-call correlation hardening
- File: `backend/app/main.py`
- Replaced fragile name-only mapping with:
  - key-based mapping (`tool_call_key`)
  - per-tool pending queue fallback
- Handles repeated same-tool invocations and out-of-order finishes better.

#### 3.4 Tool result metadata enrichment
- File: `backend/app/main.py`
- `TOOL_CALL_RESULT` now includes `toolName`.

#### 3.5 Fake mode stream enriched
- File: `backend/app/main.py`
- Fake stream now includes:
  - thinking start/content/end
  - tool start/end with `tool_call_key`
  - normal token stream

### Backend tests added/updated
- File: `tests/backend/test_copilotkit_agui_protocol.py`
- Added assertions for presence of:
  - `TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_RESULT`
  - `THINKING_START`, `THINKING_TEXT_MESSAGE_CONTENT`, `THINKING_END`
- Added test for repeated same-tool calls producing distinct IDs and proper matching.
- Added test for synthetic thinking fallback when model thinking is absent.

### UI fixes attempted

#### 3.6 Message conversion and renderer plumbing updates
- File: `ui/app/page.tsx`
- Multiple passes were attempted to normalize message handling and lineage derivation:
  - direct runtime message usage
  - AG-UI -> GQL conversion fallback (`aguiToGQL`) when needed
  - lineage derivation from Copilot hooks
  - state lifting and callback-based lineage updates

#### 3.7 Current observable UI behavior
- Tool result cards do render in chat (e.g., `Tool Result: unknown`).
- Tool lineage side panel count remains `0` in e2e scenario.
- So panel source-of-truth is still disconnected from where rendered tool messages are coming from.

## 4) Playwright/e2e work added

- New e2e test: `tests/e2e/chat-streaming.spec.ts`
- Config update: `ui/playwright.config.ts` (adds `webServer`)
- Test currently mocks `status`, `servers`, and CopilotKit AG-UI endpoints and validates:
  - tool output rendering
  - Tool Call Info count
  - thinking text

### Current status of focused e2e
- Command:
  - `cd ui && npm run test:e2e -- --grep "streams tool calls"`
- Result:
  - FAIL (Tool Call Info count expected `1`, observed `0`)
- Snapshot indicates chat contains tool result card but lineage panel remains empty.

## 5) Working vs failing checks

### Passing
- `cd backend && ../backend/.venv/bin/pytest tests/backend/test_copilotkit_agui_protocol.py`
- `cd ui && npm run build`

### Failing
- `cd ui && npm run test:e2e -- --grep "streams tool calls"`
  - fails on `.tool-lineage-header` count not incrementing

## 6) Strong hypothesis for next developer

The `CopilotChat` display path can render tool result-like messages via its legacy adapter/render path, but the panel logic is deriving lineage from a different message stream/hook that does not include equivalent tool records in this runtime configuration.

Most likely next fix direction:
- Drive `ToolLineagePanel` directly from AG-UI runtime events (`TOOL_CALL_START/ARGS/RESULT`) instead of deriving from chat messages.
- Keep chat rendering independent.
- Verify with the existing Playwright scenario.

## 7) Files touched in this session (relevant)

- `backend/app/main.py`
- `backend/app/agent.py`
- `tests/backend/test_copilotkit_agui_protocol.py`
- `ui/app/page.tsx`
- `ui/playwright.config.ts`
- `tests/e2e/chat-streaming.spec.ts`
