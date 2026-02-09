import { expect, test } from "@playwright/test";

test("backend discovery endpoint is reachable in e2e environment", async ({ request }) => {
  const base = process.env.E2E_BACKEND_URL ?? "http://localhost:8000";
  const response = await request.post(`${base}/discovery/scan`, {
    data: {
      max_hosts: 2,
      timeout_seconds: 0.05,
      ports_csv: "80",
      save: false,
    },
  });

  expect(response.ok()).toBeTruthy();
  const data = await response.json();
  expect(typeof data.hosts_scanned).toBe("number");
  expect(Array.isArray(data.servers)).toBeTruthy();
});
