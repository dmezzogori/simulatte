/** P3 keyboardAndRecoveryJourney: every primary action by keyboard, visible focus, dialog focus,
 * accessible names, no hover-only or unexplained disabled controls, reduced motion, resizing,
 * disconnection, expired authentication, an empty project and a failed model. Assertions, not screenshots. */
import { test, expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { ProjectFixture } from "./project-fixture";

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
const BROKEN = `raise RuntimeError("broken on purpose: the model cannot be imported")\n`;
const FOCUS_OUTLINE = { width: "3px", color: "rgb(69, 169, 157)" };

/** One full interactive run and one completed single-configuration experiment. */
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
service=ProjectService.open(StudioTarget(root,None))
try:
 pool=service.environments; pool.activate(pool.use_existing(Path(sys.executable)).id)
 co=service.ensure_coordinator(); model=service.workspace.models[0].id
 prepared=co.prepare_model(model,timeout=60)
 request=co.make_request(prepared,level='full')
 admission=co.admit_run(model,request,prepared.revision); co.wait(admission.key,timeout=90)
 path=root/'quick.experiment.json'
 path.write_text(json.dumps({'schema':1,'name':'Quick study','model':'models/production.py:build','replications':{'count':1},'configurations':{'grid':{'servers':[2]}},'level':'kpi'}))
 run=co.contexts[model].start_experiment(load_experiment(path))
 outcome=co.wait_experiment(run,timeout=90)
 print(json.dumps({'run':run,'attempt':admission.key.attempt_id,'status':outcome['status'],'model':model}))
finally: service.close()
`,
        fixture.root,
        MODEL,
      ],
      { encoding: "utf8" },
    ).trim(),
  ) as { run: string; attempt: string; status: string; model: string };

const focused = (page: Page) =>
  page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el) return { name: "", tag: "", outline: { width: "", color: "", style: "" }, inDialog: false };
    const style = getComputedStyle(el);
    const name =
      el.getAttribute("aria-label") ??
      (el instanceof HTMLInputElement || el instanceof HTMLSelectElement || el instanceof HTMLTextAreaElement
        ? Array.from(el.labels ?? []).map((l) => l.textContent?.trim()).join(" ")
        : (el.textContent?.trim() ?? ""));
    return {
      name,
      tag: el.tagName.toLowerCase(),
      outline: { width: style.outlineWidth, color: style.outlineColor, style: style.outlineStyle },
      inDialog: el.closest("dialog[open]") !== null,
    };
  });
/** Press Tab (or Shift+Tab) until the focused control's accessible name matches; links need Alt+Tab in WebKit.
 * The sidebar precedes the content, so its links are reached backwards from wherever a page left the focus. */
async function tabTo(page: Page, browserName: string, name: RegExp, { backward = false, limit = backward ? 160 : 60 } = {}) {
  const key = (browserName === "webkit" ? "Alt+" : "") + (backward ? "Shift+Tab" : "Tab");
  for (let i = 0; i < limit; i++) {
    await page.keyboard.press(key);
    const current = await focused(page);
    if (name.test(current.name)) return current;
  }
  throw new Error(`No control named ${name} reachable by ${key}`);
}
/** Audit the visible workspace: accessible names, hover-independent visibility and explained disabled controls. */
const audit = (page: Page) =>
  page.evaluate(() => {
    const visible = (el: HTMLElement) =>
      el.closest("[hidden]") === null &&
      el.closest("details:not([open]) > :not(summary)") === null &&
      el.getClientRects().length > 0;
    const unnamed: string[] = [],
      hoverOnly: string[] = [],
      unexplained: string[] = [];
    const scope = document.querySelector(".ws-body") ?? document.body;
    for (const el of Array.from(scope.querySelectorAll<HTMLElement>("button, a[href], input, select, textarea, summary"))) {
      if (!visible(el)) continue;
      const labelled =
        el.getAttribute("aria-label") ||
        el.getAttribute("aria-labelledby") ||
        (el instanceof HTMLInputElement || el instanceof HTMLSelectElement || el instanceof HTMLTextAreaElement
          ? (el.labels?.length ?? 0) > 0 || el.type === "checkbox" || el.type === "radio"
          : (el.textContent?.trim() ?? "").length > 0);
      if (!labelled) unnamed.push(el.outerHTML.slice(0, 80));
      const style = getComputedStyle(el);
      if (style.opacity === "0" || style.visibility === "hidden") hoverOnly.push(el.outerHTML.slice(0, 80));
      if (el instanceof HTMLButtonElement && el.disabled && !(el.title || el.getAttribute("aria-describedby")))
        unexplained.push(el.outerHTML.slice(0, 80));
    }
    const blank = Array.from(document.querySelectorAll<HTMLElement>(".ws-content > *")).length === 0;
    return { unnamed, hoverOnly, unexplained, blank };
  });
const overflow = (page: Page) =>
  page.evaluate(() => {
    const content = document.querySelector<HTMLElement>(".ws-content")!;
    return {
      document: document.documentElement.scrollWidth - window.innerWidth,
      content: content.scrollWidth - content.clientWidth,
      sidebar: document.querySelector<HTMLElement>(".ws-sidebar")!.getBoundingClientRect().width,
    };
  });

test("keyboardAndRecoveryJourney", async ({ page, browserName, context }) => {
  test.setTimeout(300000);
  const fixture = new ProjectFixture();
  const errors: string[] = [], network: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (message) => {
    // The disconnection and expiry steps make the browser log refused connections and 403s on purpose.
    if (message.type() === "error") (/WebSocket|Failed to load resource|ERR_|Forbidden|403|Could not connect|establish a connection|network/i.test(message.text()) ? network : errors).push(message.text());
  });
  const clean = async (label: string) => {
    const report = await audit(page);
    expect(report, label).toEqual({ unnamed: [], hoverOnly: [], unexplained: [], blank: false });
  };
  try {
    await fixture.setup();
    // Empty project: a guided first action, never empty charts.
    const empty = join(fixture.directory, "empty");
    await mkdir(empty);
    await page.goto(await fixture.launch(empty));
    await page.waitForURL((url) => url.pathname === "/");
    await expect(page.getByRole("heading", { name: "Start with a model" })).toBeVisible();
    await expect(page.getByRole("button", { name: /^Production/ })).toBeEnabled();
    await expect(page.getByRole("link", { name: "Register an existing Python model →" })).toBeVisible();
    await clean("empty project");
    await fixture.stop();
    // Seeded project: keyboard-only journey through the primary actions.
    const seeded = seed(fixture);
    expect(seeded.status).toBe("completed");
    await page.addInitScript(() => {
      const Native = window.WebSocket;
      let socket: WebSocket | undefined;
      window.WebSocket = class extends Native {
        constructor(url: string | URL, protocols?: string | string[]) {
          super(url, protocols);
          socket = this;
        }
      };
      Object.assign(window, { journeyDisconnect: () => socket?.close() });
    });
    await page.goto(await fixture.launch());
    await page.waitForURL((url) => url.pathname === "/");
    const origin = new URL(page.url()).origin;
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(page.getByRole("heading", { name: "Recent runs" })).toBeVisible();
    await clean("overview");
    await page.keyboard.press(browserName === "webkit" ? "Alt+Tab" : "Tab");
    expect((await focused(page)).name).toBe("Skip to content");
    await page.keyboard.press("Enter");
    await expect(page.locator("#workspace-content")).toBeFocused();
    const card = await tabTo(page, browserName, /Configure and run/);
    expect(card.outline.style).toBe("solid");
    expect(card.outline.width).toBe(FOCUS_OUTLINE.width);
    expect(card.outline.color).toBe(FOCUS_OUTLINE.color);
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Production", exact: true })).toBeVisible();
    expect(await page.evaluate(() => location.hash)).toBe("#/model/" + seeded.model);
    // A reopened project inspects the model again before it can run; that is a keyboard action too.
    await expect(page.getByRole("button", { name: /^(Prepare model|Run)$/ }).first()).toBeVisible({ timeout: 30000 });
    if (await page.getByRole("button", { name: "Prepare model", exact: true }).isVisible()) {
      await clean("model page before preparation");
      await tabTo(page, browserName, /^Prepare model$/);
      await page.keyboard.press("Enter");
    }
    await expect(page.getByRole("button", { name: "Run", exact: true })).toBeVisible({ timeout: 60000 });
    await clean("model page");
    const seedField = await tabTo(page, browserName, /^Seed$/);
    expect(seedField.outline.width).toBe(FOCUS_OUTLINE.width);
    await page.keyboard.press("ControlOrMeta+a");
    await page.keyboard.type("5");
    await tabTo(page, browserName, /^Run$/);
    await page.keyboard.press("Enter");
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText("succeeded", { timeout: 60000 });
    await expect(page.locator(".ws-recording .status")).toHaveText("paused", { timeout: 30000 });
    const runRoute = await page.evaluate(() => location.hash);
    await clean("run page");
    await tabTo(page, browserName, /^Runs$/, { backward: true });
    await page.keyboard.press("Enter");
    await expect(page.getByRole("region", { name: "Interactive runs" })).toBeVisible();
    await expect(page.getByText("All 3 saved runs loaded.")).toBeVisible();
    await clean("runs page");
    await tabTo(page, browserName, /^Edit notes of/);
    await page.keyboard.press("Space");
    await expect(page.getByRole("form", { name: "Run notes" })).toBeVisible();
    await tabTo(page, browserName, /^Run name$/);
    await page.keyboard.type("Keyboard run");
    await tabTo(page, browserName, /^Save notes$/);
    await page.keyboard.press("Enter");
    await expect(page.getByRole("link", { name: "Keyboard run" })).toBeVisible();
    await tabTo(page, browserName, /^Experiments$/, { backward: true });
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Experiment runs" })).toBeVisible();
    await clean("experiments page");
    await tabTo(page, browserName, /^Quick study$/);
    await page.keyboard.press("Enter");
    await expect(page.locator(".run-details h4")).toContainText("Quick study · completed");
    await clean("experiment detail");
    // The delete confirmation is a modal dialog: focus stays inside, Escape cancels and focus returns.
    await tabTo(page, browserName, /^Delete$/);
    await page.keyboard.press("Enter");
    const dialog = page.locator("dialog[open]");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("cannot be undone");
    expect((await focused(page)).inDialog).toBe(true);
    for (let i = 0; i < 4; i++) {
      await page.keyboard.press(browserName === "webkit" ? "Alt+Tab" : "Tab");
      expect((await focused(page)).inDialog, "focus stays inside the dialog").toBe(true);
    }
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    expect((await focused(page)).name).toBe("Delete");
    await expect(page.locator(".run-details h4")).toContainText("Quick study · completed");
    // The comparison link precedes the action buttons in the heading, so it is two Shift+Tabs away.
    await tabTo(page, browserName, /^Compare numerically/, { backward: true });
    await page.keyboard.press("Enter");
    await expect(page.getByRole("region", { name: "Comparison of Quick study" })).toBeVisible();
    await clean("comparison page");
    // Reduced motion: no transitions or animations once the preference is set.
    await page.emulateMedia({ reducedMotion: "reduce" });
    expect(
      await page.evaluate(() => {
        const link = document.querySelector<HTMLElement>(".ws-sidebar nav a")!;
        const button = document.querySelector<HTMLElement>(".ws-content button, .ws-content a[href]")!;
        return [getComputedStyle(link).transitionDuration, getComputedStyle(button).transitionDuration, getComputedStyle(link).animationName];
      }),
    ).toEqual(["0s", "0s", "none"]);
    await page.emulateMedia({ reducedMotion: "no-preference" });
    expect(await page.evaluate(() => getComputedStyle(document.querySelector(".ws-sidebar nav a")!).transitionDuration)).not.toBe("0s");
    // Resizing: nothing overflows horizontally and the sidebar narrows at 1024 px.
    const widths: number[] = [];
    for (const [width, height] of [[1440, 900], [1280, 800], [1024, 768]] as const) {
      await page.setViewportSize({ width, height });
      const measure = await overflow(page);
      expect(measure.document, `${width}px document overflow`).toBeLessThanOrEqual(0);
      expect(measure.content, `${width}px content overflow`).toBeLessThanOrEqual(1);
      widths.push(measure.sidebar);
    }
    expect(widths[2]!).toBeLessThan(widths[0]!);
    await page.setViewportSize({ width: 1440, height: 900 });
    // Failed model: the readiness failure names the problem; saved results stay viewable.
    await writeFile(join(fixture.root, "models/production.py"), BROKEN);
    await page.goto(origin + "/#/model/" + seeded.model);
    await page.getByText("Automatic runs and model preparation", { exact: true }).click();
    await page.getByRole("button", { name: "Refresh model metadata", exact: true }).click();
    const failure = page.locator(".ws-failure");
    await expect(failure.first()).toContainText("broken on purpose", { timeout: 60000 });
    await expect(failure.first()).toHaveAttribute("data-kind", "model");
    await expect(page.getByRole("heading", { name: "Production", exact: true })).toBeVisible();
    await clean("failed model");
    await page.goto(origin + "/" + runRoute);
    await expect(page.locator(".ws-recording .status")).toHaveText("paused", { timeout: 30000 });
    // Transient disconnection: the designed notice, the page stays, the route survives the reconnect.
    await page.evaluate(() => (window as unknown as { journeyDisconnect(): void }).journeyDisconnect());
    await expect(page.locator(".ws-notice")).toContainText("Reconnecting. Drafts are retained");
    await expect(page.locator(".ws-connection")).toHaveText("connected", { timeout: 30000 });
    await expect(page.locator(".ws-notice")).toHaveCount(0);
    expect(await page.evaluate(() => location.hash)).toBe(runRoute);
    await expect(page.locator(".ws-page-heading h1")).toBeVisible();
    await clean("reconnected run page");
    // Server stopped: the page being read is preserved behind the reconnecting notice.
    await fixture.stop();
    await expect(page.locator(".ws-notice")).toContainText("Reconnecting", { timeout: 30000 });
    await expect(page.locator(".ws-connection")).toHaveText("reconnecting");
    await expect(page.locator(".ws-page-heading h1")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Keep context with this run" })).toBeVisible();
    await clean("disconnected run page");
    // Relaunch: a new serving link opens the workspace and the bookmarked route still resolves.
    const link = await fixture.launch();
    await page.goto(link);
    await page.waitForURL((url) => url.pathname === "/");
    await page.evaluate((hash) => { location.hash = hash; }, runRoute);
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText("succeeded", { timeout: 30000 });
    await expect(page.locator(".ws-connection")).toHaveText("connected");
    // Expired authentication: the session cookie is gone, the designed screen appears, nothing goes blank.
    await context.clearCookies();
    await page.evaluate(() => (window as unknown as { journeyDisconnect(): void }).journeyDisconnect());
    await expect(page.locator(".ws-connection")).toHaveText("authentication_required", { timeout: 30000 });
    await expect(page.locator(".ws-notice")).toContainText("Open a fresh link from the serving terminal");
    await expect(page.locator(".ws-page-heading h1")).toBeVisible();
    await clean("expired authentication");
    // Engines differ in whether refused reconnects reach the console; only page errors fail the journey.
    expect(errors).toEqual([]);
  } finally {
    await fixture.close();
  }
});

// The two cases deferred from 4A: an analysis refused for insufficient observations and a saved
// figure whose renderer does not match the packaged plotly.js. Fixtures follow analysis.spec.ts.
const PLOTLY = "4.1.1";
const ANALYSIS_PYPROJECT = `[project]
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
const ANALYSIS_REPORT = `from simulatte.analysis import analysis, DatasetRequirement
from simulatte.analysis.outputs import Figure, Number
from simulatte.model import ParameterSchema

@analysis(parameters=ParameterSchema(()))
def analyze(context, parameters):
    rows = list(context.scalars())
    context.emit("total", Number(sum(r["scalars"]["lateness"] for r in rows), unit="h"))
    good = {"data": [{"type": "bar", "y": [1, 2, 3]}], "layout": {"title": {"text": "Lateness"}}}
    context.emit("figure", Figure("plotly", good, renderer={"name": "plotly.js", "version": "${PLOTLY}"}))
    context.emit("old", Figure("plotly", good, renderer={"name": "plotly.js", "version": "3.0.0"}))

@analysis(parameters=ParameterSchema(()), requires=(DatasetRequirement("jobs", ("lateness",), require_complete=True),))
def needs(context, parameters):
    pass
`;
const ANALYSIS_SEED = `
from dataclasses import replace
from pathlib import Path
import sys
from simulatte.execution import schema
from simulatte.execution.store import RunStore, _blob
from simulatte.projects import StudioTarget
from simulatte.projects.service import ProjectService
root = Path(sys.argv[1])
def request(i):
    return schema.ExecutionRequest(model='plant.py:build', source_hash='a'*64, input_hash='b'*64, layout_hash='c'*64, layout_revision=1,
        parameters={'x': 1.0}, seed=i, stopping={'type': 'horizon', 'horizon': 10.0, 'warmup': 0.0}, runtime={'implementation': 'CPython', 'version': '3.14.1'})
with RunStore.open(root / '.simulatte') as store:
    store.create_revision({})
    definition = {'schema': 1, 'name': 'sweep', 'model': 'plant.py:build', 'replications': {'count': 24, 'base_seed': 0}}
    configurations = [{'label': 'x=1/base', 'parameters': {'x': 1.0}, 'layout_hash': 'c'*64, 'layout_ref': None}]
    replications = [{'configuration': 0, 'index': i, 'seed': i, 'content_ids': [schema.content_id(request(i))]} for i in range(24)]
    run = store.create_experiment_run(store.put_experiment(definition), {}, configurations, replications)
    for i in range(8):
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
    (root / 'run.txt').write_text(run)
service = ProjectService.open(StudioTarget(root, None))
try:
    pool = service.environments
    pool.activate(pool.use_existing(Path(sys.executable)).id)
finally:
    service.close()
print(run)
`;
const ANALYSIS_EXECUTE = `
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
const python = (fixture: ProjectFixture, script: string) =>
  execFileSync(fixture.python, ["-I", "-c", script, fixture.root], { encoding: "utf8", timeout: 180000 }).trim();

test("insufficientObservationsAndMissingRenderer", async ({ page }) => {
  test.setTimeout(300000);
  const fixture = new ProjectFixture();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  const clean = async (label: string, unexplained: string[] = []) => {
    const report = await audit(page);
    expect(report, label).toEqual({ unnamed: [], hoverOnly: [], unexplained, blank: false });
  };
  try {
    await fixture.setup();
    await writeFile(join(fixture.root, "pyproject.toml"), ANALYSIS_PYPROJECT);
    await writeFile(join(fixture.root, "report.py"), ANALYSIS_REPORT);
    python(fixture, ANALYSIS_SEED);
    const execution = python(fixture, ANALYSIS_EXECUTE);
    await page.goto(await fixture.launch());
    await page.waitForURL((url) => url.pathname === "/");
    const origin = new URL(page.url()).origin;
    // Insufficient observations: a designed refusal with the dataset, the fields and the revise action.
    await page.goto(origin + "/#/analyses/new/needs");
    await expect(page.getByRole("heading", { name: "New analysis: Needs jobs" })).toBeVisible();
    await page.getByLabel(/^sweep/).check();
    await page.getByRole("button", { name: "Preview inputs" }).click();
    const refusal = page.locator('[data-refusal="insufficient_input"]');
    await expect(refusal).toBeVisible({ timeout: 60000 });
    await expect(refusal).toContainText("insufficient input for dataset jobs");
    await expect(refusal).toContainText(/lateness/);
    await expect(refusal).toContainText("never substitutes synthetic values");
    await expect(refusal.getByRole("link", { name: "Define a revised experiment →" })).toHaveAttribute("href", "#/experiments");
    await expect(page.locator("[data-preview]")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Preview inputs" })).toBeEnabled();
    await clean("insufficient observations");
    // Missing optional renderer: the mismatched figure is labelled, the other outputs stay usable.
    await page.goto(origin + "/#/analyses/" + execution);
    await expect(page.getByRole("heading", { name: "Weekly report" })).toBeVisible({ timeout: 30000 });
    await expect(page.locator('[data-output="old"]')).toHaveAttribute("data-figure", "mismatch");
    await expect(page.locator('[data-output="old"]')).toContainText("Renderer mismatch");
    await expect(page.locator('[data-output="old"]')).toContainText("3.0.0");
    await expect(page.locator('[data-output="old"] iframe')).toHaveCount(0);
    await expect(page.locator('[data-output="total"] .af-value')).toContainText("32");
    await expect(page.locator('[data-output="figure"]')).toHaveAttribute("data-figure", "rendered", { timeout: 60000 });
    await clean("renderer mismatch");
    expect(errors).toEqual([]);
  } finally {
    await fixture.close();
  }
});
