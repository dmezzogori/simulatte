// Usage: node bench/run.mjs chromium http://127.0.0.1:4182/?mode=performance /tmp/result.json [--shot /tmp/scene.png] [--headed]
// --shot: after the measurements, a screenshot of the scene (a frame late in the run, fitted).
// --headed: a visible browser window, which uses the GPU on every platform.
// Headless Chromium on macOS is launched with ANGLE on Metal and the GPU enabled; headless Chromium elsewhere keeps
// its defaults, which may rasterize WebGL in software (SwiftShader). The result's `webglRenderer` says which one ran.
import { chromium, firefox, webkit } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
const args = process.argv.slice(2);
const headedAt = args.indexOf("--headed");
const headed = headedAt >= 0;
if (headed) args.splice(headedAt, 1);
const shotAt = args.indexOf("--shot");
const shot = shotAt < 0 ? null : args.splice(shotAt, 2)[1];
if (shotAt >= 0 && !shot) throw new Error("--shot needs a png path");
const [engine = "chromium", base, output = "/tmp/sp3-bench.json"] = args;
const url = shot ? `${base}${base.includes("?") ? "&" : "?"}shot=1` : base;
// Without a GPU, WebGL runs on SwiftShader and each frame is read back on the main thread at the next commit, which
// the next seek's prepare then pays (.superpowers/sdd/2026-11-25-studio-map/task-10b-seeks-report.md).
const gpu = engine === "chromium" && !headed && process.platform === "darwin" ? ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"] : [];
const browser = await { chromium, firefox, webkit }[engine].launch({
  headless: !headed,
  ...(engine === "chromium" ? { args: ["--enable-precise-memory-info", ...gpu] } : {}),
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
  if (shot) await page.locator("#host").screenshot({ path: shot });
  result.wallSeconds = (Date.now() - start) / 1000;
  result.headed = headed;
  result.launchArgs = gpu;
  result.errors = errors;
  result.rss = rss;
  await writeFile(output, JSON.stringify(result, null, 2) + "\n");
  console.log(result);
} finally {
  clearInterval(sampler);
  await browser.close();
}
