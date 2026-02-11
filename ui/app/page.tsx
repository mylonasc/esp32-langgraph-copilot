"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { CopilotKit } from "@copilotkit/react-core";
import {
  CopilotChat,
  AssistantMessage,
  UserMessage,
} from "@copilotkit/react-ui";

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

type AgentMessageCount = {
  thread_id: string;
  raw_message_count: number | null;
  sanitized_message_count: number | null;
};

function ChatSurface({
}: {}) {
  return (
    <div className="copilot-layout">
      <CopilotChat
        className="copilot-chat"
        AssistantMessage={AssistantMessage}
        UserMessage={UserMessage}
        labels={{
          title: "ESP32 Copilot",
          initial:
            "Try: scan my local network for MCP servers, then save discovered servers.",
        }}
        instructions="When asked about network discovery, use the available discovery tools and report findings clearly."
      />
    </div>
  );
}

export default function HomePage() {
  const threadId = useMemo(() => crypto.randomUUID(), []);
  const [activeTab, setActiveTab] = useState<"chat" | "mcp">("chat");
  const [agentMessageCount, setAgentMessageCount] = useState<AgentMessageCount | null>(null);
  const [agentCountError, setAgentCountError] = useState<string | null>(null);

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

  async function refreshAgentMessageCount() {
    try {
      setAgentCountError(null);
      const response = await fetch(
        `${backendUrl}/agent/message-count?thread_id=${encodeURIComponent(threadId)}`,
      );
      if (!response.ok) {
        setAgentCountError(`Count endpoint error: ${response.status}`);
        return;
      }
      const data = await response.json();
      setAgentMessageCount(data as AgentMessageCount);
    } catch (err) {
      setAgentCountError(`Cannot load count: ${String(err)}`);
    }
  }

  useEffect(() => {
    void refreshServers();
    void refreshStatus();
    void refreshAgentMessageCount();
  }, []);

  useEffect(() => {
    if (activeTab !== "chat") return;
    const interval = window.setInterval(() => {
      void refreshAgentMessageCount();
    }, 2000);
    return () => window.clearInterval(interval);
  }, [activeTab, threadId]);

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
          <div className="context-metrics">
            <strong>Agent context messages:</strong>{" "}
            {agentMessageCount?.raw_message_count ?? "-"}
            <span className="context-meta">
              (effective: {agentMessageCount?.sanitized_message_count ?? "-"})
            </span>
            <button type="button" className="context-refresh" onClick={() => void refreshAgentMessageCount()}>
              Refresh
            </button>
            {agentCountError ? <span className="context-error">{agentCountError}</span> : null}
          </div>
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
