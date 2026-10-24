/** P3 analyses: isolated artifact viewers under a network trap, bounded table pages, and the eight-of-24 journey. */
import { test, expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { ProjectFixture } from "./project-fixture";

const evidence = resolve("../.superpowers/sdd/2026-10-19-studio-p3-workspace/screenshots");
const PLOTLY = "4.1.1";
const PYPROJECT = `[project]
name = "study"
version = "0.1.0"
[tool.simulatte.project]
schema = 1
id = "c09b902b-ccad-4d13-887b-693356b32f23"
[tool.simulatte.analyses.report]
id = "e64ca951-1b69-47a0-b1c5-7cfb91d90755"
name = "Report"
entrypoint = "report.py:analyze"
[tool.simulatte.analyses.needs]
id = "9a1e2f0c-7c1d-4b8e-9a52-3c0c3f0e6d21"
name = "Needs jobs"
entrypoint = "report.py:needs"
[tool.simulatte.bundle]
include = ["report.py"]
`;
const SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="160" height="48">' +
  "<script>window.__svgScriptRan=true;try{parent.__svgScriptRan=true}catch(e){}</script>" +
  '<script xlink:href="http://evil.invalid/s.js"></script>' +
  '<a href="http://evil.invalid/x"><text x="6" y="28">malicious svg</text></a>' +
  '<image href="http://evil.invalid/i.png" x="120" y="8" width="24" height="24"/></svg>';
const REPORT = `import math
from pathlib import Path
import tempfile
from simulatte.analysis import analysis, DatasetRequirement
from simulatte.analysis.outputs import Distribution, Figure, Image, Number, Table
from simulatte.analysis.types import DatasetSchema, Field
from simulatte.model import ParameterSchema

SCHEMA = DatasetSchema("values", (Field("index", "int64"), Field("big", "int64"), Field("value", "float64", unit="h")))
SPECIAL = {1: math.nan, 2: math.inf, 3: -math.inf}
SVG = ${JSON.stringify(SVG)}


@analysis(parameters=ParameterSchema(()))
def analyze(context, parameters):
    rows = list(context.scalars())
    context.emit("total", Number(sum(r["scalars"]["lateness"] for r in rows), unit="h"))
    context.emit("values", Table(SCHEMA, ({"index": i, "big": 2**62 + i, "value": SPECIAL.get(i, i / 2)} for i in range(10000))))
    context.emit("spread", Distribution({"representation": "exact", "count": len(rows), "quantiles": {"0.5": 4.5, "0.9": 7.5}}))
    folder = Path(tempfile.mkdtemp())
    (folder / "plot.svg").write_text(SVG)
    context.emit("drawing", Image(folder / "plot.svg", "image/svg+xml"))
    good = {"data": [{"type": "bar", "y": [1, 2, 3], "marker": {"color": [1, 2, 3], "colorscale": "Viridis"}}], "layout": {"title": {"text": "Lateness"}}}
    bad = {"data": [{"type": "scatter", "y": [1]}], "layout": {"images": [{"source": "http://evil.invalid/bg.png", "x": 0, "y": 0}]}}
    context.emit("figure", Figure("plotly", good, renderer={"name": "plotly.js", "version": "${PLOTLY}"}))
    context.emit("leak", Figure("plotly", bad, renderer={"name": "plotly.js", "version": "${PLOTLY}"}))
    context.emit("old", Figure("plotly", good, renderer={"name": "plotly.js", "version": "3.0.0"}))


@analysis(parameters=ParameterSchema(()), requires=(DatasetRequirement("jobs", ("lateness",), require_complete=True),))
def needs(context, parameters):
    pass
`;
/** Seeds an experiment run of 24 planned replications; ``publish`` commits a range of them with a ``lateness`` KPI. */
const SEED = `
from dataclasses import replace
from pathlib import Path
import sys
from simulatte.execution import schema
from simulatte.execution.store import RunStore, _blob
from simulatte.projects import StudioTarget
from simulatte.projects.service import ProjectService
root = Path(sys.argv[1]); mode = sys.argv[2]; first, last = int(sys.argv[3]), int(sys.argv[4])
def request(i):
    return schema.ExecutionRequest(model='plant.py:build', source_hash='a'*64, input_hash='b'*64, layout_hash='c'*64, layout_revision=1,
        parameters={'x': 1.0}, seed=i, stopping={'type': 'horizon', 'horizon': 10.0, 'warmup': 0.0}, runtime={'implementation': 'CPython', 'version': '3.14.1'})
def publish(store, run, i):
    revision = store.current_revision()
    key = store.assign(replace(request(i), layout_revision=revision.number), revision, slot='local-1', pinned=True)
    store.bind_replication(run, 0, i, key)
    for status in ('preparing', 'ready', 'running', 'finalizing'):
        store.transition(key, status)
    path = store.root / 'results' / key.attempt_id
    path.mkdir()
    (path / 'run.simtrace').write_bytes(b'trace')
    with store._transaction() as db:
        db.execute('INSERT INTO results VALUES (?,?,?,?)', (key.attempt_id, _blob({}), _blob({}), 1.0))
        db.execute("UPDATE attempts SET status='succeeded' WHERE id=?", (key.attempt_id,))
        db.execute('UPDATE slots SET active=NULL WHERE active=?', (key.attempt_id,))
        db.execute('INSERT INTO kpis VALUES (?,?,?)', (key.attempt_id, 'lateness', _blob(i + 0.5)))
    store.set_replication_status(run, 0, i, 'succeeded', attempt=key.attempt_id)
with RunStore.open(root / '.simulatte') as store:
    if mode == 'seed':
        store.create_revision({})
        definition = {'schema': 1, 'name': 'sweep', 'model': 'plant.py:build', 'replications': {'count': 24, 'base_seed': 0}}
        configurations = [{'label': 'x=1/base', 'parameters': {'x': 1.0}, 'layout_hash': 'c'*64, 'layout_ref': None}]
        replications = [{'configuration': 0, 'index': i, 'seed': i, 'content_ids': [schema.content_id(request(i))]} for i in range(24)]
        run = store.create_experiment_run(store.put_experiment(definition), {}, configurations, replications)
        (root / 'run.txt').write_text(run)
    run = (root / 'run.txt').read_text()
    if mode == 'fillers':
        for n in range(first, last):
            definition = {'schema': 1, 'name': f'filler {n:02d}', 'model': 'plant.py:build', 'replications': {'count': 1, 'base_seed': 1000 + n}}
            store.create_experiment_run(store.put_experiment(definition), {}, [{'label': 'x=1/base', 'parameters': {'x': 1.0}, 'layout_hash': 'c'*64, 'layout_ref': None}],
                [{'configuration': 0, 'index': 0, 'seed': 1000 + n, 'content_ids': [schema.content_id(request(1000 + n))]}])
    if mode == 'publish':
        store.set_run_status(run, 'running')  # Studio paused the run at shutdown; the experiment resumes
    if mode in ('seed', 'publish'):
        for i in range(first, last):
            publish(store, run, i)
if mode == 'seed':
    service = ProjectService.open(StudioTarget(root, None))
    try:
        pool = service.environments
        pool.activate(pool.use_existing(Path(sys.executable)).id)
    finally:
        service.close()
print(run)
`;
/** Runs the ``report`` analysis headlessly over the seeded experiment and prints the execution id. */
const EXECUTE = `
from pathlib import Path
import sys
from simulatte.analysis.command import prepare_analysis
from simulatte.analysis.execution import AnalysisRunner
from simulatte.analysis.selection import Selection
from simulatte.analysis.store import AnalysisStore
from simulatte.execution.store import RunStore
from simulatte.projects.environments import EnvironmentPool
root = Path(sys.argv[1])
run = (root / 'run.txt').read_text()
with RunStore.open(root / '.simulatte') as store:
    environments = EnvironmentPool(root, store)
    definition, generation = prepare_analysis(store, 'report', environments=environments)
    analysis = AnalysisStore(store)
    item = analysis.admit(analysis.preview(Selection(experiment_run_ids=(run,))).id, definition_id=definition, parameters={}, generation_id=generation)
    analysis.rename(item.id, 'Weekly report')
    with AnalysisRunner(store, environments=environments) as runner:
        runner.submit(item.id)
        state = runner.wait(item.id, timeout=120).state
    assert state == 'succeeded', (state, analysis.get(item.id)['details'])
print(item.id)
`;
const python = (fixture: ProjectFixture, script: string, ...args: string[]) =>
  execFileSync(fixture.python, ["-I", "-c", script, fixture.root, ...args], { encoding: "utf8", timeout: 180000 }).trim();
async function seed(fixture: ProjectFixture) {
  await fixture.setup();
  await mkdir(evidence, { recursive: true });
  await writeFile(join(fixture.root, "pyproject.toml"), PYPROJECT);
  await writeFile(join(fixture.root, "report.py"), REPORT);
  return python(fixture, SEED, "seed", "0", "8");
}
const hash = (page: Page) => page.evaluate(() => location.hash);
const open = async (page: Page, fixture: ProjectFixture, fragment: string) => {
  await page.goto(await fixture.launch());
  await page.waitForURL((url) => url.pathname === "/");
  await page.evaluate((h) => {
    location.hash = h;
  }, fragment);
};
const capture = async (page: Page, engine: string, step: string) => {
  for (const [width, height] of [[1440, 900], [1280, 800]] as const) {
    await page.setViewportSize({ width, height });
    await expect(page.getByRole("navigation", { name: "Project navigation" })).toBeInViewport();
    await page.screenshot({ path: join(evidence, `${engine}-${step}-${width}x${height}.png`) });
  }
};
/** Page errors always count; console errors too, except the browser's own note about the websocket while Studio is deliberately stopped. */
const RECONNECT = /establish a connection to the server at ws:|WebSocket connection to 'ws:.*' failed|Could not connect to the server/;
const watch = (page: Page, errors: string[]) => {
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (message) => {
    if (message.type() === "error" && !RECONNECT.test(message.text())) errors.push(message.text());
  });
};

test("artifactIsolation", async ({ page }, info) => {
  test.setTimeout(300000);
  const fixture = new ProjectFixture();
  const errors: string[] = [];
  const external: string[] = [];
  const rowRequests: URL[] = [];
  watch(page, errors);
  try {
    await seed(fixture);
    const execution = python(fixture, EXECUTE);
    const link = await fixture.launch();
    const origin = new URL(link).origin;
    // Network trap: nothing may leave the Studio origin; blob: and data: URLs never reach the network.
    await page.route("**/*", (route) => {
      const url = new URL(route.request().url());
      if (url.origin !== origin) {
        external.push(url.href);
        return route.abort();
      }
      return route.continue();
    });
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.protocol === "blob:" || url.protocol === "data:") return;
      if (url.origin !== origin) external.push(url.href);
      else if (url.pathname.endsWith("/rows")) rowRequests.push(url);
    });
    await page.addInitScript(() => {
      (window as unknown as { __svgScriptRan: boolean }).__svgScriptRan = false;
    });
    await page.goto(link);
    await page.waitForURL((url) => url.pathname === "/");
    await page.evaluate((h) => {
      location.hash = h;
    }, "#/analyses/" + execution);
    await expect(page.getByRole("heading", { name: "Weekly report" })).toBeVisible({ timeout: 30000 });
    await expect(page.locator(".ws-page-heading .ws-badge").first()).toHaveText("succeeded");
    // Ordered by meaning: the number first, then the distribution, the image and the figures, the whole dataset last.
    const kinds = await page.locator("[data-kind]").evaluateAll((nodes) => nodes.map((n) => n.getAttribute("data-kind")));
    expect(kinds).toEqual(["number", "distribution", "image", "figure", "figure", "figure", "table"]);
    await expect(page.locator('[data-output="total"] .af-value')).toContainText("32");
    await expect(page.locator('[data-basis="shared"]')).toContainText("8 of 24");
    // Bounded, typed, server-paged table with exact int64 and explicit non-finite labels.
    const table = page.locator('[data-output="values"]');
    const rows = table.locator("tbody tr");
    await expect(rows).toHaveCount(100);
    await expect(table.locator(".af-table-summary")).toContainText("Rows 1–100 of 10,000");
    await expect(rows.nth(0).locator("td").nth(2)).toHaveText("4611686018427387904");
    await expect(rows.nth(1).locator("td").nth(3)).toHaveText("NaN");
    await expect(rows.nth(2).locator("td").nth(3)).toHaveText("+∞");
    await expect(rows.nth(3).locator("td").nth(3)).toHaveText("−∞");
    await expect(rows.nth(4).locator("td").nth(3)).toHaveText("2");
    await table.getByRole("button", { name: "Next page" }).click();
    await expect(table.locator(".af-table-summary")).toContainText("Rows 101–200 of 10,000");
    await expect(rows.nth(0).locator("td").nth(1)).toHaveText("100");
    await table.getByLabel("Rows per page", { exact: true }).selectOption("500");
    await expect(table.locator(".af-table-summary")).toContainText("Rows 1–500 of 10,000");
    await expect(rows).toHaveCount(500);
    await table.getByLabel("Sort by", { exact: true }).selectOption("value");
    await table.getByLabel("Direction", { exact: true }).selectOption("desc");
    await table.getByRole("button", { name: "Apply order" }).click();
    await expect(table.locator(".af-table-summary")).toContainText("derived index", { timeout: 60000 });
    await expect(rows.nth(0).locator("td").nth(3)).toHaveText("NaN");
    await expect(rows.nth(1).locator("td").nth(3)).toHaveText("+∞");
    await expect(rows.nth(2).locator("td").nth(3)).toHaveText("4999.5");
    await table.getByLabel("Filter field", { exact: true }).selectOption("big");
    await table.getByLabel("Operator", { exact: true }).selectOption("ge");
    await table.getByLabel("Filter value", { exact: true }).fill("4611686018427387911");
    await table.getByRole("button", { name: "Add filter" }).click();
    await expect(table.locator(".af-table-summary")).toContainText("of 9,993", { timeout: 60000 });
    await expect(table.getByRole("list", { name: "Active filters" })).toContainText('big ge {"$int64":"4611686018427387911"}');
    expect(rowRequests.length).toBeGreaterThan(3);
    for (const url of rowRequests) expect(Number(url.searchParams.get("limit"))).toBeLessThanOrEqual(500);
    expect(await rows.count()).toBeLessThanOrEqual(500);
    const csv = page.waitForEvent("download");
    await table.getByRole("button", { name: "Download CSV" }).click();
    expect((await csv).suggestedFilename()).toBe("values.csv");
    // Distribution summary names its representation.
    await expect(page.locator('[data-output="spread"]')).toContainText("exact");
    await expect(page.locator('[data-output="spread"]')).toContainText("0.9");
    // The malicious SVG is an <img> over a blob: URL: no inline SVG, no script, no outgoing link or image.
    const image = page.locator('[data-output="drawing"] img');
    await expect(image).toBeVisible();
    expect(await image.getAttribute("src")).toMatch(/^blob:/);
    await expect(page.locator('[data-output="drawing"] svg')).toHaveCount(0);
    expect(await image.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
    expect(await page.evaluate(() => (window as unknown as { __svgScriptRan: boolean }).__svgScriptRan)).toBe(false);
    // Figures: the valid one renders in the sandboxed frame, the URL-bearing one is refused, the old renderer is labelled.
    await expect(page.locator('[data-output="figure"]')).toHaveAttribute("data-figure", "rendered", { timeout: 60000 });
    await expect(page.locator('[data-output="leak"]')).toHaveAttribute("data-figure", "refused");
    await expect(page.locator('[data-output="leak"]')).toContainText("layout.images[0].source");
    await expect(page.locator('[data-output="leak"] iframe')).toHaveCount(0);
    await expect(page.locator('[data-output="old"]')).toHaveAttribute("data-figure", "mismatch");
    await expect(page.locator('[data-output="old"]')).toContainText("Renderer mismatch");
    await expect(page.locator('[data-output="old"] iframe')).toHaveCount(0);
    const frameElement = page.locator('[data-output="figure"] iframe');
    expect(await frameElement.getAttribute("sandbox")).toBe("allow-scripts");
    const frame = page.frames().find((f) => f.url().includes("plotly-frame.html"));
    expect(frame).toBeTruthy();
    expect(
      await frame!.evaluate(() => {
        try {
          return window.parent.document ? "readable" : "empty";
        } catch {
          return "blocked";
        }
      }),
    ).toBe("blocked");
    expect(
      await frame!.evaluate(() => {
        try {
          return window.sessionStorage ? "readable" : "empty";
        } catch {
          return "blocked";
        }
      }),
    ).toBe("blocked");
    expect(await frame!.evaluate(() => document.querySelectorAll(".js-plotly-plot .main-svg").length)).toBeGreaterThan(0);
    const png = page.waitForEvent("download");
    await page.locator('[data-output="figure"]').getByRole("button", { name: "Download PNG" }).click();
    expect((await png).suggestedFilename()).toBe("figure.png");
    await page.locator('[data-output="figure"]').scrollIntoViewIfNeeded();
    await capture(page, info.project.name, "figure");
    expect(external).toEqual([]);
    expect(errors).toEqual([]);
  } finally {
    await fixture.close();
  }
});

test("eightOfTwentyFourJourney", async ({ page }, info) => {
  test.setTimeout(300000);
  const fixture = new ProjectFixture();
  const errors: string[] = [];
  watch(page, errors);
  try {
    await seed(fixture);
    await open(page, fixture, "#/analyses");
    await expect(page.getByRole("heading", { name: "Analyses", exact: true })).toBeVisible();
    await expect(page.getByText("No saved analyses yet")).toBeVisible();
    const report = page
      .getByRole("list", { name: "Analysis definitions" })
      .getByRole("listitem")
      .filter({ has: page.getByText("Report", { exact: true }) });
    await report.getByRole("link", { name: "New analysis" }).click();
    await expect(page.getByRole("heading", { name: "New analysis: Report" })).toBeVisible();
    // Nothing is selected by default; previewing an empty selection is refused locally.
    await page.getByRole("button", { name: "Preview inputs" }).click();
    await expect(page.getByRole("alert")).toContainText("nothing is selected by default");
    await page.getByLabel(/^sweep/).check();
    await page.getByRole("button", { name: "Preview inputs" }).click();
    const preview = page.locator("[data-preview]");
    await expect(preview).toBeVisible({ timeout: 60000 });
    await expect(preview).toContainText("8 of 24 replications · partial");
    await expect(preview).toContainText("Partial snapshot");
    await page.getByLabel("Name", { exact: true }).fill("Eight of 24");
    await page.getByRole("button", { name: "Admit and run" }).click();
    await expect(page.getByRole("heading", { name: "Eight of 24" })).toBeVisible({ timeout: 30000 });
    const original = await hash(page);
    expect(original).toMatch(/^#\/analyses\/[0-9a-f-]{36}$/);
    await expect(page.locator(".ws-page-heading .ws-badge").first()).toHaveText("succeeded", { timeout: 120000 });
    await expect(page.locator(".ws-page-heading")).toContainText("8 of 24 replications · partial");
    await expect(page.locator('[data-output="total"] .af-value')).toContainText("32");
    const snapshot = await page.locator("[data-snapshot]").textContent();
    await page.getByLabel("New annotation").fill("first look");
    await page.getByRole("button", { name: "Add annotation" }).click();
    await expect(page.getByRole("list", { name: "Annotation revisions" })).toContainText("first look");
    await capture(page, info.project.name, "analysis");
    await page.evaluate(() => {
      location.hash = "#/analyses";
    });
    await expect(page.getByRole("table", { name: "Saved analyses" })).toContainText("Eight of 24");
    await capture(page, info.project.name, "analyses");
    // The experiment resumes to 24 while Studio is stopped; the saved snapshot stays eight.
    await fixture.stop();
    python(fixture, SEED, "publish", "8", "24");
    await open(page, fixture, original);
    await expect(page.locator(".ws-page-heading .ws-badge").first()).toHaveText("succeeded", { timeout: 30000 });
    await expect(page.locator(".ws-page-heading")).toContainText("8 of 24 replications · partial");
    await page.getByRole("button", { name: "Rerun on the same snapshot" }).click();
    await expect.poll(() => hash(page)).not.toBe(original);
    const rerun = await hash(page);
    await expect(page.locator(".ws-page-heading .ws-badge").first()).toHaveText("succeeded", { timeout: 120000 });
    await expect(page.locator(".ws-page-heading")).toContainText("8 of 24 replications · partial");
    expect(await page.locator("[data-snapshot]").textContent()).toBe(snapshot);
    await page.evaluate((h) => {
      location.hash = h;
    }, original);
    await expect(page.locator(".ws-page-heading")).toContainText("8 of 24 replications · partial");
    await page.getByRole("button", { name: "Refresh inputs" }).click();
    const refreshed = page.locator("#analysis-refresh").locator("..");
    await expect(refreshed).toBeVisible({ timeout: 60000 });
    await expect(refreshed).toContainText("24 of 24 replications · complete");
    await expect(refreshed).toContainText("(new inputs)");
    await page.getByLabel("Name of the new execution").fill("All 24");
    await page.getByRole("button", { name: "Admit refreshed analysis" }).click();
    await expect(page.getByRole("heading", { name: "All 24" })).toBeVisible({ timeout: 30000 });
    const fresh = await hash(page);
    expect(new Set([original, rerun, fresh]).size).toBe(3);
    await expect(page.locator(".ws-page-heading .ws-badge").first()).toHaveText("succeeded", { timeout: 120000 });
    await expect(page.locator(".ws-page-heading")).toContainText("24 of 24 replications · complete");
    await expect(page.locator('[data-output="total"] .af-value')).toContainText("288");
    await expect(page.locator('[data-basis="shared"]')).toContainText("Complete snapshot");
    // Both revisions reopen with their own frozen inputs.
    for (const [fragment, text] of [
      [original, "8 of 24 replications · partial"],
      [fresh, "24 of 24 replications · complete"],
    ] as const) {
      await page.evaluate((h) => {
        location.hash = h;
      }, fragment);
      await expect(page.locator(".ws-page-heading")).toContainText(text);
      await expect(page.locator('[data-output="total"] .af-value')).toBeVisible();
    }
    await page.evaluate(() => {
      location.hash = "#/analyses";
    });
    await expect(page.getByRole("table", { name: "Saved analyses" }).locator("tbody tr")).toHaveCount(3);
    // A definition that requires retained fields refuses the selection with an actionable missing-data screen.
    await page.evaluate(() => {
      location.hash = "#/analyses/new/needs";
    });
    await expect(page.getByRole("heading", { name: "New analysis: Needs jobs" })).toBeVisible();
    await page.getByLabel(/^sweep/).check();
    await page.getByRole("button", { name: "Preview inputs" }).click();
    const refusal = page.locator('[data-refusal="insufficient_input"]');
    await expect(refusal).toBeVisible({ timeout: 60000 });
    await expect(refusal).toContainText("insufficient input for dataset jobs");
    await expect(refusal).toContainText("never substitutes synthetic values");
    await expect(refusal.getByRole("link", { name: "Define a revised experiment →" })).toHaveAttribute("href", "#/experiments");
    await expect(page.locator("[data-preview]")).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally {
    await fixture.close();
  }
});

test("olderExperimentRunsSelectable", async ({ page }) => {
  test.setTimeout(240000);
  const fixture = new ProjectFixture();
  const errors: string[] = [];
  watch(page, errors);
  try {
    await seed(fixture);
    // 24 newer runs push the seeded "sweep" run beyond the newest 20 the state carries.
    python(fixture, SEED, "fillers", "0", "24");
    await open(page, fixture, "#/analyses/new/report");
    await expect(page.getByRole("heading", { name: "New analysis: Report" })).toBeVisible();
    const group = page.getByRole("group", { name: "Experiment runs" });
    await expect(group.getByRole("checkbox")).toHaveCount(20);
    await expect(group.getByLabel(/^sweep/)).toHaveCount(0);
    await group.getByRole("button", { name: "Load older experiment runs" }).click();
    await expect(group.getByRole("checkbox")).toHaveCount(25);
    await expect(group.getByRole("button", { name: "Load older experiment runs" })).toHaveCount(0);
    const names = await group
      .getByRole("checkbox")
      .evaluateAll((nodes) => nodes.map((n) => (n.closest("label") as HTMLElement).innerText.split("\n")[0]));
    expect(new Set(names).size).toBe(25);
    expect(names[names.length - 1]).toBe("sweep");
    await group.getByLabel(/^sweep/).check();
    await page.getByRole("button", { name: "Preview inputs" }).click();
    const preview = page.locator("[data-preview]");
    await expect(preview).toBeVisible({ timeout: 60000 });
    await expect(preview).toContainText("8 of 24 replications · partial");
    expect(errors).toEqual([]);
  } finally {
    await fixture.close();
  }
});
