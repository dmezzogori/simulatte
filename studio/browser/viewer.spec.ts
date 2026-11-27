import { test, expect } from "@playwright/test";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
const fixture = resolve(
  "../tests/fixtures/traces/generated/shop_small.simtrace",
);
const traffic = resolve(
  "../tests/fixtures/traces/generated/example_traffic.simtrace",
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
  // Span playback frames: rewriting the label during a press suppresses WebKit's click.
  await page.getByRole("button", { name: "Pause", exact: true }).click({delay: 100});
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
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await page.waitForTimeout(250);
  await page.getByRole("button", { name: "Pause", exact: true }).click({delay: 100});
  await expect(page.getByRole("button", { name: "Play", exact: true })).toBeVisible();
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

test("the Overview panel is docked with the Inspector, which stays the active tab", async ({ page }) => {
  await page.goto("/");
  await page.locator("input[type=file]").setInputFiles(resolve("../tests/fixtures/traces/generated/example_traffic.simtrace"));
  await expect(page.locator(".status")).toHaveText("paused");
  await expect(page.getByRole("tab", { name: "Overview", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Inspector", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "Overview", exact: true })).toHaveAttribute("aria-selected", "false");
});

test("the scene legend keeps clear of the scale bar in the bottom-left corner of the canvas", async ({ page }) => {
  await page.goto("/");
  await page.locator("input[type=file]").setInputFiles(traffic);
  await expect(page.locator(".status")).toHaveText("paused");
  const legend = page.locator(".scene-legend");
  await expect(legend).toContainText("Lane width is nominal.");
  const box = (await legend.boundingBox())!;
  const canvas = (await page.locator(".scene-host canvas").boundingBox())!;
  // the scale bar and its length: 16 px from the left and bottom edges, at most 150 px long
  const corner = { left: canvas.x, right: canvas.x + 180, top: canvas.y + canvas.height - 40, bottom: canvas.y + canvas.height };
  const intersects =
    box.x < corner.right && box.x + box.width > corner.left && box.y < corner.bottom && box.y + box.height > corner.top;
  expect(intersects, JSON.stringify({ legend: box, canvas })).toBe(false);
  // inside the canvas, so that it cannot reach the dock's tab strip
  expect(
    box.x >= canvas.x && box.y >= canvas.y && box.x + box.width <= canvas.x + canvas.width && box.y + box.height <= canvas.y + canvas.height,
    JSON.stringify({ legend: box, canvas }),
  ).toBe(true);
  const tab = (await page.getByRole("tab", { name: "Scene", exact: true }).boundingBox())!;
  const overTab = box.x < tab.x + tab.width && box.x + box.width > tab.x && box.y < tab.y + tab.height && box.y + box.height > tab.y;
  expect(overTab, JSON.stringify({ legend: box, tab })).toBe(false);
});

test("the Overview panel written while it was hidden shows the current frame when its tab is chosen", async ({ page }) => {
  await page.goto("/");
  await page.locator("input[type=file]").setInputFiles(traffic);
  await expect(page.locator(".status")).toHaveText("paused");
  await page.getByRole("button", { name: "Next event", exact: true }).click();
  await expect(page.locator("output")).toContainText("seq");
  await page.getByRole("tab", { name: "Overview", exact: true }).click();
  await expect(page.locator(".overview .ov-line").first()).toBeVisible();
  await expect(page.locator(".overview")).toContainText("AGV-1");
});

test("a seek into a recorded wait shows it in the detail and the Overview without playing through its start", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await page.locator("input[type=file]").setInputFiles(traffic);
  await expect(page.locator(".status")).toHaveText("paused");
  // the corridor example recorded with seed 19: AGV-1 waits for AGV-2 from 121.54 s; the seek lands inside that wait
  await page.locator("input[type=range]").fill("128");
  await expect(page.locator("output")).toContainText("t = 128");
  expect((await page.locator(".status").textContent())?.trim()).toBe("paused"); // a seek, not playback
  await page.locator(".entities").getByRole("button", { name: "AGV-1", exact: true }).click();
  // the wait is recorded state: AGV-1's `wait` at the cursor, read without the event that opened it
  await expect(page.locator(".detail .detail-line").filter({ hasText: /^Blocked by/ })).toHaveText(/^Blocked by AGV-2/);
  await page.getByRole("tab", { name: "Overview", exact: true }).click();
  const trafficLine = page
    .locator(".overview .ov-section")
    .filter({ has: page.getByRole("heading", { name: "Traffic", exact: true }) })
    .locator("p");
  await expect(trafficLine).toHaveText(/^1 waiting/);
  expect((await page.locator(".status").textContent())?.trim()).toBe("paused"); // still paused: nothing played since the seek
  expect(errors).toEqual([]);
});

test("a vehicle chosen in the entity list shows its readable detail, and the offline HTML of that trace opens from file", async ({
  page,
  context,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await page.locator("input[type=file]").setInputFiles(traffic);
  await expect(page.locator(".status")).toHaveText("paused");
  await page
    .locator(".entities")
    .getByRole("button", { name: "AGV-1", exact: true })
    .click();
  await expect(page.locator(".detail .detail-line").first()).toHaveText(/^AGV-1 /);
  const waiting = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export HTML" }).click();
  const path = testInfo.outputPath("traffic.html");
  await (await waiting).saveAs(path);
  await context.setOffline(true);
  const requests: string[] = [];
  const offline = await context.newPage();
  offline.on("request", (r) => {
    if (!r.url().startsWith("file:")) requests.push(r.url());
  });
  offline.on("pageerror", (e) => errors.push(e.message));
  await offline.goto(pathToFileURL(path).href);
  await expect(offline.locator("output")).toContainText("t = 0");
  await offline.getByRole("button", { name: "Next event", exact: true }).click();
  await expect(offline.locator("output")).toContainText("seq");
  await expect(offline.locator("canvas")).toHaveCount(1);
  // The player's selection shows the readable detail above its raw state view (spec section 5.8): click a warehouse
  // where the canvas draws it, found by its fill colour (packages/scene/src/draw/palette.ts, warehouse).
  const warehouse = await offline.locator("canvas").evaluate((source: HTMLCanvasElement) => {
    const copy = document.createElement("canvas");
    copy.width = source.width;
    copy.height = source.height;
    const context = copy.getContext("2d")!;
    context.drawImage(source, 0, 0);
    const { data } = context.getImageData(0, 0, copy.width, copy.height);
    const rect = source.getBoundingClientRect();
    for (let i = 0; i < data.length; i += 4)
      if (Math.abs(data[i]! - 0xfd) <= 4 && Math.abs(data[i + 1]! - 0xe6) <= 4 && Math.abs(data[i + 2]! - 0x8a) <= 4) {
        const x = (i / 4) % copy.width,
          y = Math.floor(i / 4 / copy.width);
        // a few pixels inside the first fill pixel met, row by row
        return { x: rect.left + ((x + 4) * rect.width) / copy.width, y: rect.top + ((y + 4) * rect.height) / copy.height };
      }
    return null;
  });
  expect(warehouse).not.toBeNull();
  await offline.mouse.click(warehouse!.x, warehouse!.y);
  await expect(offline.locator("#detail .detail-line").first()).toContainText("warehouse");
  expect(requests).toEqual([]);
  expect(errors).toEqual([]);
});
