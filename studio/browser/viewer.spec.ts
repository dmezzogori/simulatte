import { test, expect } from "@playwright/test";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
const fixture = resolve(
  "../tests/fixtures/traces/generated/shop_small.simtrace",
);
const csp =
  "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'none'";
test("production viewer loads, steps, seeks and presents under CSP", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/", async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      response,
      headers: { ...response.headers(), "content-security-policy": csp },
    });
  });
  await page.goto("/");
  await page.locator("input[type=file]").setInputFiles(fixture);
  await expect(page.locator(".status")).toHaveText("paused");
  await expect(page.locator("output")).toContainText("activation");
  await page.getByRole("button", { name: "Next event", exact: true }).click();
  await expect(page.locator("output")).toContainText("seq 5");
  await page.getByRole("button", { name: "Next event", exact: true }).click();
  await expect(page.locator("output")).toContainText("seq 6");
  await page
    .getByRole("button", { name: "Previous event", exact: true })
    .click();
  await expect(page.locator("output")).toContainText("seq 5");
  await page.getByRole("button", { name: "Present", exact: true }).click();
  await expect(page.locator("main")).toHaveClass("presentation");
  await page
    .getByRole("button", { name: "Exit presentation", exact: true })
    .click();
  await page.locator("input[type=range]").fill("4");
  await expect(page.locator("output")).toContainText("t = 4");
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await page.waitForTimeout(250);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(page.locator(".status")).toHaveText("paused");
  expect(errors).toEqual([]);
});
test("exported HTML works from file without network requests", async ({
  page,
  context,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await page.locator("input[type=file]").setInputFiles(fixture);
  await expect(page.locator(".status")).toHaveText("paused");
  const waiting = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export HTML" }).click();
  const downloaded = await waiting;
  const path = testInfo.outputPath("recording.html");
  await downloaded.saveAs(path);
  await context.setOffline(true);
  const requests: string[] = [];
  page.on("request", (r) => {
    if (!r.url().startsWith("file:")) requests.push(r.url());
  });
  await page.goto(pathToFileURL(path).href);
  await expect(page.locator("output")).toContainText("t = 0");
  await page.getByRole("button", { name: "Next event", exact: true }).click();
  await expect(page.locator("output")).toContainText("seq 5");
  expect(requests).toEqual([]);
  expect(errors).toEqual([]);
});

test("format 1.2 KPI values respect duplicate times and hostile labels stay text", async ({
  page,
}) => {
  const dialogs: string[] = [];
  page.on("dialog", (dialog) => {
    dialogs.push(dialog.message());
    void dialog.dismiss();
  });
  await page.goto("/");
  await page
    .locator("input[type=file]")
    .setInputFiles(resolve("../tests/fixtures/traces/viewer/viewer.simtrace"));
  await expect(page.locator(".status")).toHaveText("paused");
  await page.getByRole("button", { name: "Next event", exact: true }).click();
  await expect(page.locator("output")).toContainText("seq 3");
  await page.getByText("Recorded KPIs", { exact: true }).click();
  await expect(page.locator(".tile strong").first()).toHaveText("0");
  await page.locator("input[type=range]").fill("5");
  await expect(page.locator("output")).toContainText("t = 5");
  await expect(page.locator(".tile strong").first()).toHaveText("3");
  expect(dialogs).toEqual([]);
  expect(await page.locator('img[src="x"]').count()).toBe(0);
});

test("playback updates stay outside React commits and paged inspection filters safely", async ({
  page,
}) => {
  await page.addInitScript(() => {
    const root = window as unknown as Record<string, unknown>;
    root["studioCommits"] = 0;
    root["__REACT_DEVTOOLS_GLOBAL_HOOK__"] = {
      supportsFiber: true,
      inject: () => 1,
      onCommitFiberRoot: () => {
        root["studioCommits"] = Number(root["studioCommits"]) + 1;
      },
      onCommitFiberUnmount: () => {},
    };
  });
  await page.goto("/");
  await page.locator("input[type=file]").setInputFiles(fixture);
  await expect(page.locator(".status")).toHaveText("paused");
  const before = await page.evaluate(
    () => (window as unknown as Record<string, unknown>)["studioCommits"],
  );
  expect(Number(before)).toBeGreaterThan(0);
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await page.waitForTimeout(10000);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  expect(
    await page.evaluate(
      () => (window as unknown as Record<string, unknown>)["studioCommits"],
    ),
  ).toBe(before);
  await page.getByLabel("Event type", { exact: true }).fill("no.such.event");
  await page.getByLabel("Event type", { exact: true }).press("Tab");
  await expect(page.locator(".event-rows button")).toHaveCount(0);
});

test("preparation history stays inspect-only and KPI tiles identify recorded sample time", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .locator("input[type=file]")
    .setInputFiles(resolve("../tests/fixtures/traces/viewer/viewer.simtrace"));
  await expect(page.locator(".status")).toHaveText("paused");
  await page.locator("input[type=range]").fill("4");
  await expect(page.locator("footer output")).toContainText("t = 4");
  const before = await page.locator("footer output").textContent();
  await page.getByLabel("Event phase", { exact: true }).selectOption("prelude");
  await expect(page.locator(".event-rows button").first()).toBeVisible();
  await page.locator(".event-rows button").first().click();
  await expect(page.locator("footer output")).toHaveText(before!);
  await page.getByRole("tab", { name: "Recorded KPIs", exact: true }).click();
  await expect(page.locator(".sample-source").first()).toContainText(
    "Interpolated",
  );
  await page
    .getByRole("button", { name: "Previous event", exact: true })
    .click();
  await expect(page.locator(".sample-source").first()).toContainText(
    "Recorded · t = 0",
  );
});

test("retired selection keeps historical state separate from the live scene", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .locator("input[type=file]")
    .setInputFiles(resolve("../tests/fixtures/traces/viewer/viewer.simtrace"));
  await expect(page.locator(".status")).toHaveText("paused");
  await page
    .locator(".entities")
    .getByRole("button", { name: "second", exact: true })
    .click();
  await page.getByText("Entity schema", { exact: true }).click();
  await expect(page.locator("details").filter({ hasText: "Entity schema" })).toContainText("level");
  await page.locator("input[type=range]").fill("19.5");
  await expect(
    page.getByRole("heading", {
      name: "second · retired at this cursor",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText("Last state before retirement", { exact: true }),
  ).toBeVisible();
  await expect(
    page
      .locator(".entities")
      .getByRole("button", { name: "second", exact: true }),
  ).toHaveCount(0);
  await page.locator("input[type=range]").fill("10");
  await expect(
    page.getByRole("heading", { name: "second", exact: true }),
  ).toBeVisible();
});
