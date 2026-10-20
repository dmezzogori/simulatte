/** P4 Task 4: the twelve product acceptance scenarios through the installed wheel (UI-bound steps).
 * Headless steps of the same scenarios run in studio/scripts/scenarios.py; the evidence file
 * .superpowers/sdd/2026-10-19-studio-p4-presentation/scenarios.md maps every scenario to both.
 * Set SIMULATTE_PROJECT_EXECUTABLE to a wheel install (bare `[studio]`) and, for the figure steps,
 * SIMULATTE_PLOT_PYTHON to an interpreter of a wheel install with the plot extras. */
import { test, expect, type Page, type Locator } from "@playwright/test";
import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { cp, mkdir, readdir, rm, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { ProjectFixture } from "./project-fixture";

const evidence = resolve("../.superpowers/sdd/2026-10-19-studio-p4-presentation/screenshots");
const PLOT_PYTHON = process.env.SIMULATTE_PLOT_PYTHON;
/** The wheel of the launcher install (SIMULATTE_WHEEL), else the fixture's dist/ wheel. */
const WHEEL = (fixture: ProjectFixture) => process.env.SIMULATTE_WHEEL ?? fixture.wheel;
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
  /** Keep the server's standard error (links redacted) and exit code with the test result. */
  async close() {
    test.info().annotations.push({ type: "server stderr", description: `exit ${this.child?.exitCode ?? "running"}\n` + this.diagnostic.replace(/token=[\w-]+/g, "token=[redacted]").slice(-4000) });
    await super.close();
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
    context.emit("plotly_figure", save_figure(go.Figure(data=[go.Bar(y=values)], layout={"title": {"text": "Lateness"}}), folder, name="lateness_plotly"))
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
    toml += '\\n[tool.simulatte.models.logistics]\\nid = "839bfc13-0d9a-455f-9fa9-08615ad6fb8b"\\nname = "Fleet"\\nentrypoint = "models/logistics.py:build"\\nlayout = "layouts/logistics.json"\\n'
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
def unrecord(v):
    if isinstance(v, dict):
        return unrecord(v['$record']) if set(v) == {'$record'} else {k: unrecord(x) for k, x in v.items()}
    return [unrecord(x) for x in v] if isinstance(v, list) else v
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
    manifest = unrecord(json.loads(codec.dumps(analysis.output(item.id)))) if state == 'succeeded' else {'outputs': []}
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
def unrecord(v):
    if isinstance(v, dict):
        return unrecord(v['$record']) if set(v) == {'$record'} else {k: unrecord(x) for k, x in v.items()}
    return [unrecord(x) for x in v] if isinstance(v, list) else v
root = Path(sys.argv[1])
with RunStore.open(root / '.simulatte') as store:
    analysis = AnalysisStore(store)
    items = analysis.list(limit=50)['items']
    out = {}
    for item in items:
        got = analysis.get(item['id'])
        state = got['execution']['state']
        manifest = unrecord(json.loads(codec.dumps(analysis.output(item['id'])))) if state == 'succeeded' else {'outputs': []}
        out[item['id']] = {'name': got['execution']['name'], 'state': state, 'snapshot': got['execution']['snapshot_hash'], 'outputs': {o['name']: sorted(a['sha256'] for a in o['artifacts']) for o in manifest['outputs']}}
    print(json.dumps(out))
`;
/** Run with the currently selected interpreter: only it opens the project writable. */
const select = (fixture: ProjectFixture, current: string, target: string) =>
  execFileSync(current, ["-I", "-c", SELECT_INTERPRETER, fixture.root, target], { encoding: "utf8", timeout: 120000 });
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
/** Runs under ``interpreter`` when the project selects another one (a writable coordinator needs the selected runtime). */
const experiments = (fixture: ProjectFixture, spec: Record<string, unknown>[], interpreter = fixture.python) =>
  JSON.parse(execFileSync(interpreter, ["-I", "-c", EXPERIMENTS, fixture.root, JSON.stringify(spec)], { encoding: "utf8", timeout: 900000 }).trim()) as Record<string, { run: string; status: string; model: string; attempts: string[] }>;
const analyze = (fixture: ProjectFixture, key: string, selection: Record<string, string[]>, name: string) =>
  JSON.parse(python(fixture, ANALYZE, key, JSON.stringify(selection), name)) as { execution: string; state: string; snapshot: string; outputs: Record<string, string[]>; details: string };
const inspect = (fixture: ProjectFixture) =>
  JSON.parse(python(fixture, INSPECT)) as Record<string, { name: string; state: string; snapshot: string; outputs: Record<string, string[]> }>;
const origin = async (page: Page, link: string) => {
  await page.goto(link);
  await page.waitForURL((url) => url.pathname === "/");
  return new URL(page.url()).origin;
};
/** The origin of the page already opened through a one-use link (a link cannot be visited twice). */
const here = (page: Page) => new URL(page.url()).origin;
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
    await page.goto(here(page) + "/" + runRoute);
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
    await writeFile(join(legacy, "plant.txt"), "an existing file that is neither a folder nor a Python model\n");
    const before = (await readdir(legacy)).sort();
    for (const [reference, message] of [["missing.py:build", "does not exist"], ["plant.py:1bad", "Invalid Python entrypoint"], ["nosuch/folder/model.py", "Python model file does not exist"], ["plant.txt", "Expected a directory or Python model file"]] as const) {
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
    await expect(page.getByRole("alert").filter({ hasText: "The project environment is incomplete" })).toContainText("Selected Studio runtime unavailable");
    await expect(page.getByText(/This session is read-only/)).toBeVisible();
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
    await page.goto(here(page) + "/" + runRoute);
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
    await writeFile(join(fixture.root, "a.py"), SLOW(45));
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
    // Automatic runs on B cannot take A's slot.
    await page.getByText("Automatic runs and model preparation", { exact: true }).click();
    // The toggle is a server command: the box reflects the answer, so click and wait for the state.
    await page.getByLabel("Automatically run after source or input changes").click();
    await expect(page.getByLabel("Automatically run after source or input changes")).toBeChecked({ timeout: 30000 });
    await writeFile(join(fixture.root, "b.py"), SLOW(2).replace("value: int = 1", "value: int = 2"));
    await page.waitForTimeout(3000);
    // Switch selection and edit A's source too: A keeps running.
    await page.goto(site + "/" + aRoute);
    await writeFile(join(fixture.root, "a.py"), SLOW(45).replace("value: int = 1", "value: int = 3"));
    // The edited source makes a new draft; the captured run of A keeps running and is announced in the banner.
    await expect(page.getByRole("link", { name: "Inspect active run →" })).toBeVisible();
    await page.getByRole("link", { name: "Runs", exact: true }).click();
    const running = page.getByRole("region", { name: "Interactive runs" }).locator("tbody tr").filter({ has: page.getByRole("cell", { name: "A", exact: true }) });
    await expect(running.first()).toContainText(/running|preparing|ready/);
    await expect(page.getByRole("region", { name: "Interactive runs" }).locator("tbody tr").filter({ has: page.getByRole("cell", { name: "B", exact: true }) })).toHaveCount(0);
    // A publishes its captured result.
    await expect(page.getByRole("region", { name: "Interactive runs" }).locator("tbody tr").filter({ has: page.getByRole("cell", { name: "A", exact: true }) }).first()).toContainText("succeeded", { timeout: 120000 });
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
    if (!(await auto.isChecked())) await auto.click();
    await expect(auto).toBeChecked({ timeout: 30000 });
    await writeFile(join(fixture.root, "b.py"), SLOW(4).replace("value: int = 1", "value: int = 5"));
    await page.waitForTimeout(1500);
    await writeFile(join(fixture.root, "b.py"), SLOW(2).replace("value: int = 1", "value: int = 6"));
    await page.goto(site + "/#/runs");
    const bRows = page.getByRole("region", { name: "Interactive runs" }).locator("tbody tr").filter({ has: page.getByRole("cell", { name: "B", exact: true }) });
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
    const runs = experiments(fixture, [{ model: "production", name: "figures", count: 2, grid: { servers: [3] }, plan: "outcomes" }], plot);
    expect(runs.figures.status).toBe("completed");
    let figures = { execution: "", state: "skipped" } as { execution: string; state: string };
    if (PLOT_PYTHON) {
      // Figures are produced where the plot packages exist (the headless Python path of scenario 8).
      const produced = JSON.parse(execFileSync(PLOT_PYTHON, ["-I", "-c", ANALYZE, fixture.root, "plots", JSON.stringify({ experiment_run_ids: [runs.figures.run] }), "Figures"], { encoding: "utf8", timeout: 600000 }).trim()) as { execution: string; state: string; details: string };
      expect(produced.state, produced.details).toBe("succeeded");
      figures = produced;
      // The project now selects an interpreter without plot packages.
      select(fixture, PLOT_PYTHON, fixture.python);
    }
    const site = await origin(page, await fixture.launch());
    if (PLOT_PYTHON) {
      await page.goto(site + "/#/analyses/" + figures.execution);
      await expect(page.getByRole("heading", { name: "Figures" })).toBeVisible({ timeout: 30000 });
      await expect(page.locator('[data-output="matplotlib_png"] img')).toBeVisible({ timeout: 60000 });
      await expect(page.locator('[data-output="seaborn_svg"] img')).toBeVisible();
      await expect.soft(page.locator('[data-output="plotly_figure"]'), "B5: a default Plotly figure (template data includes choropleth) is refused").toHaveAttribute("data-figure", "rendered", { timeout: 60000 });
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
    await expect(page.getByRole("status").filter({ hasText: /^connected$/ })).toBeVisible({ timeout: 30000 });
    await page.getByRole("button", { name: "Prepare model", exact: true }).click();
    await expect(page.locator(".ws-failure").first()).toContainText("broken on purpose", { timeout: 60000 });
    await expect(page.locator(".ws-failure").first()).toHaveAttribute("data-kind", "model");
    await capture(page, info.project.name, 3, "broken-model");
    await page.goto(site + "/#/experiments/" + runs.figures.run);
    await expect(page.locator(".run-details h4")).toContainText("figures · completed", { timeout: 30000 });
    await page.goto(site + "/#/run/" + runs.figures.attempts[0]);
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText("succeeded", { timeout: 30000 });
    await expect(page.locator("dt").filter({ hasText: /^Recording level$/ }).locator("+ dd")).toHaveText("kpi");
    await expect.soft(page.locator(".ws-page-heading .ws-tag").first(), "B1: KPI-level run labelled as a full recording").not.toHaveText("Full recording");
    await fixture.stop();
    // Environment update during an active simulation and analysis; a failed update keeps the selection; restart waits.
    await writeFile(join(fixture.root, "models/production.py"), STUDY_MODEL.replace("delay: float = 0.0", "delay: float = 12.0"));
    const site2 = await origin(page, await fixture.launch());
    await page.goto(site2 + "/#/model/" + models.production);
    await prepared(page);
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page.getByRole("button", { name: "Stop run", exact: true })).toBeVisible();
    await page.getByRole("link", { name: "Settings", exact: true }).click();
    if (!(await page.getByLabel("Python interpreter path").isVisible())) await page.getByText("Use an existing interpreter (advanced)", { exact: true }).click();
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
    // The active run finishes under its pinned generation; the completion notice announces it.
    await expect(page.getByText("Production completed", { exact: true })).toBeVisible({ timeout: 120000 });
    await page.getByRole("link", { name: "Settings", exact: true }).click();
    if (!(await page.getByLabel("Python interpreter path").isVisible())) await page.getByText("Use an existing interpreter (advanced)", { exact: true }).click();
    await page.getByLabel("Python interpreter path").fill(plot === fixture.python ? join(fixture.python, "..", "python3") : plot);
    await page.getByRole("button", { name: "Check interpreter" }).click();
    await page.getByRole("button", { name: "Use environment and restart" }).click();
    await page.goto(await fixture.link(1));
    await expect(page.getByRole("navigation", { name: "Project navigation" })).toBeVisible({ timeout: 90000 });
    await fixture.stop();
    // Launcher recovery viewer: the selected interpreter disappears; saved results stay readable, mutation is off.
    // A real second interpreter (a venv of the same wheel), selected and then deleted.
    const gone = join(fixture.directory, "gone");
    execFileSync("uv", ["venv", "-q", "--python", "3.14", gone], { stdio: "pipe" });
    execFileSync("uv", ["pip", "install", "-q", "--python", join(gone, "bin", "python"), WHEEL(fixture) + "[studio]"], { stdio: "pipe", timeout: 300000 });
    select(fixture, plot === fixture.python ? fixture.python : plot, join(gone, "bin", "python"));
    await rm(gone, { recursive: true, force: true });
    await page.goto(await fixture.launch());
    await expect(page.getByRole("heading", { name: "Saved work is available" })).toBeVisible({ timeout: 60000 });
    await page.goto(here(page) + "/#/run/" + runs.figures.attempts[0]);
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
    await expect(page.locator("details").filter({ hasText: "Entity schema" })).toContainText(/due_date|routing|location/);
    await page.getByRole("tab", { name: "Recorded KPIs", exact: true }).click();
    await expect(page.getByRole("combobox", { name: "Recorded series" })).toBeVisible();
    await expect(page.getByText("Final recorded scalars").first()).toBeVisible();
    await capture(page, info.project.name, 4, "inspect");
    const fullRoute = await hash(page);
    // Opting out of animation keeps the numerical outputs.
    await page.goto(site + "/#/model/" + models.production);
    await prepared(page);
    await page.getByLabel("Recording", { exact: true }).selectOption("kpi");
    await page.getByLabel("Seed", { exact: true }).fill("7");
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect.poll(() => hash(page), { timeout: 60000 }).toMatch(/^#\/run\//);
    await expect.poll(() => hash(page), { timeout: 60000 }).not.toBe(fullRoute);
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText("succeeded", { timeout: 120000 });
    await expect(page.getByRole("heading", { name: "Run details" })).toBeVisible();
    await expect(page.locator("dt", { hasText: "Recording level" }).locator("+ dd")).toHaveText("kpi");
    // Product defect B1 (run-page.tsx:343): a KPI-level run's KPI trace is labelled "Full recording".
    await expect.soft(page.locator(".ws-page-heading .ws-tag").first(), "B1: KPI-level run labelled as a full recording").not.toHaveText("Full recording");
    // The numerical outputs of the KPI-level trace stay inspectable.
    await page.getByRole("tab", { name: "Recorded KPIs", exact: true }).click();
    await expect(page.locator(".ws-recording")).toContainText(/lateness|utilization|makespan/);
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
    // The empty population: n = 0 and no statistics (shown as —).
    expect(await tableText(page.locator('[data-output="lateness_family_Z"]'))).toMatch(/\b0\b/);
    expect(await tableText(page.locator('[data-output="lateness_family_Z"]'))).toMatch(/—|null/);
    const all = await tableText(page.locator('[data-output="lateness_all"]'));
    const any = await tableText(page.locator('[data-output="lateness_any_family"]'));
    expect(any.replace(/any_family/g, "")).toContain(all.split(" n ")[1]?.split(" ")[0] ?? "");
    await expect(page.locator('[data-output="lateness_distribution"]')).toContainText("exact");
    const intervals = page.locator('[data-output="lateness_intervals"]');
    await expect(intervals).toContainText("replication_t");
    await expect(intervals.locator("tbody tr")).toHaveCount(2);
    await expect(intervals).toContainText("ok");
    // Warm-up handling is visible: the analysis names the inputs' warm-up (10) that excluded early jobs.
    await expect.soft(page.locator(".ws-content"), "V2: the analysis page does not show the inputs' warm-up/observation window").toContainText(/warm-?up/i);
    await capture(page, info.project.name, 5, "lateness");
    // Scenario 6: AGV utilization by family with both named aggregations and the observation window.
    await admitAnalysis(page, site, "utilization", async (p) => { await p.getByLabel(/^fleet/).check(); }, "Fleet utilization");
    await expect(page.locator('[data-output="standard_mean_of_vehicle_fractions"]')).toBeVisible();
    await expect(page.locator('[data-output="standard_ratio_of_durations"]')).toBeVisible();
    await expect(page.locator('[data-output="standard_mean_of_vehicle_fractions"]')).toContainText(/fraction/);
    // The explicit aggregation and the observation window the analysis attached as output metadata.
    await expect.soft(page.locator('[data-output="standard_mean_of_vehicle_fractions"]'), "V3: output metadata (convention, aggregation, window) is not shown on the number card").toContainText(/descriptive_within_run|window/);
    await expect(page.locator('[data-output="intervals_by_family"] tbody tr').first()).toBeVisible();
    await capture(page, info.project.name, 6, "utilization");
    // Scenario 7: a new post-run subset from retained attributes, then an unavailable field.
    await admitAnalysis(page, site, "subset", async (p) => { await p.getByLabel(/^policies/).check(); }, "Late jobs of family B", { "family (str)": "B", "late_after (float)": "0" });
    await expect(page.locator('[data-output="subset_status"]')).toContainText("available");
    await expect(page.locator('[data-output="subset_summary"] tbody tr')).toHaveCount(1);
    await expect.soft(page.locator('[data-output="subset_summary"]'), "V4: a table output's population label is not shown").toContainText("late_in_family");
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
    const runs = experiments(fixture, [{ model: "production", name: "policies", count: 3, base_seed: 11, grid: { servers: [3] }, plan: "outcomes", stopping: { type: "horizon", horizon: 60, warmup: 10 } }], PLOT_PYTHON);
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
    select(fixture, PLOT_PYTHON!, fixture.python);
    const site2 = await origin(page, await fixture.launch());
    await page.goto(site2 + "/#/analyses/" + figures.execution);
    await expect(page.getByRole("heading", { name: "Figures (Python)" })).toBeVisible({ timeout: 30000 });
    await expect(page.locator('[data-output="matplotlib_png"] img')).toBeVisible({ timeout: 60000 });
    await expect(page.locator('[data-output="seaborn_svg"] img')).toBeVisible();
    await expect.soft(page.locator('[data-output="plotly_figure"]'), "B5: a default Plotly figure (template data includes choropleth) is refused").toHaveAttribute("data-figure", "rendered", { timeout: 60000 });
    const requests: string[] = [];
    // blob: and data: URLs are the page's own in-memory objects, not network requests.
    page.on("request", (r) => { if (!/^(blob|data):/.test(r.url()) && !r.url().startsWith(site2)) requests.push(r.url()); });
    await page.reload();
    await expect.soft(page.locator('[data-output="plotly_figure"]'), "B5: a default Plotly figure (template data includes choropleth) is refused").toHaveAttribute("data-figure", "rendered", { timeout: 60000 });
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
    // The model sleeps 20 s while it builds, so the attempt is active (preparing, ready or running) when Studio dies.
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText(/^(preparing|ready|running)$/, { timeout: 60000 });
    const runRoute = await hash(page);
    fixture.kill();
    await expect(page.locator(".ws-notice")).toContainText("Reconnecting", { timeout: 30000 });
    await page.goto(await fixture.launch());
    await page.goto(here(page) + "/" + runRoute);
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText("lost", { timeout: 30000 });
    await expect(page.getByText("Studio stopped before this run could finish.")).toBeVisible();
    await capture(page, info.project.name, 11, "lost-run");
    // A completed run for the analysis, then the writer dies while the analysis runs.
    await page.goto(here(page) + "/#/model/" + models.production);
    await writeFile(join(fixture.root, "models/production.py"), STUDY_MODEL);
    await expect(page.getByRole("status").filter({ hasText: /^connected$/ })).toBeVisible({ timeout: 30000 });
    await prepared(page);
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText("succeeded", { timeout: 120000 });
    const site2 = here(page);
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
    await page.goto(here(page) + "/" + analysisRoute);
    await expect(page.locator(".ws-page-heading .ws-badge").first()).toHaveText("lost", { timeout: 30000 });
    await expect(page.locator('[data-outputs="withheld"]')).toBeVisible();
    await capture(page, info.project.name, 11, "lost-analysis");
    await page.goto(here(page) + "/#/analyses");
    await expect(page.getByRole("table", { name: "Saved analyses" })).toContainText("lost");
    await page.goto(here(page) + "/#/runs");
    const rows = page.getByRole("region", { name: "Interactive runs" }).locator("tbody tr");
    await expect(rows).toHaveCount(2);
    await expect(page.getByRole("region", { name: "Interactive runs" })).toContainText("lost");
    expect(errors).toEqual([]);
  } finally {
    await fixture.close();
  }
});

// ---------------------------------------------------------------------------------------------------------------
// Scenario 9: a presentation from a chosen experiment configuration and seed, every §9 matrix row in Studio,
// video (PNG frames and MP4) and offline HTML, target previews, a revision frozen during export, static
// substitutions and an actionable refusal. Fixture: two explicit configurations x two replications (base seed 17)
// whose stopping (horizon 60, warm-up 10) and measurement plan differ from the model's defaults (40, 5, none);
// seed 18 fails in the first configuration, so the analyses' snapshot is partial (3 of 4).
const samples = resolve("../.superpowers/sdd/2026-10-19-studio-p4-presentation/samples");
const PULSE = `
from typing import ClassVar
from simulatte.kpi import KPI, Collector
from simulatte.shopfloor import JobFinished

class Pulse(Collector):
    kpis: ClassVar = (KPI("finished", unit="jobs", kind=("series",)),)
    subscribes: ClassVar = (JobFinished,)
    def __init__(self, shopfloor):
        super().__init__(shopfloor)
        self.count = 0
    def on_event(self, event):
        self.count += 1
        self.sample("finished", float(self.count))
`;
const STUDY9 =
  STUDY_MODEL.replace("    delay: float = 0.0\n", "    delay: float = 0.0\n    fail_seed: int = -1\n")
    .replace("    time.sleep(params.delay)\n", "    if env.seed == params.fail_seed:\n        raise RuntimeError('deliberate failure')\n    time.sleep(params.delay)\n")
    .replace("    measurements().attach(env)\n", "    measurements().attach(env)\n    Pulse(system.shop_floor).attach(env)\n") + PULSE;
const FIGURES = `from pathlib import Path
import tempfile
from simulatte.analysis import analysis
from simulatte.analysis.outputs import Figure, Image
from simulatte.model import ParameterSchema

@analysis(parameters=ParameterSchema(()))
def figures(context, parameters):
    values = [r["scalars"].get("shopfloor/lateness", 0.0) for r in context.scalars()]
    folder = Path(tempfile.mkdtemp())
    (folder / "bars.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="80"><rect x="10" y="20" width="60" height="50" fill="#2a7"/><text x="90" y="50">lateness</text></svg>')
    context.emit("bars", Image(folder / "bars.svg", "image/svg+xml"))
    context.emit("lateness_figure", Figure("plotly", {"data": [{"type": "bar", "y": values}], "layout": {"title": {"text": "Lateness by replication"}}}, renderer={"name": "plotly.js", "version": "4.1.1"}))
`;
const STUDY9_SEED = `
from pathlib import Path
import sys, json
from simulatte.analysis import codec
from simulatte.analysis.command import prepare_analysis
from simulatte.analysis.execution import AnalysisRunner
from simulatte.analysis.selection import Selection
from simulatte.analysis.store import AnalysisStore
from simulatte.experiments import load_experiment
from simulatte.projects import StudioTarget
from simulatte.projects.service import ProjectService
root = Path(sys.argv[1])
service = ProjectService.open(StudioTarget(root, None))
try:
    co = service.ensure_coordinator(); model = service.workspace.models[0]
    prepared = co.prepare_model(model.id, timeout=120)
    meta = next(m for m in prepared.metadata['models'] if m['name'] == prepared.metadata['selected'])
    plan = next(p for p in meta['measurement_plans'] if p['name'] == 'outcomes')
    data = {'schema': 1, 'name': 'policy', 'model': model.entrypoint, 'replications': {'count': 2, 'base_seed': 17},
            'configurations': {'explicit': [{'label': 'two servers', 'parameters': {'servers': 2, 'fail_seed': 18}}, {'label': 'three servers', 'parameters': {'servers': 3, 'fail_seed': -1}}]},
            'level': 'kpi', 'stopping': {'type': 'horizon', 'horizon': 60.0, 'warmup': 10.0},
            'measurements': {'schema': 1, 'plan': 'outcomes', 'selection': codec.to_json({}), 'definition_hash': codec.identity(plan)}}
    path = root / 'policy.experiment.json'; path.write_text(json.dumps(data))
    run = co.start_experiment(load_experiment(path), model_id=model.id)
    outcome = co.wait_experiment(run, timeout=600)
    reps = co.store.replications(run)
    chosen = next(r for r in reps if r['configuration'] == 1 and r['index'] == 0)
    executions = {}
    for key, name in (('lateness', 'Lateness study'), ('figures', 'Figures')):
        definition, generation = prepare_analysis(co.store, key, environments=service.environments)
        analysis = AnalysisStore(co.store)
        item = analysis.admit(analysis.preview(Selection(experiment_run_ids=(run,))).id, definition_id=definition, parameters={}, generation_id=generation)
        analysis.rename(item.id, name)
        with AnalysisRunner(co.store, environments=service.environments) as runner:
            runner.submit(item.id)
            executions[key] = runner.wait(item.id, timeout=300).state
    print(json.dumps({'run': run, 'status': outcome['status'], 'statuses': sorted(r['status'] for r in reps), 'attempt': chosen['attempt'], 'seed': chosen['seed'], 'model_stopping': dict(meta['stopping']), 'executions': executions}))
finally:
    service.close()
`;
const EXPORTS = `
from pathlib import Path
import sys, json
from simulatte.execution.store import RunStore
from simulatte.presentations.store import PresentationStore
with RunStore.open(Path(sys.argv[1]) / '.simulatte') as store:
    p = PresentationStore(store)
    items = p.list(limit=50)['items']
    out = {'presentations': [{'id': i['id'], 'revision': i['revision']} for i in items], 'exports': []}
    for job in p.exports():
        d = job.to_dict(); out['exports'].append({k: d.get(k) for k in ('id', 'revision', 'target', 'snapshot_hash', 'state')})
    print(json.dumps(out))
`;

test("scenario 9: presentation from a chosen configuration and seed, every §9 row in Studio, video and offline HTML", async ({ page, context, browserName }, info) => {
  test.setTimeout(1200000);
  const engine = info.project.name;
  const fixture = new Studio();
  const errors: string[] = [];
  watch(page, errors);
  try {
    await fixture.setup();
    await mkdir(evidence, { recursive: true });
    await mkdir(samples, { recursive: true });
    seed(fixture, { model: STUDY9, files: { "report.py": REPORT, "figures.py": FIGURES }, analyses: { lateness: ANALYSES.lateness, figures: ["7e6d5c4b-3a29-4817-8f6e-5d4c3b2a1908", "Figures", "figures.py:figures"] } });
    const study = JSON.parse(python(fixture, STUDY9_SEED)) as { run: string; status: string; statuses: string[]; attempt: string; seed: string | number; model_stopping: Record<string, unknown>; executions: Record<string, string> };
    expect(study.statuses).toEqual(["failed", "succeeded", "succeeded", "succeeded"]);
    expect(study.executions).toEqual({ lateness: "succeeded", figures: "succeeded" });
    expect(String(study.seed)).toBe("17");
    expect(study.model_stopping).toMatchObject({ horizon: 40, warmup: 5 });
    const site = await origin(page, await fixture.launch());

    // The chosen configuration's replication: original seed → verified replay; new seed → labelled new recording.
    await page.goto(site + "/#/presentations");
    await expect(page.getByRole("heading", { name: "Record a run for presentation" })).toBeVisible({ timeout: 30000 });
    const recordAs = async (seedValue: string, mode: "replay" | "new_seed") => {
      await page.getByLabel("Original run", { exact: true }).selectOption(study.attempt);
      await page.getByLabel("Seed", { exact: true }).fill(seedValue);
      await page.getByRole("button", { name: "Preview differences", exact: true }).click();
      const diff = page.locator("[data-diff-mode]");
      await expect(diff).toHaveAttribute("data-diff-mode", mode, { timeout: 60000 });
      const fields = await diff.locator("tbody tr td:first-child").allInnerTexts();
      await capture(page, engine, 9, `diff-${mode}`);
      const status = page.locator("[data-recording]");
      const previous = (await status.count()) ? await status.getAttribute("data-recording") : null;
      await page.getByRole("button", { name: "Record", exact: true }).click();
      await expect(status).not.toHaveAttribute("data-recording", previous ?? "none", { timeout: 60000 });
      await expect(status).toContainText("succeeded", { timeout: 180000 });
      return { attempt: (await status.getAttribute("data-recording"))!, fields, label: await status.innerText() };
    };
    const replay = await recordAs("17", "replay");
    await expect(page.locator("[data-recording]")).toContainText("verified replay", { timeout: 60000 });
    expect(replay.fields).toEqual(["level"]);
    const fresh = await recordAs("117", "new_seed");
    expect(fresh.attempt).not.toBe(replay.attempt);
    await expect(page.locator("[data-recording]")).toContainText("new recording, not a verified reproduction");
    expect(fresh.fields.sort()).toEqual(["level", "seed"]);
    await capture(page, engine, 9, "recordings");
    // The experiment's stopping, warm-up and measurement plan survive both paths (not the model defaults 40/5).
    for (const [attempt, seedValue] of [[study.attempt, "17"], [replay.attempt, "17"], [fresh.attempt, "117"]] as const) {
      await page.goto(site + "/#/run/" + attempt);
      await expect(page.getByRole("heading", { level: 1 })).toHaveText("Run " + attempt.slice(0, 8), { timeout: 30000 });
      const details = page.getByRole("heading", { name: "Run details" }).locator("..");
      await expect(page.locator("dt").filter({ hasText: /^Stopping$/ }).locator("+ dd")).toHaveText("horizon · horizon 60 · warmup 10", { timeout: 30000 });
      await expect(page.locator("dt").filter({ hasText: /^Seed$/ }).locator("+ dd")).toHaveText(seedValue);
      await expect(page.locator(".ws-page-heading")).toContainText("Measurements · outcomes");
      await expect(details).toContainText("servers");
    }
    await capture(page, engine, 9, "new-seed-run-details");

    // Editor over the verified replay: framing, visibility, measurements, time range, rate, annotation, evidence.
    await page.goto(site + "/#/presentations/new/" + replay.attempt);
    await expect(page.locator("[data-recording-label]")).toHaveText("verified replay", { timeout: 60000 });
    await expect(page.getByRole("button", { name: "Use current view", exact: true })).toBeEnabled({ timeout: 60000 });
    await page.getByLabel("Name", { exact: true }).fill("Three servers policy");
    await page.getByLabel("Notes", { exact: true }).fill("Replay of configuration 'three servers', replication 0 (seed 17).");
    const measurements = page.getByRole("group", { name: "Measurements" }).getByRole("checkbox");
    await expect.poll(() => measurements.count(), { timeout: 30000 }).toBeGreaterThan(0);
    const finished = page.getByRole("group", { name: "Measurements" }).getByRole("checkbox", { name: /finished/ });
    if (await finished.count()) await finished.first().check();
    else await measurements.nth(0).check();
    await page.getByLabel("Start", { exact: true }).fill("10");
    await page.getByLabel("End", { exact: true }).fill("30");
    await page.getByLabel("Rate", { exact: true }).selectOption("2");
    await page.getByRole("button", { name: "Add annotation", exact: true }).click();
    await page.getByLabel("Annotation 1", { exact: true }).fill("Queues build after the warm-up");
    await page.getByLabel("Time of annotation 1", { exact: true }).fill("12");
    await page.getByLabel("Show every entity", { exact: true }).uncheck();
    const shown = page.getByRole("group", { name: "Entities" }).getByRole("checkbox", { name: /^Show / });
    await expect.poll(() => shown.count()).toBeGreaterThan(0);
    await shown.first().uncheck();
    const choose = async (label: string, name: string) => {
      const select = page.getByLabel(label, { exact: true });
      const option = select.locator("option").filter({ hasText: new RegExp(`^${name} · `) });
      await expect(option).toHaveCount(1, { timeout: 30000 });
      await select.selectOption((await option.getAttribute("value"))!);
    };
    const addEvidence = async (analysis: string, output: string, columns: string[] = []) => {
      if (!(await page.getByLabel("Saved analysis", { exact: true }).isVisible())) await page.getByRole("button", { name: "Add evidence", exact: true }).first().click();
      await choose("Saved analysis", analysis);
      await choose("Output", output);
      if (columns.length) {
        await page.getByLabel("Rows", { exact: true }).fill("2");
        for (const column of columns) await page.getByRole("checkbox", { name: column, exact: true }).check();
      }
      await page.getByRole("button", { name: "Add evidence", exact: true }).last().click();
    };
    // The saved analysis is labelled partial in the picker.
    await page.getByRole("button", { name: "Add evidence", exact: true }).first().click();
    await expect(page.getByLabel("Saved analysis", { exact: true }).locator("option").filter({ hasText: /^Lateness study · 3 of 4 · partial/ })).toHaveCount(1, { timeout: 30000 });
    await addEvidence("Lateness study", "included_jobs");
    await addEvidence("Lateness study", "lateness_by_family", ["family", "n"]);
    await addEvidence("Lateness study", "lateness_distribution");
    await addEvidence("Figures", "lateness_figure");
    await addEvidence("Figures", "bars");
    const qualifiers = page.getByRole("list", { name: "Qualifiers of included_jobs" }).first();
    await expect(qualifiers).toContainText("3 of 4 replications · partial snapshot");
    await expect(qualifiers).toContainText("unit jobs");
    await expect(qualifiers).toContainText(/Lateness study · snapshot/);
    // Saved evidence values render in the editor (number, distribution, figure, image).
    for (const name of ["included_jobs", "lateness_distribution", "lateness_figure", "bars"])
      await expect.soft(page.getByRole("region", { name, exact: true }).getByRole("alert"), `B4: ${name} evidence fails to load in Studio`).toHaveCount(0, { timeout: 15000 });
    const stage = page.locator(".pv-stage canvas");
    await expect(stage).toBeVisible();
    await stage.focus();
    await page.keyboard.press("+");
    await page.keyboard.press("ArrowRight");
    await page.getByRole("button", { name: "Use current view", exact: true }).click();
    const framing = await page.locator("[data-camera]").getAttribute("data-camera");
    await capture(page, engine, 9, "editor");
    await page.getByRole("button", { name: "Save presentation", exact: true }).click();
    await expect.poll(() => hash(page), { timeout: 30000 }).toMatch(/^#\/presentations\/[0-9a-f-]{36}$/);
    const route = await hash(page);
    await expect(page.locator(".ws-tag").filter({ hasText: /^Revision 1$/ })).toBeVisible();

    const savedRate = python(fixture, "import sqlite3, sys, json\ndb = sqlite3.connect('file:' + sys.argv[1] + '/.simulatte/index.sqlite?mode=ro', uri=True)\nrow = db.execute('SELECT manifest FROM presentation_revisions ORDER BY created DESC LIMIT 1').fetchone()\nm = json.loads(row[0])\nprint(json.dumps({'rate': m.get('rate'), 'time': m.get('time')}, default=str))");
    test.info().annotations.push({ type: "saved rate and time", description: savedRate });
    // Row 1-2 (Studio): saved framing, visibility, range and rate come back; temporary changes are not saved.
    await page.goto(site + "/" + route);
    await expect(page.getByLabel("Name", { exact: true })).toHaveValue("Three servers policy", { timeout: 30000 });
    await expect(page.getByLabel("Start", { exact: true })).toHaveValue("10");
    await expect(page.getByLabel("End", { exact: true })).toHaveValue("30");
    await expect.soft(page.getByLabel("Rate", { exact: true }), `B2: saved rate not restored in the editor (store: ${savedRate})`).toHaveValue("2");
    await expect(page.getByLabel("Show every entity", { exact: true })).not.toBeChecked();
    await expect(page.locator("[data-camera]")).toHaveAttribute("data-camera", framing!);
    await stage.focus();
    await page.keyboard.press("-");
    await page.goto(site + "/" + route);
    await expect(page.locator("[data-camera]")).toHaveAttribute("data-camera", framing!, { timeout: 30000 });

    // Rows 3-6 (Studio): presentation mode with measurements, annotation, evidence with qualifiers, links to analyses.
    await page.getByRole("link", { name: "Present →", exact: true }).click();
    const mode = page.getByRole("region", { name: "Presentation: Three servers policy" });
    await expect(mode).toBeVisible();
    await expect(mode.getByRole("button", { name: "Exit presentation" })).toBeFocused();
    await expect(mode.locator(".pv-measurements")).toBeVisible({ timeout: 30000 });
    await expect(mode).toContainText("Queues build after the warm-up");
    const aside = mode.getByRole("complementary", { name: "Supporting evidence" });
    await expect(aside).toContainText("3 of 4 replications · partial snapshot");
    await expect(aside.locator("[data-evidence]")).toHaveCount(5);
    await expect(aside.locator('[data-evidence][data-kind="table"] table')).toBeVisible({ timeout: 30000 });
    await expect(aside.locator('[data-evidence][data-kind="figure"]')).not.toContainText("Figure refused", { timeout: 30000 });
    await expect(aside.getByRole("link", { name: "Open analysis →" })).toHaveCount(5);
    await expect.soft(page.getByRole("navigation", { name: "Project navigation" }), "B3: workspace navigation stays exposed in presentation mode").toHaveCount(0);
    await capture(page, engine, 9, "present");
    await page.keyboard.press("Escape");
    await expect(mode).toHaveCount(0);
    expect(await hash(page)).toBe(route);

    // Export: target previews at the actual size, an actionable refusal naming the item, static substitutions.
    await page.evaluate(() => Object.defineProperty(window, "showSaveFilePicker", { value: undefined }));
    await page.locator("#pv-export-button").click();
    const dialog = page.getByRole("dialog", { name: /^Export presentation/ });
    await expect(dialog).toBeVisible({ timeout: 60000 });
    await expect(dialog).toContainText("Revision 1 is frozen at admission");
    const prepare = async () => {
      await dialog.getByRole("button", { name: /^Prepare preview/ }).click();
      await expect(dialog.locator("figure.export-preview, .export-refusal").first()).toBeVisible({ timeout: 90000 });
    };
    const ready = async () => {
      await expect(dialog.locator("figure.export-preview")).toHaveAttribute("data-preview", "ready", { timeout: 90000 });
      return /snapshot ([0-9a-f]{12})/.exec(await dialog.locator(".export-limits").innerText())?.[1] ?? "";
    };
    await dialog.getByLabel("Format", { exact: true }).selectOption("png");
    await dialog.getByLabel("Width", { exact: true }).fill("640");
    await dialog.getByLabel("Height", { exact: true }).fill("360");
    await prepare();
    const refusal = dialog.locator(".export-refusal");
    await expect(refusal).toBeVisible();
    const refusedItem = (await refusal.getAttribute("data-item")) ?? "";
    expect(refusedItem).toMatch(/^(title|notes|measurements|annotations|(number|table|distribution|figure|image) .+)$/);
    await expect(refusal).toContainText("Nothing is omitted or truncated on your behalf");
    await capture(page, engine, 9, "refusal");
    // A size the whole selection fits: the preview canvas is the target size, figures become static views.
    await dialog.getByLabel("Width", { exact: true }).fill("1920");
    await dialog.getByLabel("Height", { exact: true }).fill("1080");
    await dialog.getByLabel("End time", { exact: true }).fill("11");
    await prepare();
    const pngHash = await ready();
    const canvas = dialog.locator("figure.export-preview canvas");
    await expect(canvas).toHaveAttribute("width", "1920");
    await expect(canvas).toHaveAttribute("height", "1080");
    const limits = await dialog.locator(".export-limits").innerText();
    expect(limits).toContain("1920 × 1080");
    expect(limits).toMatch(/2 static views · 5 evidence items/);
    await canvas.screenshot({ path: join(samples, `scenario9-${engine}-preview-1920x1080.png`) });
    await capture(page, engine, 9, "preview");
    const download = async (timeout = 300000) => {
      const link = dialog.getByRole("link", { name: /^Download / });
      try {
        await expect(link).toBeVisible({ timeout });
      } catch (error) {
        test.info().annotations.push({ type: "export dialog without a download", description: (await dialog.innerText()).slice(-1500) });
        throw error;
      }
      const waiting = page.waitForEvent("download");
      await link.click();
      return await waiting;
    };
    // PNG frames, with an edit in a second tab while the export runs: the export keeps revision 1 and its snapshot.
    await page.evaluate(() => {
      if (typeof OffscreenCanvas !== "undefined") {
        const convert = OffscreenCanvas.prototype.convertToBlob;
        OffscreenCanvas.prototype.convertToBlob = async function (options) {
          await new Promise((r) => setTimeout(r, 150));
          return convert.call(this, options);
        };
      }
    });
    await dialog.getByRole("button", { name: "Start export" }).click();
    await page.waitForTimeout(1500);
    test.info().annotations.push({ type: "console errors after start", description: errors.join("\n").slice(-3000) + "\nSERVER: " + fixture.diagnostic.replace(/token=[\w-]+/g, "token=[redacted]").slice(-3000) });
    test.info().annotations.push({ type: "export dialog after start", description: (await dialog.count()) ? (await dialog.innerText()).slice(0, 1500) : "dialog closed: " + (await page.locator("main").innerText()).slice(0, 800) });
    await expect(dialog.locator("output")).toContainText("frames", { timeout: 60000 });
    const secret = await page.evaluate(() => sessionStorage.getItem("simulatte-session"));
    const tab = await context.newPage();
    await tab.addInitScript((value) => {
      sessionStorage.setItem("simulatte-mode", "studio");
      sessionStorage.setItem("simulatte-session", value ?? "");
    }, secret);
    await tab.goto(site + "/" + route);
    await expect(tab.getByLabel("Notes", { exact: true })).toHaveValue(/three servers/, { timeout: 60000 });
    await tab.getByLabel("Notes", { exact: true }).fill("Revised while the PNG export was running.");
    await tab.getByRole("button", { name: /^Save (new revision|presentation)$/ }).click();
    await expect(tab.locator(".ws-tag").filter({ hasText: /^Revision 2$/ })).toBeVisible({ timeout: 30000 });
    await tab.close();
    const png = await download();
    const zip = join(info.outputDir, "frames.zip");
    await png.saveAs(zip);
    const frames = execFileSync(fixture.python, ["-I", "-c", "import sys, zipfile\nwith zipfile.ZipFile(sys.argv[1]) as z:\n names=sorted(n for n in z.namelist() if n.endswith('.png'))\n open(sys.argv[2],'wb').write(z.read(names[0]))\n print(len(names))", zip, join(samples, `scenario9-${engine}-frame-000000.png`)], { encoding: "utf8" }).trim();
    expect(Number(frames)).toBe(16); // 1 simulated unit at rate 2 → 0.5 s at 30 fps, inclusive end frame
    await dialog.getByRole("button", { name: "Remove temporary copy", exact: true }).click();
    // MP4 of the same selection.
    await dialog.getByLabel("Format", { exact: true }).selectOption("mp4");
    await prepare();
    await ready();
    await dialog.getByRole("button", { name: "Start export" }).click();
    const mp4 = await download();
    await mp4.saveAs(join(samples, `scenario9-${engine}-presentation.mp4`));
    await dialog.getByRole("button", { name: "Remove temporary copy", exact: true }).click();
    // Standalone HTML.
    await dialog.getByLabel("Format", { exact: true }).selectOption("html");
    await prepare();
    await ready();
    const waiting = page.waitForEvent("download");
    await dialog.getByRole("button", { name: "Start export" }).click();
    const html = await waiting;
    const file = join(samples, `scenario9-${engine}-presentation.html`);
    await html.saveAs(file);
    await expect(dialog.locator('p[role="status"]')).toContainText("Offline HTML saved", { timeout: 60000 });
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await fixture.stop();
    // The store: every export of revision 1 succeeded; the PNG job kept the snapshot prepared before the edit.
    const state = JSON.parse(python(fixture, EXPORTS)) as { presentations: { revision: number }[]; exports: { revision: number; target: string; snapshot_hash: string; state: string }[] };
    expect(state.presentations.map((p) => p.revision)).toEqual([2]);
    expect(state.exports.map((j) => [j.target, j.state]).sort()).toEqual([["html", "succeeded"], ["video", "succeeded"], ["video", "succeeded"]]);
    const pngJob = state.exports.find((j) => j.snapshot_hash.startsWith(pngHash));
    expect(pngJob?.revision).toBe(1);
    const htmlRevision = state.exports.find((j) => j.target === "html")!.revision;
    test.info().annotations.push({ type: "export revisions", description: JSON.stringify(state.exports.map((j) => [j.target, j.revision, j.snapshot_hash.slice(0, 12)])) });

    // Offline HTML (rows 1-6): saved view and reset, interval and rate, measurements, annotation, qualifiers,
    // table excerpt and static figure views, no network and no project.
    await context.setOffline(true);
    const requests: string[] = [];
    page.on("request", (r) => {
      if (!r.url().startsWith("file:") && !r.url().startsWith("data:") && !r.url().startsWith("blob:")) requests.push(r.url());
    });
    await page.goto(pathToFileURL(file).href);
    await expect(page.locator("footer output")).toContainText("t = 10", { timeout: 30000 });
    const panel = page.locator("#evidence");
    await expect(panel).toContainText("Three servers policy");
    await expect(panel).toContainText(htmlRevision === 1 ? "Replay of configuration 'three servers'" : "Revised while the PNG export was running.");
    await expect(panel).toContainText("Measurements");
    await expect(panel).toContainText("Queues build after the warm-up");
    await expect(panel).toContainText("3 of 4 replications · partial snapshot");
    await expect(panel.locator("table")).toHaveCount(1);
    await expect(panel.locator('img[src^="data:image/png"]')).toHaveCount(1);
    await expect(panel.locator('img[src^="data:image/svg+xml"]')).toHaveCount(1);
    await expect(panel.locator(".provenance")).toContainText("snapshot");
    await expect(page.getByLabel("Playback rate")).toHaveValue("2");
    await expect(page.getByLabel("Simulation time")).toHaveAttribute("min", "10");
    await expect(page.getByLabel("Simulation time")).toHaveAttribute("max", "30");
    await page.getByRole("button", { name: "Play", exact: true }).click();
    await page.waitForTimeout(300);
    await page.getByRole("button", { name: "Pause", exact: true }).click({ delay: 100 });
    await page.locator("#evidence li button").first().click();
    await expect(page.locator("footer output")).toContainText("t = 12");
    await page.getByRole("button", { name: "Reset view", exact: true }).click();
    await expect(page.locator("footer output")).toContainText("t = 10");
    await capture(page, engine, 9, "offline-html");
    expect(requests).toEqual([]);
    await context.setOffline(false);
    if (browserName === "chromium") expect(errors).toEqual([]);
  } finally {
    await fixture.close();
  }
});
