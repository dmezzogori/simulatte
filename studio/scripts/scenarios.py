"""Headless drivers for the twelve product acceptance scenarios (Studio P4 Task 4).

Run under the fresh wheel interpreter, never the source tree::

    <venv>/bin/python -I studio/scripts/scenarios.py --output DIR \
        [--plot-python <venv-plot>/bin/python] [--wheel dist/simulatte-0.17.0-py3-none-any.whl] [--only 1,2]

Every product operation uses the interpreter running this script (the installed wheel) or an
interpreter given on the command line. Each scenario writes ``DIR/scenario<N>.json`` with the
observed facts, the exact commands and the paths it touched; ``DIR/summary.json`` lists the
outcomes. UI-bound steps of the same scenarios live in ``studio/browser/scenarios.spec.ts``.
"""

# ruff: noqa: E501  (the embedded model and analysis sources keep their own line lengths)
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from uuid import uuid4

PYTHON = Path(sys.executable).absolute()  # the venv launcher, not the resolved base interpreter
SIMULATTE = PYTHON.with_name("simulatte")
SCRIPT = Path(__file__).resolve()
REPO = SCRIPT.parents[2]
MANIFEST_FIXTURE = REPO / "tests/fixtures/presentations/valid-minimal.json"
LINK = re.compile(r"http://127\.0\.0\.1:\d+/bootstrap\?token=[\w-]+")


class Failure(AssertionError):
    pass


def check(condition, message):
    if not condition:
        raise Failure(message)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob("*")) if p.is_file()}


def plain(value):
    """Mapping proxies, tuples and frozen records as JSON-compatible data."""
    from collections.abc import Mapping

    if isinstance(value, Mapping):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, (bytes, memoryview)):
        return bytes(value).hex()
    if isinstance(value, Path):
        return str(value)
    return value


# Durable tables whose rows must survive a copy, a reconstruction or a recovery unchanged.
DURABLE_TABLES = (
    "requests",
    "attempts",
    "results",
    "artifacts",
    "kpis",
    "experiments",
    "experiment_runs",
    "replications",
    "replays",
    "snapshots",
    "references",
    "models",
    "attempt_models",
    "history_annotations",
    "analysis_definitions",
    "analysis_input_snapshots",
    "analysis_executions",
    "analysis_outputs",
    "analysis_annotations",
    "presentations",
    "presentation_revisions",
    "export_jobs",
)


def dump(root: Path, tables=DURABLE_TABLES) -> dict[str, list[str]]:
    """Row digests of the durable tables of a quiescent store (read-only connection)."""
    uri = f"file:{(root / '.simulatte/index.sqlite').as_posix()}?mode=ro"
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as db:
        present = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        out = {}
        for table in tables:
            if table not in present:
                continue
            rows = db.execute(f'SELECT * FROM "{table}"').fetchall()
            out[table] = sorted(hashlib.sha256(repr(tuple(plain(x) for x in row)).encode()).hexdigest() for row in rows)
        return out


def unrecord(value):
    """The codec's tagged JSON (``{"$record": {...}}``) as plain mappings."""
    if isinstance(value, dict):
        if set(value) == {"$record"}:
            return unrecord(value["$record"])
        return {k: unrecord(v) for k, v in value.items()}
    if isinstance(value, list):
        return [unrecord(v) for v in value]
    return value


def tables(root: Path, names) -> dict[str, list]:
    uri = f"file:{(root / '.simulatte/index.sqlite').as_posix()}?mode=ro"
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as db:
        db.row_factory = sqlite3.Row
        return {t: [dict(r) for r in db.execute(f'SELECT * FROM "{t}"')] for t in names}


class Evidence:
    def __init__(self, number: int, output: Path):
        self.number, self.output = number, output
        self.steps: list[dict] = []
        self.commands: list[dict] = []
        self.path = output / f"scenario{number}.json"

    def note(self, step: str, **facts):
        self.steps.append({"step": step, **plain(facts)})

    def command(self, argv, *, cwd=None, env=None, timeout=900, expect=0, stdin=None):
        argv = [str(a) for a in argv]
        started = time.monotonic()
        done = subprocess.run(
            argv,
            cwd=None if cwd is None else str(cwd),
            env=None if env is None else {**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=timeout,
            input=stdin,
        )
        record = {
            "argv": argv,
            "cwd": None if cwd is None else str(cwd),
            "returncode": done.returncode,
            "seconds": round(time.monotonic() - started, 2),
            "stdout_tail": done.stdout[-3000:],
            "stderr_tail": done.stderr[-3000:],
        }
        self.commands.append(record)
        if expect is not None:
            check(
                done.returncode == expect,
                f"{' '.join(argv)} exited {done.returncode}, expected {expect}: {done.stderr[-800:]}",
            )
        return done

    def save(self, status: str, error: str | None = None, seconds: float = 0.0):
        self.path.write_text(
            json.dumps(
                {
                    "scenario": self.number,
                    "status": status,
                    "error": error,
                    "seconds": round(seconds, 1),
                    "interpreter": str(PYTHON),
                    "steps": self.steps,
                    "commands": self.commands,
                },
                indent=1,
                default=str,
            )
        )


# --------------------------------------------------------------------------- fixtures

SLOW_MODEL = """import time
from dataclasses import dataclass
from simulatte import model

@dataclass(frozen=True)
class Params:
    value: int = 1
    delay: float = {delay}

@model(params=Params, horizon=3)
def build(env, layout, params):
    def work():
        time.sleep(params.delay)
        yield env.timeout(1)
    env.process(work())
"""

# Job outcomes with declared populations: two disjoint families, one overlapping cohort and an
# empty one. ``fail_seed`` makes exactly one replication fail; ``gate`` pauses replications with
# seed >= ``gate_from`` until the gate file exists (eight-of-24 journeys).
STUDY_MODEL = """import time
from dataclasses import dataclass
from pathlib import Path
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
    fail_seed: int = -1
    gate: str = ""
    gate_from: int = 0


@model(params=Params, horizon=40, warmup=5, measurement_plans=(PLAN,))
def build(env, layout, params):
    if env.seed == params.fail_seed:
        raise RuntimeError(f"deliberate failure for seed {env.seed}")
    if params.gate and env.seed >= params.gate_from:
        while not Path(params.gate).exists():
            time.sleep(0.1)
    system = build_immediate_release_system(env=env, scenario=Scenario.pure_job_shop(n_servers=params.servers))
    ShopFloorKPIs(system.shop_floor).attach(env)
    measurements().attach(env)
"""

REPORT = '''"""Named analyses over retained job outcomes (scenarios 5, 7, 8)."""
import math
from simulatte.analysis import DatasetRequirement, Population, analysis
from simulatte.analysis.builtins import select_population
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


@analysis(parameters=ParameterSchema(()), requires=(DatasetRequirement("job_outcomes", FIELDS, require_complete=True),))
def lateness_complete(context, parameters):
    context.emit("included_jobs", Number(len(included(context)), unit="jobs"))


SUBSET = ParameterSchema((ParameterField("family", "str", False, "A"), ParameterField("late_after", "float", False, 0.0)))


@analysis(parameters=SUBSET, requires=(DatasetRequirement("job_outcomes", FIELDS),))
def subset(context, parameters):
    """A post-run cohort from retained attributes only; nothing is rerun."""
    rows = included(context)
    retained = tuple(sorted({key for row in rows for key in row}))
    population = Population("late_in_family", lambda row: row["family"] == parameters["family"] and row["lateness"] > parameters["late_after"], "completion", fields=FIELDS)
    selected = select_population(rows, population, retained_fields=retained)
    context.emit("subset_status", Table(DatasetSchema("subset_status", (Field("status", "string"), Field("missing_fields", "string"))), [{"status": selected.status, "missing_fields": ",".join(selected.missing_fields)}]))
    context.emit("subset_summary", summarize_population(selected.rows, value="lateness", unit="h", population="late_in_family"))


@analysis(parameters=ParameterSchema(()), requires=(DatasetRequirement("job_outcomes", ("family", "priority")),))
def subset_priority(context, parameters):
    """Needs an attribute the model never retained: admission is refused with the missing field."""
    context.emit("included_jobs", Number(len(included(context)), unit="jobs"))
'''

PLOTS = '''"""Figures through the optional adapters (scenarios 3 and 8)."""
import tempfile
from pathlib import Path
from simulatte.analysis import analysis
from simulatte.analysis.adapters import save_figure
from simulatte.analysis.outputs import Figure, Number
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
'''

SLOW_ANALYSIS = """import time
from simulatte.analysis import analysis
from simulatte.analysis.outputs import Number
from simulatte.model import ParameterSchema


@analysis(parameters=ParameterSchema(()))
def slow(context, parameters):
    time.sleep({seconds})
    context.emit("runs", Number(len(list(context.scalars())), unit="runs"))
"""

ANALYSES = {
    "lateness": ("e64ca951-1b69-47a0-b1c5-7cfb91d90755", "Lateness by population", "report.py:lateness"),
    "lateness_complete": (
        "1f7a2c1e-5b7e-4a3d-9c0e-2d4f6a8b0c1d",
        "Lateness (complete inputs only)",
        "report.py:lateness_complete",
    ),
    "subset": ("9a1e2f0c-7c1d-4b8e-9a52-3c0c3f0e6d21", "Post-run subset", "report.py:subset"),
    "subset_priority": ("5c0d9e8f-1a2b-4c3d-8e9f-0a1b2c3d4e5f", "Subset by priority", "report.py:subset_priority"),
    "plots": ("093f63ba-8f3b-496c-a2a4-f8a6d6a39123", "Figures", "plots.py:plots"),
    "slow": ("7e6d5c4b-3a29-4817-8f6e-5d4c3b2a1908", "Slow analysis", "slow.py:slow"),
}


def new_project(
    work: Path, name: str, *, model: str = STUDY_MODEL, analyses=(), files=None, interpreter: Path = PYTHON
):
    """A production starter project with the given model, extra files, registered analyses and a selected interpreter."""
    from simulatte.projects import StudioTarget, install_starter, starter_files
    from simulatte.projects.service import ProjectService

    root = work / name
    root.mkdir(parents=True)
    install_starter(root, starter_files("production"))
    (root / "models/production.py").write_text(model)
    files = dict(files or {})
    for key in analyses:
        source = ANALYSES[key][2].split(":")[0]
        if source not in files:
            files[source] = {"report.py": REPORT, "plots.py": PLOTS}[source]
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    toml = (root / "pyproject.toml").read_text()
    include = ", ".join(['"models/*.py"', '"*.experiment.json"', *(f'"{p}"' for p in files)])
    toml = toml.replace('include = ["models/*.py"]', f"include = [{include}]")
    for key in analyses:
        ident, label, entrypoint = ANALYSES[key]
        toml += f'\n[tool.simulatte.analyses.{key}]\nid = "{ident}"\nname = "{label}"\nentrypoint = "{entrypoint}"\n'
    (root / "pyproject.toml").write_text(toml)
    service = ProjectService.open(StudioTarget(root, None))
    try:
        check(not service.read_only, f"new project opened read-only: {service.error}")
        pool = service.environments
        pool.activate(pool.use_existing(Path(interpreter)).id)
        model_id = service.workspace.models[0].id
    finally:
        service.close()
    return root, model_id


@contextlib.contextmanager
def workspace(root: Path):
    from simulatte.projects import StudioTarget
    from simulatte.projects.service import ProjectService

    service = ProjectService.open(StudioTarget(root, None))
    try:
        check(not service.read_only, f"project {root.name} opened read-only: {service.error}")
        yield service, service.ensure_coordinator()
    finally:
        service.close()


def plan_selection(prepared, name="outcomes"):
    from simulatte.analysis import codec

    model = next(m for m in prepared.metadata["models"] if m["name"] == prepared.metadata["selected"])
    plan = next(p for p in model["measurement_plans"] if p["name"] == name)
    return {"schema": 1, "plan": plan["name"], "selection": codec.to_json({}), "definition_hash": codec.identity(plan)}


def experiment_file(root: Path, name: str, *, count: int, base_seed=0, grid=None, parameters=None, **extra):
    data = {
        "schema": 1,
        "name": name,
        "model": "models/production.py:build",
        "replications": {"count": count, "base_seed": base_seed},
        "configurations": {"grid": grid or {"servers": [3]}},
        "level": "kpi",
        **extra,
    }
    path = root / f"{name}.experiment.json"
    path.write_text(json.dumps(data, indent=1))
    return path


def run_experiment(co, model_id, path, *, timeout=600):
    from simulatte.experiments import load_experiment

    run = co.start_experiment(load_experiment(path), model_id=model_id)
    outcome = co.wait_experiment(run, timeout=timeout)
    return run, outcome


def analysis_tools(store, environments):
    from simulatte.analysis.command import prepare_analysis
    from simulatte.analysis.execution import AnalysisRunner
    from simulatte.analysis.store import AnalysisStore

    return prepare_analysis, AnalysisRunner, AnalysisStore(store)


def run_analysis(store, environments, key, selection, *, parameters=None, name=None, timeout=300):
    prepare_analysis, AnalysisRunner, analysis = analysis_tools(store, environments)
    definition, generation = prepare_analysis(store, key, environments=environments)
    preview = analysis.preview(selection)
    item = analysis.admit(preview.id, definition_id=definition, parameters=parameters or {}, generation_id=generation)
    if name:
        analysis.rename(item.id, name)
    with AnalysisRunner(store, environments=environments) as runner:
        runner.submit(item.id)
        state = runner.wait(item.id, timeout=timeout).state
    return item, preview, state


def outputs_digest(store, execution_id) -> dict:
    """Output names, kinds and artifact hashes of a succeeded execution, plus the files on disk."""
    from simulatte.analysis import codec
    from simulatte.analysis.store import AnalysisStore

    manifest = unrecord(json.loads(codec.dumps(AnalysisStore(store).output(execution_id))))
    digest = {}
    for output in manifest["outputs"]:
        digest[output["name"]] = {
            "kind": output["kind"],
            "artifacts": sorted((a["path"], a["sha256"]) for a in output["artifacts"]),
            "metadata": {k: output["metadata"].get(k) for k in ("planned", "available", "partial_snapshot", "unit")},
        }
    files = tree(store.root / "analysis-results" / execution_id)
    return {"outputs": digest, "files": files}


def number_value(store, execution_id, name):
    from simulatte.analysis import codec
    from simulatte.analysis.store import AnalysisStore

    manifest = unrecord(json.loads(codec.dumps(AnalysisStore(store).output(execution_id))))
    output = next(o for o in manifest["outputs"] if o["name"] == name)
    folder = store.root / "analysis-results" / execution_id
    for artifact in output["artifacts"]:
        candidates = list(folder.rglob(artifact["path"].split("/")[-1]))
        for candidate in candidates:
            if sha(candidate) == artifact["sha256"]:
                return codec.from_json(json.loads(candidate.read_text()))
    raise Failure(f"artifact of {name} not found")


def throwaway_venv(path: Path, wheel: Path) -> Path:
    """A second, runnable interpreter with the wheel installed; the caller removes the folder later."""
    subprocess.run(["uv", "venv", "-q", "--python", "3.14", str(path)], check=True)
    subprocess.run(
        ["uv", "pip", "install", "-q", "--python", str(path / "bin/python"), f"simulatte[studio] @ {wheel.as_uri()}"],
        check=True,
    )
    return path / "bin/python"


def until(predicate, timeout=60, what="condition"):
    deadline = time.monotonic() + timeout
    while not predicate():
        check(time.monotonic() < deadline, f"timed out waiting for {what}")
        time.sleep(0.05)


def launch(argv, *, cwd, env=None, timeout=60):
    """Start a Studio or viewer process and wait for its one-use link; returns (process, link, output)."""
    process = subprocess.Popen(
        [str(a) for a in argv],
        cwd=str(cwd),
        env={**os.environ, **(env or {}), "PYTHONUNBUFFERED": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines: list[str] = []
    found: list[str] = []

    def reader():
        assert process.stdout is not None
        for line in process.stdout:
            lines.append(line)
            match = LINK.search(line)
            if match:
                found.append(match.group(0))

    threading.Thread(target=reader, daemon=True).start()
    deadline = time.monotonic() + timeout
    while not found and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    return process, (found[0] if found else None), lines


def stop(process):
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    return process.returncode


def redact(lines):
    return [LINK.sub("http://127.0.0.1:<port>/bootstrap?token=[redacted]", line.rstrip()) for line in lines][-30:]


# --------------------------------------------------------------------------- scenarios


def scenario_1(ev: Evidence, work: Path, args):
    """Launcher references: missing folder, no folder argument, legacy MODEL.py[:name] with flags, invalid references."""
    studio = [SIMULATTE, "studio"]
    missing = work / "fresh/study"
    process, link, out = launch([*studio, missing, "--no-browser", "--port", "0"], cwd=work)
    code = stop(process)
    ev.note(
        "missing folder is created and served",
        link=bool(link),
        exit=code,
        created=missing.is_dir(),
        entries=sorted(p.name for p in missing.iterdir()) if missing.is_dir() else None,
        output=redact(out),
    )
    check(link is not None and missing.is_dir(), "launcher did not create and serve the missing folder")

    root, model_id = new_project(work, "existing", model=SLOW_MODEL.format(delay=0))
    before = dump(root)
    process, link, out = launch([*studio, "--no-browser", "--port", "0"], cwd=root)
    code = stop(process)
    ev.note(
        "no folder argument opens the current folder",
        link=bool(link),
        exit=code,
        store_unchanged=dump(root) == before,
        output=redact(out),
    )
    check(link is not None, "launcher did not serve the current folder")
    check(dump(root)["attempts"] == before["attempts"], "opening a project started a run")

    legacy = work / "legacy"
    legacy.mkdir()
    (legacy / "plant.py").write_text('raise RuntimeError("legacy model must not be imported on open")\n')
    (legacy / "plant.txt").write_text("an existing file that is neither a folder nor a Python model\n")
    for reference, flags in (
        ("plant.py", []),
        ("plant.py:alternate", ["--seed", "5", "--horizon", "3", "--level", "kpi"]),
    ):
        process, link, out = launch([*studio, reference, *flags, "--no-browser", "--port", "0"], cwd=legacy)
        code = stop(process)
        attempts = tables(legacy, ("attempts",))["attempts"] if (legacy / ".simulatte/index.sqlite").exists() else []
        ev.note(
            "legacy reference served without execution",
            reference=reference,
            flags=flags,
            link=bool(link),
            exit=code,
            attempts=len(attempts),
            imported="imported" in "".join(out),
            output=redact(out),
        )
        check(link is not None, f"legacy reference {reference} was not served")
        check(not attempts, f"legacy reference {reference} executed on open")
        check("legacy model must not be imported" not in "".join(out), "legacy model was imported on open")

    entries = sorted(p.name for p in legacy.iterdir())
    for reference, message in (
        ("missing.py:build", "does not exist"),
        ("plant.py:1bad", "Invalid Python entrypoint"),
        ("nosuch/folder/model.py", "Python model file does not exist"),
        ("plant.txt", "Expected a directory or Python model file"),
    ):
        done = ev.command([*studio, reference, "--no-browser", "--port", "0"], cwd=legacy, expect=None, timeout=60)
        after = sorted(p.name for p in legacy.iterdir())
        ev.note(
            "invalid reference refused",
            reference=reference,
            exit=done.returncode,
            stderr=done.stderr[-300:],
            entries_unchanged=after == entries,
        )
        check(done.returncode != 0, f"invalid reference {reference} was accepted")
        check(after == entries, f"invalid reference {reference} created entries: {after}")
        check(message in done.stderr, f"refusal for {reference} does not name the problem: {done.stderr[-300:]}")


def two_models(work: Path, name: str, delay_a: float):
    from simulatte.projects import ModelRegistration, StudioTarget, WorkspaceDefinition, apply_config, preview_config
    from simulatte.projects.service import ProjectService

    root = work / name
    root.mkdir(parents=True)
    (root / "a.py").write_text(SLOW_MODEL.format(delay=delay_a))
    (root / "b.py").write_text(SLOW_MODEL.format(delay=0))
    models = tuple(
        ModelRegistration(str(uuid4()), key, key.upper(), f"{key}.py:build", f"{key}.json") for key in ("a", "b")
    )
    apply_config(preview_config(root, WorkspaceDefinition(root, str(uuid4()), models, (), None)))
    # Non-Git projects must declare their sources (docs/studio/projects.md); the Studio registration
    # form adds them through its ``include`` field, the bare config preview does not.
    with (root / "pyproject.toml").open("a") as toml:
        toml.write('\n[tool.simulatte.bundle]\ninclude = ["a.py", "b.py"]\n')
    service = ProjectService.open(StudioTarget(root, None))
    try:
        pool = service.environments
        pool.activate(pool.use_existing(PYTHON).id)
    finally:
        service.close()
    return root, models[0].id, models[1].id


ACTIVE = ("queued", "preparing", "ready", "running", "finalizing")


def scenario_2(ev: Evidence, work: Path, args):
    """Ownership of the interactive slot between two models, through the installed coordinator."""
    from simulatte.execution.project_store import ProjectStore

    root, a, b = two_models(work, "ownership", 12.0)
    with workspace(root) as (service, co):
        store = co.store
        pa = co.prepare_model(a, timeout=90)
        ra = co.make_request(pa)
        first = co.admit_run(a, ra, pa.revision)
        check(first.status == "started", f"A admission {first.status}")
        until(lambda: store.attempt(first.key)["status"] == "running", 60, "A running")
        pb = co.prepare_model(b, timeout=90)  # inspecting uninspected B while A runs
        rb = co.make_request(pb, seed=44)
        blocked = co.admit_run(b, rb, pb.revision)
        ev.note(
            "B blocked while A runs",
            status=blocked.status,
            owner_is_a=getattr(blocked, "owner", None) == first.key,
            a_status=store.attempt(first.key)["status"],
        )
        check(blocked.status == "busy", "B was admitted over A")
        token = co.enable_auto(b)
        auto = co.admit_run(b, rb, pb.revision, auto_session=token)
        co.disable_auto(b)
        stale = co.admit_run(b, rb, pb.revision, auto_session=token)
        ev.note("auto-run cannot take another owner's slot", auto=auto.status, after_disable=stale.status)
        check((auto.status, stale.status) == ("busy", "stale"), "auto-run admission over A")
        (root / "a.py").write_text(SLOW_MODEL.format(delay=12.0).replace("value: int = 1", "value: int = 2"))
        (root / "b.py").write_text(SLOW_MODEL.format(delay=0).replace("value: int = 1", "value: int = 3"))
        time.sleep(0.5)
        ev.note("sources edited while A runs", a_status=store.attempt(first.key)["status"])
        check(store.attempt(first.key)["status"] in ACTIVE, "editing sources interrupted A")
        original_cancel = co.cancel
        co.cancel = lambda owner: False  # the slot's child does not answer the stop request
        try:
            refused = co.stop_and_run(first.key, b, rb, pb.revision)
        finally:
            del co.cancel
        ev.note("cancellation failure keeps A", status=refused.status, a_status=store.attempt(first.key)["status"])
        check(
            refused.status == "busy" and store.attempt(first.key)["status"] in ACTIVE, "failed cancellation replaced A"
        )
        check(co.cancel == original_cancel, "cancel restored")
        done = co.wait(first.key, timeout=90)
        result = store.result(first.key.attempt_id)
        ev.note(
            "A publishes its captured result",
            attempt=first.key.attempt_id,
            status=store.attempt(first.key)["status"],
            manifest_level=result.manifest["request"]["level"],
            published_value=result.manifest["request"]["parameters"],
        )
        check(
            done.key == first.key and result.manifest["request"]["parameters"]["value"] == 1,
            "A did not publish its captured inputs",
        )
        pa2 = co.prepare_model(a, timeout=90)
        from simulatte.execution.store import RevisionConflict

        stale_a = co.admit_run(a, ra, pa.revision)
        try:
            co.make_request(pa)
            remade = "allowed"
        except RevisionConflict as error:
            remade = f"RevisionConflict: {error}"
        ev.note(
            "edited source makes the old draft stale",
            status=stale_a.status,
            new_request_from_old_preparation=remade,
            revisions=(pa.revision.number, pa2.revision.number),
        )
        check(stale_a.status == "stale", "old A request admitted at a new revision")
        check(remade != "allowed", "a request was built from an obsolete preparation")
        second = co.admit_run(a, co.make_request(pa2), pa2.revision)
        until(lambda: store.attempt(second.key)["status"] == "running", 60, "A running again")
        pb2 = co.prepare_model(b, timeout=90)
        rb2 = co.make_request(pb2, seed=44)
        replaced = co.stop_and_run(second.key, b, rb2, pb2.revision)
        check(replaced.status == "started", f"explicit stop and run: {replaced.status}")
        co.wait(replaced.key, timeout=90)
        ev.note(
            "explicit stop and run",
            a_status=store.attempt(second.key)["status"],
            b_status=store.attempt(replaced.key)["status"],
        )
        check(
            store.attempt(second.key)["status"] == "cancelled" and store.attempt(replaced.key)["status"] == "succeeded",
            "stop and run outcome",
        )
        with contextlib.suppress(KeyError):
            store.result(second.key.attempt_id)
            raise Failure("cancelled A kept a result")
        # Same-model auto-run: a slow B attempt owned by the session is replaced by the session's
        # next admission; a different session cannot take it.
        slow_b = co.make_request(pb2, seed=45, parameters={"value": 3, "delay": 8.0})
        owner_after_wait = co._owner()
        released = time.monotonic()
        until(lambda: co._owner() is None, 30, "interactive slot released after wait()")
        ev.note(
            "slot owner right after wait() returned",
            owner=None if owner_after_wait is None else owner_after_wait.attempt_id,
            released_after_s=round(time.monotonic() - released, 3),
        )
        token = co.enable_auto(b)
        one = co.admit_run(b, slow_b, pb2.revision, auto_session=token)
        check(one.status == "started", f"first auto admission {one.status}")
        until(lambda: store.attempt(one.key)["status"] == "running", 60, "auto B running")
        two = co.admit_run(b, slow_b, pb2.revision, auto_session=token)
        check(two.status == "started", f"same-session auto admission over its own attempt: {two.status}")
        until(lambda: store.attempt(two.key)["status"] == "running", 60, "second auto B running")
        co.disable_auto(b)
        other = co.admit_run(b, slow_b, pb2.revision, auto_session=co.enable_auto(b))
        co.wait(two.key, timeout=90)
        co.disable_auto(b)
        ev.note(
            "same-model auto-run ownership",
            first=one.status,
            second=two.status,
            distinct=one.key != two.key,
            first_status=store.attempt(one.key)["status"],
            second_status=store.attempt(two.key)["status"],
            new_session_over_old_session=other.status,
            new_session_owner_is_second=getattr(other, "owner", None) == two.key,
        )
        check(
            one.key != two.key and store.attempt(one.key)["status"] == "cancelled",
            "auto session did not replace its own attempt",
        )
        check(store.attempt(two.key)["status"] == "succeeded", "replacing auto attempt did not publish")
        check(other.status == "busy", f"a new auto session took the old session's slot: {other.status}")
        history = {
            key: [(i["id"], i["status"]) for i in ProjectStore(store).history(model, after=None).items]
            for key, model in (("A", a), ("B", b))
        }
        ev.note("history of both models", **{k: v for k, v in history.items()})
        check(
            any(s == "succeeded" for _, s in history["A"]) and any(s == "succeeded" for _, s in history["B"]),
            "both models' results not found",
        )


def scenario_3(ev: Evidence, work: Path, args):
    """Environment update during active work, reconstruction availability, missing plot packages, broken model."""
    from simulatte.analysis.selection import Selection
    from simulatte.analysis.store import AnalysisStore
    from simulatte.projects import StudioTarget
    from simulatte.projects.service import ProjectService

    plot_python = Path(args.plot_python).absolute() if args.plot_python else None
    wheel = Path(args.wheel).resolve() if args.wheel else None
    check(wheel is not None and wheel.is_file(), "--wheel is required for the vanished interpreters of scenario 3")
    root, model_id = new_project(
        work,
        "environments",
        model=SLOW_MODEL.format(delay=8.0),
        analyses=("slow",),
        files={"slow.py": SLOW_ANALYSIS.format(seconds=6)},
    )
    with workspace(root) as (service, co):
        pool, store = service.environments, co.store
        selected = pool.selected
        evidence_before = plain(selected.evidence)
        other = pool.use_existing(plot_python or PYTHON.with_name("python3"))
        prepared = co.prepare_model(model_id, timeout=90)
        run = co.admit_run(model_id, co.make_request(prepared), prepared.revision)
        until(lambda: store.attempt(run.key)["status"] == "running", 60, "run running")
        during_run = service.select_environment({"id": other.id})
        pool_answer = pool.select(other.id)
        ev.note(
            "environment update during an active simulation",
            answer=during_run,
            pool=pool_answer,
            owners=pool.owners(selected.id),
            selected_unchanged=pool.selected.id == selected.id,
            evidence_unchanged=plain(pool.get(selected.id).evidence) == evidence_before,
        )
        check(during_run.get("status") == "busy" and pool_answer == "busy", "selection changed under an active run")
        check(run.key.attempt_id in pool.owners(selected.id), "the run does not pin its generation")
        try:
            service.prepare_environment({"existing": str(work / "nowhere/bin/python")})
            raise Failure("missing interpreter accepted")
        except ValueError as error:
            ev.note(
                "failed update preserves the selection",
                error=str(error)[:200],
                selected=pool.selected.id == selected.id,
            )
        co.wait(run.key, timeout=90)
        prepare_analysis, AnalysisRunner, analysis = analysis_tools(store, pool)
        definition, generation = prepare_analysis(store, "slow", environments=pool)
        item = analysis.admit(
            analysis.preview(Selection(run_ids=(run.key.attempt_id,))).id,
            definition_id=definition,
            parameters={},
            generation_id=generation,
        )
        with AnalysisRunner(store, environments=pool) as runner:
            runner.submit(item.id)
            until(
                lambda: analysis.get(item.id)["execution"]["state"] in ("running", "finalizing"), 60, "analysis running"
            )
            during_analysis = pool.select(other.id)
            owners = pool.owners(selected.id)
            state = runner.wait(item.id, timeout=120).state
        ev.note("environment update during an active analysis", pool=during_analysis, owners=owners, final=state)
        check(during_analysis == "busy" and owners, "selection changed under an active analysis")
        check(state == "succeeded", f"analysis {state}")
        until(lambda: not pool.owners(selected.id), 30, "owners released")
        idle = service.select_environment({"id": other.id})
        ev.note(
            "activation requiring restart waits",
            answer=idle,
            selected=pool.selected.id == selected.id,
            restart_pending=service.restart_generation,
        )
        check(
            idle.get("status") == "restart_required" and pool.selected.id == selected.id,
            "generation activated without a restart",
        )
        try:
            co.prepare_model(model_id, timeout=30)
            prepared_after = "allowed"
        except Exception as error:
            prepared_after = str(error)[:120]
        ev.note("new work after a pending restart", outcome=prepared_after)
        # Reconstruction availability.
        restored = pool.reconstruct(plain(selected.evidence))
        gone = work / "gone"
        vanished = pool.use_existing(throwaway_venv(gone, wheel))
        shutil.rmtree(gone)
        rows_before = len(tables(root, ("environment_generations",))["environment_generations"])
        try:
            pool.reconstruct(plain(vanished.evidence))
            raise Failure("reconstruction of a vanished interpreter succeeded")
        except ValueError as error:
            vanished_message = str(error)
        foreign = plain(selected.evidence)
        foreign = {
            "spec": {
                "kind": "managed",
                "runtime_format": "installation-v1",
                "files": {"pyproject.toml": b"[project]\nname='x'\n".hex(), "uv.lock": b"version = 1\n".hex()},
                "python": ">=3.11",
                "platform": "foreign-os",
                "architecture": "foreign-cpu",
                "implementation": "cpython",
                "provisioning": ["sync", "--locked"],
            },
            "probe": foreign["probe"],
        }
        try:
            pool.reconstruct(foreign)
            raise Failure("foreign platform reconstructed")
        except ValueError as error:
            foreign_message = str(error)
        rows_after = len(tables(root, ("environment_generations",))["environment_generations"])
        ev.note(
            "historical environment reconstruction",
            available=restored.id == selected.id,
            vanished=vanished_message[:200],
            foreign=foreign_message[:200],
            generation_rows=(rows_before, rows_after),
            selected=pool.selected.id == selected.id,
            vanished_evidence_kept=plain(pool.get(vanished.id).evidence) == plain(vanished.evidence),
        )
        check(
            "reconstruction unavailable" in vanished_message and "reconstruction unavailable" in foreign_message,
            "unavailable reconstruction not reported",
        )
        check(
            rows_after == rows_before and pool.selected.id == selected.id,
            "unavailable reconstruction substituted something",
        )
    # Missing optional plot packages: figures produced by an interpreter that has them, read by one that lacks them.
    if plot_python is None:
        ev.note("plot packages", skipped="--plot-python not given")
        return
    plots_root, plots_model = new_project(
        work, "plots", model=STUDY_MODEL, analyses=("plots",), interpreter=plot_python
    )
    child = ev.command([plot_python, "-I", SCRIPT, "--child", "produce-plots", plots_root], timeout=600)
    produced = json.loads(child.stdout.strip().splitlines()[-1])
    check(produced["state"] == "succeeded", f"plot analysis in the plot interpreter: {produced}")
    service = ProjectService.open(StudioTarget(plots_root, None))
    try:
        check(
            service.read_only and "launcher handoff" in (service.error or ""),
            f"bare interpreter opened a plot-interpreter project writable: {service.error}",
        )
        ev.note("project pinned to another interpreter needs the launcher", error=service.error)
    finally:
        service.close()
    ev.command([plot_python, "-I", SCRIPT, "--child", "select-interpreter", plots_root, PYTHON], timeout=120)
    with workspace(plots_root) as (service, co):
        store, pool = co.store, service.environments
        saved = AnalysisStore(store).get(produced["execution"])
        digest = outputs_digest(store, produced["execution"])
        verified = all(
            sha(store.root / "analysis-results" / produced["execution"] / path) == digest["files"][path]
            for path in digest["files"]
        )
        ev.note(
            "saved figures readable without plot packages",
            state=saved["execution"]["state"],
            outputs={k: v["kind"] for k, v in digest["outputs"].items()},
            files=list(digest["files"]),
            verified=verified,
        )
        check(
            saved["execution"]["state"] == "succeeded"
            and {"matplotlib_png", "seaborn_svg", "plotly_figure"} <= set(digest["outputs"]),
            "saved figure outputs missing",
        )
        item, preview, state = run_analysis(store, pool, "plots", Selection(run_ids=(produced["attempt"],)))
        details = AnalysisStore(store).get(item.id)
        diagnostics = json.dumps(
            plain(details["execution"].get("details") or details.get("details") or details), default=str
        )
        ev.note(
            "rerun without plot packages fails with a diagnostic",
            state=state,
            mentions_matplotlib="matplotlib" in diagnostics,
            diagnostics=diagnostics[:600],
        )
        check(state == "failed" and "matplotlib" in diagnostics, "missing package not diagnosed")
        check(
            AnalysisStore(store).get(produced["execution"])["execution"]["state"] == "succeeded", "earlier outputs lost"
        )
        (plots_root / "models/production.py").write_text('raise RuntimeError("broken on purpose")\n')
        try:
            co.prepare_model(plots_model, timeout=60)
            broken = "prepared"
        except Exception as error:
            broken = str(error)[:300]
        result = store.result(produced["attempt"])
        ev.note(
            "broken model keeps saved results readable",
            preparation=broken,
            result_level=result.manifest["request"]["level"],
            kpis=sorted(result.manifest.get("kpis", {}) or []),
        )
        check("broken on purpose" in broken, "broken model preparation did not name the error")
    # Launcher recovery viewer when the selected interpreter disappears.
    gone = work / "gone-selected"
    with workspace(plots_root) as (service, co):
        pool = service.environments
        vanished = pool.use_existing(throwaway_venv(gone, wheel))
        answer = service.select_environment({"id": vanished.id})
        ev.note("select a second interpreter that will be removed", answer=answer)
    ev.command([PYTHON, "-I", SCRIPT, "--child", "activate", plots_root, vanished.id], timeout=60)
    shutil.rmtree(gone)
    service = ProjectService.open(StudioTarget(plots_root, None))
    try:
        ev.note(
            "recovery when the selected interpreter is gone",
            read_only=service.read_only,
            error=service.error,
            state=sorted(service.state()) if service.workspace else None,
        )
        check(service.read_only and "unavailable" in (service.error or ""), "missing interpreter did not open recovery")
    finally:
        service.close()
    process, link_url, out = launch([SIMULATTE, "studio", plots_root, "--no-browser", "--port", "0"], cwd=plots_root)
    code = stop(process)
    ev.note("launcher serves the recovery viewer", link=bool(link_url), exit=code, output=redact(out))
    check(link_url is not None, "launcher did not serve recovery")


def scenario_8(ev: Evidence, work: Path, args):
    """Python and CLI paths of one analysis, eight of 24 then resume, rerun/refresh, complete-input refusal."""
    from simulatte.analysis.selection import Selection
    from simulatte.analysis.store import AnalysisStore, InsufficientInput

    root, model_id = new_project(work, "study", analyses=("lateness", "lateness_complete", "subset", "subset_priority"))
    gate = work / "study-gate"
    with workspace(root) as (service, co):
        store, pool = co.store, service.environments
        prepared = co.prepare_model(model_id, timeout=90)
        selection = plan_selection(prepared)
        path = experiment_file(
            root,
            "lateness-study",
            count=24,
            base_seed=0,
            grid={"servers": [3]},
            measurements=selection,
            stopping={"type": "horizon", "horizon": 60.0, "warmup": 10.0},
        )
        data = json.loads(path.read_text())
        data["configurations"] = {
            "explicit": [
                {"label": "gated", "parameters": {"servers": 3, "fail_seed": 5, "gate": str(gate), "gate_from": 9}}
            ]
        }
        path.write_text(json.dumps(data, indent=1))
        from simulatte.experiments import load_experiment

        run = co.start_experiment(load_experiment(path), model_id=model_id)
        until(
            lambda: (
                len(store.replications(run, status="succeeded")) >= 8
                and len(store.replications(run, status="failed")) == 1
            ),
            300,
            "eight succeeded and one failed replication",
        )
        time.sleep(1.0)
        statuses = sorted(r["status"] for r in store.replications(run))
        preview_tools = analysis_tools(store, pool)
        analysis = preview_tools[2]
        partial_preview = analysis.preview(Selection(experiment_run_ids=(run,)))
        ev.note(
            "eight of 24 after the gate",
            statuses={s: statuses.count(s) for s in set(statuses)},
            available=partial_preview.available,
            planned=partial_preview.planned,
            partial=partial_preview.partial,
        )
        check(
            (partial_preview.available, partial_preview.planned, partial_preview.partial) == (8, 24, True),
            "partial preview counts",
        )
        item, preview, state = run_analysis(
            store, pool, "lateness", Selection(experiment_run_ids=(run,)), name="Eight of 24"
        )
        check(state == "succeeded", f"partial analysis {state}: {analysis.get(item.id)}")
        original = outputs_digest(store, item.id)
        snapshot = plain(analysis.snapshot(item.snapshot_hash))
        replication_statuses = sorted(r["status"] for r in snapshot["replications"])
        ev.note(
            "partial analysis saved",
            execution=item.id,
            snapshot=item.snapshot_hash,
            snapshot_statuses={s: replication_statuses.count(s) for s in set(replication_statuses)},
            outputs={k: v["artifacts"] for k, v in original["outputs"].items()},
            included_jobs=number_value(store, item.id, "included_jobs"),
        )
        check(
            "failed" in replication_statuses and "pending" in replication_statuses,
            "missing/failed statuses absent from the snapshot",
        )
    # Studio stops while the gated replications hang: they become pending again and the run pauses.
    gate.write_text("open")
    with workspace(root) as (service, co):
        store, pool = co.store, service.environments
        analysis = AnalysisStore(store)
        paused = store.experiment_run(run)["status"]
        co.resume_experiment(run)
        outcome = co.wait_experiment(run, timeout=600)
        statuses = sorted(r["status"] for r in store.replications(run))
        ev.note(
            "resumed to 24",
            paused_status=paused,
            outcome=outcome["status"],
            statuses={s: statuses.count(s) for s in set(statuses)},
        )
        check(
            statuses.count("succeeded") == 23 and statuses.count("failed") == 1, f"resume did not reach 24: {statuses}"
        )
        after = outputs_digest(store, item.id)
        saved = analysis.get(item.id)
        ev.note(
            "original snapshot unchanged after resume",
            same_outputs=after == original,
            same_snapshot=plain(analysis.snapshot(item.snapshot_hash)) == snapshot,
            counts=(saved["snapshot"]["available"], saved["snapshot"]["planned"], saved["snapshot"]["partial"]),
        )
        check(
            after == original and plain(analysis.snapshot(item.snapshot_hash)) == snapshot,
            "original partial analysis changed",
        )
        check((saved["snapshot"]["available"], saved["snapshot"]["planned"]) == (8, 24), "original counts changed")
        rerun = analysis.rerun(item.id)
        prepare_analysis, AnalysisRunner, _ = analysis_tools(store, pool)
        with AnalysisRunner(store, environments=pool) as runner:
            runner.submit(rerun.id)
            rerun_state = runner.wait(rerun.id, timeout=300).state
        recomputed = outputs_digest(store, rerun.id)
        ev.note(
            "recompute on the original inputs",
            execution=rerun.id,
            state=rerun_state,
            same_snapshot=rerun.snapshot_hash == item.snapshot_hash,
            same_outputs=recomputed["outputs"] == original["outputs"],
        )
        check(
            rerun_state == "succeeded"
            and rerun.snapshot_hash == item.snapshot_hash
            and recomputed["outputs"] == original["outputs"],
            "rerun differs from the original",
        )
        refreshed_preview = analysis.refresh(item.id)
        refreshed = analysis.admit(
            refreshed_preview.id, definition_id=rerun.definition_id, parameters={}, generation_id=rerun.generation_id
        )
        with AnalysisRunner(store, environments=pool) as runner:
            runner.submit(refreshed.id)
            refreshed_state = runner.wait(refreshed.id, timeout=300).state
        fresh = outputs_digest(store, refreshed.id)
        ev.note(
            "explicit refresh to a new snapshot",
            execution=refreshed.id,
            state=refreshed_state,
            preview=(refreshed_preview.available, refreshed_preview.planned, refreshed_preview.partial),
            new_snapshot=refreshed.snapshot_hash != item.snapshot_hash,
            outputs_differ=fresh["outputs"] != original["outputs"],
            included_jobs=number_value(store, refreshed.id, "included_jobs"),
            original_still=outputs_digest(store, item.id) == original,
        )
        check(
            refreshed_state == "succeeded" and refreshed.snapshot_hash != item.snapshot_hash,
            "refresh did not create a new snapshot",
        )
        check(
            (refreshed_preview.available, refreshed_preview.planned, refreshed_preview.partial) == (23, 24, True),
            "refreshed counts",
        )
        check(outputs_digest(store, item.id) == original, "refresh changed the original")
        definition, generation = prepare_analysis(store, "lateness_complete", environments=pool)
        try:
            analysis.admit(
                analysis.preview(Selection(experiment_run_ids=(run,))).id,
                definition_id=definition,
                parameters={},
                generation_id=generation,
            )
            raise Failure("incomplete inputs admitted by a complete-only definition")
        except InsufficientInput as error:
            ev.note("definition rejecting incomplete inputs", message=str(error), details=plain(error.details))
        subset_item, _, subset_state = run_analysis(
            store, pool, "subset", Selection(experiment_run_ids=(run,)), parameters={"family": "A", "late_after": 0.0}
        )
        ev.note(
            "post-run subset from retained attributes",
            state=subset_state,
            outputs=list(outputs_digest(store, subset_item.id)["outputs"]),
        )
        check(subset_state == "succeeded", f"subset analysis {subset_state}: {analysis.get(subset_item.id)}")
        definition, generation = prepare_analysis(store, "subset_priority", environments=pool)
        try:
            analysis.admit(
                analysis.preview(Selection(experiment_run_ids=(run,))).id,
                definition_id=definition,
                parameters={},
                generation_id=generation,
            )
            raise Failure("unretained field admitted")
        except InsufficientInput as error:
            ev.note(
                "unavailable field refused with the missing data named",
                message=str(error),
                details=plain(error.details),
            )
            check("priority" in json.dumps(plain(error.details)), "missing field not named")
    # The CLI path over the same evidence produces the same snapshot and artifacts as the API refresh.
    done = ev.command(
        [SIMULATTE, "analysis", "run", "lateness", "--project", root, "--experiment", run, "--json"],
        cwd=work,
        timeout=600,
    )
    cli = json.loads(done.stdout)
    cli_id = (
        cli.get("execution", cli).get("id")
        if isinstance(cli.get("execution", None), dict)
        else cli.get("id") or cli.get("execution")
    )
    with workspace(root) as (service, co):
        store = co.store
        execution = AnalysisStore(store).get(cli_id)
        cli_digest = outputs_digest(store, cli_id)
        ev.note(
            "CLI and API agree",
            cli_execution=cli_id,
            same_snapshot=execution["execution"]["snapshot_hash"] == refreshed.snapshot_hash,
            same_outputs=cli_digest["outputs"] == fresh["outputs"],
            cli_state=execution["execution"]["state"],
        )
        check(
            execution["execution"]["snapshot_hash"] == refreshed.snapshot_hash
            and cli_digest["outputs"] == fresh["outputs"],
            "CLI outputs differ from the API outputs",
        )
    done = ev.command(
        [
            SIMULATTE,
            "analysis",
            "run",
            "lateness",
            "--project",
            root,
            "--experiment",
            run,
            "--require-complete",
            "--json",
        ],
        cwd=work,
        expect=2,
        timeout=120,
    )
    ev.note(
        "CLI refuses incomplete inputs with --require-complete",
        exit=done.returncode,
        stdout=done.stdout[-400:],
        stderr=done.stderr[-400:],
    )
    done = ev.command([SIMULATTE, "analysis", "show", cli_id, "--project", root, "--json"], cwd=work, timeout=60)
    ev.note("analysis show", keys=sorted(json.loads(done.stdout)))
    (work / "study-run.txt").write_text(
        json.dumps({"root": str(root), "run": run, "execution": item.id, "refreshed": refreshed.id})
    )


def scenario_9(ev: Evidence, work: Path, args):
    """Headless half: original-seed verified replay versus a labelled new-seed recording, study settings surviving."""
    from dataclasses import replace
    from simulatte.presentations.recording import preview_recording, record_presentation, recordings

    root, model_id = new_project(work, "presentation", analyses=("lateness",))
    with workspace(root) as (service, co):
        store = co.store
        prepared = co.prepare_model(model_id, timeout=90)
        model_default = next(m for m in prepared.metadata["models"] if m["name"] == prepared.metadata["selected"])[
            "stopping"
        ]
        selection = plan_selection(prepared)
        study = {"type": "horizon", "horizon": 60.0, "warmup": 10.0}
        path = experiment_file(
            root, "policy", count=2, base_seed=17, grid={"servers": [2, 3]}, measurements=selection, stopping=study
        )
        run, outcome = run_experiment(co, model_id, path, timeout=300)
        check(outcome["status"] == "completed", f"experiment {outcome}")
        replication = next(
            r for r in store.replications(run, status="succeeded") if r["configuration"] == 1 and r["index"] == 0
        )
        original = store.request(replication["request_id"])
        ev.note(
            "captured study differs from the model defaults",
            model_default=plain(model_default),
            study=plain(original.stopping),
            measurements=plain(original.measurements)["plan"],
            seed=original.seed,
            level=original.level,
        )
        check(
            plain(original.stopping) != plain(model_default),
            "study stopping equals the model default; the test is void",
        )
        preview = preview_recording(store, replication["attempt"], seed=original.seed)
        replayed = record_presentation(co, replication["attempt"], seed=original.seed, timeout=300)
        fresh = record_presentation(co, replication["attempt"], seed=original.seed + 100, timeout=300)
        for job, label in ((replayed, "replay"), (fresh, "new_seed")):
            target = store.request(job.key.request_id)
            ev.note(
                f"{label} recording",
                attempt=job.key.attempt_id,
                mode=job.mode,
                verification=None
                if job.verification is None
                else plain(job.verification.__dict__ if hasattr(job.verification, "__dict__") else job.verification),
                diff=[d["field"] for d in job.diff],
                stopping_survives=plain(target.stopping) == plain(original.stopping),
                measurements_survive=plain(target.measurements) == plain(original.measurements),
                level=target.level,
                seed=target.seed,
                trace=(store.result(job.key.attempt_id).path / "run.simtrace").is_file(),
            )
            check(job.mode == label, f"{label} labelled {job.mode}")
            check(
                plain(target.stopping) == plain(original.stopping)
                and plain(target.measurements) == plain(original.measurements),
                f"{label} lost the study settings",
            )
            check(
                target == replace(original, seed=target.seed, level="full"),
                f"{label} request is not the captured request at full level",
            )
        check(
            replayed.verification is not None and replayed.verification.verified is True,
            f"replay not verified: {replayed.verification}",
        )
        check(fresh.verification is None, "new seed carries a verification")
        check(
            preview["mode"] == "replay" and preview["target_request_id"] == replayed.key.request_id,
            "preview disagrees with the recording",
        )
        with store._transaction() as db:
            listed = recordings(db, original_attempt=replication["attempt"])
        ev.note(
            "recordings listed with their mode and verification",
            items=[{k: r[k] for k in ("attempt_id", "mode", "seed", "status", "verified")} for r in listed],
        )
        check({(r["mode"], r["verified"]) for r in listed} == {("replay", True), ("new_seed", None)}, "listing")


def scenario_10(ev: Evidence, work: Path, args):
    """Quiescent copy on this host, managed environment rebuilt from evidence, history intact, caches cleaned."""
    from simulatte.analysis.selection import Selection
    from simulatte.execution.store import RunStore
    from simulatte.execution.store_reader import StoreReader
    from simulatte.projects import StudioTarget, load_workspace
    from simulatte.projects.environments import EnvironmentPool
    from simulatte.projects.service import ProjectService

    wheel = Path(args.wheel).resolve() if args.wheel else None
    check(wheel is not None and wheel.is_file(), "--wheel is required for the managed environment of scenario 10")
    root, model_id = new_project(work, "portable", analyses=("lateness",))
    with workspace(root) as (service, co):
        store, pool = co.store, service.environments
        prepared = co.prepare_model(model_id, timeout=90)
        full = co.admit_run(model_id, co.make_request(prepared, level="full", seed=3), prepared.revision)
        co.wait(full.key, timeout=120)
        path = experiment_file(root, "portable", count=3, base_seed=0, measurements=plan_selection(prepared))
        run, outcome = run_experiment(co, model_id, path, timeout=300)
        check(outcome["status"] == "completed", f"experiment {outcome}")
        item, _, state = run_analysis(
            store, pool, "lateness", Selection(experiment_run_ids=(run,)), name="Portable analysis"
        )
        check(state == "succeeded", f"analysis {state}")
        ev.note("original study", full_run=full.key.attempt_id, experiment=run, analysis=item.id)
    # A managed generation built from the vendored wheel becomes the selected environment.
    (root / "vendor").mkdir()
    shutil.copyfile(wheel, root / "vendor" / wheel.name)
    toml = (root / "pyproject.toml").read_text()
    toml = toml.replace('"models/*.py"', '"models/*.py", "vendor/*.whl"', 1)
    toml += f'\n[tool.uv.sources]\nsimulatte = {{path = "vendor/{wheel.name}"}}\n'
    (root / "pyproject.toml").write_text(toml)
    with RunStore.open(root / ".simulatte") as store:
        pool = EnvironmentPool(root, store)
        definition = load_workspace(root)
        try:
            pool.prepare(definition)
            raise Failure("prepare without a lockfile did not ask for a preview")
        except Exception as preview:
            edit = getattr(preview, "edit", None)
            check(edit is not None, f"unexpected preparation error: {preview}")
            (root / "uv.lock").write_bytes(edit.proposed_bytes)
        started = time.monotonic()
        managed = pool.prepare(load_workspace(root))
        ev.note(
            "managed generation prepared",
            generation=managed.id,
            seconds=round(time.monotonic() - started, 1),
            interpreter=str(managed.interpreter),
            state=managed.state,
        )
        check(pool.select(managed.id) == "restart_required", "managed selection")
        pool.activate(managed.id)
        check(pool.selected.id == managed.id, "managed generation not selected")
    managed_simulatte = managed.interpreter.with_name("simulatte")
    done = ev.command(
        [
            managed_simulatte,
            "run",
            "models/production.py:build",
            "--project",
            root,
            "--seed",
            "9",
            "--level",
            "kpi",
            "--json",
        ],
        cwd=root,
        timeout=300,
    )
    managed_run = json.loads(done.stdout)
    ev.note(
        "run under the managed interpreter",
        output_keys=sorted(managed_run) if isinstance(managed_run, dict) else str(managed_run)[:200],
    )
    # Control: the same replay at the original location, before any copy.
    done = ev.command(
        [managed_simulatte, "replay", managed_run["request_id"], "--project", root, "--json"],
        cwd=root,
        timeout=300,
        expect=None,
    )
    ev.note("control: replay of the CLI run at the original location", exit=done.returncode, output=done.stdout[-400:])
    # A run admitted by the project coordinator (Studio's path) under the managed generation.
    done = ev.command([managed.interpreter, "-I", SCRIPT, "--child", "studio-run", root], timeout=300)
    studio_run = json.loads(done.stdout.strip().splitlines()[-1])
    ev.note("run through the project coordinator under the managed generation", **studio_run)
    done = ev.command(
        [managed_simulatte, "replay", studio_run["request_id"], "--project", root, "--json"],
        cwd=root,
        timeout=300,
        expect=None,
    )
    ev.note(
        "control: replay of the coordinator run at the original location",
        exit=done.returncode,
        output=done.stdout[-400:],
    )
    before_tables, before_files = dump(root), tree(root / ".simulatte/results")
    before_analysis = tree(root / ".simulatte/analysis-results")
    # Quiescent copy without the environment cache, as a user moving the folder would.
    copy = work / "portable-copy"
    shutil.copytree(root, copy, ignore=shutil.ignore_patterns("cache"))
    done = ev.command([SIMULATTE, "store", "check", "--project", copy, "--json"], cwd=work, timeout=120, expect=None)
    ev.note("storage.md step 3: store check verifies the copy", exit=done.returncode, output=done.stdout[-600:])
    check(done.returncode == 0, f"store check on the quiescent copy: {done.stdout[-400:]}")
    service = ProjectService.open(StudioTarget(copy, None))
    try:
        ev.note(
            "copy opens read-only until its environment is rebuilt", read_only=service.read_only, error=service.error
        )
        check(
            service.read_only and "Reconstruct" in (service.error or ""),
            f"copy without cache did not ask for reconstruction: {service.error}",
        )
    finally:
        service.close()
    with contextlib.closing(StoreReader.open(copy / ".simulatte")) as reader:
        evidence = next(g["evidence"] for g in reader.generations() if g["state"] == "selected")
        history = [(h["id"], h["status"]) for h in reader.history(limit=50).items]
    with RunStore.open(copy / ".simulatte") as store:
        started = time.monotonic()
        generation = EnvironmentPool(copy, store).reconstruct(evidence)
        ev.note(
            "reconstruction from retained evidence",
            generation=generation.id,
            same_id=generation.id == managed.id,
            seconds=round(time.monotonic() - started, 1),
            interpreter=str(generation.interpreter),
            selected=EnvironmentPool(copy, store).selected.id,
        )
        check(
            generation.id == managed.id and generation.interpreter.is_file(),
            "reconstruction changed the generation identity",
        )
    after_tables, after_files = dump(copy), tree(copy / ".simulatte/results")
    ev.note(
        "history and reference integrity after the copy and rebuild",
        tables_equal=after_tables == before_tables,
        differing_tables=[t for t in before_tables if before_tables[t] != after_tables.get(t)],
        results_equal=after_files == before_files,
        analysis_equal=tree(copy / ".simulatte/analysis-results") == before_analysis,
        history=history[:5],
    )
    check(after_tables == before_tables and after_files == before_files, "durable rows or result files changed")
    service = ProjectService.open(StudioTarget(copy, None))
    try:
        ev.note("copy opens after reconstruction", read_only=service.read_only, error=service.error)
        check(
            service.read_only and "launcher handoff" in (service.error or ""),
            f"the copy should ask for the launcher handoff from this interpreter: {service.error}",
        )
    finally:
        service.close()
    copy_simulatte = generation.interpreter.with_name("simulatte")
    done = ev.command(
        [copy_simulatte, "replay", managed_run["request_id"], "--project", copy, "--json"],
        cwd=copy,
        timeout=300,
        expect=None,
    )
    replayed = json.loads(done.stdout)
    ev.note(
        "replay of the CLI run in the rebuilt environment",
        exit=done.returncode,
        status=replayed.get("status"),
        verified=replayed.get("verified"),
        reason=replayed.get("reason"),
        message=replayed.get("message"),
    )
    done = ev.command(
        [copy_simulatte, "replay", studio_run["request_id"], "--project", copy, "--json"],
        cwd=copy,
        timeout=300,
        expect=None,
    )
    studio_replay = json.loads(done.stdout)
    ev.note("replay of the coordinator run in the rebuilt environment", exit=done.returncode, output=studio_replay)
    replay_outcomes = {"cli": replayed, "coordinator": studio_replay}
    done = ev.command(
        [SIMULATTE, "replay", managed_run["request_id"], "--project", copy, "--json"],
        cwd=copy,
        timeout=300,
        expect=None,
    )
    ev.note(
        "replay from another installation is refused, not reconstructed",
        exit=done.returncode,
        stdout=done.stdout[-400:],
    )
    check(done.returncode != 0 and '"verified":true' not in done.stdout, "foreign runtime replay was substituted")
    process, link, out = launch([SIMULATTE, "studio", copy, "--no-browser", "--port", "0"], cwd=copy, timeout=90)
    code = stop(process)
    ev.note("launcher hands off to the rebuilt environment", link=bool(link), exit=code, output=redact(out))
    check(link is not None, "launcher could not serve the rebuilt copy")
    # Cache cleanup never deletes durable study results.
    pre_prune_tables, pre_prune_files = dump(copy), tree(copy / ".simulatte/results")
    done = ev.command([SIMULATTE, "store", "prune", "--dry-run", "--project", copy, "--json"], cwd=copy, timeout=120)
    dry = json.loads(done.stdout)
    done = ev.command([SIMULATTE, "store", "prune", "--project", copy, "--json"], cwd=copy, timeout=120)
    pruned = json.loads(done.stdout)
    with RunStore.open(copy / ".simulatte") as store:
        pool = EnvironmentPool(copy, store)
        refusals = {}
        for label, identity in (
            ("selected managed", managed.id),
            (
                "existing interpreter",
                next(
                    g["id"]
                    for g in tables(copy, ("environment_generations",))["environment_generations"]
                    if g["id"] != managed.id
                ),
            ),
        ):
            try:
                pool.cleanup(identity)
                refusals[label] = "removed"
            except ValueError as error:
                refusals[label] = str(error)
    ev.note(
        "cache cleanup",
        dry_run=dry,
        prune=pruned,
        results_intact=tree(copy / ".simulatte/results") == pre_prune_files,
        analysis_intact=tree(copy / ".simulatte/analysis-results") == before_analysis,
        cleanup=refusals,
        tables_intact=dump(copy) == pre_prune_tables,
    )
    check(
        tree(copy / ".simulatte/results") == pre_prune_files and dump(copy) == pre_prune_tables,
        "prune removed durable study results",
    )
    check(pruned.get("results") == [] and "selected" in refusals["selected managed"], "cleanup touched protected state")
    unverified = {
        k: v.get("message") or v.get("reason") for k, v in replay_outcomes.items() if v.get("verified") is not True
    }
    check(not unverified, f"replay in the rebuilt environment of the copy not verified: {unverified}")


def scenario_11(ev: Evidence, work: Path, args):
    """Interruption, failed publication, admission races, cleanup under active and saved work."""
    from simulatte.analysis.selection import Selection
    from simulatte.analysis.store import AnalysisStore
    from simulatte.execution.store import RunStore
    from simulatte.presentations.store import PresentationStore

    root, model_id = new_project(
        work,
        "recovery",
        model=SLOW_MODEL.format(delay=10.0),
        analyses=("slow",),
        files={"slow.py": SLOW_ANALYSIS.format(seconds=10)},
    )
    # (a) The writer dies during a run.
    child = subprocess.Popen(
        [str(PYTHON), "-I", str(SCRIPT), "--child", "hang-run", str(root)], stdout=subprocess.PIPE, text=True
    )
    line = child.stdout.readline()
    attempt = json.loads(line)
    until(
        lambda: (
            tables(root, ("attempts",))["attempts"]
            and any(
                a["id"] == attempt["attempt_id"] and a["status"] == "running"
                for a in tables(root, ("attempts",))["attempts"]
            )
        ),
        90,
        "child run running",
    )
    child.kill()
    child.wait()
    with RunStore.open(root / ".simulatte") as store:
        row = next(a for a in tables(root, ("attempts",))["attempts"] if a["id"] == attempt["attempt_id"])
        slots = store.slots()
        has_result = (store.root / "results" / attempt["attempt_id"]).exists()
    ev.note(
        "interrupted execution recovers as lost",
        status=row["status"],
        interactive_active=slots["interactive"]["active"],
        result_dir=has_result,
        results_rows=len(tables(root, ("results",))["results"]),
    )
    check(
        row["status"] == "lost" and slots["interactive"]["active"] is None and not has_result,
        "interrupted run left dangling state",
    )
    # (b) The writer dies during an analysis.
    with workspace(root) as (service, co):
        prepared = co.prepare_model(model_id, timeout=90)
        quick = co.admit_run(
            model_id, co.make_request(prepared, parameters={"value": 1, "delay": 0.0}), prepared.revision
        )
        co.wait(quick.key, timeout=120)
    child = subprocess.Popen(
        [str(PYTHON), "-I", str(SCRIPT), "--child", "hang-analysis", str(root), quick.key.attempt_id],
        stdout=subprocess.PIPE,
        text=True,
    )
    execution = json.loads(child.stdout.readline())["execution"]
    until(
        lambda: any(
            e["id"] == execution and e["state"] in ("running", "finalizing")
            for e in tables(root, ("analysis_executions",))["analysis_executions"]
        ),
        90,
        "child analysis running",
    )
    child.kill()
    child.wait()
    with RunStore.open(root / ".simulatte") as store:
        state = AnalysisStore(store).get(execution)["execution"]["state"]
        outputs = tables(root, ("analysis_outputs",))["analysis_outputs"]
        staging = (store.root / "analysis-staging" / execution).exists()
        results_dir = (store.root / "analysis-results" / execution).exists()
    ev.note(
        "interrupted analysis recovers as lost",
        state=state,
        outputs=[o["execution"] for o in outputs],
        staging=staging,
        results_dir=results_dir,
    )
    check(
        state == "lost" and execution not in [o["execution"] for o in outputs] and not staging and not results_dir,
        "interrupted analysis left dangling output",
    )
    # (c) Publication fails: no successful record, no result files.
    with workspace(root) as (service, co):
        import simulatte.execution.coordinator as module

        store = co.store
        original = module.publish

        def failing(*a, **k):
            raise OSError("disk full on purpose")

        module.publish = failing
        try:
            prepared = co.prepare_model(model_id, timeout=90)
            failed = co.admit_run(
                model_id, co.make_request(prepared, parameters={"value": 2, "delay": 0.0}), prepared.revision
            )
            until(lambda: store.attempt(failed.key)["status"] not in ACTIVE, 120, "failed publication settles")
        finally:
            module.publish = original
        row = store.attempt(failed.key)
        ev.note(
            "failed publication",
            status=row["status"],
            details=str(plain(row.get("details")))[:200],
            result_row=failed.key.attempt_id in {r["attempt"] for r in tables(root, ("results",))["results"]},
            result_dir=(store.root / "results" / failed.key.attempt_id).exists(),
        )
        check(
            row["status"] in ("failed", "disk_full")
            and failed.key.attempt_id not in {r["attempt"] for r in tables(root, ("results",))["results"]},
            "failed publication left a success record",
        )
        check(not (store.root / "results" / failed.key.attempt_id).exists(), "failed publication left result files")
        ok = co.admit_run(model_id, co.make_request(prepared, parameters={"value": 3, "delay": 0.0}), prepared.revision)
        co.wait(ok.key, timeout=120)
        ev.note("execution continues after the failure", status=store.attempt(ok.key)["status"])
    # (d) Admission races publication and cleanup on synthetic replications (deterministic ordering).
    race_root = work / "race"
    race_root.mkdir()
    race = ev.command([PYTHON, "-I", SCRIPT, "--child", "admission-race", race_root], timeout=120)
    ev.note("analysis admission versus publication and prune", **json.loads(race.stdout.strip().splitlines()[-1]))
    # (e) Cleanup while an analysis is active, then after several analysis and presentation revisions.
    study_root, study_model = new_project(
        work, "retention", analyses=("lateness", "slow"), files={"slow.py": SLOW_ANALYSIS.format(seconds=6)}
    )
    with workspace(study_root) as (service, co):
        store, pool = co.store, service.environments
        prepared = co.prepare_model(study_model, timeout=90)
        path = experiment_file(
            study_root,
            "retained",
            count=2,
            base_seed=0,
            measurements=plan_selection(prepared),
            stopping={"type": "horizon", "horizon": 30.0, "warmup": 5.0},
        )
        run, outcome = run_experiment(co, study_model, path, timeout=300)
        check(outcome["status"] == "completed", f"experiment {outcome}")
        attempts = [r["attempt"] for r in store.replications(run, status="succeeded")]
        prepare_analysis, AnalysisRunner, analysis = analysis_tools(store, pool)
        definition, generation = prepare_analysis(store, "slow", environments=pool)
        active = analysis.admit(
            analysis.preview(Selection(experiment_run_ids=(run,))).id,
            definition_id=definition,
            parameters={},
            generation_id=generation,
        )
        with AnalysisRunner(store, environments=pool) as runner:
            runner.submit(active.id)
            until(
                lambda: analysis.get(active.id)["execution"]["state"] in ("running", "finalizing"),
                60,
                "analysis running",
            )
            pruned_during = store.prune()
            try:
                store.delete_experiment_run(run)
                deleted_during = "deleted"
            except ValueError as error:
                deleted_during = str(error)
            state = runner.wait(active.id, timeout=120).state
        ev.note(
            "cleanup while an analysis is active",
            prune=pruned_during,
            delete_experiment=deleted_during,
            analysis=state,
            results_present=all((store.root / "results" / a).is_dir() for a in attempts),
        )
        check(
            state == "succeeded" and pruned_during["results"] == [] and "referenced" in deleted_during,
            "cleanup touched an active analysis' inputs",
        )
        one, _, s1 = run_analysis(store, pool, "lateness", Selection(experiment_run_ids=(run,)), name="Lateness v1")
        two = analysis.rerun(one.id)
        with AnalysisRunner(store, environments=pool) as runner:
            runner.submit(two.id)
            s2 = runner.wait(two.id, timeout=300).state
        check(s1 == "succeeded" and s2 == "succeeded", "analysis revisions")
        from simulatte.presentations.recording import record_presentation
        from simulatte.presentations.schema import output_content_hash

        recording = record_presentation(
            co, attempts[0], seed=store.request(store.replications(run)[0]["request_id"]).seed + 1, timeout=300
        )
        trace = store.result(recording.key.attempt_id).path / "run.simtrace"
        manifest = json.loads(MANIFEST_FIXTURE.read_text())
        from simulatte.analysis import codec

        outputs = unrecord(json.loads(codec.dumps(analysis.output(one.id))))["outputs"]
        descriptor = next(o for o in outputs if o["kind"] == "number")
        evidence_item = {
            "id": str(uuid4()),
            "execution_id": one.id,
            "output_id": descriptor["id"],
            "content_hash": output_content_hash(descriptor),
            "kind": "number",
            "selection": {},
            "labels": {
                "name": descriptor["name"],
                "unit": descriptor["metadata"].get("unit"),
                "population": None,
                "sample": {"planned": 2, "available": 2, "partial": False},
                "convention": "descriptive",
                "source": {"analysis_name": "Lateness v1", "snapshot_hash": one.snapshot_hash, "execution_id": one.id},
            },
        }
        manifest.update(
            {
                "recording": {
                    "attempt_id": recording.key.attempt_id,
                    "request_id": recording.key.request_id,
                    "trace_sha256": sha(trace),
                },
                "time": {"start": 5.0, "end": 20.0},
                "evidence": [evidence_item],
            }
        )
        presentations = PresentationStore(store)
        pid = presentations.create(manifest)
        second = dict(
            manifest,
            evidence=[
                {
                    **evidence_item,
                    "id": str(uuid4()),
                    "execution_id": two.id,
                    "labels": {
                        **evidence_item["labels"],
                        "source": {
                            **evidence_item["labels"]["source"],
                            "execution_id": two.id,
                            "snapshot_hash": two.snapshot_hash,
                        },
                    },
                }
            ],
            notes="revision two",
        )
        revision = presentations.revise(pid, 1, second)
        outcomes = {}

        def attempt_delete(label, action):
            try:
                action()
                outcomes[label] = "allowed"
            except ValueError as error:
                outcomes[label] = str(error)[:160]

        attempt_delete(
            "experiment while analyses and presentation reference it", lambda: store.delete_experiment_run(run)
        )
        attempt_delete("delete analysis v1 (presentation revision 1 cites it)", lambda: analysis.delete(one.id))
        attempt_delete("delete analysis v2 (presentation revision 2 cites it)", lambda: analysis.delete(two.id))
        attempt_delete("delete analysis of the active slot (slow)", lambda: analysis.delete(active.id))
        attempt_delete("experiment after one owner removed", lambda: store.delete_experiment_run(run))
        before = tree(store.root / "results")
        pruned = store.prune()
        attempt_delete("presentation", lambda: presentations.delete(pid))
        attempt_delete("delete analysis v1 after the presentation", lambda: analysis.delete(one.id))
        attempt_delete("experiment while analysis v2 remains", lambda: store.delete_experiment_run(run))
        attempt_delete("delete analysis v2", lambda: analysis.delete(two.id))
        attempt_delete("experiment after the last owner", lambda: store.delete_experiment_run(run))
        final = store.prune()
        ev.note(
            "explicit removals keep evidence needed by another owner",
            presentation=pid,
            revisions=revision,
            outcomes=outcomes,
            prune_with_owners=pruned,
            results_kept_with_owners=tree(store.root / "results") == before if False else None,
            final_prune=final,
            recording_kept=(store.root / "results" / recording.key.attempt_id).is_dir(),
            replication_results_removed=[not (store.root / "results" / a).exists() for a in attempts],
        )
        check(
            outcomes["experiment while analyses and presentation reference it"] != "allowed",
            "experiment deleted while referenced",
        )
        check(outcomes["delete analysis v1 (presentation revision 1 cites it)"] != "allowed", "cited analysis deleted")
        check(
            outcomes["delete analysis of the active slot (slow)"] == "allowed"
            and outcomes["experiment after one owner removed"] != "allowed",
            "removing one owner removed shared evidence",
        )
        check(
            outcomes["presentation"] == "allowed"
            and outcomes["delete analysis v1 after the presentation"] == "allowed"
            and outcomes["experiment while analysis v2 remains"] != "allowed",
            "second owner did not protect the evidence",
        )
        check(outcomes["experiment after the last owner"] == "allowed", "experiment undeletable after the last owner")
        check((store.root / "results" / recording.key.attempt_id).is_dir(), "the recording went with the presentation")


def scenario_12(ev: Evidence, work: Path, args):
    """The documented command workflow with the installed package (docs/studio/*.md at HEAD)."""
    starter = work / "documented"
    starter.mkdir()
    from simulatte.projects import install_starter, starter_files

    install_starter(starter, starter_files("production"))
    done = ev.command([PYTHON, "models/production.py"], cwd=starter, timeout=300)
    ev.note("index.md: the starter runs as ordinary Python", stdout=done.stdout.strip()[-200:])
    check("Completed simulation" in done.stdout, "starter __main__ did not complete")
    done = ev.command(
        [SIMULATTE, "run", "models/production.py:build", "--seed", "29", "--json"], cwd=starter, timeout=300
    )
    first = json.loads(done.stdout)
    ev.note(
        "index.md: simulatte run <starter> --seed 29",
        keys=sorted(first) if isinstance(first, dict) else None,
        status=first.get("status") if isinstance(first, dict) else None,
    )
    # index.md:59-60 literally: the uv commands in the starter folder (a new user's own project).
    for argv in (
        ["uv", "run", "python", "models/production.py"],
        ["uv", "run", "simulatte", "run", "models/production.py:build", "--seed", "29"],
    ):
        done = ev.command(argv, cwd=starter, timeout=600, expect=None)
        ev.note(
            "index.md:59-60 literal uv command",
            argv=argv,
            exit=done.returncode,
            stdout=done.stdout[-300:],
            stderr=done.stderr[-600:],
        )
    check(
        isinstance(first, dict) and {"status", "request_id", "attempt_id", "path", "trace", "reproduced"} <= set(first),
        f"cli.md: run --json keys {sorted(first)}",
    )
    # cli.md conventions: experiment, replay and store need an existing store.
    empty = work / "empty"
    empty.mkdir()
    for argv in (["experiment", "status"], ["replay", "0" * 32], ["store", "prune", "--dry-run"], ["store", "check"]):
        done = ev.command([SIMULATTE, *argv, "--project", empty], cwd=work, expect=None, timeout=60)
        ev.note(
            "cli.md: no store exits 2 with 'No run store in DIR'",
            argv=argv,
            exit=done.returncode,
            stderr=done.stderr[-300:],
            stdout=done.stdout[-300:],
        )
    done = ev.command([SIMULATTE, "studio", "--help"], cwd=work, timeout=60)
    documented = [
        "--project",
        "--port",
        "--no-browser",
        "--layout",
        "--param",
        "--seed",
        "--horizon",
        "--arrivals",
        "--cutoff",
        "--warmup",
        "--safety-limit",
        "--level",
        "--kpi",
        "--timeout",
    ]
    ev.note(
        "cli.md: studio --help lists every documented option",
        missing=[o for o in documented if o not in done.stdout],
        help=done.stdout[-1500:],
    )
    done = ev.command([SIMULATTE, "experiment", "run", "--help"], cwd=work, timeout=60)
    ev.note("cli.md: experiment run --help (local-slots default)", help=done.stdout[-1500:])
    example = work / "studio_project"
    shutil.copytree(
        REPO / "examples/studio_project", example, ignore=shutil.ignore_patterns("__pycache__", ".simulatte")
    )
    done = ev.command([PYTHON, "run_numerical.py"], cwd=example, timeout=300)
    ev.note("measurements.md: run_numerical.py", stdout=done.stdout.strip()[-300:])
    check("numerical rows" in done.stdout, "run_numerical.py output")
    done = ev.command([PYTHON, "run_managed.py"], cwd=example, timeout=600)
    managed = {}
    for line in done.stdout.splitlines():
        match = re.match(r"(\w+): run=(\w+) analysis=([\w-]+) snapshot=(\w+)", line)
        if match:
            managed[match.group(1)] = {"run": match.group(2), "analysis": match.group(3), "snapshot": match.group(4)}
    ev.note("measurements.md/analysis.md: run_managed.py publishes runs and analyses", managed=managed)
    check(
        {"lateness", "utilization"} <= set(managed),
        f"run_managed.py did not publish both analyses: {done.stdout[-400:]}",
    )
    run_attempt = managed["lateness"]["run"]
    done = ev.command(
        [SIMULATTE, "analysis", "run", "lateness", "--run", run_attempt, "--json"], cwd=example, timeout=300
    )
    cli = json.loads(done.stdout)
    execution = (
        (cli.get("execution") or {}).get("id")
        if isinstance(cli.get("execution"), dict)
        else cli.get("id") or cli.get("execution")
    )
    ev.note("analysis.md: analysis run without --project defaults to cwd", execution=execution, keys=sorted(cli))
    for sub in ("show", "rerun", "refresh"):
        done = ev.command(
            [SIMULATTE, "analysis", sub, execution, "--project", example, "--json"], cwd=work, timeout=300
        )
        ev.note(f"analysis.md: analysis {sub}", keys=sorted(json.loads(done.stdout)), exit=done.returncode)
    done = ev.command(
        [SIMULATTE, "analysis", "run", "lateness", "--project", example, "--run", "0" * 32, "--json"],
        cwd=work,
        expect=None,
        timeout=120,
    )
    ev.note(
        "analysis.md: invalid input exits 2", exit=done.returncode, stdout=done.stdout[-300:], stderr=done.stderr[-300:]
    )
    check(done.returncode == 2, f"invalid input exited {done.returncode}")
    done = ev.command(
        [
            SIMULATTE,
            "analysis",
            "run",
            "lateness",
            "--project",
            example,
            "--run",
            run_attempt,
            "--param",
            "bogus=1",
            "--json",
        ],
        cwd=work,
        expect=None,
        timeout=120,
    )
    ev.note(
        "analysis.md: unknown --param is invalid input",
        exit=done.returncode,
        stderr=done.stderr[-200:],
        stdout=done.stdout[-200:],
    )
    experiment = example / "speed.experiment.json"
    experiment.write_text(
        json.dumps(
            {
                "schema": 1,
                "name": "speed",
                "model": "models/production.py:build",
                "layouts": [{"label": "base", "file": "layouts/production.json"}],
                "replications": {"count": 3, "base_seed": 100},
                "configurations": {"grid": {"speed": [1.0, 1.5]}},
                "level": "kpi",
            }
        )
    )
    done = ev.command(
        [SIMULATTE, "experiment", "run", experiment, "--project", example, "--local-slots", "2", "--json"],
        cwd=work,
        timeout=600,
    )
    outcome = json.loads(done.stdout)
    run_id = outcome.get("run_id") or outcome.get("run") or outcome.get("id")
    ev.note("experiments guide: experiment run", status=outcome.get("status"), keys=sorted(outcome))
    done = ev.command(
        [SIMULATTE, "experiment", "status", run_id, "--project", example, "--json"], cwd=work, timeout=120
    )
    ev.note("experiment status", keys=sorted(json.loads(done.stdout)))
    done = ev.command(
        [
            SIMULATTE,
            "experiment",
            "results",
            run_id,
            "--project",
            example,
            "--baseline",
            "speed=1.0/base",
            "--bonferroni",
            "--json",
        ],
        cwd=work,
        timeout=120,
        expect=None,
    )
    ev.note(
        "experiment results with baseline and Bonferroni",
        exit=done.returncode,
        stdout=done.stdout[-600:],
        stderr=done.stderr[-300:],
    )
    check(done.returncode == 0, "experiment results with the documented baseline label")
    from simulatte.execution.store_reader import StoreReader

    with contextlib.closing(StoreReader.open(example / ".simulatte")) as reader:
        replication = reader.replications(run_id)[0]
    done = ev.command(
        [SIMULATTE, "replay", replication["request_id"], "--project", example, "--json"], cwd=work, timeout=600
    )
    replay = json.loads(done.stdout)
    ev.note("replay of a replication at full level", verified=replay.get("verified"), keys=sorted(replay))
    check(replay.get("verified") is True, f"replay unverified: {replay}")
    done = ev.command([SIMULATTE, "store", "prune", "--dry-run", "--project", example, "--json"], cwd=work, timeout=120)
    dry = json.loads(done.stdout)
    ev.note(
        "store prune --dry-run --json",
        keys=sorted(dry),
        output=dry,
        documented_keys_present={"status", "dry_run", "results", "snapshots", "protected"} <= set(dry),
    )
    done = ev.command([SIMULATTE, "store", "prune", "--dry-run", "--project", example], cwd=work, timeout=120)
    ev.note(
        "storage.md: store prune --dry-run text ends with the kept count",
        stdout=done.stdout[-400:],
        stderr=done.stderr[-200:],
        documented_suffix="results kept (--json names the owners keeping each)" in done.stdout,
    )
    done = ev.command([SIMULATTE, "store", "check", "--project", example], cwd=work, timeout=120, expect=None)
    ev.note(
        "storage.md: store check",
        exit=done.returncode,
        stdout=done.stdout[-300:],
        stderr=done.stderr[-300:],
        documented=bool(re.search(r"verified \d+ results and \d+ analyses", done.stdout)),
    )
    with contextlib.closing(StoreReader.open(example / ".simulatte")) as reader:
        pass
    trace = Path(replay["path"]) / "run.simtrace"
    process, link, out = launch([SIMULATTE, "view", trace, "--no-browser", "--port", "0"], cwd=work, timeout=60)
    code = stop(process)
    ev.note(
        "simulatte view <trace> --no-browser",
        trace_exists=trace.is_file(),
        link=bool(link),
        exit=code,
        output=redact(out),
    )
    check(trace.is_file() and link is not None, "viewer did not serve the replayed trace")
    done = ev.command([SIMULATTE, "studio", "--help"], cwd=work, timeout=60)
    ev.note(
        "studio --help lists the documented flags",
        port="--port" in done.stdout,
        no_browser="--no-browser" in done.stdout,
        project="--project" in done.stdout,
    )
    done = ev.command([SIMULATTE, "workers", "list"], cwd=work, timeout=60, expect=None)
    ev.note("workers list", exit=done.returncode, stdout=done.stdout[-300:], stderr=done.stderr[-300:])


SCENARIOS = {
    1: scenario_1,
    2: scenario_2,
    3: scenario_3,
    8: scenario_8,
    9: scenario_9,
    10: scenario_10,
    11: scenario_11,
    12: scenario_12,
}


# --------------------------------------------------------------------------- child programs


def child(command: str, argv: list[str]):
    """Helpers that must run in another interpreter or die mid-operation."""
    from simulatte.projects import StudioTarget
    from simulatte.projects.service import ProjectService

    if command == "produce-plots":
        root = Path(argv[0])
        from simulatte.analysis.selection import Selection

        with workspace(root) as (service, co):
            model_id = service.workspace.models[0].id
            prepared = co.prepare_model(model_id, timeout=90)
            run = co.admit_run(model_id, co.make_request(prepared, level="kpi", seed=5), prepared.revision)
            co.wait(run.key, timeout=120)
            item, _, state = run_analysis(
                co.store, service.environments, "plots", Selection(run_ids=(run.key.attempt_id,)), name="Figures"
            )
            print(
                json.dumps(
                    {"attempt": run.key.attempt_id, "execution": item.id, "state": state, "interpreter": sys.executable}
                )
            )
        return
    if command == "select-interpreter":
        root, interpreter = Path(argv[0]), Path(argv[1])
        service = ProjectService.open(StudioTarget(root, None))
        try:
            pool = service.environments
            pool.activate(pool.use_existing(interpreter).id)
        finally:
            service.close()
        return
    if command == "activate":
        from simulatte.execution.store import RunStore
        from simulatte.projects.environments import EnvironmentPool

        root, identity = Path(argv[0]), argv[1]
        with RunStore.open(root / ".simulatte") as store:
            EnvironmentPool(root, store).activate(identity)
        return
    if command == "studio-run":
        root = Path(argv[0])
        with workspace(root) as (service, co):
            model_id = service.workspace.models[0].id
            prepared = co.prepare_model(model_id, timeout=90)
            run = co.admit_run(model_id, co.make_request(prepared, seed=21, level="kpi"), prepared.revision)
            co.wait(run.key, timeout=120)
            print(
                json.dumps(
                    {
                        "attempt_id": run.key.attempt_id,
                        "request_id": run.key.request_id,
                        "status": co.store.attempt(run.key)["status"],
                    }
                )
            )
        return
    if command == "hang-run":
        root = Path(argv[0])
        with workspace(root) as (service, co):
            model_id = service.workspace.models[0].id
            prepared = co.prepare_model(model_id, timeout=90)
            run = co.admit_run(model_id, co.make_request(prepared), prepared.revision)
            print(json.dumps({"attempt_id": run.key.attempt_id, "request_id": run.key.request_id}), flush=True)
            co.wait(run.key, timeout=600)
        return
    if command == "hang-analysis":
        from simulatte.analysis.selection import Selection

        root, attempt = Path(argv[0]), argv[1]
        with workspace(root) as (service, co):
            prepare_analysis, AnalysisRunner, analysis = analysis_tools(co.store, service.environments)
            definition, generation = prepare_analysis(co.store, "slow", environments=service.environments)
            item = analysis.admit(
                analysis.preview(Selection(run_ids=(attempt,))).id,
                definition_id=definition,
                parameters={},
                generation_id=generation,
            )
            with AnalysisRunner(co.store, environments=service.environments) as runner:
                runner.submit(item.id)
                print(json.dumps({"execution": item.id}), flush=True)
                runner.wait(item.id, timeout=600)
        return
    if command == "admission-race":
        admission_race(Path(argv[0]))
        return
    raise SystemExit(f"unknown child command {command}")


def admission_race(root: Path):
    """Deterministic ordering of analysis admission against publication and pruning (synthetic rows)."""
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from threading import Event

    from simulatte.analysis.definition import AnalysisDefinition
    from simulatte.analysis.selection import Selection
    from simulatte.analysis.store import AnalysisStore
    from simulatte.execution import schema
    from simulatte.execution.store import RunStore, _blob
    from simulatte.model import ParameterSchema

    def request(i):
        return schema.ExecutionRequest(
            model="plant.py:build",
            source_hash="a" * 64,
            input_hash="b" * 64,
            layout_hash="c" * 64,
            layout_revision=1,
            parameters={"x": 1.0},
            seed=i,
            stopping={"type": "horizon", "horizon": 10.0, "warmup": 0.0},
            runtime={"implementation": "CPython", "version": "3.14.1"},
        )

    def publish(store, run, i, value):
        revision = store.current_revision()
        key = store.assign(replace(request(i), layout_revision=revision.number), revision, slot="local-1", pinned=True)
        store.bind_replication(run, 0, i, key)
        for status in ("preparing", "ready", "running", "finalizing"):
            store.transition(key, status)
        path = store.root / "results" / key.attempt_id
        path.mkdir()
        (path / "run.simtrace").write_bytes(b"trace")
        with store._transaction() as db:
            db.execute("INSERT INTO results VALUES (?,?,?,?)", (key.attempt_id, _blob({}), _blob({}), 1.0))
            db.execute("UPDATE attempts SET status='succeeded' WHERE id=?", (key.attempt_id,))
            db.execute("UPDATE slots SET active=NULL WHERE active=?", (key.attempt_id,))
            db.execute("INSERT INTO kpis VALUES (?,?,?)", (key.attempt_id, "lateness", _blob(value)))
        store.set_replication_status(run, 0, i, "succeeded", attempt=key.attempt_id)
        return key.attempt_id

    facts = {}
    with RunStore.open(root / ".simulatte") as store:
        store.create_revision({})
        analysis = AnalysisStore(store)
        definition = analysis.put_definition(
            AnalysisDefinition("lateness", "report.py:analyze", "a" * 64, ParameterSchema(()), ())
        )
        with store._transaction() as db:
            db.execute("INSERT INTO environment_generations VALUES (?,?,?)", ("env", _blob({}), "selected"))
        configurations = [{"label": "x=1/base", "parameters": {"x": 1.0}, "layout_hash": "c" * 64, "layout_ref": None}]
        replications = [
            {"configuration": 0, "index": i, "seed": i, "content_ids": [schema.content_id(request(i))]}
            for i in range(2)
        ]
        run = store.create_experiment_run(
            store.put_experiment(
                {"schema": 1, "name": "race", "model": "plant.py:build", "replications": {"count": 2, "base_seed": 0}}
            ),
            {},
            configurations,
            replications,
        )
        first = publish(store, run, 0, 2**53 - 1)
        preview = analysis.preview(Selection(experiment_run_ids=(run,)))
        publish(store, run, 1, 7)  # publication commits after the preview
        execution = analysis.admit(preview.id, definition_id=definition, parameters={}, generation_id="env")
        snapshot = analysis.snapshot(execution.snapshot_hash)
        facts["second_replication_in_snapshot"] = {
            "status": snapshot["replications"][1]["status"],
            "scalars": plain(snapshot["replications"][1]["scalars"]),
        }
        facts["scalar_exact"] = plain(snapshot["replications"][0]["scalars"])
        store.set_run_status(run, "paused")
        try:
            store.delete_experiment_run(run)
            facts["delete_while_admitted"] = "allowed"
        except ValueError as error:
            facts["delete_while_admitted"] = str(error)
        analysis.cancel(execution.id)
        facts["prune_with_admitted_snapshot"] = store.prune()
        analysis.delete(execution.id)
        standalone = analysis.preview(Selection(run_ids=(first,)))
        locked, release = Event(), Event()
        original = store._drop_results

        def blocked(db, attempts):
            original(db, attempts)
            locked.set()
            assert release.wait(5)

        store._drop_results = blocked
        with ThreadPoolExecutor(2) as pool:
            deleting = pool.submit(store.delete_experiment_run, run)
            assert locked.wait(5)
            admitting = pool.submit(
                lambda: analysis.admit(standalone.id, definition_id=definition, parameters={}, generation_id="env")
            )
            release.set()
            deleting.result()
            try:
                admitting.result()
                facts["admission_after_cleanup"] = "admitted (defect)"
            except ValueError as error:
                facts["admission_after_cleanup"] = str(error)
        del store._drop_results
        facts["results_after"] = sorted(p.name for p in (store.root / "results").iterdir())
    print(json.dumps(facts))


# --------------------------------------------------------------------------- main


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, help="evidence directory (JSON per scenario)")
    parser.add_argument("--work", type=Path, help="working directory for projects (default: <output>/work)")
    parser.add_argument("--plot-python", help="interpreter of a wheel install with the plot extras")
    parser.add_argument("--wheel", help="the built wheel, vendored into the managed environment of scenario 10")
    parser.add_argument("--only", help="comma-separated scenario numbers")
    parser.add_argument("--child", help=argparse.SUPPRESS)
    parser.add_argument("rest", nargs="*", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        child(args.child, args.rest)
        return
    if args.output is None:
        parser.error("--output is required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    work = (args.work or output / "work").resolve()
    selected = [int(n) for n in args.only.split(",")] if args.only else sorted(SCENARIOS)
    summary = {}
    for number in selected:
        ev = Evidence(number, output)
        scenario_work = work / f"scenario{number}"
        if scenario_work.exists():
            shutil.rmtree(scenario_work)
        scenario_work.mkdir(parents=True)
        started = time.monotonic()
        try:
            SCENARIOS[number](ev, scenario_work, args)
            ev.save("passed", seconds=time.monotonic() - started)
            summary[number] = "passed"
        except Failure as error:
            ev.save("failed", str(error), time.monotonic() - started)
            summary[number] = f"failed: {error}"
        except Exception:
            ev.save("error", traceback.format_exc(), time.monotonic() - started)
            summary[number] = "error: " + traceback.format_exc().strip().splitlines()[-1]
        print(f"scenario {number}: {summary[number]} ({time.monotonic() - started:.0f} s)", flush=True)
    summary_path = output / "summary.json"
    previous = json.loads(summary_path.read_text())["scenarios"] if summary_path.exists() else {}
    merged = {**previous, **{str(k): v for k, v in summary.items()}}
    summary_path.write_text(
        json.dumps(
            {"interpreter": str(PYTHON), "scenarios": dict(sorted(merged.items(), key=lambda kv: int(kv[0])))}, indent=1
        )
    )
    if any(not v.startswith("passed") for v in summary.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
