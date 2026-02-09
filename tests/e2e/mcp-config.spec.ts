import { expect, test } from "@playwright/test";

test("mcp configuration add/edit/delete flow", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "MCP Configuration" }).click();

  const nameInput = page.getByPlaceholder("Server name");
  const urlInput = page.getByPlaceholder("Base URL");
  await nameInput.fill("pw-e2e-server");
  await urlInput.fill("http://127.0.0.1:8099");
  await page.getByRole("button", { name: "Add Server" }).click();

  const row = page.locator(".server-item", { hasText: "pw-e2e-server" });
  await expect(row).toBeVisible();

  await row.getByRole("button", { name: "Edit" }).click();
  await urlInput.fill("http://127.0.0.1:8098");
  await page.getByRole("button", { name: "Save Changes" }).click();

  const updatedRow = page.locator(".server-item", { hasText: "pw-e2e-server" });
  await expect(updatedRow).toContainText("http://127.0.0.1:8098");

  await updatedRow.getByRole("button", { name: "Delete" }).click();
  await expect(page.locator(".server-item", { hasText: "pw-e2e-server" })).toHaveCount(0);
});
