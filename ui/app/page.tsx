"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { CopilotKit } from "@copilotkit/react-core";
import {
  CopilotChat,
  RenderMessageProps,
  AssistantMessage,
  UserMessage,
} from "@copilotkit/react-ui";
import { aguiToGQL, Message as RuntimeMessage, ResultMessage } from "@copilotkit/runtime-client-gql";

const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

type ServerConfig = {
  name: string;
  base_url: string;
  transport: string;
  mcp_path: string;
  timeout_seconds: number;
  auth_token?: string | null;
};

type ProbeState = {
  status: "idle" | "checking" | "ok" | "error";
  message: string;
};

type BackendStatus = {
  status: string;
  openai_configured: boolean;
  server_count: number;
  warnings: string[];
};

type ToolLineageStatus = "started" | "finished" | "error";

type ToolLineageItem = {
  id: string;
  name: string;
  status: ToolLineageStatus;
  args: string;
  result: string;
  startedAt: number | null;
  endedAt: number | null;
};

function toMillis(value: unknown): number | null {
  if (value instanceof Date) {
    return Number.isFinite(value.getTime()) ? value.getTime() : null;
  }
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value === "string") {
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function formatLatency(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function asRuntimeMessage(message: unknown): RuntimeMessage | null {
  if (message && typeof message === "object" && "isTextMessage" in message) {
    return message as RuntimeMessage;
  }
  try {
    const converted = aguiToGQL(message as Parameters<typeof aguiToGQL>[0]);
    return converted[0] ?? null;
  } catch {
    return null;
  }
}

function toRuntimeMessages(messages: unknown[]): RuntimeMessage[] {
  const runtimeOnly = messages.map((message) => asRuntimeMessage(message)).filter(isRuntimeMessage);
  if (runtimeOnly.length === messages.length) {
    return runtimeOnly;
  }

  try {
    return aguiToGQL(messages as Parameters<typeof aguiToGQL>[0]);
  } catch {
    return runtimeOnly;
  }
}

function findByToolCallId(messages: RuntimeMessage[], toolCallId: string): RuntimeMessage | undefined {
  for (const msg of messages) {
    if (msg.isActionExecutionMessage() && msg.id === toolCallId) {
      return msg;
    }
  }
  return undefined;
}

function isRuntimeMessage(message: RuntimeMessage | null): message is RuntimeMessage {
  return message !== null;
}

function maybePrettyJson(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

async function consumeSse(response: Response, onEvent: (event: Record<string, unknown>) => void) {
  if (!response.body) return;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";

    for (const chunk of chunks) {
      const lines = chunk.split("\n").map((line) => line.trim());
      for (const line of lines) {
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (!payload || payload === "[DONE]") continue;
        try {
          onEvent(JSON.parse(payload) as Record<string, unknown>);
        } catch {
          // Ignore non-JSON SSE payloads.
        }
      }
    }
  }
}

function ToolLineagePanel({ toolItems }: { toolItems: ToolLineageItem[] }) {
  const [collapsed, setCollapsed] = useState(true);
  const [expandedItems, setExpandedItems] = useState<Record<string, boolean>>({});
  const displayToolItems = toolItems;

  const activeItem = useMemo(() => {
    if (displayToolItems.length === 0) return null;
    const running = [...displayToolItems].reverse().find((item) => item.status === "started");
    return running ?? displayToolItems[displayToolItems.length - 1];
  }, [displayToolItems]);

  const statusLabel = (status: ToolLineageStatus) => {
    if (status === "started") return "started";
    if (status === "error") return "error";
    return "finished";
  };

  return (
    <aside className="tool-lineage-panel">
      <button type="button" className="tool-lineage-header" onClick={() => setCollapsed((v) => !v)}>
        <span>Tool Call Info</span>
        <span>{displayToolItems.length}</span>
      </button>

      <div className="tool-live-strip">
        {activeItem ? (
          <div key={`${activeItem.id}:${activeItem.status}`} className={`tool-live-card ${activeItem.status}`}>
            <strong>{activeItem.name}</strong>
            <span>{statusLabel(activeItem.status)}</span>
          </div>
        ) : (
          <div className="tool-live-empty">No tool activity for this turn yet.</div>
        )}
      </div>

      {!collapsed ? (
        <div className="tool-lineage-list">
          {displayToolItems.length === 0 ? (
            <div className="tool-live-empty">Waiting for tool calls...</div>
          ) : (
            displayToolItems.map((item) => {
              const latency =
                item.startedAt !== null && item.endedAt !== null && item.endedAt >= item.startedAt
                  ? formatLatency(item.endedAt - item.startedAt)
                  : null;
              const expanded = expandedItems[item.id] ?? false;
              return (
                <div key={item.id} className={`tool-lineage-item ${item.status}`}>
                  <div className="tool-lineage-summary">
                    <span className={`tool-status-dot ${item.status}`} />
                    <strong>{item.name}</strong>
                    <span className="tool-status-label">{statusLabel(item.status)}</span>
                    {latency ? <span className="tool-latency">{latency}</span> : null}
                    <button
                      type="button"
                      className="tool-toggle"
                      onClick={() =>
                        setExpandedItems((prev) => ({
                          ...prev,
                          [item.id]: !expanded,
                        }))
                      }
                    >
                      {expanded ? "Hide" : "Show"}
                    </button>
                  </div>
                  {expanded ? (
                    <div className="tool-lineage-details">
                      <pre>{item.args}</pre>
                      {item.result ? <pre>{item.result}</pre> : null}
                    </div>
                  ) : null}
                </div>
              );
            })
          )}
        </div>
      ) : null}
    </aside>
  );
}

function ToolCallMessageRenderer({
  message,
}: RenderMessageProps) {
  const runtimeMessage = asRuntimeMessage(message);
  const [collapsed, setCollapsed] = useState(false);

  const toolCallMessage =
    runtimeMessage && runtimeMessage.isActionExecutionMessage() ? runtimeMessage : null;
  const args =
    toolCallMessage && Object.keys(toolCallMessage.arguments ?? {}).length
      ? JSON.stringify(toolCallMessage.arguments, null, 2)
      : "{}";

  if (!runtimeMessage || !runtimeMessage.isActionExecutionMessage()) {
    return null;
  }

  return (
    <div className="tool-call-card tool-running" data-message-role="assistant">
      <div className="tool-call-header">
        <div className="tool-call-title">Tool Running: {runtimeMessage.name}</div>
        <button type="button" className="tool-toggle" onClick={() => setCollapsed((v) => !v)}>
          {collapsed ? "Show" : "Hide"} args
        </button>
      </div>
      {!collapsed ? <pre>{args}</pre> : null}
    </div>
  );
}

function ToolResultMessageRenderer({
  message,
  messages,
}: RenderMessageProps) {
  const runtimeMessage = asRuntimeMessage(message);
  const [collapsed, setCollapsed] = useState(false);
  const toolResultMessage =
    runtimeMessage && runtimeMessage.isResultMessage() ? runtimeMessage : null;
  const runtimeMessages = toRuntimeMessages(messages as unknown[]);
  const actionMsg =
    toolResultMessage ? findByToolCallId(runtimeMessages, toolResultMessage.actionExecutionId) : undefined;

  const startMs = actionMsg ? toMillis(actionMsg.createdAt) : null;
  const endMs = toolResultMessage ? toMillis(toolResultMessage.createdAt) : null;
  const latencyMs =
    startMs !== null && endMs !== null && endMs >= startMs ? Math.round(endMs - startMs) : null;
  const linkedActionName =
    actionMsg && actionMsg.isActionExecutionMessage() ? actionMsg.name : undefined;
  const actionName = toolResultMessage?.actionName || linkedActionName || "unknown";

  const decoded = toolResultMessage ? ResultMessage.decodeResult(toolResultMessage.result) : "";
  const rendered = typeof decoded === "string" ? decoded : JSON.stringify(decoded, null, 2);

  if (!runtimeMessage || !runtimeMessage.isResultMessage()) {
    return null;
  }

  return (
    <div className="tool-call-card tool-done" data-message-role="assistant">
      <div className="tool-call-header">
        <div className="tool-call-title">
          Tool Result: {actionName}
          {latencyMs !== null ? <span className="tool-latency">{formatLatency(latencyMs)}</span> : null}
        </div>
        <button type="button" className="tool-toggle" onClick={() => setCollapsed((v) => !v)}>
          {collapsed ? "Show" : "Hide"} result
        </button>
      </div>
      {!collapsed ? <pre>{rendered}</pre> : null}
    </div>
  );
}

function ChatSurface({
}: {}) {
  const [toolItems, setToolItems] = useState<ToolLineageItem[]>([]);

  const onAguiEvent = useCallback((event: Record<string, unknown>) => {
    const type = String(event.type ?? "");

    if (type === "RUN_STARTED") {
      setToolItems([]);
      return;
    }

    setToolItems((prev) => {
      if (type === "TOOL_CALL_START") {
        const id = String(event.toolCallId ?? "");
        if (!id || prev.some((item) => item.id === id)) return prev;
        return [
          ...prev,
          {
            id,
            name: String(event.toolCallName ?? "unknown"),
            status: "started",
            args: "{}",
            result: "",
            startedAt: Date.now(),
            endedAt: null,
          },
        ];
      }

      if (type === "TOOL_CALL_ARGS") {
        const id = String(event.toolCallId ?? "");
        const delta = String(event.delta ?? "");
        const index = prev.findIndex((item) => item.id === id);
        if (index === -1) return prev;
        const next = [...prev];
        const current = next[index];
        const merged = current.args === "{}" ? delta : `${current.args}${delta}`;
        next[index] = { ...current, args: maybePrettyJson(merged) };
        return next;
      }

      if (type === "TOOL_CALL_RESULT") {
        const id = String(event.toolCallId ?? "");
        const content = String(event.content ?? "");
        const parsedResult = maybePrettyJson(content);
        const status: ToolLineageStatus = content.includes('"error"') ? "error" : "finished";
        const index = prev.findIndex((item) => item.id === id);

        if (index === -1) {
          return [
            ...prev,
            {
              id: id || `tool-${Date.now()}`,
              name: String(event.toolName ?? "unknown"),
              status,
              args: "{}",
              result: parsedResult,
              startedAt: null,
              endedAt: Date.now(),
            },
          ];
        }

        const next = [...prev];
        const current = next[index];
        next[index] = {
          ...current,
          name: String(event.toolName ?? current.name ?? "unknown"),
          status,
          result: parsedResult,
          endedAt: Date.now(),
        };
        return next;
      }

      return prev;
    });
  }, []);

  useEffect(() => {
    const originalFetch = window.fetch.bind(window);

    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const response = await originalFetch(input, init);

      try {
        const requestUrl =
          typeof input === "string" ? input : input instanceof Request ? input.url : String(input);
        const method = (init?.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
        const contentType = response.headers.get("content-type") || "";

        if (!requestUrl.includes("/copilotkit") || method !== "POST" || !contentType.includes("text/event-stream")) {
          return response;
        }

        let requestBody = "";
        if (typeof init?.body === "string") {
          requestBody = init.body;
        } else if (input instanceof Request) {
          requestBody = await input.clone().text();
        }

        const parsedBody = requestBody ? (JSON.parse(requestBody) as Record<string, unknown>) : {};
        if (parsedBody.method === "agent/run") {
          void consumeSse(response.clone(), onAguiEvent);
        }
      } catch {
        // Ignore diagnostic parsing errors.
      }

      return response;
    };

    return () => {
      window.fetch = originalFetch;
    };
  }, [onAguiEvent]);

  return (
    <div className="copilot-layout">
      <CopilotChat
        className="copilot-chat"
        AssistantMessage={AssistantMessage}
        UserMessage={UserMessage}
        RenderActionExecutionMessage={ToolCallMessageRenderer}
        RenderResultMessage={ToolResultMessageRenderer}
        labels={{
          title: "ESP32 Copilot",
          initial:
            "Try: scan my local network for MCP servers, then save discovered servers.",
        }}
        instructions="When asked about network discovery, use the available discovery tools and report findings clearly."
      />
      <ToolLineagePanel toolItems={toolItems} />
    </div>
  );
}

export default function HomePage() {
  const threadId = useMemo(() => crypto.randomUUID(), []);
  const [activeTab, setActiveTab] = useState<"chat" | "mcp">("chat");

  const [servers, setServers] = useState<ServerConfig[]>([]);
  const [loadingServers, setLoadingServers] = useState(false);
  const [serverName, setServerName] = useState("");
  const [baseUrl, setBaseUrl] = useState("http://host.docker.internal:8090");
  const [transport, setTransport] = useState("mcp_jsonrpc");
  const [mcpPath, setMcpPath] = useState("/mcp");
  const [timeoutSeconds, setTimeoutSeconds] = useState("15");
  const [authToken, setAuthToken] = useState("");
  const [editingServerName, setEditingServerName] = useState<string | null>(null);
  const [formProbe, setFormProbe] = useState<ProbeState>({
    status: "idle",
    message: "Enter a base URL to check connectivity.",
  });
  const [rowProbes, setRowProbes] = useState<Record<string, ProbeState>>({});

  const [backendStatus, setBackendStatus] = useState<BackendStatus | null>(null);
  const [backendStatusError, setBackendStatusError] = useState<string | null>(null);
  function resetForm() {
    setEditingServerName(null);
    setServerName("");
    setBaseUrl("http://host.docker.internal:8090");
    setTransport("mcp_jsonrpc");
    setMcpPath("/mcp");
    setTimeoutSeconds("15");
    setAuthToken("");
    setFormProbe({ status: "idle", message: "Enter a base URL to check connectivity." });
  }

  async function testServerConfig(config: {
    base_url: string;
    transport: string;
    mcp_path: string;
    timeout_seconds: number;
    auth_token: string | null;
  }): Promise<ProbeState> {
    try {
      const response = await fetch(`${backendUrl}/servers/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      const data = await response.json();
      if (!response.ok) {
        return {
          status: "error",
          message: data?.detail ? String(data.detail) : `Request failed (${response.status})`,
        };
      }
      if (data.ok) {
        return {
          status: "ok",
          message: `Reachable via ${String(data.endpoint ?? config.base_url)}`,
        };
      }
      return {
        status: "error",
        message: String(data.error ?? "Connection check failed"),
      };
    } catch (err) {
      return {
        status: "error",
        message: `Network error: ${String(err)}`,
      };
    }
  }

  async function refreshServers() {
    setLoadingServers(true);
    try {
      const response = await fetch(`${backendUrl}/servers`);
      const data = await response.json();
      const nextServers = Array.isArray(data.servers) ? (data.servers as ServerConfig[]) : [];
      setServers(nextServers);
      setRowProbes((prev) => {
        const keep = new Set(nextServers.map((server) => server.name));
        const next: Record<string, ProbeState> = {};
        for (const [name, state] of Object.entries(prev)) {
          if (keep.has(name)) next[name] = state;
        }
        return next;
      });
    } finally {
      setLoadingServers(false);
    }
  }

  async function refreshStatus() {
    try {
      setBackendStatusError(null);
      const response = await fetch(`${backendUrl}/status`);
      if (!response.ok) {
        setBackendStatusError(`Status endpoint error: ${response.status}`);
        return;
      }
      const data = await response.json();
      setBackendStatus(data as BackendStatus);
    } catch (err) {
      setBackendStatusError(`Cannot reach backend: ${String(err)}`);
    }
  }

  useEffect(() => {
    void refreshServers();
    void refreshStatus();
  }, []);

  useEffect(() => {
    if (!baseUrl.trim()) {
      setFormProbe({ status: "idle", message: "Enter a base URL to check connectivity." });
      return;
    }
    if (!/^https?:\/\//i.test(baseUrl.trim())) {
      setFormProbe({ status: "error", message: "Base URL must start with http:// or https://" });
      return;
    }

    setFormProbe({ status: "checking", message: "Checking connectivity..." });
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      const result = await testServerConfig({
        base_url: baseUrl.trim(),
        transport,
        mcp_path: mcpPath.trim() || "/mcp",
        timeout_seconds: Number(timeoutSeconds) || 15,
        auth_token: authToken.trim() || null,
      });
      if (!cancelled) setFormProbe(result);
    }, 450);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [baseUrl, transport, mcpPath, timeoutSeconds, authToken]);

  async function addOrUpdateServer(event: FormEvent) {
    event.preventDefault();
    if (!serverName.trim() || !baseUrl.trim()) return;

    const payload = {
      name: serverName.trim(),
      base_url: baseUrl.trim(),
      transport,
      mcp_path: mcpPath.trim() || "/mcp",
      timeout_seconds: Number(timeoutSeconds) || 15,
      auth_token: authToken.trim() || null,
    };

    const endpoint = editingServerName
      ? `${backendUrl}/servers/${encodeURIComponent(editingServerName)}`
      : `${backendUrl}/servers`;
    const method = editingServerName ? "PUT" : "POST";

    const response = await fetch(endpoint, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const err = await response.text();
      setFormProbe({ status: "error", message: `Save failed: ${err}` });
      return;
    }

    await refreshServers();
    resetForm();
  }

  function startEdit(server: ServerConfig) {
    setEditingServerName(server.name);
    setServerName(server.name);
    setBaseUrl(server.base_url);
    setTransport(server.transport);
    setMcpPath(server.mcp_path || "/mcp");
    setTimeoutSeconds(String(server.timeout_seconds || 15));
    setAuthToken(server.auth_token ?? "");
    setActiveTab("mcp");
  }

  async function deleteServer(serverNameToDelete: string) {
    const response = await fetch(`${backendUrl}/servers/${encodeURIComponent(serverNameToDelete)}`, {
      method: "DELETE",
    });
    if (!response.ok) return;
    if (editingServerName === serverNameToDelete) resetForm();
    await refreshServers();
  }

  async function testExistingServer(server: ServerConfig) {
    setRowProbes((prev) => ({
      ...prev,
      [server.name]: { status: "checking", message: "Checking connectivity..." },
    }));

    const result = await testServerConfig({
      base_url: server.base_url,
      transport: server.transport,
      mcp_path: server.mcp_path,
      timeout_seconds: server.timeout_seconds,
      auth_token: server.auth_token ?? null,
    });

    setRowProbes((prev) => ({ ...prev, [server.name]: result }));
  }

  return (
    <main className="shell">
      <h1 className="title">ESP32 MCP Copilot</h1>
      <p className="subtitle">CopilotKit chat + MCP server configuration panel.</p>

      <div className="tabs" role="tablist" aria-label="Main sections">
        <button type="button" className={`tab ${activeTab === "chat" ? "active" : ""}`} onClick={() => setActiveTab("chat")}>
          Chat
        </button>
        <button type="button" className={`tab ${activeTab === "mcp" ? "active" : ""}`} onClick={() => setActiveTab("mcp")}>
          MCP Configuration
        </button>
      </div>

      {backendStatusError ? (
        <section className="warning-panel">
          <strong>Backend unreachable</strong>
          <p>{backendStatusError}</p>
        </section>
      ) : null}

      {backendStatus && (!backendStatus.openai_configured || backendStatus.warnings.length > 0) ? (
        <section className="warning-panel">
          <strong>Backend warnings</strong>
          {!backendStatus.openai_configured ? (
            <p>`OPENAI_API_KEY` is not configured. Chat completions will fail until it is set.</p>
          ) : null}
          {backendStatus.warnings.map((warning, idx) => (
            <p key={idx}>{warning}</p>
          ))}
        </section>
      ) : null}

      {activeTab === "chat" ? (
        <section className="chat chat-box copilot-wrap">
          <CopilotKit
            runtimeUrl={`${backendUrl}/copilotkit`}
            agent="default"
            threadId={threadId}
            showDevConsole={false}
          >
            <ChatSurface />
          </CopilotKit>
        </section>
      ) : (
        <section className="server-panel">
          <h2 className="panel-title">MCP Servers</h2>
          <form onSubmit={addOrUpdateServer} className="server-form">
            <input placeholder="Server name" value={serverName} onChange={(e) => setServerName(e.target.value)} />
            <input placeholder="Base URL" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
            <select value={transport} onChange={(e) => setTransport(e.target.value)}>
              <option value="mcp_jsonrpc">mcp_jsonrpc</option>
              <option value="esp32_rest">esp32_rest</option>
            </select>
            <input placeholder="MCP path (for mcp_jsonrpc)" value={mcpPath} onChange={(e) => setMcpPath(e.target.value)} />
            <input placeholder="Timeout seconds" value={timeoutSeconds} onChange={(e) => setTimeoutSeconds(e.target.value)} />
            <input placeholder="Auth token (optional)" value={authToken} onChange={(e) => setAuthToken(e.target.value)} />
            <div className="form-actions">
              <button type="submit">{editingServerName ? "Save Changes" : "Add Server"}</button>
              {editingServerName ? (
                <button type="button" className="button-muted" onClick={resetForm}>
                  Cancel Edit
                </button>
              ) : null}
            </div>
          </form>

          <div className={`probe ${formProbe.status}`}>
            <strong>Connectivity check:</strong> {formProbe.message}
          </div>

          <div className="server-list">
            {loadingServers ? "Loading servers..." : null}
            {!loadingServers && servers.length === 0 ? "No servers configured." : null}
            {servers.map((server) => {
              const rowProbe = rowProbes[server.name];
              return (
                <div key={server.name} className="server-item">
                  <div className="server-main">
                    <strong>{server.name}</strong>
                    <span>
                      {server.base_url} [{server.transport}]
                    </span>
                    {rowProbe ? <span className={`probe-inline ${rowProbe.status}`}>{rowProbe.message}</span> : null}
                  </div>
                  <div className="server-actions">
                    <button type="button" onClick={() => void testExistingServer(server)}>
                      Test
                    </button>
                    <button type="button" onClick={() => startEdit(server)}>
                      Edit
                    </button>
                    <button type="button" className="button-danger" onClick={() => void deleteServer(server.name)}>
                      Delete
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}
    </main>
  );
}
