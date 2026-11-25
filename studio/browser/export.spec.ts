import { test as base, expect } from "@playwright/test";
import path from "node:path";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
const test = base.extend({
  page: async ({ playwright, browserName }, use) => {
    // WebKit's ephemeral data store rejects OPFS; export requires a persistent store.
    const directory = await mkdtemp(
      path.join(os.tmpdir(), "simulatte-browser-"),
    );
    const context = await playwright[browserName].launchPersistentContext(
      directory,
      {
        headless: true,
        viewport: { width: 1440, height: 1000 },
        baseURL: "http://127.0.0.1:4173",
      },
    );
    try {
      await use(context.pages()[0]!);
    } finally {
      await context.close();
      await rm(directory, { recursive: true, force: true });
    }
  },
});
const fixture = path.resolve("../tests/fixtures/traces/viewer/viewer.simtrace");
for (const format of ["mp4", "webm", "png"] as const)
  test(`exports ${format} through OPFS without moving the viewer`, async ({
    page,
  }, info) => {
    await page.goto("/");
    await page.locator('input[type="file"]').setInputFiles(fixture);
    await expect(page.locator(".run-title strong")).toHaveText(
      "viewer.simtrace",
    );
    await expect(page.locator(".status")).toHaveText("paused");
    const before = await page.evaluate(async () => {
      const root = await navigator.storage.getDirectory();
      const names = [];
      for await (const name of (root as any).keys())
        if (name.startsWith("simulatte-export-")) names.push(name);
      return names.sort();
    });
    const time = await page.locator("footer output").textContent();
    await page.getByRole("button", { name: "Export video / PNG" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Format", { exact: true }).selectOption(format);
    await dialog.getByLabel("End time", { exact: true }).fill("0.1");
    if (format !== "mp4") {
      await dialog.getByLabel("Width", { exact: true }).fill("320");
      await dialog.getByLabel("Height", { exact: true }).fill("180");
    } // MP4 exercises the default 1920 × 1080 configuration.
    // Direct file pickers require manual UI. This exercises the cross-browser OPFS route.
    await page.evaluate(() => {
      Object.defineProperty(window, "showSaveFilePicker", { value: undefined });
    });
    await dialog.getByRole("button", { name: "Start export" }).click();
    const link = dialog.getByRole("link", { name: /Download simulatte/ });
    await expect(link).toBeVisible({ timeout: 30000 });
    const download = page.waitForEvent("download");
    await link.click();
    await (
      await download
    ).saveAs(
      path.join(info.outputDir, `export.${format === "png" ? "zip" : format}`),
    );
    await expect(page.locator("footer output")).toHaveText(time!);
    await dialog
      .getByRole("button", { name: "Remove temporary copy", exact: true })
      .click();
    await expect(link).toHaveCount(0);
    expect(
      await page.evaluate(async () => {
        const root = await navigator.storage.getDirectory();
        const keys = [];
        for await (const key of (root as any).keys()) keys.push(key);
        return keys
          .filter((key: string) => key.startsWith("simulatte-export-"))
          .sort();
      }),
    ).toEqual(before);
  });

test("cancels a running PNG export, keeps the viewer position and removes staging", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles(fixture);
  await expect(page.locator(".status")).toHaveText("paused");
  await page.evaluate(() => {
    Object.defineProperty(window, "showSaveFilePicker", { value: undefined });
  });
  const staged = () =>
    page.evaluate(async () => {
      const root = await navigator.storage.getDirectory(),
        names: string[] = [];
      for await (const name of (root as any).keys())
        if (name.startsWith("simulatte-export-")) names.push(name);
      return names.sort();
    });
  // Hold real PNG conversion briefly so a fast engine cannot finish before the cancel gesture.
  await page.evaluate(() => {
    if (typeof OffscreenCanvas !== "undefined") {
      const convert = OffscreenCanvas.prototype.convertToBlob;
      OffscreenCanvas.prototype.convertToBlob = async function (options) {
        await new Promise((resolve) => setTimeout(resolve, 100));
        return convert.call(this, options);
      };
    }
  });
  const before = await staged(),
    time = await page.locator("footer output").textContent();
  await page.getByRole("button", { name: "Export video / PNG" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Format", { exact: true }).selectOption("png");
  await dialog.getByLabel("Width", { exact: true }).fill("320");
  await dialog.getByLabel("Height", { exact: true }).fill("180");
  await dialog.getByRole("button", { name: "Start export" }).click();
  await expect(dialog.locator("output")).toContainText("frames");
  await dialog.getByRole("button", { name: "Cancel export" }).click();
  await expect(dialog.locator('p[role="status"]')).toContainText(
    "Export cancelled",
  );
  await expect(dialog.getByRole("link")).toHaveCount(0);
  expect(await staged()).toEqual(before);
  await expect(page.locator("footer output")).toHaveText(time!);
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(dialog).toHaveCount(0);
});

/** The first PNG of a stored (level 0) zip: from its signature to the end of its IEND chunk. */
function firstPng(zip: Buffer): Buffer {
  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const start = zip.indexOf(signature);
  expect(start, "a PNG in the archive").toBeGreaterThan(0);
  let at = start + signature.length;
  for (;;) {
    const length = zip.readUInt32BE(at);
    const type = zip.toString("latin1", at + 4, at + 8);
    at += 12 + length;
    if (type === "IEND") return zip.subarray(start, at);
  }
}

test("a PNG export of the corridor trace draws the map: the first image holds strip pixels", async ({
  page,
}, info) => {
  await page.goto("/");
  await page
    .locator('input[type="file"]')
    .setInputFiles(
      path.resolve("../tests/fixtures/traces/generated/example_traffic.simtrace"),
    );
  await expect(page.locator(".status")).toHaveText("paused");
  await page.evaluate(() => {
    Object.defineProperty(window, "showSaveFilePicker", { value: undefined });
  });
  await page.getByRole("button", { name: "Export video / PNG" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Format", { exact: true }).selectOption("png");
  await dialog.getByLabel("End time", { exact: true }).fill("0.1");
  await dialog.getByLabel("Width", { exact: true }).fill("640");
  await dialog.getByLabel("Height", { exact: true }).fill("360");
  await dialog.getByRole("button", { name: "Start export" }).click();
  const link = dialog.getByRole("link", { name: /Download simulatte/ });
  await expect(link).toBeVisible({ timeout: 30000 });
  const download = page.waitForEvent("download");
  await link.click();
  const archive = path.join(info.outputDir, "traffic.zip");
  await (await download).saveAs(archive);
  const png = firstPng(await readFile(archive)).toString("base64");
  // Decode it in the page and count the pixels of the palette's strip colour (0xcbd5e1) within a small tolerance.
  const strips = await page.evaluate(async (encoded) => {
    const bytes = Uint8Array.from(atob(encoded), (c) => c.charCodeAt(0));
    const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/png" }));
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const context = canvas.getContext("2d")!;
    context.drawImage(bitmap, 0, 0);
    const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
    let n = 0;
    for (let i = 0; i < data.length; i += 4)
      if (
        Math.abs(data[i]! - 0xcb) <= 6 &&
        Math.abs(data[i + 1]! - 0xd5) <= 6 &&
        Math.abs(data[i + 2]! - 0xe1) <= 6
      )
        n++;
    return { width: bitmap.width, height: bitmap.height, strips: n };
  }, png);
  expect([strips.width, strips.height]).toEqual([640, 360]);
  expect(strips.strips).toBeGreaterThanOrEqual(20);
});
