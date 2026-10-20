/** P3 shell: focused routes, completion notices and the overview → model → run flow at laptop and workstation sizes. */
import { test, expect, type Page } from "@playwright/test";
import { writeFile, mkdir } from "node:fs/promises";
import { join, resolve } from "node:path";
import { ProjectFixture } from "./project-fixture";

const evidence = resolve("../.superpowers/sdd/2026-10-19-studio-p3-workspace/screenshots");
const model = (delay: number) =>
  `import time\nfrom simulatte import model\nfrom dataclasses import dataclass\n@dataclass(frozen=True)\nclass Params:\n    value: int = 1\n@model(params=Params,horizon=3)\ndef build(env, layout, params):\n    def work():\n        time.sleep(${delay})\n        yield env.timeout(1)\n    env.process(work())\n`;
const hash = (page: Page) => page.evaluate(() => location.hash);
const capture = async (page: Page, engine: string, step: string) => {
  for (const [width, height] of [[1440, 900], [1280, 800]] as const) {
    await page.setViewportSize({ width, height });
    await expect(page.getByRole("navigation", { name: "Project navigation" })).toBeInViewport();
    await page.screenshot({ path: join(evidence, `${engine}-${step}-${width}x${height}.png`) });
  }
};

test("completionPreservesRouteAndDraft", async ({ page }, info) => {
  test.setTimeout(240000);
  const fixture = new ProjectFixture();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  try {
    await fixture.setup();
    await mkdir(evidence, { recursive: true });
    await writeFile(join(fixture.root, "a.py"), model(14));
    await writeFile(join(fixture.root, "b.py"), model(0));
    await page.goto(await fixture.launch());
    await page.waitForURL((url) => url.pathname === "/");
    await page.getByRole("link", { name: "Models", exact: true }).click();
    for (const key of ["a", "b"]) {
      await page.getByLabel("Registration key", { exact: true }).fill(key);
      await page.getByLabel("Display name", { exact: true }).fill(key.toUpperCase());
      await page.getByLabel("Entrypoint", { exact: true }).fill(`${key}.py:build`);
      await page.getByRole("button", { name: "Preview registration" }).click();
      await page.getByRole("button", { name: "Apply registration" }).click();
      await expect(page.getByRole("heading", { name: key.toUpperCase(), exact: true })).toBeVisible();
    }
    await page.getByRole("link", { name: "Settings", exact: true }).click();
    await page.getByText("Use an existing interpreter (advanced)", { exact: true }).click();
    await page.getByLabel("Python interpreter path").fill(fixture.python);
    await page.getByRole("button", { name: "Check interpreter" }).click();
    await page.getByRole("button", { name: "Use environment and restart" }).click();
    await expect(page.getByRole("heading", { name: "Selected environment" })).toBeVisible();
    // Model B: configure, run and inspect; this is the flow the screenshots document.
    await page.getByRole("link", { name: "Models", exact: true }).click();
    await page.getByRole("link", { name: "B", exact: true }).click();
    const modelB = (await hash(page)).replace("#/model/", "");
    await page.getByRole("button", { name: "Prepare model", exact: true }).click();
    await expect(page.getByRole("button", { name: "Run", exact: true })).toBeVisible({ timeout: 30000 });
    await capture(page, info.project.name, "model");
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page.locator(".ws-page-heading .ws-badge").first()).toHaveText("succeeded", { timeout: 30000 });
    await expect(page.locator(".ws-recording .status")).toHaveText("paused", { timeout: 30000 });
    const runB = await hash(page);
    expect(runB).toMatch(/^#\/run\/.+/);
    await capture(page, info.project.name, "run");
    await page.getByRole("link", { name: "Overview", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Recent runs" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Open run →" })).toBeVisible();
    await capture(page, info.project.name, "overview");
    // Model A: start a slow run (the active run), then leave its page.
    await page.getByRole("link", { name: "Models", exact: true }).click();
    await page.getByRole("link", { name: "A", exact: true }).click();
    await page.getByRole("button", { name: "Prepare model", exact: true }).click();
    await expect(page.getByRole("button", { name: "Run", exact: true })).toBeVisible({ timeout: 30000 });
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page.getByRole("button", { name: "Stop run", exact: true })).toBeVisible();
    const runA = await hash(page);
    expect(runA).not.toBe(runB);
    // Edit B's draft, then read B's finished run while A completes.
    await page.getByRole("link", { name: "Models", exact: true }).click();
    await page.getByRole("link", { name: "B", exact: true }).click();
    await page.getByLabel("Seed", { exact: true }).fill("44");
    await page.evaluate((h) => { location.hash = h; }, runB);
    await expect(page.locator(".ws-page-heading .ws-badge").first()).toHaveText("succeeded");
    const notice = page.locator(".ws-toast[role=status]");
    await expect(notice).toHaveCount(1, { timeout: 60000 });
    await expect(notice).toContainText(/A completed/);
    await expect(notice.getByRole("link", { name: "Open →" })).toHaveAttribute("href", runA);
    expect(await hash(page)).toBe(runB);
    await expect(page.locator(".ws-page-heading .ws-badge").first()).toHaveText("succeeded");
    await expect(page.getByRole("link", { name: "Inspect active run →" })).toHaveCount(0);
    // The draft of B survived the completion; keyboard dismissal removes the notice.
    await page.getByRole("link", { name: "Models", exact: true }).click();
    await page.getByRole("link", { name: "B", exact: true }).click();
    await expect(page.getByLabel("Seed", { exact: true })).toHaveValue("44");
    await notice.getByRole("button", { name: /^Dismiss/ }).focus();
    await page.keyboard.press("Enter");
    await expect(notice).toHaveCount(0);
    // Back and forward keep the selected identities.
    await page.goBack();
    expect(await hash(page)).toBe("#/models");
    await page.goBack();
    expect(await hash(page)).toBe(runB);
    await expect(page.locator(".ws-page-heading .ws-badge").first()).toHaveText("succeeded");
    await page.goForward();
    await page.goForward();
    expect(await hash(page)).toBe("#/model/" + modelB);
    await expect(page.getByLabel("Seed", { exact: true })).toHaveValue("44");
    // Direct reopening of result and history routes keeps their identities.
    const origin = new URL(page.url()).origin;
    await page.goto(origin + "/" + runB);
    await expect(page.locator(".ws-page-heading .ws-badge").first()).toHaveText("succeeded", { timeout: 30000 });
    expect(await hash(page)).toBe(runB);
    await page.goto(origin + "/#/runs/" + modelB);
    await expect(page.getByRole("heading", { name: "Runs of B" })).toBeVisible();
    await expect(page.getByLabel("Filter by model", { exact: true })).toHaveValue(modelB);
    await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toContainText("Runs");
    await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toContainText("B");
    expect(await hash(page)).toBe("#/runs/" + modelB);
    await page.getByLabel("Filter by model", { exact: true }).selectOption("");
    expect(await hash(page)).toBe("#/runs");
    await expect(page.getByRole("heading", { name: "Runs", exact: true })).toBeVisible();
    await page.goto(origin + "/#/compare/x,y");
    await expect(page.getByRole("heading", { name: "Comparison", exact: true })).toBeVisible();
    await expect(page.getByRole("list", { name: "Selected results" })).toContainText("x");
    await page.goto(origin + "/#/model/%ZZ");
    await expect(page.getByRole("heading", { name: "Recent runs" })).toBeVisible();
    expect(errors).toEqual([]);
  } finally {
    await fixture.close();
  }
});
