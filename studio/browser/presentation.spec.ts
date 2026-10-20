/** P4 presentations in Studio: recording provenance (original seed → verified replay; new seed → new recording),
 * saved framing/visibility/measurements/time/rate restored after reload, temporary changes not saved until Save,
 * evidence qualifiers, keyboard exit from presentation mode. Needs the P4 Python commands (R3/R4) in the wheel. */
import { test, expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { join, resolve } from "node:path";
import { ProjectFixture } from "./project-fixture";

const evidence = resolve("../.superpowers/sdd/2026-10-19-studio-p4-presentation/screenshots");
const MODEL = `from dataclasses import dataclass
from simulatte import Environment, Layout, model
from simulatte.builders import build_immediate_release_system
from simulatte.collectors import ShopFloorKPIs
from simulatte.scenario import Scenario

@dataclass(frozen=True)
class Params:
    servers: int = 3

@model(params=Params, horizon=40)
def build(env, layout, params):
    system = build_immediate_release_system(env=env, scenario=Scenario.pure_job_shop(n_servers=params.servers))
    ShopFloorKPIs(system.shop_floor).attach(env)
`;
const REPORT = `from simulatte.analysis import analysis
from simulatte.analysis.outputs import Number
from simulatte.model import ParameterSchema

@analysis(parameters=ParameterSchema(()))
def analyze(context, parameters):
    rows = list(context.scalars())
    context.emit("mean_lateness", Number(sum(r["scalars"].get("shopfloor/lateness", 0.0) for r in rows) / max(len(rows), 1), unit="h"))
`;
/** A production project with one completed KPI-only experiment replication (the original), a registered
 * analysis executed over it, so the evidence picker has a saved output with real qualifiers. */
const seed = (fixture: ProjectFixture) =>
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
root=Path(sys.argv[1]); install_starter(root, starter_files('production'))
(root/'models/production.py').write_text(sys.argv[2])
(root/'report.py').write_text(sys.argv[3])
toml=(root/'pyproject.toml').read_text().replace('include = ["models/*.py"]', 'include = ["models/*.py", "report.py"]')
toml+='\n[tool.simulatte.analyses.report]\nid = "e64ca951-1b69-47a0-b1c5-7cfb91d90755"\nname = "Report"\nentrypoint = "report.py:analyze"\n'
(root/'pyproject.toml').write_text(toml)
service=ProjectService.open(StudioTarget(root,None))
try:
 pool=service.environments; pool.activate(pool.use_existing(Path(sys.executable)).id)
 co=service.ensure_coordinator(); model=service.workspace.models[0].id
 prepared=co.prepare_model(model,timeout=60)
 path=root/'policy.experiment.json'
 path.write_text(json.dumps({'schema':1,'name':'Policy study','model':'models/production.py:build','replications':{'count':1,'base_seed':17},'configurations':{'grid':{'servers':[3]}},'level':'kpi'}))
 run=co.contexts[model].start_experiment(load_experiment(path))
 outcome=co.wait_experiment(run,timeout=90)
 reps=co.store.replications(run)
 from simulatte.analysis.command import prepare_analysis
 from simulatte.analysis.execution import AnalysisRunner
 from simulatte.analysis.selection import Selection
 from simulatte.analysis.store import AnalysisStore
 definition, generation = prepare_analysis(co.store, 'report', environments=service.environments)
 analysis = AnalysisStore(co.store)
 item = analysis.admit(analysis.preview(Selection(experiment_run_ids=(run,))).id, definition_id=definition, parameters={}, generation_id=generation)
 analysis.rename(item.id, 'Weekly report')
 with AnalysisRunner(co.store, environments=service.environments) as runner:
  runner.submit(item.id)
  state = runner.wait(item.id, timeout=120).state
 print(json.dumps({'run':run,'status':outcome['status'],'attempt':reps[0]['attempt'],'model':model,'analysis':state}))
finally: service.close()
`,
        fixture.root,
        MODEL,
        REPORT,
      ],
      { encoding: "utf8" },
    ).trim(),
  ) as { run: string; status: string; attempt: string; model: string; analysis: string };
const hash = (page: Page) => page.evaluate(() => location.hash);
const capture = async (page: Page, engine: string, step: string) => {
  for (const [width, height] of [[1440, 900], [1280, 800]] as const) {
    await page.setViewportSize({ width, height });
    await page.screenshot({ path: join(evidence, `${engine}-${step}-${width}x${height}.png`) });
  }
};
/** Records the original replication with the given seed through the UI and returns the recording's attempt id. */
async function record(page: Page, seedValue: string, expectedMode: "replay" | "new_seed") {
  await page.getByLabel("Original run", { exact: true }).selectOption({ index: 0 });
  await page.getByLabel("Seed", { exact: true }).fill(seedValue);
  await page.getByRole("button", { name: "Preview differences", exact: true }).click();
  const diff = page.locator("[data-diff-mode]");
  await expect(diff).toHaveAttribute("data-diff-mode", expectedMode);
  await page.getByRole("button", { name: "Record", exact: true }).click();
  const status = page.locator("[data-recording]");
  await expect(status).toContainText("succeeded", { timeout: 120000 });
  return (await status.getAttribute("data-recording"))!;
}

test("studio presentation rows: provenance, saved view roundtrip, evidence qualifiers and keyboard exit", async ({ page, browserName }, info) => {
  test.setTimeout(420000);
  const fixture = new ProjectFixture();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  try {
    await fixture.setup();
    await mkdir(evidence, { recursive: true });
    const seeded = seed(fixture);
    expect(seeded.status).toBe("completed");
    await page.goto(await fixture.launch());
    await page.waitForURL((url) => url.pathname === "/");
    const origin = new URL(page.url()).origin;
    await page.goto(origin + "/#/presentations");
    await expect(page.getByRole("heading", { name: "Presentations", exact: true })).toBeVisible();
    await expect(page.getByText("No presentations yet")).toBeVisible();
    // Row 1: the original seed records a verified replay of the replication.
    expect(seeded.analysis).toBe("succeeded");
    const replay = await record(page, "17", "replay");
    await expect(page.locator("[data-recording]")).toContainText("verified replay", { timeout: 60000 });
    // Row 2: a new seed is a new recording, never a verified reproduction.
    const fresh = await record(page, "18", "new_seed");
    await expect(page.locator("[data-recording]")).toContainText("new recording, not a verified reproduction");
    await capture(page, info.project.name, "presentations");
    // Editor over the verified replay: framing, visibility, measurements, time, rate, annotation, evidence.
    await page.goto(origin + "/#/presentations/new/" + replay);
    await expect(page.getByText("verified replay")).toBeVisible();
    await expect(page.getByRole("button", { name: "Use current view", exact: true })).toBeEnabled({ timeout: 60000 });
    await page.getByLabel("Name", { exact: true }).fill("Selected policy");
    await page.getByLabel("Notes", { exact: true }).fill("Replay of the policy study replication.");
    const measurements = page.getByRole("group", { name: "Measurements" }).getByRole("checkbox");
    await expect.poll(() => measurements.count(), { timeout: 30000 }).toBeGreaterThan(0);
    await measurements.nth(0).check();
    await page.getByLabel("End", { exact: true }).fill("20");
    await page.getByLabel("Rate", { exact: true }).selectOption("2");
    await page.getByRole("button", { name: "Add annotation", exact: true }).click();
    await page.getByLabel("Annotation 1", { exact: true }).fill("Queue builds at the bottleneck");
    await page.getByLabel("Time of annotation 1", { exact: true }).fill("5");
    await page.getByLabel("Show every entity", { exact: true }).uncheck();
    const entityBoxes = page.getByRole("group", { name: "Entities" }).getByRole("checkbox", { name: /^Show / });
    await expect.poll(() => entityBoxes.count()).toBeGreaterThan(0);
    await entityBoxes.first().uncheck();
    // Evidence from the saved analysis: the labels come from P3 and are shown as qualifiers.
    await page.getByRole("button", { name: "Add evidence", exact: true }).click();
    await page.getByLabel("Saved analysis", { exact: true }).selectOption({ index: 1 });
    await page.getByLabel("Output", { exact: true }).selectOption({ index: 1 });
    await page.getByRole("button", { name: "Add evidence", exact: true }).last().click();
    const qualifiers = page.getByRole("list", { name: "Qualifiers of mean_lateness" }).first();
    await expect(qualifiers).toContainText("1 of 1 replications");
    await expect(qualifiers).toContainText("unit h");
    await expect(qualifiers).toContainText("Weekly report · snapshot");
    const stage = page.locator(".pv-stage canvas");
    await expect(stage).toBeVisible();
    await stage.focus();
    await page.keyboard.press("+");
    await page.keyboard.press("ArrowRight");
    await page.getByRole("button", { name: "Use current view", exact: true }).click();
    const framing = await page.locator("[data-camera]").getAttribute("data-camera");
    expect(framing).toMatch(/^-?\d/);
    await capture(page, info.project.name, "editor");
    await page.getByRole("button", { name: "Save presentation", exact: true }).click();
    await expect.poll(() => hash(page), { timeout: 30000 }).toMatch(/^#\/presentations\/[0-9a-f-]{36}$/);
    const editorRoute = await hash(page);
    await expect(page.getByText("Revision 1")).toBeVisible();
    // Reload: every saved setting comes back from the server.
    await page.goto(origin + "/" + editorRoute);
    await expect(page.getByLabel("Name", { exact: true })).toHaveValue("Selected policy", { timeout: 30000 });
    await expect(page.getByLabel("End", { exact: true })).toHaveValue("20");
    await expect(page.getByLabel("Rate", { exact: true })).toHaveValue("2");
    await expect(page.getByLabel("Annotation 1", { exact: true })).toHaveValue("Queue builds at the bottleneck");
    await expect(page.getByLabel("Show every entity", { exact: true })).not.toBeChecked();
    await expect(page.locator("[data-camera]")).toHaveAttribute("data-camera", framing!);
    await expect(page.getByRole("group", { name: "Measurements" }).getByRole("checkbox").nth(0)).toBeChecked({ timeout: 30000 });
    // Temporary viewing changes do not touch the saved framing until Save.
    await stage.focus();
    await page.keyboard.press("-");
    await page.keyboard.press("ArrowLeft");
    await page.goto(origin + "/" + editorRoute);
    await expect(page.locator("[data-camera]")).toHaveAttribute("data-camera", framing!, { timeout: 30000 });
    // Presentation mode: clean view, measurements apart from evidence, Escape returns focus to Present.
    await page.getByRole("link", { name: "Present →", exact: true }).click();
    const mode = page.getByRole("region", { name: "Presentation: Selected policy" });
    await expect(mode).toBeVisible();
    await expect(mode.getByRole("button", { name: "Exit presentation" })).toBeFocused();
    await expect(mode.getByRole("region", { name: "Supporting evidence" })).toBeVisible();
    await expect(mode.locator(".pv-measurements")).toBeVisible({ timeout: 30000 });
    await expect(mode).toContainText("Queue builds at the bottleneck");
    await expect(mode.locator('[data-evidence][data-kind="number"] .pv-value')).not.toHaveText("…", { timeout: 30000 });
    await expect(mode.getByRole("region", { name: "Supporting evidence" })).toContainText("1 of 1 replications");
    await expect(page.getByRole("navigation", { name: "Project navigation" })).toHaveCount(0);
    await capture(page, info.project.name, "present");
    await page.keyboard.press("Escape");
    await expect(mode).toHaveCount(0);
    expect(await hash(page)).toBe(editorRoute);
    await expect(page.locator("#pv-present-button")).toBeFocused();
    // The new-seed recording opens its own editor with the honest label.
    await page.goto(origin + "/#/presentations/new/" + fresh);
    await expect(page.locator("[data-recording-label]")).toHaveText("new recording, not a verified reproduction", { timeout: 30000 });
    await page.goto(origin + "/#/presentations");
    await expect(page.getByRole("table", { name: "Saved presentations" })).toContainText("Selected policy");
    await expect(page.getByRole("table", { name: "Saved presentations" })).toContainText("verified replay");
    if (browserName === "chromium") expect(errors).toEqual([]);
  } finally {
    await fixture.close();
  }
});
