import { expect, test } from "@playwright/test";

test("chat sends and receives assistant response", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "ESP32 MCP Copilot" })).toBeVisible();

  const input = page.getByPlaceholder("Type a message...");
  await input.fill("Respond with one short sentence.");
  await page.getByRole("button", { name: "Send" }).click();

  const messages = page.locator(".copilotKitAssistantMessage .copilotKitMarkdownElement");
  await expect(messages.last()).toBeVisible();
  await expect(messages.last()).not.toHaveText(/Try:\s*scan my local network/i);
});
