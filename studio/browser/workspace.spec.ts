/** P3 shell: focused routes, completion notices and the overview → model → run flow at laptop and workstation sizes. */
import { test, expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { writeFile, mkdir } from "node:fs/promises";
import { join, resolve } from "node:path";
import { ProjectFixture } from "./project-fixture";

const evidence = resolve("../.superpowers/sdd/2026-10-19-studio-p3-workspace/screenshots");
const model = (delay: number) =>
  `import time\nfrom simulatte import model\nfrom dataclasses import dataclass\n@dataclass(frozen=True)\nclass Params:\n    value: int = 1\n@model(params=Params,horizon=3)\ndef build(env, layout, params):\n    def work():\n        time.sleep(${delay})\n        yield env.timeout(1)\n    env.process(work())\n`;
const hash = (page: Page) => page.evaluate(() => location.hash);
const capture = async (page: Page, engine: string, step: string) => {
  await page.evaluate(() => document.getElementById("workspace-content")?.scrollTo(0, 0));
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
    await expect(page.getByRole("alert")).toContainText("Ignored 2 selection entries");
    await expect(page.getByRole("heading", { name: "Nothing selected to compare" })).toBeVisible();
    await page.goto(origin + "/#/model/%ZZ");
    await expect(page.getByRole("heading", { name: "Recent runs" })).toBeVisible();
    expect(errors).toEqual([]);
  } finally {
    await fixture.close();
  }
});

const RESEARCH_MODEL = `from dataclasses import dataclass
from simulatte import Environment, Layout, model
from simulatte.builders import build_immediate_release_system
from simulatte.collectors import ShopFloorKPIs
from simulatte.scenario import Scenario

@dataclass(frozen=True)
class Params:
    servers: int = 3
    boom: int = 0

@model(params=Params, horizon=40)
def build(env, layout, params):
    if params.boom:
        raise RuntimeError("boom: this configuration fails on purpose")
    system = build_immediate_release_system(env=env, scenario=Scenario.pure_job_shop(n_servers=params.servers))
    ShopFloorKPIs(system.shop_floor).attach(env)
`;
/** Seeds one interactive run, a two-configuration experiment with one failed replication and 120 unassigned attempts. */
const seedResearch = (fixture: ProjectFixture) =>
  JSON.parse(
    execFileSync(
      fixture.python,
      [
        "-I",
        "-c",
        `
from pathlib import Path
import sys, json
from simulatte.projects import StudioTarget, install_starter, starter_files
from simulatte.projects.service import ProjectService
from simulatte.experiments import load_experiment
from simulatte.execution.schema import ExecutionRequest
from simulatte.execution.project_store import ProjectStore
root=Path(sys.argv[1]); install_starter(root, starter_files('production'))
(root/'models/production.py').write_text(sys.argv[2])
service=ProjectService.open(StudioTarget(root,None))
try:
 pool=service.environments; pool.activate(pool.use_existing(Path(sys.executable)).id)
 store=service.store
 legacy=ExecutionRequest(model='plant.py:build',source_hash='a'*64,input_hash='b'*64,layout_hash='c'*64,layout_revision=1,parameters={},seed=0,stopping={'type':'horizon','horizon':10,'warmup':0},runtime={})
 keys=[]
 for _ in range(120):
  key=store.assign(legacy,store.create_revision({})); store.revoke(key,'cancelled'); keys.append(key)
 ProjectStore(store).annotate(keys[0].attempt_id,name='Keep this study',text='Original note',important=True)
 co=service.ensure_coordinator(); model=service.workspace.models[0].id
 prepared=co.prepare_model(model,timeout=60)
 request=co.make_request(prepared,level='full')  # A full recording: the experiment cannot reuse it as a KPI replication.
 admission=co.admit_run(model,request,prepared.revision); co.wait(admission.key,timeout=90)
 path=root/'boom.experiment.json'
 path.write_text(json.dumps({'schema':1,'name':'Boom study','model':'models/production.py:build','replications':{'count':1},'configurations':{'grid':{'boom':[0,1]}},'level':'kpi'}))
 run=co.contexts[model].start_experiment(load_experiment(path))
 outcome=co.wait_experiment(run,timeout=90)
 print(json.dumps({'run':run,'attempt':admission.key.attempt_id,'status':outcome['status'],'model':model}))
finally: service.close()
`,
        fixture.root,
        RESEARCH_MODEL,
      ],
      { encoding: "utf8" },
    ).trim(),
  ) as { run: string; attempt: string; status: string; model: string };

test("runs, experiments and comparison research pages", async ({ page }, info) => {
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
    const seeded = seedResearch(fixture);
    expect(seeded.status).toBe("completed");
    await page.goto(await fixture.launch());
    await page.waitForURL((url) => url.pathname === "/");
    const origin = new URL(page.url()).origin;
    // Runs: grouped history, bounded pages, search over loaded rows, inline notes.
    await page.goto(origin + "/#/runs");
    const interactive = page.getByRole("region", { name: "Interactive runs" });
    const replications = page.getByRole("region", { name: "Experiment and replay replications" });
    const unassigned = page.getByRole("region", { name: "Unassigned history" });
    await expect(interactive).toBeVisible();
    await expect(replications).toBeVisible();
    await expect(unassigned).toBeVisible();
    await expect(interactive.locator("tbody tr")).toHaveCount(1);
    await expect(replications.locator("tbody tr")).toHaveCount(2);
    await expect(replications.getByRole("link", { name: "Boom study" }).first()).toHaveAttribute("href", "#/experiments/" + seeded.run);
    await expect(replications.getByText(/boom=1/)).toBeVisible();
    await expect(interactive.locator("tbody tr td").nth(3)).not.toHaveText("…", { timeout: 15000 });
    await expect(interactive.locator("tbody tr td").nth(4)).toHaveText("Full recording");
    const loadedBefore = await unassigned.locator("tbody tr").count();
    expect(loadedBefore).toBeLessThan(120);
    await page.getByRole("button", { name: "Load older runs", exact: true }).click();
    await expect(unassigned.locator("tbody tr")).toHaveCount(120);
    await expect(page.getByText("All 123 saved runs loaded.")).toBeVisible();
    await expect(page.getByRole("link", { name: "Inspect active run →" })).toHaveCount(0);
    await capture(page, info.project.name, "runs");
    await page.getByLabel("Search loaded runs", { exact: true }).fill("keep this");
    await expect(page.getByRole("link", { name: "★ Keep this study" })).toBeVisible();
    await expect(page.locator(".ws-runs tbody tr")).toHaveCount(1);
    await expect(page.getByText(/1 of 123 loaded runs shown/)).toBeVisible();
    await page.getByLabel("Search loaded runs", { exact: true }).fill("");
    await interactive.getByRole("button", { name: /^Edit notes of/ }).click();
    await page.getByLabel("Run name", { exact: true }).fill("Renamed run");
    await page.getByLabel("Notes", { exact: true }).fill("Seeded interactive run");
    await page.getByRole("button", { name: "Save notes", exact: true }).click();
    await expect(interactive.getByRole("link", { name: "Renamed run" })).toBeVisible();
    await expect(page.getByRole("form", { name: "Run notes" })).toHaveCount(0);
    await page.goto(origin + "/#/runs/" + seeded.model);
    await expect(page.getByRole("link", { name: "Renamed run" })).toBeVisible();
    await expect(page.getByText("Seeded interactive run")).toBeVisible();
    await expect(page.getByRole("region", { name: "Unassigned history" })).toHaveCount(0);
    // Experiments: the seeded run, its results with the failed pair, then a new definition started from the form.
    await page.goto(origin + "/#/experiments");
    await expect(page.getByRole("heading", { name: "Experiment runs" })).toBeVisible();
    await page.getByRole("button", { name: "Boom study", exact: true }).click();
    expect(await hash(page)).toBe("#/experiments/" + seeded.run);
    await expect(page.locator(".run-details h4")).toContainText("Boom study · completed");
    await expect(page.getByText("Missing pairs (1)")).toBeVisible();
    await expect(page.locator(".run-details")).toContainText("boom=1");
    await capture(page, info.project.name, "experiment");
    await page.goto(origin + "/#/model/" + seeded.model);
    const prepare = page.getByRole("button", { name: "Prepare model", exact: true });
    if (await prepare.isVisible()) await prepare.click();
    await expect(page.getByRole("button", { name: "Run", exact: true })).toBeVisible({ timeout: 60000 });
    await page.getByRole("link", { name: "Define experiment →" }).first().click();
    await page.getByRole("button", { name: "Define a new experiment", exact: true }).click();
    const form = page.getByRole("form", { name: "New experiment" });
    // The legacy form nests its issue text inside each label, so names are matched by prefix.
    await form.getByLabel(/^Name/).fill("quick");
    await form.getByLabel(/^Replications/).fill("1");
    await form.getByLabel(/^boom/).fill("0");
    await form.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("quick.experiment.json")).toBeVisible();
    await page.locator(".ws-list li").filter({ hasText: "quick.experiment.json" }).getByRole("button", { name: "Start", exact: true }).click();
    await expect(page.getByRole("heading", { name: "quick", exact: true })).toBeVisible({ timeout: 30000 });
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText("completed", { timeout: 90000 });
    await expect(page.locator(".run-details h4")).toContainText("quick · completed");
    await expect(page.getByText("Experiments (2) · what the controls send")).toHaveCount(0);
    await page.goto(origin + "/#/experiments");
    await expect(page.getByText("Experiments (2) · what the controls send")).toBeVisible();
    // Comparison: counts, the failed pair and the interval convention, all from comparison.get.
    await page.goto(origin + "/#/compare/" + seeded.run);
    await expect(page.getByRole("heading", { name: "Comparison", exact: true })).toBeVisible();
    const comparison = page.getByRole("region", { name: "Comparison of Boom study" });
    await expect(comparison).toBeVisible();
    await expect(comparison).toContainText("failed 1");
    await expect(comparison).toContainText("succeeded 1");
    await expect(comparison).toContainText("Missing pairs (1)");
    await expect(comparison).toContainText("pointwise 95 % t intervals");
    await capture(page, info.project.name, "comparison");
    await comparison.getByLabel("Bonferroni").check();
    await expect(comparison).toContainText("Bonferroni-adjusted", { timeout: 15000 });
    await page.goto(origin + "/#/compare/" + seeded.run + ":1");
    await expect(page.getByRole("region", { name: "Comparison of Boom study" }).locator(".ws-counts tbody tr")).toHaveCount(1);
    expect(errors).toEqual([]);
  } finally {
    await fixture.close();
  }
});
