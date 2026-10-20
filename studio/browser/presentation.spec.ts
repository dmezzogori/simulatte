/** P4 presentations in Studio: recording provenance (original seed → verified replay; new seed → new recording),
 * saved framing/visibility/measurements/time/rate restored after reload, temporary changes not saved until Save,
 * evidence qualifiers, keyboard exit from presentation mode. Needs the P4 Python commands (R3/R4) in the wheel. */
import { test, expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { ProjectFixture } from "./project-fixture";

const evidence = resolve("../.superpowers/sdd/2026-10-19-studio-p4-presentation/screenshots");
const MODEL = `from dataclasses import dataclass
from typing import ClassVar
from simulatte import Environment, Layout, model
from simulatte.builders import build_immediate_release_system
from simulatte.collectors import ShopFloorKPIs
from simulatte.kpi import KPI, Collector
from simulatte.scenario import Scenario
from simulatte.shopfloor import JobFinished

class Pulse(Collector):
    """A recorded series: the running count of finished jobs, so presentations have a measurement to show."""
    kpis: ClassVar[tuple[KPI, ...]] = (KPI("finished", unit="jobs", kind=("series",)),)
    subscribes: ClassVar = (JobFinished,)
    def __init__(self, shopfloor):
        super().__init__(shopfloor)
        self.count = 0
    def on_event(self, event):
        self.count += 1
        self.sample("finished", float(self.count))

@dataclass(frozen=True)
class Params:
    servers: int = 3

@model(params=Params, horizon=40)
def build(env, layout, params):
    system = build_immediate_release_system(env=env, scenario=Scenario.pure_job_shop(n_servers=params.servers))
    ShopFloorKPIs(system.shop_floor).attach(env)
    Pulse(system.shop_floor).attach(env)
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
toml+='\\n[tool.simulatte.analyses.report]\\nid = "e64ca951-1b69-47a0-b1c5-7cfb91d90755"\\nname = "Report"\\nentrypoint = "report.py:analyze"\\n'
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
  const status = page.locator("[data-recording]");
  // A previous recording's status stays on screen until the new one starts: wait for the new attempt first.
  const previous = (await status.count()) ? await status.getAttribute("data-recording") : null;
  await page.getByRole("button", { name: "Record", exact: true }).click();
  if (previous) await expect(status).not.toHaveAttribute("data-recording", previous, { timeout: 60000 });
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
    try {
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
    await expect(page.locator(".ws-tag").filter({ hasText: /^Revision 1$/ })).toBeVisible();
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
    await expect(mode.getByRole("complementary", { name: "Supporting evidence" })).toBeVisible();
    await expect(mode.locator(".pv-measurements")).toBeVisible({ timeout: 30000 });
    await expect(mode).toContainText("Queue builds at the bottleneck");
    await expect(mode.locator('[data-evidence][data-kind="number"] .pv-value')).toHaveText(/\d/, { timeout: 30000 });
    await expect(mode.getByRole("status", { name: "Current time" })).toHaveText("t = 0", { timeout: 30000 });
    await expect(mode.getByRole("complementary", { name: "Supporting evidence" })).toContainText("1 of 1 replications");
    await expect(page.getByRole("navigation", { name: "Project navigation" })).toHaveCount(0);
    // The mode covers the whole viewport, also wider than the workspace content column (1350 px).
    await page.setViewportSize({ width: 1600, height: 900 });
    await expect.poll(() => mode.boundingBox()).toEqual({ x: 0, y: 0, width: 1600, height: 900 });
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
    } catch (error) {
      console.log("server diagnostic:", fixture.diagnostic.slice(-4000), "\nconsole:", errors.slice(-10).join("\n"));
      throw error;
    }
  } finally {
    await fixture.close();
  }
});

// ---------------------------------------------------------------------------------------------------------------
// Export (worker p3-artifacts): target preview, refusals, video/PNG/HTML parity and pinned export jobs. Appended
// only; the helpers above (seed, record, capture, hash) are shared. Needs the export entry on the presentation page.
/** Exports stage files in origin-private storage, which Playwright's ephemeral WebKit contexts reject
 * (UnknownError): like export.spec.ts, the export tests run in a persistent context on every engine. */
const persistent = test.extend({
  page: async ({ playwright, browserName }, use) => {
    const directory = await mkdtemp(join(tmpdir(), "simulatte-presentation-"));
    const context = await playwright[browserName].launchPersistentContext(directory, { headless: true, viewport: { width: 1440, height: 1000 } });
    try {
      await use(context.pages()[0] ?? (await context.newPage()));
    } finally {
      await context.close();
      await rm(directory, { recursive: true, force: true });
    }
  },
});
test.describe("export", () => {
  const samples = resolve("../.superpowers/sdd/2026-10-19-studio-p4-presentation/samples");
  const EVIL = `from pathlib import Path
import tempfile
from simulatte.analysis import analysis
from simulatte.analysis.outputs import Figure, Image
from simulatte.model import ParameterSchema
SVG = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="160" height="48"><script>window.__svgScriptRan=true;try{parent.__svgScriptRan=true}catch(e){}</script><script xlink:href="http://evil.invalid/s.js"></script><a href="http://evil.invalid/x"><text x="6" y="28">malicious svg</text></a><image href="http://evil.invalid/i.png" x="120" y="8" width="24" height="24"/></svg>'

@analysis(parameters=ParameterSchema(()))
def analyze(context, parameters):
    folder = Path(tempfile.mkdtemp())
    (folder / "plot.svg").write_text(SVG)
    context.emit("drawing", Image(folder / "plot.svg", "image/svg+xml"))
    good = {"data": [{"type": "bar", "y": [1, 2, 3], "marker": {"color": [1, 2, 3], "colorscale": "Viridis"}}], "layout": {"title": {"text": "Lateness"}}}
    context.emit("figure", Figure("plotly", good, renderer={"name": "plotly.js", "version": "4.1.1"}))
`;
  /** A second analysis (malicious SVG image and a Plotly figure) executed over the seeded experiment run. */
  const addEvilAnalysis = (fixture: ProjectFixture, run: string) =>
    execFileSync(
      fixture.python,
      [
        "-I",
        "-c",
        `
from pathlib import Path
import sys
from simulatte.projects import StudioTarget
from simulatte.projects.service import ProjectService
from simulatte.analysis.command import prepare_analysis
from simulatte.analysis.execution import AnalysisRunner
from simulatte.analysis.selection import Selection
from simulatte.analysis.store import AnalysisStore
root=Path(sys.argv[1]); run=sys.argv[2]
(root/'evil.py').write_text(sys.argv[3])
toml=(root/'pyproject.toml').read_text().replace('"report.py"]', '"report.py", "evil.py"]')
toml+='\\n[tool.simulatte.analyses.evil]\\nid = "9a1e2f0c-7c1d-4b8e-9a52-3c0c3f0e6d21"\\nname = "Evil evidence"\\nentrypoint = "evil.py:analyze"\\n'
(root/'pyproject.toml').write_text(toml)
service=ProjectService.open(StudioTarget(root,None))
try:
 store=service.store
 definition, generation = prepare_analysis(store, 'evil', environments=service.environments)
 analysis = AnalysisStore(store)
 item = analysis.admit(analysis.preview(Selection(experiment_run_ids=(run,))).id, definition_id=definition, parameters={}, generation_id=generation)
 analysis.rename(item.id, 'Evil evidence')
 with AnalysisRunner(store, environments=service.environments) as runner:
  runner.submit(item.id)
  print(runner.wait(item.id, timeout=120).state)
finally: service.close()
`,
        fixture.root,
        run,
        EVIL,
      ],
      { encoding: "utf8" },
    ).trim();
  /** Export jobs and presentation revisions as the store records them, read after Studio stopped. */
  const inspect = (fixture: ProjectFixture) =>
    JSON.parse(
      execFileSync(
        fixture.python,
        [
          "-I",
          "-c",
          `
from pathlib import Path
import sys, json
from simulatte.execution.store import RunStore
from simulatte.presentations.store import PresentationStore
with RunStore.open(Path(sys.argv[1])/'.simulatte') as store:
 p=PresentationStore(store)
 items=p.list(limit=50)['items']
 out={'presentations':[{'id':i['id'],'revision':i['revision']} for i in items],'exports':[]}
 for job in p.exports():
  d=job.to_dict(); out['exports'].append({k:d.get(k) for k in ('id','revision','target','snapshot_hash','state','details')})
 print(json.dumps(out))
`,
          fixture.root,
        ],
        { encoding: "utf8" },
      ).trim(),
    ) as { presentations: { id: string; revision: number }[]; exports: { id: string; revision: number; target: string; snapshot_hash: string; state: string; details: Record<string, unknown> }[] };
  const extractFrame = (fixture: ProjectFixture, zip: string, destination: string) =>
    execFileSync(fixture.python, ["-I", "-c", "import sys, zipfile\nwith zipfile.ZipFile(sys.argv[1]) as z:\n names=sorted(n for n in z.namelist() if n.endswith('.png'))\n open(sys.argv[2],'wb').write(z.read(names[0]))\n print(len(names))", zip, destination], { encoding: "utf8" }).trim();
  /** Save a presentation over the replay through the editor: measurements, range, rate, annotation and evidence. */
  async function buildPresentation(page: Page, origin: string, replay: string, evil: boolean) {
    await page.goto(origin + "/#/presentations/new/" + replay);
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
    // Analyses are listed newest first, so options are chosen by name; the output list waits for the chosen one.
    const choose = async (label: string, name: string) => {
      const select = page.getByLabel(label, { exact: true });
      const option = select.locator("option").filter({ hasText: new RegExp(`^${name} · `) });
      await expect(option).toHaveCount(1, { timeout: 30000 });
      await select.selectOption((await option.getAttribute("value"))!);
    };
    const addEvidence = async (analysis: string, output: string) => {
      if (!(await page.getByLabel("Saved analysis", { exact: true }).isVisible())) await page.getByRole("button", { name: "Add evidence", exact: true }).first().click();
      await choose("Saved analysis", analysis);
      await choose("Output", output);
      await page.getByRole("button", { name: "Add evidence", exact: true }).last().click();
    };
    await addEvidence("Weekly report", "mean_lateness");
    if (evil) {
      await addEvidence("Evil evidence", "drawing");
      await addEvidence("Evil evidence", "figure");
    }
    await page.getByRole("button", { name: "Use current view", exact: true }).click();
    await page.getByRole("button", { name: "Save presentation", exact: true }).click();
    await expect.poll(() => hash(page), { timeout: 30000 }).toMatch(/^#\/presentations\/[0-9a-f-]{36}$/);
    await expect(page.locator(".ws-tag").filter({ hasText: /^Revision 1$/ })).toBeVisible();
    return await hash(page);
  }
  const openExport = async (page: Page) => {
    await page.evaluate(() => {
      Object.defineProperty(window, "showSaveFilePicker", { value: undefined });
    });
    await page.locator("#pv-export-button").click();
    const dialog = page.getByRole("dialog", { name: /^Export presentation/ });
    await expect(dialog).toBeVisible({ timeout: 60000 });
    return dialog;
  };
  const prepared = async (dialog: ReturnType<Page["getByRole"]>) => {
    await dialog.getByRole("button", { name: /^Prepare preview/ }).click();
    await expect(dialog.locator("figure.export-preview")).toHaveAttribute("data-preview", "ready", { timeout: 60000 });
    const limits = await dialog.locator(".export-limits").innerText();
    return /snapshot ([0-9a-f]{12})/.exec(limits)![1]!;
  };
  const exported = async (dialog: ReturnType<Page["getByRole"]>, page: Page, timeout = 120000) => {
    await dialog.getByRole("button", { name: "Start export" }).click();
    const link = dialog.getByRole("link", { name: /^Download / });
    try {
      await expect(link).toBeVisible({ timeout });
    } catch (error) {
      // The dialog's progress, status and refusal say why the export did not finish.
      console.log("export dialog:", await dialog.locator('output, p[role="status"], .export-refusal').allInnerTexts().catch(() => []));
      throw error;
    }
    const download = page.waitForEvent("download");
    await link.click();
    return await download;
  };

  persistent("targetPreviewAndEvidenceLimits", async ({ page }, info) => {
    const context = page.context();
    test.setTimeout(600000);
    const fixture = new ProjectFixture();
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    try {
      await fixture.setup();
      await mkdir(evidence, { recursive: true });
      await mkdir(samples, { recursive: true });
      const seeded = seed(fixture);
      expect(seeded.analysis).toBe("succeeded");
      expect(addEvilAnalysis(fixture, seeded.run)).toBe("succeeded");
      await page.goto(await fixture.launch());
      await page.waitForURL((url) => url.pathname === "/");
      const origin = new URL(page.url()).origin;
      await page.goto(origin + "/#/presentations");
      const replay = await record(page, "17", "replay");
      const route = await buildPresentation(page, origin, replay, true);
      const dialog = await openExport(page);
      // A panel needs room beside the animation: the refusal names the item and the remedy.
      await dialog.getByLabel("Width", { exact: true }).fill("400");
      await dialog.getByLabel("Height", { exact: true }).fill("300");
      await dialog.getByRole("button", { name: /^Prepare preview/ }).click();
      const refusal = dialog.locator(".export-refusal");
      await expect(refusal).toBeVisible({ timeout: 60000 });
      await expect(refusal).toHaveAttribute("data-item", "video size");
      await expect(refusal).toContainText("turn the panel off");
      // Portrait without the panel: the preview is the actual target size and keeps the framing by containment.
      await dialog.getByLabel("Evidence panel").uncheck();
      await dialog.getByLabel("Width", { exact: true }).fill("360");
      await dialog.getByLabel("Height", { exact: true }).fill("640");
      await prepared(dialog);
      await expect(dialog.locator("figure.export-preview canvas")).toHaveAttribute("width", "360");
      await expect(dialog.locator("figure.export-preview canvas")).toHaveAttribute("height", "640");
      // Landscape with the panel, the same selection: every annotation and evidence item is laid out or refused.
      await dialog.getByLabel("Evidence panel").check();
      await dialog.getByLabel("Width", { exact: true }).fill("640");
      await dialog.getByLabel("Height", { exact: true }).fill("360");
      await dialog.getByLabel("End time", { exact: true }).fill("1");
      await dialog.getByLabel("Format", { exact: true }).selectOption("png");
      const pngHash = await prepared(dialog);
      await expect(dialog.locator(".export-limits")).toContainText("640 × 360");
      await expect(dialog.locator(".export-limits")).toContainText("2 static views · 3 evidence items");
      await expect(dialog.locator("figure.export-preview canvas")).toHaveAttribute("width", "640");
      await capture(page, info.project.name, "export");
      const png = await exported(dialog, page);
      const zip = join(info.outputDir, "frames.zip");
      await png.saveAs(zip);
      expect(Number(extractFrame(fixture, zip, join(samples, `${info.project.name}-frame-000000.png`)))).toBe(16);
      await dialog.getByRole("button", { name: "Remove temporary copy", exact: true }).click();
      // Video: the same snapshot renders through the same path.
      await dialog.getByLabel("Format", { exact: true }).selectOption("mp4");
      const mp4Hash = await prepared(dialog);
      expect(mp4Hash).not.toBe(pngHash);
      const mp4 = await exported(dialog, page);
      await mp4.saveAs(join(samples, `${info.project.name}-presentation.mp4`));
      await dialog.getByRole("button", { name: "Remove temporary copy", exact: true }).click();
      // Standalone HTML: preview through the same renderer, then the download.
      await dialog.getByLabel("Format", { exact: true }).selectOption("html");
      await prepared(dialog);
      await expect(dialog.locator(".export-limits")).toContainText("offline HTML about");
      await expect(dialog.locator("figure.export-preview canvas")).toHaveAttribute("width", "1280");
      const waiting = page.waitForEvent("download");
      await dialog.getByRole("button", { name: "Start export" }).click();
      const html = await waiting;
      const file = join(samples, `${info.project.name}-presentation.html`);
      await html.saveAs(file);
      await expect(dialog.locator('p[role="status"]')).toContainText("Offline HTML saved", { timeout: 60000 });
      await dialog.getByRole("button", { name: "Close", exact: true }).click();
      expect(await hash(page)).toBe(route);
      // The downloaded HTML offline: saved view, evidence, annotations, inert SVG, zero network.
      await context.setOffline(true);
      const requests: string[] = [];
      page.on("request", (r) => {
        if (!r.url().startsWith("file:")) requests.push(r.url());
      });
      await page.addInitScript(() => {
        (window as unknown as { __svgScriptRan: boolean }).__svgScriptRan = false;
      });
      await page.goto(pathToFileURL(file).href);
      await expect(page.locator("footer output")).toContainText("t = 0", { timeout: 30000 });
      const panel = page.locator("#evidence");
      await expect(panel).toBeVisible();
      await expect(panel).toContainText("Selected policy");
      await expect(panel).toContainText("Measurements");
      await expect(panel).toContainText("Queue builds at the bottleneck");
      await expect(panel).toContainText("1 of 1 replications");
      await expect(panel.locator('img[src^="data:image/svg+xml"]')).toHaveCount(1);
      await expect(panel.locator('img[src^="data:image/png"]')).toHaveCount(1);
      await expect(panel.locator("svg")).toHaveCount(0);
      await expect(panel.locator(".provenance")).toContainText("snapshot");
      expect(await page.evaluate(() => (window as unknown as { __svgScriptRan: boolean }).__svgScriptRan)).toBe(false);
      await expect(page.getByRole("button", { name: "Reset view", exact: true })).toBeVisible();
      await expect(page.getByLabel("Playback rate")).toHaveValue("2");
      await expect(page.getByLabel("Simulation time")).toHaveAttribute("max", "20");
      await page.getByRole("button", { name: "Play", exact: true }).click();
      await page.waitForTimeout(300);
      await page.getByRole("button", { name: "Pause", exact: true }).click({ delay: 100 });
      await page.locator("#evidence li button").first().click();
      await expect(page.locator("footer output")).toContainText("t = 5");
      await page.getByRole("button", { name: "Reset view", exact: true }).click();
      await expect(page.locator("footer output")).toContainText("t = 0");
      expect(requests).toEqual([]);
      expect(errors).toEqual([]);
      await context.setOffline(false);
    } finally {
      await fixture.close();
    }
  });

  persistent("exportPinsRevisionAndEvidence", async ({ page }) => {
    test.setTimeout(600000);
    const fixture = new ProjectFixture();
    try {
      await fixture.setup();
      const seeded = seed(fixture);
      expect(seeded.analysis).toBe("succeeded");
      await page.goto(await fixture.launch());
      await page.waitForURL((url) => url.pathname === "/");
      const origin = new URL(page.url()).origin;
      await page.goto(origin + "/#/presentations");
      const replay = await record(page, "17", "replay");
      const route = await buildPresentation(page, origin, replay, false);
      // Hold PNG conversion so the export is still running while the presentation is revised.
      await page.evaluate(() => {
        if (typeof OffscreenCanvas !== "undefined") {
          const convert = OffscreenCanvas.prototype.convertToBlob;
          OffscreenCanvas.prototype.convertToBlob = async function (options) {
            await new Promise((resolve) => setTimeout(resolve, 150));
            return convert.call(this, options);
          };
        }
      });
      const dialog = await openExport(page);
      await dialog.getByLabel("Format", { exact: true }).selectOption("png");
      await dialog.getByLabel("Width", { exact: true }).fill("640");
      await dialog.getByLabel("Height", { exact: true }).fill("360");
      await dialog.getByLabel("End time", { exact: true }).fill("6");
      const pinned = await prepared(dialog);
      await dialog.getByRole("button", { name: "Start export" }).click();
      await expect(dialog.locator("output")).toContainText("frames", { timeout: 60000 });
      // Meanwhile: a new revision of the presentation and another analysis execution in a second tab.
      // A second tab of the same browser session: the Studio mode and secret live in per-tab sessionStorage.
      const secret = await page.evaluate(() => sessionStorage.getItem("simulatte-session"));
      const tab = await page.context().newPage();
      await tab.addInitScript((value) => {
        sessionStorage.setItem("simulatte-mode", "studio");
        sessionStorage.setItem("simulatte-session", value ?? "");
      }, secret);
      await tab.goto(origin + "/" + route);
      await expect(tab.getByLabel("Notes", { exact: true })).toHaveValue("Replay of the policy study replication.", { timeout: 60000 });
      await tab.getByLabel("Notes", { exact: true }).fill("Revised while an export was running.");
      await tab.getByRole("button", { name: "Save new revision", exact: true }).click({ timeout: 30000 });
      await expect(tab.locator(".ws-tag").filter({ hasText: /^Revision 2$/ })).toBeVisible({ timeout: 30000 });
      await tab.goto(origin + "/#/analyses/new/report");
      await tab.getByLabel(/^Policy study/).check({ timeout: 60000 });
      await tab.getByRole("button", { name: "Preview inputs" }).click({ timeout: 30000 });
      await expect(tab.locator("[data-preview]")).toBeVisible({ timeout: 60000 });
      await tab.getByRole("button", { name: "Admit and run" }).click({ timeout: 30000 });
      await expect(tab.locator(".ws-page-heading .ws-badge").first()).toHaveText("succeeded", { timeout: 120000 });
      await tab.close();
      const link = dialog.getByRole("link", { name: /^Download / });
      await expect(link).toBeVisible({ timeout: 240000 });
      await dialog.getByRole("button", { name: "Remove temporary copy", exact: true }).click();
      await dialog.getByRole("button", { name: "Close", exact: true }).click();
      // The page followed the revision saved in the other tab without losing the running export.
      await expect(page.locator(".ws-tag").filter({ hasText: /^Revision 2$/ })).toBeVisible({ timeout: 30000 });
      // A second export (of revision 2) is interrupted by a disconnect: recovery marks it lost, never succeeded.
      const again = await openExport(page);
      await again.getByLabel("Format", { exact: true }).selectOption("png");
      await again.getByLabel("Width", { exact: true }).fill("640");
      await again.getByLabel("Height", { exact: true }).fill("360");
      await again.getByLabel("End time", { exact: true }).fill("20");
      await prepared(again);
      await again.getByRole("button", { name: "Start export" }).click();
      await expect(again.locator("output")).toContainText("frames", { timeout: 60000 });
      await fixture.stop();
      await expect(page.locator(".ws-notice")).toContainText("Reconnecting", { timeout: 60000 });
      const state = inspect(fixture);
      expect(state.presentations.map((p) => p.revision)).toEqual([2]);
      const jobs = state.exports.sort((a, b) => (a.state < b.state ? -1 : 1));
      expect(jobs.map((j) => [j.revision, j.state])).toEqual([
        [2, "lost"],
        [1, "succeeded"],
      ]);
      expect(jobs[1]!.snapshot_hash.slice(0, 12)).toBe(pinned);
      expect(jobs[1]!.details).toMatchObject({ output: { media_type: "application/zip" } });
    } finally {
      await fixture.close();
    }
  });
});
