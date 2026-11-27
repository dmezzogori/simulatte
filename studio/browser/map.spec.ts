import { test, expect, type Page } from "@playwright/test";
import { existsSync } from "node:fs";

// The map through the render harness (harness/, served on its own port by playwright.config.ts): seven canvases with
// labels hidden (three of them pin the scene without a map and the sprites), then the Overview panel and the click
// detail with hostile labels, and their updates in place. The harness sets `document.title = "ready"` once the scene
// has drawn.
const harness = "http://127.0.0.1:4174/";
const hostile = "</script><img src=x onerror=alert(1)>";
const hostileVehicle = "H1"; // its label in fixtures/map/sprites.json

// Palette colours (packages/scene/src/draw/palette.ts); the shots are read back as pixels.
const strip = 0xcbd5e1;
const moving = 0x2563eb;
const waiting = 0xd97706;
// Glyph colours of the scene without a map (packages/scene/src/project.ts): a node and a server. The vehicles of the
// fallback grid are too small at the fitted scale for the 20-pixel check.
const nodeGlyph = 0xdce1dc;
const serverGlyph = 0xb7c9ad;

interface Shot {
  name: string;
  query: string;
  /** The colours that must cover at least 20 pixels: on unheld strips and on the vehicle bodies, where nothing tints them. */
  colours: Record<string, number>;
}
const SHOTS: Shot[] = [
  { name: "corridor-symbol", query: "frame=corridor-wait&scale=8", colours: { strip, moving, waiting } },
  { name: "corridor-true", query: "frame=corridor-wait&scale=30&x=46&y=2", colours: { strip, moving, waiting } },
  { name: "corridor-select-agv", query: "frame=corridor-wait&scale=8&select=AGV-1", colours: { strip, moving, waiting } },
  { name: "advanced-symbol", query: "frame=advanced-charging", colours: { strip } },
  // the path without a map is drawn, fitted and picked as before the map: these two pin it
  { name: "no-layout", query: "frame=no-layout", colours: { nodeGlyph } },
  { name: "production", query: "frame=production", colours: { serverGlyph } },
  { name: "sprites-symbol", query: "frame=sprites&scale=8", colours: { strip, moving, waiting } },
];

async function open(page: Page, query: string): Promise<string[]> {
  const problems: string[] = [];
  page.on("pageerror", (error) => problems.push(error.message));
  page.on("console", (message) => message.type() === "error" && problems.push(message.text()));
  page.on("dialog", (dialog) => {
    problems.push(`dialog: ${dialog.message()}`);
    void dialog.dismiss();
  });
  await page.goto(`${harness}?${query}`);
  await page.waitForFunction(() => document.title === "ready" || document.title === "error");
  expect(await page.title()).toBe("ready");
  return problems;
}

/** The pixels of the scene's canvas (it keeps its drawing buffer) within `tolerance` per channel of each colour. */
async function count(page: Page, colours: Record<string, number>, tolerance = 6): Promise<Record<string, number>> {
  return page.evaluate(
    ({ colours, tolerance }) => {
      const source = document.querySelector<HTMLCanvasElement>("#host canvas")!;
      const copy = document.createElement("canvas");
      copy.width = source.width;
      copy.height = source.height;
      const context = copy.getContext("2d")!;
      context.drawImage(source, 0, 0);
      const { data } = context.getImageData(0, 0, copy.width, copy.height);
      const totals: Record<string, number> = {};
      for (const [name, rgb] of Object.entries(colours)) {
        const [r, g, b] = [(rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255];
        let n = 0;
        for (let i = 0; i < data.length; i += 4)
          if (
            Math.abs(data[i]! - r) <= tolerance &&
            Math.abs(data[i + 1]! - g) <= tolerance &&
            Math.abs(data[i + 2]! - b) <= tolerance
          )
            n++;
        totals[name] = n;
      }
      return totals;
    },
    { colours, tolerance },
  );
}

for (const shot of SHOTS)
  test(`map shot ${shot.name}`, async ({ page }, info) => {
    const problems = await open(page, `${shot.query}&labels=0`);
    // The palette check runs everywhere: a drawing that lost its strips or its vehicles fails on any engine.
    const counts = await count(page, shot.colours);
    for (const name of Object.keys(shot.colours)) expect(counts[name], `${name} pixels`).toBeGreaterThanOrEqual(20);
    expect(problems).toEqual([]);
    // The stored images are Linux Chromium renders (studio/browser/README-baselines.md).
    if (info.project.name !== "chromium" || process.platform !== "linux") return;
    const baseline = info.snapshotPath(`${shot.name}.png`);
    // Playwright's default mode, "missing", would write the first image and pass: a run without the file skips,
    // and only an explicit --update-snapshots ("changed" or "all") makes the images.
    const updating = info.config.updateSnapshots === "all" || info.config.updateSnapshots === "changed";
    test.skip(
      !existsSync(baseline) && !updating,
      "baseline not generated: see studio/browser/README-baselines.md",
    );
    // 300 pixels: fewer than one vehicle symbol, so a missing sprite fails (the default per-pixel threshold stays)
    await expect(page.locator("#host canvas")).toHaveScreenshot(`${shot.name}.png`, { maxDiffPixels: 300 });
  });

test("the Overview panel shows a hostile label as text and a click on a line marks it selected", async ({ page }) => {
  const problems = await open(page, "frame=sprites&panel=1");
  const line = page.locator(`#overview .ov-line[data-id="${hostileVehicle}"]`);
  await expect(line).toContainText(hostile);
  expect(await page.locator("img").count()).toBe(0);
  await expect(line).not.toHaveAttribute("aria-current", "true");
  await line.click();
  await expect(line).toHaveAttribute("aria-current", "true");
  await expect(page.locator('#overview .ov-line[aria-current="true"]')).toHaveCount(1);
  expect(await page.locator("img").count()).toBe(0);
  expect(problems).toEqual([]);
});

test("the click detail shows a hostile label as text", async ({ page }) => {
  const problems = await open(page, `frame=sprites&detail=${hostileVehicle}`);
  await expect(page.locator("#detail .detail-line").first()).toContainText(hostile);
  expect(await page.locator("img").count()).toBe(0);
  expect(problems).toEqual([]);
});

test("a ref of the detail shows the lines of the entity it names", async ({ page }) => {
  const problems = await open(page, "frame=corridor-wait&detail=AGV-1");
  const detail = page.locator("#detail");
  await expect(detail.locator(".detail-line").first()).toContainText(/^AGV-1 /);
  await detail.getByRole("button", { name: "AGV-2", exact: true }).first().click();
  await expect(detail.locator(".detail-line").first()).toContainText(/^AGV-2 /);
  await expect(detail).not.toContainText("Blocked by AGV-2");
  expect(problems).toEqual([]);
});

// The in-place update paths: a second fixture (`then`) is rendered after DOM nodes are tagged with a property, and the
// tagged nodes must still be in the page with new texts. corridor-wait and corridor-idle differ in the refs of every
// detail and in the order of the panel's lines, so both views are rebuilt between them (checked last); the pairs
// below keep the structure and change texts.
async function renderThen(page: Page): Promise<void> {
  await page.evaluate(() => (globalThis as unknown as { renderThen: () => void }).renderThen());
  await expect(page).toHaveTitle("then");
}

test("the detail writes new texts into the same lines and buttons when its refs are unchanged", async ({ page }) => {
  // AGV-3 moves to another fleet: the first line changes, the refs do not
  const problems = await open(page, "frame=corridor-wait&then=two-fleets&detail=AGV-3");
  const first = page.locator("#detail .detail-line").first();
  await expect(first).toHaveText("AGV-3 · fleet tuggers · WAITING_LOAD");
  const ref = page.locator("#detail .detail-ref").first();
  await ref.evaluate((node) => ((node as unknown as { tagged: boolean }).tagged = true));
  await first.evaluate((node) => ((node as unknown as { tagged: boolean }).tagged = true));
  await renderThen(page);
  await expect(first).toHaveText("AGV-3 · fleet shuttles · WAITING_LOAD");
  expect(await ref.evaluate((node) => (node as unknown as { tagged?: boolean }).tagged === true && node.isConnected)).toBe(true);
  expect(await first.evaluate((node) => (node as unknown as { tagged?: boolean }).tagged === true)).toBe(true);
  expect(problems).toEqual([]);
});

test("the Overview writes new texts into the same lines when its shape is unchanged", async ({ page }) => {
  // the same moment without the recorded wait: AGV-1 stops waiting, the lines keep their places
  const problems = await open(page, "frame=corridor-wait&then=bare-layout&panel=1");
  const line = page.locator('#overview .ov-line[data-id="AGV-1"]');
  await expect(line).toContainText("waiting 6 s for AGV-2");
  await line.evaluate((node) => ((node as unknown as { tagged: boolean }).tagged = true));
  await renderThen(page);
  await expect(line).toContainText("AGV-1 · loaded, 1 crate · stopped");
  expect(await line.evaluate((node) => (node as unknown as { tagged?: boolean }).tagged === true && node.isConnected)).toBe(true);
  expect(problems).toEqual([]);
});

test("the Overview and the detail show the second frame after a rebuild", async ({ page }) => {
  const problems = await open(page, "frame=corridor-wait&then=corridor-idle&panel=1");
  await renderThen(page);
  await expect(page.locator('#overview .ov-line[data-id="AGV-3"]')).toContainText("waiting 1 s for AGV-1");
  await expect(page.locator("#overview")).not.toContainText("waiting 6 s for AGV-2");
  const detail = await open(page, "frame=corridor-wait&then=corridor-idle&detail=AGV-1");
  await renderThen(page);
  await expect(page.locator("#detail .detail-line").first()).toHaveText("AGV-1 · fleet tuggers · TRAVELING_EMPTY");
  expect([...problems, ...detail]).toEqual([]);
});
