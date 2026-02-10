import { expect, test, type Page } from "@playwright/test";

function sse(events: Array<Record<string, unknown>>): string {
  return events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
}

async function mockCopilotRoutes(page: Page, runEvents: Array<Record<string, unknown>>) {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        openai_configured: true,
        server_count: 0,
        warnings: [],
      }),
    });
  });

  await page.route("**/servers", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ servers: [] }),
    });
  });

  await page.route("**/copilotkit/info", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        version: "0.1.0",
        audioFileTranscriptionEnabled: false,
        agents: {
          default: {
            name: "default",
            className: "ESPLangGraphAgent",
            description: "Default test agent",
          },
        },
      }),
    });
  });

  await page.route("**/copilotkit", async (route) => {
    const request = route.request();
    const method = request.method();
    if (method !== "POST") {
      await route.fulfill({ status: 405, body: "Unsupported" });
      return;
    }

    const body = request.postDataJSON() as
      | { method?: string; params?: Record<string, unknown> }
      | undefined;

    if (body?.method === "info") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          version: "0.1.0",
          audioFileTranscriptionEnabled: false,
          agents: {
            default: {
              name: "default",
              className: "ESPLangGraphAgent",
              description: "Default test agent",
            },
          },
        }),
      });
      return;
    }

    if (body?.method === "agent/connect") {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: sse([
          {
            type: "RUN_STARTED",
            threadId: "e2e-thread-1",
            runId: "e2e-connect-1",
            input: {
              threadId: "e2e-thread-1",
              runId: "e2e-connect-1",
              messages: [],
              tools: [],
              context: [],
              state: {},
              forwardedProps: {},
            },
          },
          {
            type: "RUN_FINISHED",
            threadId: "e2e-thread-1",
            runId: "e2e-connect-1",
          },
        ]),
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: {
        "cache-control": "no-cache",
        connection: "keep-alive",
      },
      body: sse(runEvents),
    });
  });
}

test.describe("Copilot chat streaming", () => {
  test("streams tool calls and thinking", async ({ page }) => {
    await mockCopilotRoutes(page, [
      {
        type: "RUN_STARTED",
        threadId: "e2e-thread-1",
        runId: "e2e-run-1",
        input: {
          threadId: "e2e-thread-1",
          runId: "e2e-run-1",
          messages: [{ id: "u1", role: "user", content: "scan now" }],
          tools: [],
          context: [],
          state: {},
          forwardedProps: {},
        },
      },
      { type: "TEXT_MESSAGE_START", messageId: "msg_1", role: "assistant" },
      { type: "THINKING_START", title: "Reasoning" },
      { type: "THINKING_TEXT_MESSAGE_START" },
      { type: "THINKING_TEXT_MESSAGE_CONTENT", delta: "Analyzing tools and planning..." },
      { type: "THINKING_TEXT_MESSAGE_END" },
      { type: "THINKING_END" },
      {
        type: "TOOL_CALL_START",
        toolCallId: "tool_1",
        toolCallName: "esp_scan_network",
        parentMessageId: "msg_1",
      },
      { type: "TOOL_CALL_ARGS", toolCallId: "tool_1", delta: '{"timeout":3}' },
      { type: "TOOL_CALL_END", toolCallId: "tool_1" },
      {
        type: "TOOL_CALL_RESULT",
        messageId: "toolmsg_1",
        toolCallId: "tool_1",
        role: "tool",
        toolName: "esp_scan_network",
        content: '{"ok":true,"networks":["lab"]}',
      },
      { type: "TEXT_MESSAGE_CONTENT", messageId: "msg_1", delta: "Scan complete." },
      { type: "TEXT_MESSAGE_END", messageId: "msg_1" },
      { type: "RUN_FINISHED", threadId: "e2e-thread-1", runId: "e2e-run-1" },
    ]);

    await page.goto("/");

    const input = page.getByPlaceholder("Type a message...");
    await expect(input).toBeVisible();
    await input.fill("Please scan nearby networks");
    await input.press("Enter");

    await expect(page.getByText("Analyzing tools and planning...")).toBeVisible();
    await expect(page.getByText("Scan complete.")).toBeVisible();
  });

  test("tracks repeated same-name tools by call id", async ({ page }) => {
    await mockCopilotRoutes(page, [
      {
        type: "RUN_STARTED",
        threadId: "e2e-thread-1",
        runId: "e2e-run-2",
        input: {
          threadId: "e2e-thread-1",
          runId: "e2e-run-2",
          messages: [{ id: "u2", role: "user", content: "scan twice" }],
          tools: [],
          context: [],
          state: {},
          forwardedProps: {},
        },
      },
      { type: "TEXT_MESSAGE_START", messageId: "msg_2", role: "assistant" },
      {
        type: "TOOL_CALL_START",
        toolCallId: "tool_a",
        toolCallName: "esp_scan_network",
        parentMessageId: "msg_2",
      },
      { type: "TOOL_CALL_ARGS", toolCallId: "tool_a", delta: '{"segment":"a"}' },
      { type: "TOOL_CALL_END", toolCallId: "tool_a" },
      {
        type: "TOOL_CALL_START",
        toolCallId: "tool_b",
        toolCallName: "esp_scan_network",
        parentMessageId: "msg_2",
      },
      { type: "TOOL_CALL_ARGS", toolCallId: "tool_b", delta: '{"segment":"b"}' },
      { type: "TOOL_CALL_END", toolCallId: "tool_b" },
      {
        type: "TOOL_CALL_RESULT",
        messageId: "toolmsg_b",
        toolCallId: "tool_b",
        role: "tool",
        toolName: "esp_scan_network",
        content: '{"ok":true,"networks":["b"]}',
      },
      {
        type: "TOOL_CALL_RESULT",
        messageId: "toolmsg_a",
        toolCallId: "tool_a",
        role: "tool",
        toolName: "esp_scan_network",
        content: '{"ok":true,"networks":["a"]}',
      },
      { type: "TEXT_MESSAGE_CONTENT", messageId: "msg_2", delta: "Done." },
      { type: "TEXT_MESSAGE_END", messageId: "msg_2" },
      { type: "RUN_FINISHED", threadId: "e2e-thread-1", runId: "e2e-run-2" },
    ]);

    await page.goto("/");

    const input = page.getByPlaceholder("Type a message...");
    await expect(input).toBeVisible();
    await input.fill("Please scan twice");
    await input.press("Enter");

    await expect(page.getByText("Done.")).toBeVisible();
  });
});
