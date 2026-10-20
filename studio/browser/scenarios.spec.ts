/** P4 Task 4: the twelve product acceptance scenarios through the installed wheel (UI-bound steps).
 * Headless steps of the same scenarios run in studio/scripts/scenarios.py; the evidence file
 * .superpowers/sdd/2026-10-19-studio-p4-presentation/scenarios.md maps every scenario to both.
 * Set SIMULATTE_PROJECT_EXECUTABLE to a wheel install (bare `[studio]`) and, for the figure steps,
 * SIMULATTE_PLOT_PYTHON to an interpreter of a wheel install with the plot extras. */
import { test, expect, type Page, type Locator } from "@playwright/test";
import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { cp, mkdir, readdir, rm, symlink, unlink, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";
import { ProjectFixture } from "./project-fixture";

const evidence = resolve("../.superpowers/sdd/2026-10-19-studio-p4-presentation/screenshots");
const PLOT_PYTHON = process.env.SIMULATTE_PLOT_PYTHON;
const hash = (page: Page) => page.evaluate(() => location.hash);
const capture = async (page: Page, engine: string, scenario: number, step: string) => {
  for (const [width, height] of [[1440, 900], [1280, 800]] as const) {
    await page.setViewportSize({ width, height });
    await page.screenshot({ path: join(evidence, `${engine}-scenario${scenario}-${step}-${width}x${height}.png`) });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
};
const python = (fixture: ProjectFixture, script: string, ...args: string[]) =>
  execFileSync(fixture.python, ["-I", "-c", script, fixture.root, ...args], { encoding: "utf8", timeout: 900000 }).trim();
/** Console noise that the deliberately stopped or killed Studio produces is not a product error. */
const RECONNECT = /WebSocket|Failed to load resource|ERR_|Could not connect|establish a connection|network|403|Forbidden/i;
const watch = (page: Page, errors: string[]) => {
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (message) => {
    if (message.type() === "error" && !RECONNECT.test(message.text())) errors.push(message.text());
  });
};

/** The fixture's launcher with explicit arguments and working directory (legacy references, flags, no argument). */
class Studio extends ProjectFixture {
  async launchArgs(args: string[], cwd = this.root, environment: NodeJS.ProcessEnv = {}) {
    this.links = [];
    this.child = spawn(this.executable, ["studio", ...args, "--port", "0", "--no-browser"], {
      cwd,
      env: { ...process.env, ...environment, PYTHONUNBUFFERED: "1" },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let buffer = "";
    this.child.stdout!.on("data", (data) => {
      buffer = (buffer + data.toString()).slice(-65536);
      let m: RegExpExecArray | null;
      const re = /http:\/\/127\.0\.0\.1:\d+\/bootstrap\?token=[\w-]+/g;
      while ((m = re.exec(buffer))) if (!this.links.includes(m[0])) this.links.push(m[0]);
    });
    this.child.stderr!.on("data", (data) => {
      this.diagnostic = (this.diagnostic + data.toString()).slice(-10000);
    });
    return this.link(0);
  }
  /** The launcher must refuse without creating anything. */
  refuse(args: string[], cwd = this.root) {
    try {
      execFileSync(this.executable, ["studio", ...args, "--port", "0", "--no-browser"], { cwd, encoding: "utf8", timeout: 60000, stdio: "pipe" });
      return { code: 0, stderr: "" };
    } catch (error) {
      const e = error as { status: number | null; stderr: string };
      return { code: e.status ?? -1, stderr: String(e.stderr) };
    }
  }
  kill() {
    const child = this.child as ChildProcess | null;
    if (child && child.exitCode === null) child.kill("SIGKILL");
    this.child = null;
  }
}

const SLOW = (delay: number) =>
  `import time\nfrom simulatte import model\nfrom dataclasses import dataclass\n@dataclass(frozen=True)\nclass Params:\n    value: int = 1\n@model(params=Params,horizon=3)\ndef build(env, layout, params):\n    def work():\n        time.sleep(${delay})\n        yield env.timeout(1)\n    env.process(work())\n`;

/** Job outcomes with declared populations (two families, an overlapping cohort, an empty one) and warm-up. */
const STUDY_MODEL = `import time
from dataclasses import dataclass
from simulatte import model
from simulatte.analysis import MeasurementPlan, Population
from simulatte.analysis.builtins import JobOutcomeCollector
from simulatte.builders import build_immediate_release_system
from simulatte.collectors import ShopFloorKPIs
from simulatte.scenario import Scenario

def family(job):
    return "A" if int(job.id.rsplit("-", 1)[1]) % 2 == 0 else "B"

POPULATIONS = (
    Population("family_A", lambda row: row["family"] == "A", "arrival", fields=("family",)),
    Population("family_B", lambda row: row["family"] == "B", "arrival", fields=("family",)),
    Population("any_family", lambda row: row["family"] in ("A", "B"), "arrival", fields=("family",)),
    Population("family_Z", lambda row: row["family"] == "Z", "arrival", fields=("family",)),
)

def measurements():
    return JobOutcomeCollector(fields={"family": ("string", family)}, populations=POPULATIONS, membership_at="arrival")

PLAN = MeasurementPlan("outcomes", (measurements().schema,), POPULATIONS)

@dataclass(frozen=True)
class Params:
    servers: int = 3
    delay: float = 0.0

@model(params=Params, horizon=40, warmup=5, measurement_plans=(PLAN,))
def build(env, layout, params):
    time.sleep(params.delay)
    system = build_immediate_release_system(env=env, scenario=Scenario.pure_job_shop(n_servers=params.servers))
    ShopFloorKPIs(system.shop_floor).attach(env)
    measurements().attach(env)
`;
/** AGV state intervals by vehicle family; the fleet model of the example project with a second family. */
const FLEET_MODEL = `from dataclasses import dataclass
from simulatte import model
from simulatte.analysis import MeasurementPlan, Population
from simulatte.analysis.builtins import AgvDurationCollector
from simulatte.intralogistics import build_simple_system

POPULATIONS = (Population("all_vehicles", lambda row: True, "interval_start"), Population("family_default", lambda row: row["family"] == "default", "interval_start", fields=("family",)))

def measurements():
    return AgvDurationCollector(fields={"family": ("string", lambda agv: agv.agv_type.name)}, populations=POPULATIONS)

PLAN = MeasurementPlan("durations", (measurements().schema,), POPULATIONS)

@dataclass(frozen=True)
class Params:
    vehicles: int = 2

@model(params=Params, horizon=100, warmup=10, measurement_plans=(PLAN,))
def build(env, layout, params):
    fleet, _, origin, destination, _ = build_simple_system(env, n_agvs=params.vehicles)
    measurements().attach(env)
    for sku in origin.inventory:
        fleet.submit(fleet.create_order(sku=sku, quantity=1, origin=origin, destination=destination))
`;
const REPORT = `import math
from simulatte.analysis import DatasetRequirement, Population, analysis
from simulatte.analysis.builtins import fleet_utilization, select_population
from simulatte.analysis.outputs import Distribution, Number, Table
from simulatte.analysis.reports import compare_replications, summarize_population
from simulatte.analysis.types import DatasetSchema, Field
from simulatte.model import ParameterField, ParameterSchema

FIELDS = ("family", "lateness")
POPULATIONS = ("family_A", "family_B", "any_family", "family_Z")

def included(context):
    return [row for row in context.rows("job_outcomes") if row["included"]]

def quantile(values, q):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)

@analysis(parameters=ParameterSchema(()), requires=(DatasetRequirement("job_outcomes", FIELDS),))
def lateness(context, parameters):
    rows = included(context)
    context.emit("lateness_all", summarize_population(rows, value="lateness", unit="h", population="all included jobs"))
    context.emit("lateness_by_family", summarize_population(rows, value="lateness", group_by=("family",), unit="h"))
    for name in POPULATIONS:
        members = [row for row in rows if name in row["populations"]]
        context.emit("lateness_" + name, summarize_population(members, value="lateness", unit="h", population=name))
    finite = [row["lateness"] for row in rows if row["lateness"] is not None and math.isfinite(row["lateness"])]
    context.emit("lateness_distribution", Distribution({"representation": "exact", "count": len(finite), "quantiles": {"0.1": quantile(finite, 0.1), "0.5": quantile(finite, 0.5), "0.9": quantile(finite, 0.9)}}, metadata={"unit": "h", "population": "all included jobs"}))
    context.emit("lateness_intervals", compare_replications(context.inputs, metric="shopfloor/lateness"))
    context.emit("included_jobs", Number(len(rows), unit="jobs"))

SUBSET = ParameterSchema((ParameterField("family", "str", False, "A"), ParameterField("late_after", "float", False, 0.0)))

@analysis(parameters=SUBSET, requires=(DatasetRequirement("job_outcomes", FIELDS),))
def subset(context, parameters):
    rows = included(context)
    retained = tuple(sorted({key for row in rows for key in row}))
    population = Population("late_in_family", lambda row: row["family"] == parameters["family"] and row["lateness"] > parameters["late_after"], "completion", fields=FIELDS)
    selected = select_population(rows, population, retained_fields=retained)
    context.emit("subset_status", Table(DatasetSchema("subset_status", (Field("status", "string"), Field("missing_fields", "string"))), [{"status": selected.status, "missing_fields": ",".join(selected.missing_fields)}]))
    context.emit("subset_summary", summarize_population(selected.rows, value="lateness", unit="h", population="late_in_family"))

@analysis(parameters=ParameterSchema(()), requires=(DatasetRequirement("job_outcomes", ("family", "priority")),))
def subset_priority(context, parameters):
    context.emit("included_jobs", Number(len(included(context)), unit="jobs"))

@analysis(parameters=ParameterSchema(()), requires=(DatasetRequirement("agv_durations", ("agv_id", "state", "duration", "family")),))
def utilization(context, parameters):
    rows = list(context.rows("agv_durations"))
    windows = sorted({(r["datasets"]["agv_durations"]["manifest"].get("window") or {}).get("start", 0) for r in context.inputs["replications"] if r["available"] and "agv_durations" in r.get("datasets", {})}, key=str)
    by_family = {}
    for row in rows:
        by_family.setdefault(row["family"], []).append(row)
    for family, members in sorted(by_family.items()):
        values = fleet_utilization(members)
        for key in ("mean_of_vehicle_fractions", "ratio_of_durations"):
            context.emit(f"{family}_{key}", Number(values[key], unit="fraction", metadata={"convention": "descriptive_within_run", "aggregation": key, "population": family, "window_start": str(windows)}))
    context.emit("intervals_by_family", summarize_population(rows, value="duration", group_by=("family", "state"), unit="time units"))
`;
const PLOTS = `import tempfile
from pathlib import Path
from simulatte.analysis import analysis
from simulatte.analysis.adapters import save_figure
from simulatte.analysis.outputs import Number
from simulatte.model import ParameterSchema

@analysis(parameters=ParameterSchema(()))
def plots(context, parameters):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn
    import plotly.graph_objects as go
    rows = list(context.scalars())
    values = [row["scalars"].get("shopfloor/lateness", 0.0) for row in rows]
    context.emit("runs", Number(len(rows), unit="runs"))
    folder = Path(tempfile.mkdtemp())
    figure, axis = plt.subplots()
    axis.bar(range(len(values)), values)
    context.emit("matplotlib_png", save_figure(figure, folder, name="lateness.png"))
    grid = seaborn.catplot(x=list(range(len(values))), y=values, kind="bar")
    context.emit("seaborn_svg", save_figure(grid.figure, folder, name="lateness.svg"))
    context.emit("plotly_figure", save_figure(go.Figure(data=[go.Bar(y=values)], layout={"title": {"text": "Lateness"}}), folder, name="lateness.json"))
`;
const ANALYSES: Record<string, [string, string, string]> = {
  lateness: ["e64ca951-1b69-47a0-b1c5-7cfb91d90755", "Lateness by population", "report.py:lateness"],
  subset: ["9a1e2f0c-7c1d-4b8e-9a52-3c0c3f0e6d21", "Post-run subset", "report.py:subset"],
  subset_priority: ["5c0d9e8f-1a2b-4c3d-8e9f-0a1b2c3d4e5f", "Subset by priority", "report.py:subset_priority"],
  utilization: ["093f63ba-8f3b-496c-a2a4-f8a6d6a39123", "Fleet utilization", "report.py:utilization"],
  plots: ["7e6d5c4b-3a29-4817-8f6e-5d4c3b2a1908", "Figures", "plots.py:plots"],
};
/** Starter project + the study model (+ optional fleet model), registered analyses, selected interpreter. */
const SEED = `
from pathlib import Path
import sys, json
from simulatte.projects import StudioTarget, install_starter, starter_files
from simulatte.projects.service import ProjectService
root = Path(sys.argv[1]); spec = json.loads(sys.argv[2])
install_starter(root, starter_files('production'))
(root / 'models/production.py').write_text(spec['model'])
for name, text in spec['files'].items():
    (root / name).write_text(text)
toml = (root / 'pyproject.toml').read_text()
toml = toml.replace('include = ["models/*.py"]', 'include = [' + ', '.join(['"models/*.py"', '"*.experiment.json"'] + ['"%s"' % n for n in spec['files']]) + ']')
if spec.get('fleet'):
    (root / 'models/logistics.py').write_text(spec['fleet'])
    toml += '\\n[tool.simulatte.models.logistics]\\nid = "839bfc13-0d9a-455f-9fa9-08615ad6fb8b"\\nname = "Fleet"\\nentrypoint = "models/logistics.py:build"\\n'
for key, (ident, label, entry) in spec['analyses'].items():
    toml += '\\n[tool.simulatte.analyses.%s]\\nid = "%s"\\nname = "%s"\\nentrypoint = "%s"\\n' % (key, ident, label, entry)
(root / 'pyproject.toml').write_text(toml)
service = ProjectService.open(StudioTarget(root, None))
try:
    pool = service.environments
    pool.activate(pool.use_existing(Path(spec.get('interpreter') or sys.executable)).id)
    print(json.dumps({m.key: m.id for m in service.workspace.models}))
finally:
    service.close()
`;
/** Experiments over the seeded models through the coordinator; prints run ids and replication attempts. */
const EXPERIMENTS = `
from pathlib import Path
import sys, json
from simulatte.analysis import codec
from simulatte.experiments import load_experiment
from simulatte.projects import StudioTarget
from simulatte.projects.service import ProjectService
root = Path(sys.argv[1]); spec = json.loads(sys.argv[2])
def selection(prepared, name):
    meta = next(m for m in prepared.metadata['models'] if m['name'] == prepared.metadata['selected'])
    plan = next(p for p in meta['measurement_plans'] if p['name'] == name)
    return {'schema': 1, 'plan': plan['name'], 'selection': codec.to_json({}), 'definition_hash': codec.identity(plan)}
service = ProjectService.open(StudioTarget(root, None))
out = {}
try:
    co = service.ensure_coordinator()
    for item in spec:
        model = next(m for m in service.workspace.models if m.key == item['model'])
        prepared = co.prepare_model(model.id, timeout=120)
        data = {'schema': 1, 'name': item['name'], 'model': model.entrypoint, 'replications': {'count': item['count'], 'base_seed': item.get('base_seed', 0)}, 'configurations': {'grid': item['grid']}, 'level': item.get('level', 'kpi'), 'measurements': selection(prepared, item['plan'])}
        if item.get('stopping'): data['stopping'] = item['stopping']
        path = root / (item['name'] + '.experiment.json'); path.write_text(json.dumps(data))
        run = co.start_experiment(load_experiment(path), model_id=model.id)
        outcome = co.wait_experiment(run, timeout=600)
        out[item['name']] = {'run': run, 'status': outcome['status'], 'model': model.id, 'attempts': [r['attempt'] for r in co.store.replications(run, status='succeeded')]}
finally:
    service.close()
print(json.dumps(out))
`;
/** Runs a registered analysis headlessly (the Python path) and prints its execution id and output hashes. */
const ANALYZE = `
from pathlib import Path
import sys, json
from simulatte.analysis import codec
from simulatte.analysis.command import prepare_analysis
from simulatte.analysis.execution import AnalysisRunner
from simulatte.analysis.selection import Selection
from simulatte.analysis.store import AnalysisStore
from simulatte.projects import StudioTarget
from simulatte.projects.service import ProjectService
root = Path(sys.argv[1]); key = sys.argv[2]; selection = json.loads(sys.argv[3]); name = sys.argv[4]
service = ProjectService.open(StudioTarget(root, None))
try:
    store, pool = service.store, service.environments
    definition, generation = prepare_analysis(store, key, environments=pool)
    analysis = AnalysisStore(store)
    item = analysis.admit(analysis.preview(Selection(**{k: tuple(v) for k, v in selection.items()})).id, definition_id=definition, parameters={}, generation_id=generation)
    analysis.rename(item.id, name)
    with AnalysisRunner(store, environments=pool) as runner:
        runner.submit(item.id)
        state = runner.wait(item.id, timeout=600).state
    manifest = json.loads(codec.dumps(analysis.output(item.id))) if state == 'succeeded' else {'outputs': []}
    print(json.dumps({'execution': item.id, 'state': state, 'snapshot': item.snapshot_hash, 'outputs': {o['name']: sorted(a['sha256'] for a in o['artifacts']) for o in manifest['outputs']}, 'details': str(analysis.get(item.id).get('details'))[:400]}))
finally:
    service.close()
`;
/** Output hashes of a saved execution, read after Studio stopped. */
const INSPECT = `
from pathlib import Path
import sys, json
from simulatte.analysis import codec
from simulatte.analysis.store import AnalysisStore
from simulatte.execution.store import RunStore
root = Path(sys.argv[1])
with RunStore.open(root / '.simulatte') as store:
    analysis = AnalysisStore(store)
    items = analysis.list(limit=50)['items']
    out = {}
    for item in items:
        got = analysis.get(item['id'])
        state = got['execution']['state']
        manifest = json.loads(codec.dumps(analysis.output(item['id']))) if state == 'succeeded' else {'outputs': []}
        out[item['id']] = {'name': got['execution']['name'], 'state': state, 'snapshot': got['execution']['snapshot_hash'], 'outputs': {o['name']: sorted(a['sha256'] for a in o['artifacts']) for o in manifest['outputs']}}
    print(json.dumps(out))
`;
const SELECT_INTERPRETER = `
from pathlib import Path
import sys
from simulatte.projects import StudioTarget
from simulatte.projects.service import ProjectService
root = Path(sys.argv[1])
service = ProjectService.open(StudioTarget(root, None))
try:
    pool = service.environments
    pool.activate(pool.use_existing(Path(sys.argv[2])).id)
finally:
    service.close()
`;
/** The documented reconstruction snippet (docs/studio/environments.md). */
const RECONSTRUCT = `
from pathlib import Path
from contextlib import closing
import sys
from simulatte.execution.store import RunStore
from simulatte.execution.store_reader import StoreReader
from simulatte.projects.environments import EnvironmentPool
root = Path(sys.argv[1]).resolve()
with closing(StoreReader.open(root / ".simulatte")) as reader:
    evidence = next(g["evidence"] for g in reader.generations() if g["state"] == "selected")
with RunStore.open(root / ".simulatte") as store:
    generation = EnvironmentPool(root, store).reconstruct(evidence)
    print(generation.id)
`;
const HISTORY = `
from pathlib import Path
import sys, json
from simulatte.execution.store_reader import StoreReader
reader = StoreReader.open(Path(sys.argv[1]) / '.simulatte')
try:
    print(json.dumps([(h['id'], h['status']) for h in reader.history(limit=50).items]))
finally:
    reader.close()
`;
const seed = (fixture: ProjectFixture, spec: Record<string, unknown>) => JSON.parse(python(fixture, SEED, JSON.stringify(spec))) as Record<string, string>;
const experiments = (fixture: ProjectFixture, spec: Record<string, unknown>[]) =>
  JSON.parse(python(fixture, EXPERIMENTS, JSON.stringify(spec))) as Record<string, { run: string; status: string; model: string; attempts: string[] }>;
const analyze = (fixture: ProjectFixture, key: string, selection: Record<string, string[]>, name: string) =>
  JSON.parse(python(fixture, ANALYZE, key, JSON.stringify(selection), name)) as { execution: string; state: string; snapshot: string; outputs: Record<string, string[]>; details: string };
const inspect = (fixture: ProjectFixture) =>
  JSON.parse(python(fixture, INSPECT)) as Record<string, { name: string; state: string; snapshot: string; outputs: Record<string, string[]> }>;
const origin = async (page: Page, link: string) => {
  await page.goto(link);
  await page.waitForURL((url) => url.pathname === "/");
  return new URL(page.url()).origin;
};
const prepared = async (page: Page) => {
  await expect(page.getByRole("button", { name: /^(Prepare model|Run)$/ }).first()).toBeVisible({ timeout: 60000 });
  if (await page.getByRole("button", { name: "Prepare model", exact: true }).isVisible()) await page.getByRole("button", { name: "Prepare model", exact: true }).click();
  await expect(page.getByRole("button", { name: "Run", exact: true })).toBeVisible({ timeout: 120000 });
};
const registerExisting = async (page: Page, interpreter: string) => {
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByText("Use an existing interpreter (advanced)", { exact: true }).click();
  await page.getByLabel("Python interpreter path").fill(interpreter);
  await page.getByRole("button", { name: "Check interpreter" }).click();
  await page.getByRole("button", { name: "Use environment and restart" }).click();
  await expect(page.getByRole("heading", { name: "Selected environment" })).toBeVisible({ timeout: 60000 });
};
const admitAnalysis = async (page: Page, site: string, key: string, pick: (page: Page) => Promise<void>, name: string, parameters: Record<string, string> = {}) => {
  await page.goto(site + "/#/analyses/new/" + key);
  await expect(page.getByRole("heading", { name: /^New analysis: / })).toBeVisible();
  await pick(page);
  await page.getByRole("button", { name: "Preview inputs" }).click();
  const preview = page.locator("[data-preview]");
  await expect(preview).toBeVisible({ timeout: 120000 });
  for (const [label, value] of Object.entries(parameters)) await page.getByLabel(label, { exact: true }).fill(value);
  await page.getByLabel("Name", { exact: true }).fill(name);
  await page.getByRole("button", { name: "Admit and run" }).click();
  await expect(page.getByRole("heading", { name })).toBeVisible({ timeout: 30000 });
  await expect(page.locator(".ws-page-heading .ws-badge").first()).toHaveText("succeeded", { timeout: 300000 });
  return (await hash(page)).replace("#/analyses/", "");
};
const tableText = async (card: Locator) => (await card.locator("table").innerText()).replace(/\s+/g, " ");

test("scenario 1 and 10: fresh install, missing folder, starter, environment, run, reopen, legacy references, quiescent copy", async ({ page, browserName }, info) => {
  test.setTimeout(900000);
  const fixture = new Studio();
  const errors: string[] = [];
  watch(page, errors);
  try {
    await fixture.setup();
    await mkdir(evidence, { recursive: true });
    // A missing folder argument is created; the first-run card guides the setup.
    const missing = join(fixture.directory, "studies", "plant");
    await page.goto(await fixture.launch(missing));
    await expect(page.getByRole("navigation", { name: "Project navigation" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Start with a model" })).toBeVisible();
    expect(existsSync(missing)).toBe(true);
    await capture(page, info.project.name, 1, "setup");
    await page.getByRole("button", { name: "Production", exact: false }).click();
    await expect(page.getByRole("heading", { name: "Review proposed files" })).toBeVisible();
    await page.getByRole("button", { name: "Apply changes", exact: true }).click();
    await expect(page.getByRole("button", { name: "Prepare environment", exact: true })).toBeVisible();
    fixture.root = missing; // the development wheel is vendored into the project that was just created
    await fixture.useDevelopmentWheel();
    await page.getByRole("button", { name: "Prepare environment", exact: true }).click();
    await expect(page.getByRole("button", { name: "Apply changes", exact: true })).toBeVisible({ timeout: 120000 });
    await page.getByRole("button", { name: "Apply changes", exact: true }).click();
    await page.getByRole("button", { name: "Prepare environment", exact: true }).click();
    await expect(page.getByRole("button", { name: "Use environment and restart" })).toBeVisible({ timeout: 300000 });
    await capture(page, info.project.name, 1, "environment");
    await page.getByRole("button", { name: "Use environment and restart" }).click();
    const site = await origin(page, await fixture.link(1));
    await expect(page.getByText("No runs yet", { exact: true })).toBeVisible();
    await page.getByRole("link", { name: "Production", exact: false }).first().click();
    await prepared(page);
    await page.getByLabel("Seed", { exact: true }).fill("29");
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText("succeeded", { timeout: 120000 });
    await expect(page.locator(".ws-recording .status")).toHaveText("paused", { timeout: 60000 });
    const runRoute = await hash(page);
    await page.getByLabel("Run name", { exact: true }).fill("Starter run");
    await page.getByRole("button", { name: "Save notes", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Starter run" })).toBeVisible();
    await capture(page, info.project.name, 1, "run");
    // Reopen the existing project with no folder argument from its own directory: the saved result is there, nothing runs.
    await fixture.stop();
    await page.goto(await fixture.launchArgs([], missing));
    await expect(page.getByRole("link", { name: "Starter run" })).toBeVisible({ timeout: 60000 });
    await expect(page.getByRole("link", { name: "Inspect active run →" })).toHaveCount(0);
    await page.goto((await origin(page, await fixture.link(0))) + "/" + runRoute);
    await expect(page.locator(".ws-recording .status")).toHaveText("paused", { timeout: 60000 });
    await capture(page, info.project.name, 1, "reopen");
    await fixture.stop();
    // Legacy MODEL.py and MODEL.py:name references with flags: served, registered, never executed on open.
    const legacy = join(fixture.directory, "legacy");
    await mkdir(legacy);
    await writeFile(join(legacy, "plant.py"), 'raise RuntimeError("legacy model must not be imported on open")\n');
    await page.goto(await fixture.launchArgs(["plant.py:alternate", "--seed", "5", "--horizon", "3", "--level", "kpi"], legacy));
    await page.getByRole("link", { name: "Models", exact: true }).click();
    await expect(page.getByLabel("Entrypoint", { exact: true })).toHaveValue("plant.py:alternate");
    await page.getByRole("button", { name: "Preview registration" }).click();
    await page.getByRole("button", { name: "Apply registration" }).click();
    await page.getByRole("link", { name: "Runs", exact: true }).click();
    await expect(page.getByRole("heading", { name: "No runs yet" })).toBeVisible();
    await capture(page, info.project.name, 1, "legacy");
    await fixture.stop();
    await page.goto(await fixture.launchArgs(["plant.py"], legacy));
    await expect(page.getByRole("navigation", { name: "Project navigation" })).toBeVisible();
    await page.getByRole("link", { name: "Runs", exact: true }).click();
    await expect(page.getByRole("heading", { name: "No runs yet" })).toBeVisible();
    await fixture.stop();
    // Invalid explicit references are refused without creating directories or files.
    const before = (await readdir(legacy)).sort();
    for (const [reference, message] of [["missing.py:build", "does not exist"], ["plant.py:1bad", "Invalid Python entrypoint"], ["nosuch/folder/model.py", ""], ["plant.txt", "Expected a directory or Python model file"]] as const) {
      const refusal = fixture.refuse([reference], legacy);
      expect(refusal.code, reference).not.toBe(0);
      expect(refusal.stderr, reference).toContain(message);
      expect((await readdir(legacy)).sort(), reference).toEqual(before);
    }
    // Scenario 10: a quiescent copy without the environment cache opens read-only, is rebuilt from
    // evidence with the documented snippet, and reopens with the same history.
    const copy = join(fixture.directory, "copied-plant");
    await cp(missing, copy, { recursive: true, filter: (source) => !source.includes("/.simulatte/cache") });
    const historyBefore = python(fixture, HISTORY.replace("sys.argv[1]", JSON.stringify(missing)));
    await page.goto(await fixture.launch(copy));
    await expect(page.getByRole("heading", { name: "Saved work is available" })).toBeVisible({ timeout: 60000 });
    await expect(page.getByText(/Reconstruct its environment/)).toBeVisible();
    await page.getByRole("link", { name: "Starter run", exact: true }).click();
    await expect(page.locator(".ws-recording .status")).toHaveText("paused", { timeout: 60000 });
    await expect(page.getByRole("button", { name: "Save notes", exact: true })).toHaveCount(0);
    await capture(page, info.project.name, 10, "recovery");
    await fixture.stop();
    const generation = execFileSync(fixture.python, ["-I", "-c", RECONSTRUCT, copy], { encoding: "utf8", timeout: 600000 }).trim();
    expect(generation).toMatch(/^[0-9a-f]{64}$/);
    const historyAfter = python(fixture, HISTORY.replace("sys.argv[1]", JSON.stringify(copy)));
    expect(historyAfter).toBe(historyBefore);
    await page.goto(await fixture.launch(copy));
    await expect(page.getByRole("link", { name: "Starter run" })).toBeVisible({ timeout: 90000 });
    await expect(page.getByRole("heading", { name: "Saved work is available" })).toHaveCount(0);
    await page.goto((await origin(page, await fixture.link(0))) + "/" + runRoute);
    await expect(page.locator(".ws-recording .status")).toHaveText("paused", { timeout: 60000 });
    await expect(page.getByRole("button", { name: "Save notes", exact: true })).toBeVisible();
    await capture(page, info.project.name, 10, "rebuilt");
    expect(errors).toEqual([]);
  } finally {
    await fixture.close();
  }
});

test("scenario 2: two models, blocked Run, source edits during a run, auto-run ownership, stop and run, history", async ({ page }, info) => {
  test.setTimeout(600000);
  const fixture = new Studio();
  const errors: string[] = [];
  watch(page, errors);
  try {
    await fixture.setup();
    await writeFile(join(fixture.root, "a.py"), SLOW(15));
    await writeFile(join(fixture.root, "b.py"), SLOW(2));
    const site = await origin(page, await fixture.launch());
    await page.getByRole("link", { name: "Models", exact: true }).click();
    for (const key of ["a", "b"]) {
      await page.getByLabel("Registration key", { exact: true }).fill(key);
      await page.getByLabel("Display name", { exact: true }).fill(key.toUpperCase());
      await page.getByLabel("Entrypoint", { exact: true }).fill(`${key}.py:build`);
      await page.getByRole("button", { name: "Preview registration" }).click();
      await page.getByRole("button", { name: "Apply registration" }).click();
      await expect(page.getByRole("heading", { name: key.toUpperCase(), exact: true })).toBeVisible();
    }
    await registerExisting(page, fixture.python);
    await page.getByRole("link", { name: "Models", exact: true }).click();
    await page.getByRole("link", { name: "A", exact: true }).click();
    await prepared(page);
    const aRoute = await hash(page);
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page.getByRole("button", { name: "Stop run", exact: true })).toBeVisible();
    // Inspect and configure the previously uninspected B while A runs; its Run is blocked, the draft survives.
    await page.getByRole("link", { name: "Models", exact: true }).click();
    await page.getByRole("link", { name: "B", exact: true }).click();
    await prepared(page);
    const bRoute = await hash(page);
    await page.getByLabel("Seed", { exact: true }).fill("44");
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page.getByRole("heading", { name: "The interactive slot is busy" })).toBeVisible();
    await expect(page.getByLabel("Seed", { exact: true })).toHaveValue("44");
    await capture(page, info.project.name, 2, "busy");
    await page.getByRole("button", { name: "Close", exact: true }).first().click().catch(() => page.keyboard.press("Escape"));
    // Automatic runs on B cannot take A's slot.
    await page.getByText("Automatic runs and model preparation", { exact: true }).click();
    await page.getByLabel("Automatically run after source or input changes").check();
    await writeFile(join(fixture.root, "b.py"), SLOW(2).replace("value: int = 1", "value: int = 2"));
    await page.waitForTimeout(3000);
    // Switch selection and edit A's source too: A keeps running.
    await page.goto(site + "/" + aRoute);
    await writeFile(join(fixture.root, "a.py"), SLOW(15).replace("value: int = 1", "value: int = 3"));
    await expect(page.getByRole("button", { name: "Stop run", exact: true })).toBeVisible();
    await page.getByRole("link", { name: "Runs", exact: true }).click();
    const running = page.getByRole("region", { name: "Interactive runs" }).locator("tbody tr").filter({ hasText: "A" });
    await expect(running.first()).toContainText(/running|preparing|ready/);
    await expect(page.getByRole("region", { name: "Interactive runs" }).locator("tbody tr").filter({ hasText: "B" })).toHaveCount(0);
    // A publishes its captured result.
    await expect(page.getByRole("region", { name: "Interactive runs" }).locator("tbody tr").filter({ hasText: "A" }).first()).toContainText("succeeded", { timeout: 60000 });
    await capture(page, info.project.name, 2, "a-published");
    // Explicit Stop and run: A again (reloading its edited inputs), then B replaces it.
    await page.goto(site + "/" + aRoute);
    const reload = page.getByRole("button", { name: "Reload current inputs" });
    if (await reload.isVisible()) await reload.click();
    await prepared(page);
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page.getByRole("button", { name: "Stop run", exact: true })).toBeVisible();
    await page.goto(site + "/" + bRoute);
    const reloadB = page.getByRole("button", { name: "Reload current inputs" });
    if (await reloadB.isVisible()) await reloadB.click();
    await prepared(page);
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page.getByRole("heading", { name: "The interactive slot is busy" })).toBeVisible();
    await page.getByRole("button", { name: "Stop active run and run B" }).click();
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText("succeeded", { timeout: 60000 });
    await capture(page, info.project.name, 2, "stop-and-run");
    // Same-model auto-run ownership: a source change while B's own automatic run is active replaces only that attempt.
    await page.goto(site + "/" + bRoute);
    const reloadB2 = page.getByRole("button", { name: "Reload current inputs" });
    if (await reloadB2.isVisible()) await reloadB2.click();
    await prepared(page);
    await page.getByText("Automatic runs and model preparation", { exact: true }).click();
    const auto = page.getByLabel("Automatically run after source or input changes");
    if (!(await auto.isChecked())) await auto.check();
    await writeFile(join(fixture.root, "b.py"), SLOW(4).replace("value: int = 1", "value: int = 5"));
    await page.waitForTimeout(1500);
    await writeFile(join(fixture.root, "b.py"), SLOW(2).replace("value: int = 1", "value: int = 6"));
    await page.goto(site + "/#/runs");
    const bRows = page.getByRole("region", { name: "Interactive runs" }).locator("tbody tr").filter({ hasText: "B" });
    await expect.poll(async () => (await bRows.allInnerTexts()).join(" "), { timeout: 60000 }).toMatch(/succeeded/);
    await expect.poll(async () => (await bRows.allInnerTexts()).join(" "), { timeout: 60000 }).not.toMatch(/running|preparing|ready|queued/);
    const texts = (await bRows.allInnerTexts()).join("\n");
    // Both models' history is found afterwards, filtered by model.
    await page.getByLabel("Filter by model").selectOption({ label: "A" });
    await expect(page.getByRole("region", { name: "Interactive runs" }).locator("tbody tr")).toHaveCount(2);
    await expect(page.getByRole("region", { name: "Interactive runs" })).toContainText("cancelled");
    await expect(page.getByRole("region", { name: "Interactive runs" })).toContainText("succeeded");
    await page.getByLabel("Filter by model").selectOption({ label: "B" });
    await expect(page.getByRole("region", { name: "Interactive runs" }).locator("tbody tr").first()).toBeVisible();
    await expect(page.getByRole("region", { name: "Interactive runs" })).toContainText("succeeded");
    await capture(page, info.project.name, 2, "history");
    test.info().annotations.push({ type: "auto-run rows of B", description: texts });
    expect(errors).toEqual([]);
  } finally {
    await fixture.close();
  }
});

test("scenario 3: broken model, missing plot packages, recovery viewer, environment update during active work", async ({ page }, info) => {
  test.setTimeout(900000);
  const fixture = new Studio();
  const errors: string[] = [];
  watch(page, errors);
  try {
    await fixture.setup();
    const plot = PLOT_PYTHON ?? fixture.python;
    const models = seed(fixture, { model: STUDY_MODEL.replace("delay: float = 0.0", "delay: float = 0.0"), files: { "report.py": REPORT, "plots.py": PLOTS }, analyses: { lateness: ANALYSES.lateness, plots: ANALYSES.plots }, interpreter: plot });
    const runs = experiments(fixture, [{ model: "production", name: "figures", count: 2, grid: { servers: [3] }, plan: "outcomes" }]);
    expect(runs.figures.status).toBe("completed");
    let figures = { execution: "", state: "skipped" } as { execution: string; state: string };
    if (PLOT_PYTHON) {
      // Figures are produced where the plot packages exist (the headless Python path of scenario 8).
      const produced = JSON.parse(execFileSync(PLOT_PYTHON, ["-I", "-c", ANALYZE, fixture.root, "plots", JSON.stringify({ experiment_run_ids: [runs.figures.run] }), "Figures"], { encoding: "utf8", timeout: 600000 }).trim()) as { execution: string; state: string; details: string };
      expect(produced.state, produced.details).toBe("succeeded");
      figures = produced;
      // The project now selects an interpreter without plot packages.
      python(fixture, SELECT_INTERPRETER, fixture.python);
    }
    const site = await origin(page, await fixture.launch());
    if (PLOT_PYTHON) {
      await page.goto(site + "/#/analyses/" + figures.execution);
      await expect(page.getByRole("heading", { name: "Figures" })).toBeVisible({ timeout: 30000 });
      await expect(page.locator('[data-output="matplotlib_png"] img')).toBeVisible({ timeout: 60000 });
      await expect(page.locator('[data-output="seaborn_svg"] img')).toBeVisible();
      await expect(page.locator('[data-output="plotly_figure"]')).toHaveAttribute("data-figure", "rendered", { timeout: 60000 });
      await capture(page, info.project.name, 3, "saved-figures-without-plot-packages");
      // Running the figure analysis here fails with a diagnostic that names the missing package.
      await page.goto(site + "/#/analyses/new/plots");
      await page.getByLabel(/^figures/).check();
      await page.getByRole("button", { name: "Preview inputs" }).click();
      await expect(page.locator("[data-preview]")).toBeVisible({ timeout: 60000 });
      await page.getByLabel("Name", { exact: true }).fill("Figures without packages");
      await page.getByRole("button", { name: "Admit and run" }).click();
      await expect(page.locator(".ws-page-heading .ws-badge").first()).toHaveText("failed", { timeout: 300000 });
      await expect(page.locator('[data-outputs="withheld"]')).toBeVisible();
      await expect(page.locator(".ws-content")).toContainText(/matplotlib/);
      await capture(page, info.project.name, 3, "missing-package");
    }
    // Broken model code: the failure names the problem; saved results stay viewable.
    await writeFile(join(fixture.root, "models/production.py"), 'raise RuntimeError("broken on purpose: the model cannot be imported")\n');
    await page.goto(site + "/#/model/" + models.production);
    await page.getByText("Automatic runs and model preparation", { exact: true }).click();
    await page.getByRole("button", { name: "Refresh model metadata", exact: true }).click();
    await expect(page.locator(".ws-failure").first()).toContainText("broken on purpose", { timeout: 60000 });
    await expect(page.locator(".ws-failure").first()).toHaveAttribute("data-kind", "model");
    await capture(page, info.project.name, 3, "broken-model");
    await page.goto(site + "/#/experiments/" + runs.figures.run);
    await expect(page.locator(".run-details h4")).toContainText("figures · completed", { timeout: 30000 });
    await page.goto(site + "/#/run/" + runs.figures.attempts[0]);
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText("succeeded", { timeout: 30000 });
    await expect(page.getByText("Numerical outputs were kept without a recording. Analysis remains available.")).toBeVisible();
    await fixture.stop();
    // Environment update during an active simulation and analysis; a failed update keeps the selection; restart waits.
    await writeFile(join(fixture.root, "models/production.py"), STUDY_MODEL.replace("delay: float = 0.0", "delay: float = 12.0"));
    const site2 = await origin(page, await fixture.launch());
    await page.goto(site2 + "/#/model/" + models.production);
    await prepared(page);
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page.getByRole("button", { name: "Stop run", exact: true })).toBeVisible();
    await page.getByRole("link", { name: "Settings", exact: true }).click();
    await page.getByText("Use an existing interpreter (advanced)", { exact: true }).click();
    await page.getByLabel("Python interpreter path").fill(join(fixture.directory, "nowhere", "bin", "python"));
    await page.getByRole("button", { name: "Check interpreter" }).click();
    await expect(page.getByRole("alert").first()).toBeVisible({ timeout: 30000 });
    await expect(page.getByRole("heading", { name: "Selected environment" })).toBeVisible();
    await capture(page, info.project.name, 3, "failed-update");
    await page.getByLabel("Python interpreter path").fill(plot === fixture.python ? join(fixture.python, "..", "python3") : plot);
    await page.getByRole("button", { name: "Check interpreter" }).click();
    await page.getByRole("button", { name: "Use environment and restart" }).click();
    await expect(page.getByRole("alert").filter({ hasText: /active work/ })).toBeVisible({ timeout: 30000 });
    await capture(page, info.project.name, 3, "update-while-active");
    await page.goto(site2 + "/#/model/" + models.production);
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText("succeeded", { timeout: 120000 });
    await page.getByRole("link", { name: "Settings", exact: true }).click();
    await page.getByText("Use an existing interpreter (advanced)", { exact: true }).click();
    await page.getByLabel("Python interpreter path").fill(plot === fixture.python ? join(fixture.python, "..", "python3") : plot);
    await page.getByRole("button", { name: "Check interpreter" }).click();
    await page.getByRole("button", { name: "Use environment and restart" }).click();
    await page.goto(await fixture.link(1));
    await expect(page.getByRole("navigation", { name: "Project navigation" })).toBeVisible({ timeout: 90000 });
    await fixture.stop();
    // Launcher recovery viewer: the selected interpreter disappears; saved results stay readable, mutation is off.
    const gone = join(fixture.directory, "gone");
    await mkdir(gone);
    await symlink(fixture.python, join(gone, "python"));
    python(fixture, SELECT_INTERPRETER, join(gone, "python"));
    await unlink(join(gone, "python"));
    await page.goto(await fixture.launch());
    await expect(page.getByRole("heading", { name: "Saved work is available" })).toBeVisible({ timeout: 60000 });
    await page.goto((await origin(page, await fixture.link(0))) + "/#/run/" + runs.figures.attempts[0]);
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText("succeeded", { timeout: 30000 });
    await expect(page.getByRole("button", { name: "Save notes", exact: true })).toHaveCount(0);
    await capture(page, info.project.name, 3, "recovery");
    expect(errors).toEqual([]);
  } finally {
    await fixture.close();
  }
});

test("scenario 4: full recording with entity inspection, then a numerical-only run", async ({ page }, info) => {
  test.setTimeout(600000);
  const fixture = new Studio();
  const errors: string[] = [];
  watch(page, errors);
  try {
    await fixture.setup();
    const models = seed(fixture, { model: STUDY_MODEL, files: {}, analyses: {} });
    const site = await origin(page, await fixture.launch());
    await page.goto(site + "/#/model/" + models.production);
    await prepared(page);
    await page.getByLabel("Recording", { exact: true }).selectOption("full");
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText("succeeded", { timeout: 120000 });
    await expect(page.locator(".ws-recording .status")).toHaveText("paused", { timeout: 60000 });
    await page.getByRole("button", { name: "Next event", exact: true }).click();
    await page.getByRole("button", { name: "Next event", exact: true }).click();
    const entities = page.locator(".ws-recording .entities button");
    await expect(entities.first()).toBeVisible({ timeout: 30000 });
    const name = (await entities.first().innerText()).trim();
    await entities.first().click();
    await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
    await page.getByText("Entity schema", { exact: true }).click();
    await expect(page.locator("details").filter({ hasText: "Entity schema" })).toContainText(/kind|state/);
    await page.getByRole("tab", { name: "Recorded KPIs", exact: true }).click();
    await expect(page.locator(".sample-source").first()).toBeVisible();
    await capture(page, info.project.name, 4, "inspect");
    // Opting out of animation keeps the numerical outputs.
    await page.goto(site + "/#/model/" + models.production);
    await prepared(page);
    await page.getByLabel("Recording", { exact: true }).selectOption("kpi");
    await page.getByLabel("Seed", { exact: true }).fill("7");
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText("succeeded", { timeout: 120000 });
    await expect(page.getByText("Numerical outputs were kept without a recording. Analysis remains available.")).toBeVisible();
    await expect(page.locator(".ws-recording")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Run details" })).toBeVisible();
    await expect(page.locator(".ws-content")).toContainText(/lateness|utilization|makespan/);
    await capture(page, info.project.name, 4, "numerical");
    await page.goto(site + "/#/runs");
    await expect(page.getByRole("region", { name: "Interactive runs" })).toContainText("KPIs only");
    expect(errors).toEqual([]);
  } finally {
    await fixture.close();
  }
});

test("scenarios 5, 6 and 7: lateness over populations, AGV utilization by family with window, post-run subsets", async ({ page }, info) => {
  test.setTimeout(900000);
  const fixture = new Studio();
  const errors: string[] = [];
  watch(page, errors);
  try {
    await fixture.setup();
    seed(fixture, { model: STUDY_MODEL, fleet: FLEET_MODEL, files: { "report.py": REPORT }, analyses: { lateness: ANALYSES.lateness, subset: ANALYSES.subset, subset_priority: ANALYSES.subset_priority, utilization: ANALYSES.utilization } });
    const runs = experiments(fixture, [
      { model: "production", name: "policies", count: 3, base_seed: 11, grid: { servers: [2, 3] }, plan: "outcomes", stopping: { type: "horizon", horizon: 60, warmup: 10 } },
      { model: "logistics", name: "fleet", count: 2, base_seed: 5, grid: { vehicles: [2, 3] }, plan: "durations", stopping: { type: "horizon", horizon: 100, warmup: 10 } },
    ]);
    expect(runs.policies.status).toBe("completed");
    expect(runs.fleet.status).toBe("completed");
    const site = await origin(page, await fixture.launch());
    // Scenario 5: lateness over all jobs, declared populations (overlapping and empty), a distribution, t intervals.
    const lateness = await admitAnalysis(page, site, "lateness", async (p) => { await p.getByLabel(/^policies/).check(); }, "Lateness study");
    await expect(page.locator(".ws-page-heading")).toContainText("6 of 6 replications · complete");
    const byFamily = page.locator('[data-output="lateness_by_family"]');
    await expect(byFamily).toBeVisible();
    await expect(byFamily.locator("tbody tr")).toHaveCount(2);
    await expect(page.locator('[data-output="lateness_family_Z"]')).toContainText(/\b0\b/);
    expect(await tableText(page.locator('[data-output="lateness_family_Z"]'))).toMatch(/—|null/);
    const all = await tableText(page.locator('[data-output="lateness_all"]'));
    const any = await tableText(page.locator('[data-output="lateness_any_family"]'));
    expect(any.replace(/any_family/g, "")).toContain(all.split(" n ")[1]?.split(" ")[0] ?? "");
    await expect(page.locator('[data-output="lateness_distribution"]')).toContainText("exact");
    const intervals = page.locator('[data-output="lateness_intervals"]');
    await expect(intervals).toContainText("replication_t");
    await expect(intervals.locator("tbody tr")).toHaveCount(2);
    await expect(intervals).toContainText("ok");
    await expect(page.locator("#analysis-inputs ~ dl, .ws-content")).toContainText(/warm-up|warmup|10/);
    await capture(page, info.project.name, 5, "lateness");
    // Scenario 6: AGV utilization by family with both named aggregations and the observation window.
    await admitAnalysis(page, site, "utilization", async (p) => { await p.getByLabel(/^fleet/).check(); }, "Fleet utilization");
    await expect(page.locator('[data-output="default_mean_of_vehicle_fractions"]')).toBeVisible();
    await expect(page.locator('[data-output="default_ratio_of_durations"]')).toBeVisible();
    await expect(page.locator('[data-output="default_mean_of_vehicle_fractions"]')).toContainText(/fraction/);
    await expect(page.locator('[data-output="default_mean_of_vehicle_fractions"]')).toContainText(/descriptive_within_run/);
    await expect(page.locator('[data-output="intervals_by_family"] tbody tr').first()).toBeVisible();
    await capture(page, info.project.name, 6, "utilization");
    // Scenario 7: a new post-run subset from retained attributes, then an unavailable field.
    await admitAnalysis(page, site, "subset", async (p) => { await p.getByLabel(/^policies/).check(); }, "Late jobs of family B", { "family": "B", "late_after": "0" });
    await expect(page.locator('[data-output="subset_status"]')).toContainText("available");
    await expect(page.locator('[data-output="subset_summary"]')).toContainText("late_in_family");
    await capture(page, info.project.name, 7, "subset");
    await page.goto(site + "/#/analyses/new/subset_priority");
    await page.getByLabel(/^policies/).check();
    await page.getByRole("button", { name: "Preview inputs" }).click();
    const refusal = page.locator('[data-refusal="insufficient_input"]');
    await expect(refusal).toBeVisible({ timeout: 60000 });
    await expect(refusal).toContainText("insufficient input for dataset job_outcomes");
    await expect(refusal).toContainText("priority");
    await expect(refusal).toContainText("never substitutes synthetic values");
    await expect(refusal.getByRole("link", { name: "Define a revised experiment →" })).toHaveAttribute("href", "#/experiments");
    await capture(page, info.project.name, 7, "missing-field");
    test.info().annotations.push({ type: "lateness execution", description: lateness });
    expect(errors).toEqual([]);
  } finally {
    await fixture.close();
  }
});

test("scenario 8: the same analysis through Python and Studio, figures reopened offline without Python", async ({ page }, info) => {
  test.setTimeout(900000);
  test.skip(!PLOT_PYTHON, "SIMULATTE_PLOT_PYTHON (a wheel install with matplotlib, seaborn and plotly) is required");
  const fixture = new Studio();
  const errors: string[] = [];
  watch(page, errors);
  try {
    await fixture.setup();
    seed(fixture, { model: STUDY_MODEL, files: { "report.py": REPORT, "plots.py": PLOTS }, analyses: { lateness: ANALYSES.lateness, plots: ANALYSES.plots }, interpreter: PLOT_PYTHON });
    const runs = experiments(fixture, [{ model: "production", name: "policies", count: 3, base_seed: 11, grid: { servers: [3] }, plan: "outcomes", stopping: { type: "horizon", horizon: 60, warmup: 10 } }]);
    expect(runs.policies.status).toBe("completed");
    const pythonPath = JSON.parse(execFileSync(PLOT_PYTHON!, ["-I", "-c", ANALYZE, fixture.root, "lateness", JSON.stringify({ experiment_run_ids: [runs.policies.run] }), "Lateness (Python)"], { encoding: "utf8", timeout: 600000 }).trim()) as ReturnType<typeof analyze>;
    const figures = JSON.parse(execFileSync(PLOT_PYTHON!, ["-I", "-c", ANALYZE, fixture.root, "plots", JSON.stringify({ experiment_run_ids: [runs.policies.run] }), "Figures (Python)"], { encoding: "utf8", timeout: 600000 }).trim()) as ReturnType<typeof analyze>;
    expect(pythonPath.state, pythonPath.details).toBe("succeeded");
    expect(figures.state, figures.details).toBe("succeeded");
    // Studio path over the same evidence (the launcher hands off to the plot interpreter the project selects).
    const site = await origin(page, await fixture.launch());
    const studioExecution = await admitAnalysis(page, site, "lateness", async (p) => { await p.getByLabel(/^policies/).check(); }, "Lateness (Studio)");
    await capture(page, info.project.name, 8, "studio-analysis");
    await fixture.stop();
    const saved = inspect(fixture);
    expect(saved[studioExecution]!.snapshot).toBe(pythonPath.snapshot);
    expect(saved[studioExecution]!.outputs).toEqual(pythonPath.outputs);
    // Reopen the saved Matplotlib, Seaborn and Plotly artifacts with an interpreter that has none of those packages.
    python(fixture, SELECT_INTERPRETER, fixture.python);
    const site2 = await origin(page, await fixture.launch());
    await page.goto(site2 + "/#/analyses/" + figures.execution);
    await expect(page.getByRole("heading", { name: "Figures (Python)" })).toBeVisible({ timeout: 30000 });
    await expect(page.locator('[data-output="matplotlib_png"] img')).toBeVisible({ timeout: 60000 });
    await expect(page.locator('[data-output="seaborn_svg"] img')).toBeVisible();
    await expect(page.locator('[data-output="plotly_figure"]')).toHaveAttribute("data-figure", "rendered", { timeout: 60000 });
    const requests: string[] = [];
    page.on("request", (r) => { if (!r.url().startsWith(site2)) requests.push(r.url()); });
    await page.reload();
    await expect(page.locator('[data-output="plotly_figure"]')).toHaveAttribute("data-figure", "rendered", { timeout: 60000 });
    expect(requests).toEqual([]);
    await capture(page, info.project.name, 8, "figures-offline");
    await fixture.stop();
    const after = inspect(fixture);
    expect(Object.keys(after).length).toBe(Object.keys(saved).length);
    expect(after[figures.execution]!.outputs).toEqual(figures.outputs);
    expect(errors).toEqual([]);
  } finally {
    await fixture.close();
  }
});

test("scenario 11: Studio killed during a run and during an analysis recovers without dangling successes", async ({ page }, info) => {
  test.setTimeout(600000);
  const fixture = new Studio();
  const errors: string[] = [];
  watch(page, errors);
  try {
    await fixture.setup();
    const slowAnalysis = `import time\nfrom simulatte.analysis import analysis\nfrom simulatte.analysis.outputs import Number\nfrom simulatte.model import ParameterSchema\n\n@analysis(parameters=ParameterSchema(()))\ndef slow(context, parameters):\n    time.sleep(20)\n    context.emit("runs", Number(len(list(context.scalars())), unit="runs"))\n`;
    const models = seed(fixture, { model: STUDY_MODEL.replace("delay: float = 0.0", "delay: float = 20.0"), files: { "slow.py": slowAnalysis }, analyses: { slow: ["7e6d5c4b-3a29-4817-8f6e-5d4c3b2a1908", "Slow analysis", "slow.py:slow"] } });
    const site = await origin(page, await fixture.launch());
    await page.goto(site + "/#/model/" + models.production);
    await prepared(page);
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText("running", { timeout: 60000 });
    const runRoute = await hash(page);
    fixture.kill();
    await expect(page.locator(".ws-notice")).toContainText("Reconnecting", { timeout: 30000 });
    await page.goto(await fixture.launch());
    await page.goto((await origin(page, await fixture.link(0))) + "/" + runRoute);
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText("lost", { timeout: 30000 });
    await expect(page.getByText("Studio stopped before this run could finish.")).toBeVisible();
    await capture(page, info.project.name, 11, "lost-run");
    // A completed run for the analysis, then the writer dies while the analysis runs.
    await page.goto(site.replace(/:\d+$/, "") && (await origin(page, await fixture.link(0))) + "/#/model/" + models.production);
    await writeFile(join(fixture.root, "models/production.py"), STUDY_MODEL);
    await page.getByText("Automatic runs and model preparation", { exact: true }).click();
    await page.getByRole("button", { name: "Refresh model metadata", exact: true }).click();
    await prepared(page);
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText("succeeded", { timeout: 120000 });
    const site2 = await origin(page, await fixture.link(0));
    await page.goto(site2 + "/#/analyses/new/slow");
    await page.getByRole("group", { name: "Completed runs" }).getByRole("checkbox").first().check();
    await page.getByRole("button", { name: "Preview inputs" }).click();
    await expect(page.locator("[data-preview]")).toBeVisible({ timeout: 60000 });
    await page.getByLabel("Name", { exact: true }).fill("Interrupted analysis");
    await page.getByRole("button", { name: "Admit and run" }).click();
    await expect(page.locator(".ws-page-heading .ws-badge").first()).toHaveText("running", { timeout: 60000 });
    const analysisRoute = await hash(page);
    fixture.kill();
    await page.goto(await fixture.launch());
    await page.goto((await origin(page, await fixture.link(0))) + "/" + analysisRoute);
    await expect(page.locator(".ws-page-heading .ws-badge").first()).toHaveText("lost", { timeout: 30000 });
    await expect(page.locator('[data-outputs="withheld"]')).toBeVisible();
    await capture(page, info.project.name, 11, "lost-analysis");
    await page.goto((await origin(page, await fixture.link(0))) + "/#/analyses");
    await expect(page.getByRole("table", { name: "Saved analyses" })).toContainText("lost");
    await page.goto((await origin(page, await fixture.link(0))) + "/#/runs");
    const rows = page.getByRole("region", { name: "Interactive runs" }).locator("tbody tr");
    await expect(rows).toHaveCount(2);
    await expect(page.getByRole("region", { name: "Interactive runs" })).toContainText("lost");
    expect(errors).toEqual([]);
  } finally {
    await fixture.close();
  }
});
