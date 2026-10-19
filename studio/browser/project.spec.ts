import { test, expect } from "@playwright/test";
import { writeFile, readFile, mkdir, rm, readdir } from "node:fs/promises";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { ProjectFixture } from "./project-fixture";

test("installed project: starter, managed generation, explicit run and reopen", async ({
  page,
  browserName,
  browser,
}, info) => {
  test.setTimeout(240000);
  const fixture = new ProjectFixture();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    await fixture.setup();
    await page.goto(await fixture.launch());
    await expect(
      page.getByRole("navigation", { name: "Project navigation" }),
    ).toBeVisible();
    await expect(page.getByText("No runs yet", { exact: true })).toBeVisible();
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.keyboard.press(browserName === "webkit" ? "Alt+Tab" : "Tab");
    await expect(page.locator(":focus")).toHaveText("Skip to content");
    await page.keyboard.press("Enter");
    await expect(page.locator("#workspace-content")).toBeFocused();
    await expect(
      page.getByRole("button", { name: "Production", exact: false }),
    ).toBeInViewport();
    const evidence = resolve(
      "../.superpowers/sdd/2026-10-19-studio-p1-projects/screenshots",
    );
    await mkdir(evidence, { recursive: true });
    await page.screenshot({
      path: join(evidence, info.project.name + "-setup-1280x800.png"),
    });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.screenshot({
      path: join(evidence, info.project.name + "-setup-1440x900.png"),
    });
    await page
      .getByRole("button", { name: "Production", exact: false })
      .click();
    await expect(
      page.getByRole("heading", { name: "Review proposed files" }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Apply changes", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Prepare environment", exact: true }),
    ).toBeVisible();
    await fixture.useDevelopmentWheel();
    await page
      .getByRole("button", { name: "Prepare environment", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Apply changes", exact: true }),
    ).toBeVisible({ timeout: 60000 });
    await page
      .getByRole("button", { name: "Apply changes", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Prepare environment", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Use environment and restart" }),
    ).toBeVisible({ timeout: 60000 });
    await page
      .getByRole("button", { name: "Use environment and restart" })
      .click();
    await page.goto(await fixture.link(1));
    await expect(page.getByText("No runs yet", { exact: true })).toBeVisible();
    await page
      .getByRole("link", { name: "Production", exact: false })
      .first()
      .click();
    await page
      .getByRole("button", { name: "Prepare model", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Run", exact: true }),
    ).toBeVisible({ timeout: 30000 });
    await page.getByLabel("Seed", { exact: true }).fill("29");
    await page.getByRole("link", { name: "Overview", exact: true }).click();
    await page.goBack();
    await expect(page.getByLabel("Seed", { exact: true })).toHaveValue("29");
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.screenshot({
      path: join(evidence, info.project.name + "-model-1440x900.png"),
    });
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.screenshot({
      path: join(evidence, info.project.name + "-model-1280x800.png"),
    });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText(
      "succeeded",
      { timeout: 30000 },
    );
    await expect(page.locator(".ws-recording .status")).toHaveText("paused", {
      timeout: 30000,
    });
    await page.getByRole("button", { name: "Next event", exact: true }).click();
    await expect(page.locator(".ws-recording output")).toContainText("seq");
    await page.screenshot({
      path: join(evidence, info.project.name + "-run-1440x900.png"),
    });
    await page.setViewportSize({ width: 1280, height: 800 });
    await expect(page.getByLabel("Event phase", { exact: true })).toBeVisible();
    expect(
      await page
        .getByLabel("Event phase", { exact: true })
        .evaluate((e) => e.getBoundingClientRect().width),
    ).toBeGreaterThan(160);
    await page.screenshot({
      path: join(evidence, info.project.name + "-run-1280x800.png"),
    });
    await page
      .getByLabel("Run name", { exact: true })
      .fill("First production run");
    await page.getByRole("button", { name: "Save notes", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "First production run" }),
    ).toBeVisible();
    const downloaded = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "Export HTML", exact: true })
      .click();
    const html = join(fixture.root, "first-run.html");
    await (await downloaded).saveAs(html);
    expect((await readFile(html, "utf8")).length).toBeGreaterThan(1000);
    const offline = await browser.newContext({ offline: true });
    const offlinePage = await offline.newPage();
    const requests: string[] = [];
    offlinePage.on("request", (r) => {
      if (!r.url().startsWith("file:")) requests.push(r.url());
    });
    await offlinePage.goto(pathToFileURL(html).href);
    await expect(offlinePage.locator("output")).toContainText("t = 0");
    expect(requests).toEqual([]);
    await offline.close();
    await page
      .getByRole("button", { name: "Export video / PNG", exact: true })
      .click();
    await expect(
      page.getByRole("dialog", { name: "Export recording" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await fixture.stop();
    await page.goto(await fixture.launch());
    await expect(
      page.getByRole("link", { name: "First production run" }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Inspect active run →" }),
    ).toHaveCount(0);
    await fixture.stop();
    const cache = join(fixture.root, ".simulatte/cache/environments");
    for (const entry of await readdir(cache, { withFileTypes: true }))
      if (entry.isDirectory() && !entry.name.startsWith("build-"))
        await rm(join(cache, entry.name), { recursive: true, force: true });
    await writeFile(
      join(fixture.root, "models/production.py"),
      'raise AssertionError("recovery must not import")',
    );
    await page.goto(await fixture.launch());
    await expect(
      page.getByRole("heading", { name: "Saved work is available" }),
    ).toBeVisible();
    await page
      .getByRole("link", { name: "First production run", exact: true })
      .click();
    await expect(page.locator(".ws-recording .status")).toHaveText("paused", {
      timeout: 30000,
    });
    await expect(
      page.getByRole("button", { name: "Save notes", exact: true }),
    ).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally {
    await fixture.close();
  }
});

test("existing and legacy model registration never executes on open", async ({
  page,
}) => {
  test.setTimeout(120000);
  const fixture = new ProjectFixture();
  try {
    await fixture.setup();
    await writeFile(
      join(fixture.root, "plant.py"),
      'raise RuntimeError("broken model must not be imported on open")\n',
    );
    await page.goto(
      await fixture.launch(join(fixture.root, "plant.py") + ":alternate"),
    );
    await page.getByRole("link", { name: "Models", exact: true }).click();
    await expect(page.getByLabel("Entrypoint", { exact: true })).toHaveValue(
      "plant.py:alternate",
    );
    await page.getByRole("button", { name: "Preview registration" }).click();
    await page.getByRole("button", { name: "Apply registration" }).click();
    expect(await readFile(join(fixture.root, "plant.py"), "utf8")).toContain(
      "raise RuntimeError",
    );
    await page.getByRole("link", { name: "Runs", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "No runs yet" }),
    ).toBeVisible();
    await fixture.stop();
    await page.goto(await fixture.launch());
    await expect(page.getByText("No runs yet", { exact: true })).toBeVisible();
  } finally {
    await fixture.close();
  }
});

test("two models keep drafts and explicit busy ownership across navigation", async ({
  page,
}) => {
  test.setTimeout(120000);
  const fixture = new ProjectFixture();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    await fixture.setup();
    const model = (delay: number) =>
      `import time\nfrom simulatte import model\nfrom dataclasses import dataclass\n@dataclass(frozen=True)\nclass Params:\n    value: int = 1\n@model(params=Params,horizon=3)\ndef build(env, layout, params):\n    def work():\n        time.sleep(${delay})\n        yield env.timeout(1)\n    env.process(work())\n`;
    await writeFile(join(fixture.root, "a.py"), model(20));
    await writeFile(join(fixture.root, "b.py"), model(0));
    await page.goto(await fixture.launch());
    await page.getByRole("link", { name: "Models", exact: true }).click();
    for (const key of ["a", "b"]) {
      await page.getByLabel("Registration key", { exact: true }).fill(key);
      await page
        .getByLabel("Display name", { exact: true })
        .fill(key.toUpperCase());
      await page
        .getByLabel("Entrypoint", { exact: true })
        .fill(`${key}.py:build`);
      await page.getByRole("button", { name: "Preview registration" }).click();
      await page.getByRole("button", { name: "Apply registration" }).click();
      await expect(
        page.getByRole("heading", { name: key.toUpperCase(), exact: true }),
      ).toBeVisible();
    }
    await page.getByRole("link", { name: "Settings", exact: true }).click();
    await page
      .getByText("Use an existing interpreter (advanced)", { exact: true })
      .click();
    await page.getByLabel("Python interpreter path").fill(fixture.python);
    await page.getByRole("button", { name: "Check interpreter" }).click();
    await page
      .getByRole("button", { name: "Use environment and restart" })
      .click();
    await expect(
      page.getByRole("heading", { name: "Selected environment" }),
    ).toBeVisible();
    await page.getByRole("link", { name: "Models", exact: true }).click();
    await page.getByRole("link", { name: "A", exact: true }).click();
    await page
      .getByRole("button", { name: "Prepare model", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Run", exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Stop run", exact: true }),
    ).toBeVisible();
    await page.getByRole("link", { name: "Models", exact: true }).click();
    await page.getByRole("link", { name: "B", exact: true }).click();
    await page
      .getByRole("button", { name: "Prepare model", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Run", exact: true }),
    ).toBeVisible();
    await page.getByLabel("Seed", { exact: true }).fill("44");
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "The interactive slot is busy" }),
    ).toBeVisible();
    await expect(page.getByLabel("Seed", { exact: true })).toHaveValue("44");
    await page
      .getByRole("button", { name: "Stop active run and run B" })
      .click();
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText(
      "succeeded",
      { timeout: 30000 },
    );
    expect(errors).toEqual([]);
  } finally {
    await fixture.close();
  }
});
