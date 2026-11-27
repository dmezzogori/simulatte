// Usage: node harness/render.mjs <out-dir>
// Builds the harness, serves it, and writes one PNG per shot (device scale 2) plus mockup.png into <out-dir>.
// <out-dir> must be outside studio/: nothing untracked may remain there.
import { chromium } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { createReadStream } from "node:fs";
import { mkdir, readFile, stat } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = fileURLToPath(new URL(".", import.meta.url));
const studio = resolve(here, "..");
const outArg = process.argv[2];
if (!outArg) {
  console.error("usage: node harness/render.mjs <out-dir>");
  process.exit(2);
}
const out = resolve(outArg);
const inside = relative(studio, out);
if (inside === "" || (!inside.startsWith("..") && !inside.startsWith("/"))) {
  console.error(`refusing to write renders under studio/: ${out}`);
  process.exit(2);
}
await mkdir(out, { recursive: true });

/** Shots: name, fixture, scale in px per metre (absent: fit), centre in metres (absent: the fit's), selection. */
const SHOTS = [
  { name: "corridor-symbol", frame: "corridor-wait", scale: 8 },
  { name: "corridor-true", frame: "corridor-wait", scale: 30, x: 46, y: 2 },
  { name: "corridor-select-agv", frame: "corridor-wait", scale: 8, select: "AGV-1" },
  { name: "corridor-select-order", frame: "corridor-wait", scale: 8, select: "order-0" },
  { name: "corridor-select-facility", frame: "corridor-wait", scale: 8, select: "East" },
  { name: "idle-symbol", frame: "corridor-idle", scale: 8 },
  { name: "advanced-symbol", frame: "advanced-charging" },
  { name: "advanced-true", frame: "advanced-charging", scale: 30, x: 80, y: 15 },
  // the two other intralogistics examples, recorded with every vehicle moving (acceptance 1 of the spec)
  { name: "simple-fit", frame: "simple-moving" },
  { name: "intermediate-fit", frame: "intermediate-moving" },
  { name: "two-fleets", frame: "two-fleets", scale: 8 },
  { name: "bare", frame: "bare-layout" },
  { name: "sprites-symbol", frame: "sprites", scale: 8 },
  // the sprite row is about 80 m long, 2400 px at true size: two shots of 40 m each
  { name: "sprites-true-west", frame: "sprites", scale: 30, x: 18.5 },
  { name: "sprites-true-east", frame: "sprites", scale: 30, x: 60 },
  { name: "production", frame: "production" },
  { name: "no-layout", frame: "no-layout" },
];

// `vite` does not resolve from studio/: run the one of the scene package, with an absolute config path.
execFileSync(
  "pnpm",
  ["--filter", "@simulatte/scene", "exec", "vite", "build", "--config", join(here, "vite.config.ts")],
  { cwd: studio, stdio: ["ignore", "inherit", "inherit"] },
);
const dist = join(studio, ".build", "harness");

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json" };
const server = createServer(async (request, response) => {
  const path = join(dist, new URL(request.url ?? "/", "http://localhost").pathname.replace(/^\/$/, "/index.html"));
  try {
    if (relative(dist, path).startsWith("..") || !(await stat(path)).isFile()) throw new Error("not found");
    response.writeHead(200, { "content-type": TYPES[extname(path)] ?? "application/octet-stream" });
    createReadStream(path).pipe(response);
  } catch {
    response.writeHead(404).end();
  }
});
await new Promise((done) => server.listen(0, "127.0.0.1", done));
const base = `http://127.0.0.1:${server.address().port}/`;

const browser = await chromium.launch({ headless: true });
let failed = false;
try {
  const context = await browser.newContext({ viewport: { width: 1200, height: 500 }, deviceScaleFactor: 2 });
  for (const shot of SHOTS) {
    const query = new URLSearchParams({ frame: shot.frame, labels: "1" });
    for (const key of ["scale", "x", "y", "select"]) if (shot[key] !== undefined) query.set(key, String(shot[key]));
    const page = await context.newPage();
    const problems = [];
    page.on("pageerror", (error) => problems.push(String(error)));
    page.on("console", (message) => message.type() === "error" && problems.push(message.text()));
    await page.goto(`${base}?${query}`);
    try {
      await page.waitForFunction(() => document.title === "ready" || document.title === "error", null, {
        timeout: 30000,
      });
    } catch (error) {
      problems.push(String(error));
    }
    if ((await page.title()) !== "ready") problems.push(`the page did not become ready: ${await page.title()}`);
    if (problems.length) {
      failed = true;
      console.error(`${shot.name}: ${problems.join("; ")}`);
    } else {
      await page.screenshot({ path: join(out, `${shot.name}.png`) });
      console.log(join(out, `${shot.name}.png`));
    }
    await page.close();
  }
  // the agreed mockup (an HTML fragment with inline SVG) in a minimal page
  const mockup = await readFile(resolve(studio, "..", "specs", "research", "2026-11-25-studio-map-mockup.html"), "utf8");
  const page = await (await browser.newContext({ viewport: { width: 1200, height: 800 }, deviceScaleFactor: 2 })).newPage();
  await page.setContent(
    `<!doctype html><html lang="en"><meta charset="utf-8"><body style="margin:16px;font-family:sans-serif;background:#fff">${mockup}</body></html>`,
  );
  await page.screenshot({ path: join(out, "mockup.png"), fullPage: true });
  console.log(join(out, "mockup.png"));
} finally {
  await browser.close();
  server.close();
}
process.exit(failed ? 1 : 0);
