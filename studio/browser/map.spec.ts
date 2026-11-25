import { test, expect, type Page } from "@playwright/test";
import { existsSync } from "node:fs";

// The map through the render harness (harness/, served on its own port by playwright.config.ts): four canvases with
// labels hidden, then the Overview panel and the click detail with hostile labels. The harness sets
// `document.title = "ready"` once the scene has drawn.
const harness = "http://127.0.0.1:4174/";
const hostile = "</script><img src=x onerror=alert(1)>";
const hostileVehicle = "H1"; // its label in fixtures/map/sprites.json

// Palette colours (packages/scene/src/draw/palette.ts); the shots are read back as pixels.
const strip = 0xcbd5e1;
const moving = 0x2563eb;
const waiting = 0xd97706;

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
    await expect(page.locator("#host canvas")).toHaveScreenshot(`${shot.name}.png`, { maxDiffPixelRatio: 0.01 });
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
