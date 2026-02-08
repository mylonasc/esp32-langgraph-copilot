"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

type ServerConfig = {
  name: string;
  base_url: string;
  transport: string;
  mcp_path: string;
  timeout_seconds: number;
  auth_token?: string | null;
};

type ChatMsg = {
  role: "user" | "assistant";
  content: string;
};

type BackendStatus = {
  status: string;
  openai_configured: boolean;
  server_count: number;
  warnings: string[];
};

export default function HomePage() {
  const threadId = useMemo(() => crypto.randomUUID(), []);
  const [servers, setServers] = useState<ServerConfig[]>([]);
  const [loadingServers, setLoadingServers] = useState(false);
  const [serverName, setServerName] = useState("");
  const [baseUrl, setBaseUrl] = useState("http://host.docker.internal:8090");
  const [transport, setTransport] = useState("mcp_jsonrpc");
  const [mcpPath, setMcpPath] = useState("/mcp");
  const [timeoutSeconds, setTimeoutSeconds] = useState("15");
  const [authToken, setAuthToken] = useState("");
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [backendStatus, setBackendStatus] = useState<BackendStatus | null>(null);
  const [backendStatusError, setBackendStatusError] = useState<string | null>(null);

  async function refreshServers() {
    setLoadingServers(true);
    try {
      const response = await fetch(`${backendUrl}/servers`);
      const data = await response.json();
      setServers(Array.isArray(data.servers) ? data.servers : []);
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

  async function addServer(event: FormEvent) {
    event.preventDefault();
    if (!serverName.trim() || !baseUrl.trim()) return;
    const response = await fetch(`${backendUrl}/servers`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: serverName.trim(),
        base_url: baseUrl.trim(),
        transport,
        mcp_path: mcpPath.trim() || "/mcp",
        timeout_seconds: Number(timeoutSeconds) || 15,
        auth_token: authToken.trim() || null,
      }),
    });
    if (!response.ok) {
      const err = await response.text();
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Failed to add server: ${err}` },
      ]);
      return;
    }
    setServerName("");
    setAuthToken("");
    await refreshServers();
  }

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setBusy(true);
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    try {
      const response = await fetch(`${backendUrl}/agent/invoke`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, thread_id: threadId }),
      });
      const data = await response.json();
      if (!response.ok) {
        const detail = data?.detail ?? "Unknown backend error";
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: `Error: ${detail}` },
        ]);
        return;
      }
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: data.response || "" },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Network error: ${String(err)}` },
      ]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="shell">
      <h1 className="title">ESP32 MCP Copilot</h1>
      <p className="subtitle">
        Add MCP servers, then chat with the ESP32 LangGraph agent.
      </p>

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
            <p>
              `OPENAI_API_KEY` is not configured. Chat completions will fail until it is set.
            </p>
          ) : null}
          {backendStatus.warnings.map((warning, idx) => (
            <p key={idx}>{warning}</p>
          ))}
        </section>
      ) : null}

      <section className="server-panel">
        <h2 className="panel-title">MCP Servers</h2>
        <form onSubmit={addServer} className="server-form">
          <input
            placeholder="Server name"
            value={serverName}
            onChange={(e) => setServerName(e.target.value)}
          />
          <input
            placeholder="Base URL"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
          <select value={transport} onChange={(e) => setTransport(e.target.value)}>
            <option value="mcp_jsonrpc">mcp_jsonrpc</option>
            <option value="esp32_rest">esp32_rest</option>
          </select>
          <input
            placeholder="MCP path (for mcp_jsonrpc)"
            value={mcpPath}
            onChange={(e) => setMcpPath(e.target.value)}
          />
          <input
            placeholder="Timeout seconds"
            value={timeoutSeconds}
            onChange={(e) => setTimeoutSeconds(e.target.value)}
          />
          <input
            placeholder="Auth token (optional)"
            value={authToken}
            onChange={(e) => setAuthToken(e.target.value)}
          />
          <button type="submit">Add Server</button>
        </form>
        <div className="server-list">
          {loadingServers ? "Loading servers..." : null}
          {!loadingServers && servers.length === 0 ? "No servers configured." : null}
          {servers.map((server) => (
            <div key={server.name} className="server-item">
              <strong>{server.name}</strong>
              <span>
                {server.base_url} [{server.transport}]
              </span>
            </div>
          ))}
        </div>
      </section>

      <div className="chat chat-box">
        <div className="messages">
          {messages.length === 0 ? (
            <p className="hint">Try: "list mcp servers"</p>
          ) : (
            messages.map((m, idx) => (
              <div key={idx} className={`msg ${m.role}`}>
                {m.content}
              </div>
            ))
          )}
        </div>
        <form onSubmit={sendMessage} className="chat-form">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Send a message"
          />
          <button type="submit" disabled={busy}>
            {busy ? "Sending..." : "Send"}
          </button>
        </form>
      </div>
    </main>
  );
}
