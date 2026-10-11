// Usage: node bench/run.mjs chromium http://127.0.0.1:4182/?mode=performance /tmp/result.json
import { chromium, firefox, webkit } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
const [engine = "chromium", url, output = "/tmp/sp3-bench.json"] =
  process.argv.slice(2);
const browser = await { chromium, firefox, webkit }[engine].launch({
  headless: true,
  ...(engine === "chromium" ? { args: ["--enable-precise-memory-info"] } : {}),
});
let sampler;
try {
  const context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
    }),
    page = await context.newPage();
  const rss = {
    method:
      "macOS ps RSS sampled every 100 ms via CDP process IDs; renderer includes engine/native, total includes GPU and browser",
    baselineRenderer: 0,
    peakRenderer: 0,
    peakTotal: 0,
  };
  if (engine === "chromium" && process.platform === "darwin") {
    const cdp = await browser.newBrowserCDPSession();
    const sample = async () => {
      const { processInfo } = await cdp.send("SystemInfo.getProcessInfo");
      let total = 0,
        renderer = 0;
      for (const p of processInfo) {
        try {
          const bytes =
            Number(
              execFileSync("ps", ["-o", "rss=", "-p", String(p.id)], {
                encoding: "utf8",
              }).trim(),
            ) * 1024;
          total += bytes;
          if (p.type === "renderer") renderer += bytes;
        } catch {}
      }
      rss.peakRenderer = Math.max(rss.peakRenderer, renderer);
      rss.peakTotal = Math.max(rss.peakTotal, total);
      return renderer;
    };
    rss.baselineRenderer = await sample();
    let active = false;
    sampler = setInterval(() => {
      if (active) return;
      active = true;
      void sample()
        .catch(() => {})
        .finally(() => {
          active = false;
        });
    }, 100);
  }
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.addInitScript(() => {
    globalThis.__longTasks = [];
    try {
      new PerformanceObserver((list) => {
        for (const e of list.getEntries())
          globalThis.__longTasks.push(e.duration);
      }).observe({ type: "longtask", buffered: true });
    } catch {}
  });
  const start = Date.now();
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 180000 });
  await page.waitForFunction(
    () => document.querySelector("#status")?.textContent?.startsWith("{"),
    {},
    { timeout: Number(process.env.SP3_BENCH_TIMEOUT_MS ?? 1200000) },
  );
  const result = await page.evaluate(() => ({
    ...JSON.parse(document.querySelector("#status").textContent),
    longestTaskMs: Math.max(0, ...globalThis.__longTasks),
  }));
  result.wallSeconds = (Date.now() - start) / 1000;
  result.errors = errors;
  result.rss = rss;
  await writeFile(output, JSON.stringify(result, null, 2) + "\n");
  console.log(result);
} finally {
  clearInterval(sampler);
  await browser.close();
}
