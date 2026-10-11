import { chromium, expect } from "@playwright/test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

// Run against a wheel-installed executable, outside the source checkout. Neither
// CLI output nor browser credentials are forwarded to logs or persisted artifacts.
const [executable, fixture, ...extra] = process.argv.slice(2);
if (!executable || !fixture || extra.length) {
  console.error("Usage: node installed-smoke.mjs <installed-simulatte> <trace-fixture>");
  process.exitCode = 2;
} else {
  const temporary = await mkdtemp(join(tmpdir(), "simulatte-installed-smoke-"));
  const child = spawn(resolve(executable), [
    "view", resolve(fixture), "--no-browser", "--port", "0",
  ], {
    cwd: temporary,
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const closed = new Promise(resolveClosed => child.once("close", resolveClosed));
  child.stderr.resume();
  let browser;
  let cleanupPromise;
  let stopping = false;
  const cleanup = () => cleanupPromise ??= (async () => {
    stopping = true;
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGINT");
    const force = setTimeout(() => child.kill("SIGKILL"), 3000);
    try {
      await Promise.allSettled([closed, browser?.close()]);
    } finally {
      clearTimeout(force);
      await rm(temporary, { recursive: true, force: true });
    }
  })();
  const interrupted = () => {
    process.exitCode = 1;
    void cleanup();
  };
  process.once("SIGINT", interrupted);
  process.once("SIGTERM", interrupted);
  const watchdog = setTimeout(interrupted, 90000);
  let stage = "CLI startup";
  try {
    const link = await new Promise((resolveLink, reject) => {
      let output = "";
      const finish = (error, value) => {
        clearTimeout(timer);
        child.stdout.removeListener("data", onData);
        child.stdout.resume();
        child.removeListener("error", onError);
        child.removeListener("exit", onExit);
        output = "";
        if (error) reject(error);
        else resolveLink(value);
      };
      const onError = () => finish(new Error("CLI could not start"));
      const onExit = () => finish(new Error("CLI exited before startup"));
      const onData = data => {
        output = (output + data.toString()).slice(-65536);
        const match = output.match(/http:\/\/127\.0\.0\.1:\d+\/bootstrap\?token=[\w-]+/);
        if (match) finish(null, match[0]);
      };
      const timer = setTimeout(() => finish(new Error("CLI startup timeout")), 20000);
      child.once("error", onError);
      child.once("exit", onExit);
      child.stdout.on("data", onData);
    });
    if (stopping) throw new Error("Interrupted");
    stage = "browser launch";
    browser = await chromium.launch({ timeout: 20000 });
    if (stopping) {
      await browser.close();
      throw new Error("Interrupted");
    }
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    let errors = 0;
    let rangeResponses = 0;
    page.on("pageerror", () => errors++);
    page.on("response", response => {
      if (new URL(response.url()).pathname === "/trace" && response.status() === 206)
        rangeResponses++;
    });
    stage = "bootstrap and installed assets";
    await page.goto(link);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 20000 });
    assert.equal(page.url(), `${new URL(link).origin}/`);
    await page.getByRole("button", { name: "Next event", exact: true }).click();
    await expect(page.locator("footer output")).toContainText("seq");
    assert.ok(rangeResponses > 0);
    stage = "HTML export and authenticated reload";
    const downloading = page.waitForEvent("download");
    await page.getByRole("button", { name: "Export HTML", exact: true }).click();
    const html = join(temporary, "recording.html");
    await (await downloading).saveAs(html);
    await page.reload();
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 20000 });
    stage = "network-off standalone playback";
    await context.setOffline(true);
    let networkRequests = 0;
    page.on("request", request => {
      if (!/^(file|data|blob):/.test(request.url())) networkRequests++;
    });
    await page.goto(pathToFileURL(html).href);
    await expect(page.locator("footer output")).toContainText("t =", { timeout: 20000 });
    await page.getByRole("button", { name: "Next event", exact: true }).click();
    await expect(page.locator("footer output")).toContainText("seq");
    const before = Number(await page.getByLabel("Simulation time", { exact: true }).inputValue());
    await page.getByRole("button", { name: "Play", exact: true }).click();
    await page.waitForFunction(start => Number(document.querySelector('input[type="range"]').value) > start, before);
    const pause = page.getByRole("button", { name: "Pause", exact: true });
    if (await pause.isVisible()) await pause.click();
    assert.equal(await page.locator("#error").textContent(), "");
    assert.equal(networkRequests, 0);
    assert.equal(errors, 0);
    assert.ok(!stopping);
    console.log(JSON.stringify({ installedCLI: true, bootstrap: true, rangePlayback: true,
      reload: true, htmlExport: true, offlinePlayback: true, networkRequests, errors }));
  } catch {
    // Playwright errors can embed navigation URLs. Report only a fixed stage label.
    console.error(`Installed Studio smoke failed during ${stage}.`);
    process.exitCode = 1;
  } finally {
    clearTimeout(watchdog);
    await cleanup();
    process.removeListener("SIGINT", interrupted);
    process.removeListener("SIGTERM", interrupted);
  }
}
