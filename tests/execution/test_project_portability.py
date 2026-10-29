"""A quiescent project copy reads, rebuilds and prunes without its source location (design §5, scenarios 10-11)."""

import asyncio
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
from uuid import uuid4

import pytest

from simulatte.analysis import codec
from simulatte.analysis.definition import AnalysisDefinition
from simulatte.analysis.execution import AnalysisRunner
from simulatte.analysis.measurements import select_measurements
from simulatte.analysis.selection import Selection
from simulatte.analysis.store import AnalysisStore
from simulatte.cli import main
from simulatte.execution.coordinator import Coordinator
from simulatte.execution.project_store import ProjectStore
from simulatte.execution.replay import Verification
from simulatte.execution.snapshot import ProjectSpec, capture_source
from simulatte.execution.store import RunStore, _read
from simulatte.execution.store_reader import StoreReader
from simulatte.experiments import load_experiment
from simulatte.model import ParameterSchema
from simulatte.presentations.schema import output_content_hash
from simulatte.presentations.store import PresentationStore
from simulatte.projects import (
    ModelRegistration,
    StudioTarget,
    WorkspaceDefinition,
    apply_config,
    load_workspace,
    preview_config,
)
from simulatte.projects.environments import EnvironmentPool, environment_directory
from simulatte.projects.service import ProjectService
from simulatte.studio.http import ImmutableFile
from simulatte.studio.project_commands import ProjectServer
from tests.execution.test_presentation_recording import PLAN, RECORDED
from tests.execution.test_project_environments import fake_pool
from tests.core.test_experiment_results import seeded
from tests.execution.test_scheduler import write_experiment
from tests.execution.test_store_slots import FIXTURE, FIXTURE_ATTEMPT, advance, commit

FIXTURES = Path(__file__).parents[1] / "fixtures"
# A measured model: one replication of the second configuration fails on purpose.
PLANT = RECORDED.replace(
    "    cell = Cell(env)",
    "    if params.n == 4 and env.seed == 1:\n        raise RuntimeError('deliberate replication failure')\n"
    "    cell = Cell(env)",
)
REPORT = """from simulatte.analysis.outputs import Number

def analyze(context, parameters):
    context.emit('available', Number(sum(1 for _ in context.scalars())))
"""


@dataclass
class Built:
    """Identities of everything the original project saved."""

    interactive: str
    run_id: str
    failed: str
    partial: str
    full: str
    presentation: str
    export_job: str
    generation: str


def workspace(root: Path) -> WorkspaceDefinition:
    root.mkdir()
    (root / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude = ["plant.py"]\n')
    (root / "plant.py").write_text(PLANT)
    (root / "input.txt").write_text("5")
    (root / "report.py").write_text(REPORT)
    model = ModelRegistration(str(uuid4()), "plant", "Plant", "plant.py:build", "plant.json")
    definition = WorkspaceDefinition(root, str(uuid4()), (model,), (), None)
    apply_config(preview_config(root, definition))
    return definition


def admit(store, root, selection, generation):
    analysis = AnalysisStore(store)
    source = capture_source(ProjectSpec(root, "report.py", "layout.json", ("report.py",), (), (".",), False))
    definition = analysis.put_definition(
        AnalysisDefinition("available", "report.py:analyze", source.content_hash, ParameterSchema(()), ()),
        source=source,
    )
    preview = analysis.preview(selection)
    return analysis.admit(preview.id, definition_id=definition, parameters={}, generation_id=generation)


def analyse(store, environments, selection, generation):
    """Admit and run the ``available`` report: one number output, published under ``analysis-results``."""
    execution = admit(store, environments.root, selection, generation)
    with AnalysisRunner(store, environments=environments) as runner:
        runner.submit(execution.id)
        assert runner.wait(execution.id, timeout=60).state == "succeeded"
    return AnalysisStore(store).execution(execution.id)


def evidence(store, execution):
    analysis = AnalysisStore(store)
    (descriptor,) = analysis.output(execution.id)["outputs"]
    snapshot = analysis.snapshot(execution.snapshot_hash)
    return {
        "id": str(uuid4()),
        "execution_id": execution.id,
        "output_id": descriptor["id"],
        "content_hash": output_content_hash(descriptor),
        "kind": descriptor["kind"],
        "selection": {},
        "labels": {
            "name": descriptor["name"],
            "unit": None,
            "population": None,
            "sample": {
                "planned": snapshot["planned"],
                "available": snapshot["available"],
                "partial": snapshot["partial"],
            },
            "convention": "descriptive",
            "source": {
                "analysis_name": "Available",
                "snapshot_hash": execution.snapshot_hash,
                "execution_id": execution.id,
            },
        },
    }


def presentation_manifest(store, attempt, items, **changes):
    result = store.result(attempt)
    document = json.loads((FIXTURES / "presentations/valid-minimal.json").read_text())
    document.update(
        {
            "recording": {
                "attempt_id": attempt,
                "request_id": result.key.request_id,
                "trace_sha256": hashlib.sha256((result.path / "run.simtrace").read_bytes()).hexdigest(),
            },
            "time": {"start": 0, "end": 1},
            "evidence": list(items),
            **changes,
        }
    )
    return document


def build(root: Path) -> Built:
    """An interactive full run, a measured experiment with a failed replication, a partial and a full
    analysis with outputs, and a presentation with two revisions and a finished export; the writer closes."""
    definition = workspace(root)
    model = definition.models[0].id
    experiment = write_experiment(
        root,
        model="plant.py:build",
        replications={"count": 3},
        configurations={"grid": {"n": [3, 4]}},
        measurements=select_measurements(PLAN, {"limit": 3}),
    )
    with RunStore.open(root / ".simulatte") as store:
        environments = EnvironmentPool(root, store)
        generation = environments.use_existing(Path(sys.executable)).id
        environments.activate(generation)
        with Coordinator.for_workspace(root, store=store, environments=environments) as co:
            prepared = co.prepare_model(model, timeout=30)
            started = co.admit_run(model, co.make_request(prepared, seed=11, level="full"), prepared.revision)
            interactive = co.wait(started.key, timeout=30).key.attempt_id
            run_id = co.contexts[model].start_experiment(load_experiment(experiment))
            counts = co.wait_experiment(run_id, timeout=60)["counts"]
            assert (counts["succeeded"], counts["failed"]) == (5, 1)
        failed = next(r["attempt"] for r in store.replications(run_id) if r["status"] == "failed")
        partial = analyse(store, environments, Selection(experiment_run_ids=(run_id,)), generation)
        full = analyse(store, environments, Selection(experiment_run_ids=(run_id,), configurations=(0,)), generation)
        presentations = PresentationStore(store)
        items = [evidence(store, partial), evidence(store, full)]
        presentation = presentations.create(presentation_manifest(store, interactive, items[:1]))
        presentations.revise(presentation, 1, presentation_manifest(store, interactive, items, name="Both"))
        job = presentations.admit_export(presentation, 2, "studio", {"panel": True}, [])
        presentations.start_export(job.id)
        output = {"name": "view.json", "size": 3, "sha256": "e" * 64, "media_type": "application/json"}
        presentations.finish_export(job.id, {"state": "succeeded", "output": output})
    return Built(interactive, run_id, failed, partial.id, full.id, presentation, job.id, generation)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root: Path) -> dict:
    """Every saved identity and content hash, read through the read-only reader as Studio does."""
    reader = StoreReader.open(root / ".simulatte")
    try:
        with reader._transaction() as db:
            attempts = {
                row["id"]: {**dict(row), "details": _read(row["details"])}
                for row in db.execute("SELECT * FROM attempts")
            }
            requests = {
                row[0]: hashlib.sha256(row[1]).hexdigest() for row in db.execute("SELECT id,body FROM requests")
            }
            committed = sorted(row[0] for row in db.execute("SELECT attempt FROM results"))
            runs = sorted(row[0] for row in db.execute("SELECT id FROM experiment_runs"))
            executions = sorted(row[0] for row in db.execute("SELECT id FROM analysis_executions"))
        results = {}
        for attempt in committed:
            published = reader.result(attempt)
            results[attempt] = {
                "key": asdict(published.key),
                "path": published.path.relative_to(reader.root).as_posix(),
                "manifest": published.manifest,
                "fingerprint": published.fingerprint,
                "trace": digest(published.path / "run.simtrace"),
                "artifacts": [
                    {**item, "read": digest(reader.artifact(attempt, item["path"]))}
                    for item in reader.artifacts(attempt)
                ],
            }
        experiments = {
            run: {
                "run": reader.experiment_run(run),
                "replications": reader.replications(run),
                "kpis": {f"{c}/{i}": v for (c, i), v in reader.replication_kpis(run).items()},
            }
            for run in runs
        }
        analysis = AnalysisStore(reader)
        analyses = {}
        for identity in executions:
            execution = analysis.execution(identity)
            output = analysis.output(identity)
            files = [
                (item["path"], item["sha256"], digest(reader.root / "analysis-results" / identity / item["path"]))
                for descriptor in output["outputs"]
                for item in descriptor["artifacts"]
            ]
            analyses[identity] = {
                "execution": asdict(execution),
                "output": codec.to_json(output),
                "files": files,
                "snapshot": codec.to_json(analysis.snapshot(execution.snapshot_hash)),
            }
        presentations = PresentationStore(reader)
        listed = presentations.list()
        saved = {
            item["id"]: {
                "revisions": presentations.revisions(item["id"]),
                "manifests": [
                    presentations.get(item["id"], r["revision"]) for r in presentations.revisions(item["id"])
                ],
                "exports": [job.to_dict() for job in presentations.exports(item["id"])],
            }
            for item in listed["items"]
        }
        return {
            "history": [item["id"] for item in reader.history(None, limit=200).items],
            "attempts": attempts,
            "requests": requests,
            "results": results,
            "experiments": experiments,
            "analyses": analyses,
            "presentations": {"list": listed, "saved": saved},
            "generations": list(reader.generations()),
        }
    finally:
        reader.close()


def provenance(root: Path) -> dict:
    """Historical provenance bytes: preparation snapshots, requests, result rows and manifests, outputs."""
    with closing(sqlite3.connect((root / ".simulatte/index.sqlite").as_uri() + "?mode=ro", uri=True)) as db:
        rows = {
            f"{table}:{row[0]}": tuple(row[1:])
            for table, query in (
                ("snapshot", "SELECT id||'/'||kind,manifest FROM snapshots"),
                ("request", "SELECT id,body,content_id FROM requests"),
                ("result", "SELECT attempt,manifest,fingerprint,created FROM results"),
                ("artifact", "SELECT attempt||'/'||path,length,sha256 FROM artifacts"),
                ("output", "SELECT execution,manifest FROM analysis_outputs"),
                ("generation", "SELECT id,evidence FROM environment_generations"),
                ("revision", "SELECT id||':'||revision,manifest,content_hash FROM presentation_revisions"),
            )
            for row in db.execute(query)
        }
    for manifest in (root / ".simulatte/results").glob("*/manifest.msgpack"):
        rows[f"file:{manifest.parent.name}"] = (manifest.read_bytes(),)
    return rows


_WATCHED: list[tuple[tuple[str, ...], list]] = []


def _audit(event, args):
    if not _WATCHED:
        return
    prefixes, touched = _WATCHED[-1]
    for value in args:
        for item in value if isinstance(value, (list, tuple)) else (value,):
            if isinstance(item, (str, bytes, os.PathLike)):
                text = item if isinstance(item, str) else os.fsdecode(item)
                if any(prefix in text for prefix in prefixes):
                    touched.append((event, text))


_AUDITING: list[bool] = []


@contextmanager
def untouched(root: Path):
    """Record every audited file, directory, database or process access that names ``root``."""
    if not _AUDITING:
        sys.addaudithook(_audit)
        _AUDITING.append(True)
    prefixes = tuple(dict.fromkeys((str(root), str(root.resolve()), root.as_uri(), root.resolve().as_uri())))
    touched: list = []
    _WATCHED.append((prefixes, touched))
    try:
        yield touched
    finally:
        _WATCHED.pop()


# Absolute source paths a store keeps as historical evidence, never as a location it reads.
HISTORICAL = (
    re.compile(r"\.project\.root$"),  # captured preparation; execution uses the opening project's root
    re.compile(r"(_ref|\.document)\.path$"),  # reference documents, re-rooted under the opening store
    re.compile(r"\.traceback$"),  # failure diagnostic text
    re.compile(r"\.spec\.interpreter$"),  # an existing interpreter's evidence
)


def mentions(value, prefixes, where=""):
    """``(where, text)`` of every string in ``value`` naming one of ``prefixes``."""
    if isinstance(value, dict):
        for key, item in value.items():
            # Analysis codec records wrap mappings in ``$record``; the field path skips the wrapper.
            yield from mentions(item, prefixes, where if key == "$record" else f"{where}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from mentions(item, prefixes, f"{where}[{index}]")
    elif isinstance(value, str) and any(prefix in value for prefix in prefixes):
        yield where, value


def unexplained(value, root: Path):
    """Mentions of ``root`` outside historical fields and recorded import origins (``external:...``)."""
    return [
        (where, text)
        for where, text in mentions(value, (str(root), str(root.resolve())))
        if not text.startswith("external:") and not any(pattern.search(where) for pattern in HISTORICAL)
    ]


def studio_answers(root: Path, built: Built, static: Path) -> dict:
    """The read-only Studio command answers for every saved identity."""
    service = ProjectService.open(StudioTarget(root, None), read_only=True)
    try:
        assert service.read_only and service.error is None and service.store is None
        server = ProjectServer(service, 8123, static)

        def ask(kind, **body):
            reply = asyncio.run(server.command({"type": kind, "body": body}, None))
            assert reply["ok"], reply
            return reply

        return {
            "state": ask("project.get")["state"],
            "history": ask("history.get", limit=200),
            "run": ask("run.get", attempt_id=built.interactive)["run"],
            "experiment": ask("experiment.get", run_id=built.run_id),
            "comparison": ask("comparison.get", run_id=built.run_id),
            "analyses": ask("analysis.list"),
            "partial": ask("analysis.get", execution_id=built.partial),
            "full": ask("analysis.get", execution_id=built.full),
            "presentations": ask("presentation.list"),
            "presentation": ask("presentation.get", id=built.presentation),
            "first": ask("presentation.get", id=built.presentation, revision=1),
            "exports": ask("export.list"),
            "export": ask("export.get", job_id=built.export_job),
        }
    finally:
        service.close()


def foreign_interpreter(root: Path, monkeypatch) -> dict:
    """Register an existing interpreter inside ``root``; its absolute path is captured as evidence."""
    with RunStore.open(root / ".simulatte") as store:
        pool = EnvironmentPool(root, store)
        runtime = {"facts": {"python": "3.14.0"}, "dependencies": {"packages": {}}, "warnings": []}
        monkeypatch.setattr(pool, "_probe", lambda path: {"runtime": runtime, "distribution_roots": [], "studio": True})
        return dict(pool.use_existing(root / ".venv/bin/python").evidence)


def static_dir(tmp_path: Path) -> Path:
    static = tmp_path / "static"
    static.mkdir()
    (static / "manifest.json").write_text('{"assets":{}}')
    return static


def test_quiescent_copy_has_no_source_machine_dependency(tmp_path, monkeypatch, capsys):
    original = tmp_path / "source" / "study"
    original.parent.mkdir()
    built = build(original)
    foreign = foreign_interpreter(original, monkeypatch)
    # Quiescent: the writer closed and left no unmerged write-ahead log.
    wal = original / ".simulatte/index.sqlite-wal"
    assert not wal.exists() or wal.stat().st_size == 0
    expected = inventory(original)
    static = static_dir(tmp_path)
    expected_answers = studio_answers(original, built, static)

    copy = tmp_path / "elsewhere" / "study"
    shutil.copytree(original, copy, symlinks=True)
    original.rename(tmp_path / "source" / "unreachable")
    assert not original.exists()

    with untouched(original) as touched:
        seen = inventory(copy)
        answers = studio_answers(copy, built, static)
    assert touched == []
    # Every identity and hash is the same, and readable, in the copy.
    assert seen == expected
    assert built.interactive in seen["results"] and built.failed in seen["attempts"]
    assert built.failed not in seen["results"]
    for result in seen["results"].values():
        assert result["trace"] == result["manifest"]["trace_sha256"]
        assert result["artifacts"] and all(item["read"] == item["sha256"] for item in result["artifacts"])
        assert result["path"] == f"results/{result['key']['attempt_id']}"
    for item in seen["analyses"].values():
        assert item["files"] and all(read == recorded for _, recorded, read in item["files"])
    snapshots = {identity: item["snapshot"]["$record"] for identity, item in seen["analyses"].items()}
    assert (snapshots[built.partial]["available"], snapshots[built.partial]["planned"]) == (5, 6)
    assert snapshots[built.partial]["partial"] is True and snapshots[built.full]["partial"] is False
    saved = seen["presentations"]["saved"][built.presentation]
    assert [r["revision"] for r in saved["revisions"]] == [1, 2]
    assert [len(m["evidence"]) for m in saved["manifests"]] == [1, 2]
    (job,) = saved["exports"]
    assert (job["id"], job["state"], job["revision"]) == (built.export_job, "succeeded", 2)
    # Studio answers the read-only commands from the copy exactly as from the original.
    for name in ("history", "experiment", "comparison", "analyses", "partial", "full", "presentation", "first"):
        assert answers[name] == expected_answers[name], name
    assert answers["run"]["artifacts"] == expected_answers["run"]["artifacts"]
    assert answers["export"]["job"] == job and answers["exports"]["items"] == [job]
    state, expected_state = answers["state"]["project"], expected_answers["state"]["project"]
    assert state["read_only"] is True and state["history"] == expected_state["history"]
    assert answers["state"]["results"] == expected_answers["state"]["results"]
    # The source location survives only as historical evidence, never as a path the copy reads.
    leaks = unexplained([seen, answers], original)
    assert leaks == [], "\n".join(sorted({re.sub(r"[0-9a-f-]{32,}", "<id>", w) for w, _ in leaks}))
    generations = {item["id"]: item for item in seen["generations"]}
    assert generations[built.generation]["state"] == "selected"
    interpreters = {item["evidence"]["spec"]["interpreter"] for item in generations.values()}
    assert interpreters == {str(Path(sys.executable).absolute()), str(original / ".venv/bin/python")}

    # A rebuilt environment in the copy re-executes without rewriting historical provenance.
    before = provenance(copy)
    with RunStore.open(copy / ".simulatte") as writer:
        pool = EnvironmentPool(copy, writer)
        with pytest.raises(ValueError, match="Historical environment reconstruction unavailable"):
            pool.reconstruct(foreign)
        with untouched(original) as touched:
            assert pool.reconstruct(pool.selected.evidence).id == built.generation
            assert pool.use_existing(Path(sys.executable)).id == built.generation
            with Coordinator.for_workspace(copy, store=writer, environments=pool) as co:
                replay = co.replay(writer.result(built.interactive).key.request_id, timeout=30)
        assert touched == []
        assert replay.verification == Verification(True, None)
    after = provenance(copy)
    assert {key: after[key] for key in before} == before
    assert len(after) > len(before)

    # A partial transfer is diagnosed by name and hash; a same-named file is never adopted.
    victim = next(a for a in seen["results"] if a != built.interactive)
    (lost,) = [item for item in seen["results"][victim]["artifacts"] if item["path"] == "answer.txt"]
    (copy / ".simulatte/results" / victim / "artifacts/answer.txt").unlink()
    ((name, recorded, _),) = seen["analyses"][built.partial]["files"]
    target = (copy / ".simulatte/analysis-results" / built.partial / name).resolve()
    shutil.copyfile(copy / ".simulatte/analysis-results" / built.full / name, target)
    assert digest(target) != recorded
    problems = [
        {
            "kind": "analysis",
            "id": built.partial,
            "name": name,
            "path": f"analysis-results/{built.partial}/{name}",
            "sha256": recorded,
            "problem": "changed",
        },
        {
            "kind": "result",
            "id": victim,
            "name": "answer.txt",
            "path": f"results/{victim}/artifacts/answer.txt",
            "sha256": lost["sha256"],
            "problem": "missing",
        },
    ]
    reader = StoreReader.open(copy / ".simulatte")
    try:
        assert reader.check() == problems
    finally:
        reader.close()
    with pytest.raises(ValueError, match="hash mismatch"):
        with ImmutableFile(target, target.stat().st_size, recorded).open():
            pass
    assert main(["store", "check", "--project", str(copy), "--json"]) == 1
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["status"] == "failed" and envelope["problems"] == problems
    assert main(["store", "check", "--project", str(copy)]) == 1
    text = capsys.readouterr().err
    assert f"missing results/{victim}/artifacts/answer.txt (sha256 {lost['sha256']})" in text
    assert f"changed analysis-results/{built.partial}/{name} (sha256 {recorded})" in text


UNAVAILABLE = "Project interpreter is unavailable. Reconstruct its environment or open with a compatible runtime."


def test_same_machine_copy_never_runs_from_the_original_projects_interpreter(tmp_path, monkeypatch):
    from simulatte.projects import bootstrap
    from simulatte.studio import command

    original = tmp_path / "source" / "study"
    (original / ".venv/bin").mkdir(parents=True)
    interpreter = original / ".venv/bin/python"
    interpreter.write_text("")  # stands in for the project's own virtual environment; never started
    runtime = {"facts": {"python": "3.14.0"}, "dependencies": {"packages": {}}, "warnings": []}
    with RunStore.open(original / ".simulatte") as store:
        pool = EnvironmentPool(original, store)
        monkeypatch.setattr(pool, "_probe", lambda path: {"runtime": runtime, "distribution_roots": [], "studio": True})
        pool.activate(pool.use_existing(interpreter).id)
    copy = tmp_path / "elsewhere" / "study"
    shutil.copytree(original, copy, symlinks=True)
    assert original.exists()  # the original is still present on this machine
    monkeypatch.setattr(bootstrap, "validate_runtime", lambda path: None)
    # In the interpreter it registered, the original opens for writing; the copy opens in recovery.
    monkeypatch.setattr(sys, "executable", str(interpreter))
    for root, error in ((original, None), (copy, UNAVAILABLE)):
        service = ProjectService.open(StudioTarget(root, None))
        try:
            assert (service.read_only, service.error) == (error is not None, error), root
        finally:
            service.close()
    monkeypatch.undo()
    # The launcher never hands the copy off to the original's interpreter.
    monkeypatch.setattr(bootstrap, "validate_runtime", lambda path: None)
    monkeypatch.setattr(bootstrap, "handoff", lambda *args, **kwargs: pytest.fail("handed off to the original"))
    served = []
    monkeypatch.setattr(command, "serve_project", lambda service, **kw: served.append(service.error))
    assert bootstrap.selected_interpreter(StudioTarget(original, None)) == interpreter
    assert bootstrap.selected_interpreter(StudioTarget(copy, None)) is None
    bootstrap.launch_project(StudioTarget(copy, None), port=0, no_browser=True)
    assert served == [UNAVAILABLE]


def test_an_interpreter_of_another_simulatte_project_is_foreign(tmp_path):
    from simulatte.projects.environments import interpreter_of_another_project as foreign

    other, plain, outside = tmp_path / "other", tmp_path / "plain", tmp_path / "outside"
    for folder in (other / ".simulatte", other / "nested/.simulatte", plain / "nested/.simulatte"):
        folder.mkdir(parents=True)
    (plain / "pyproject.toml").write_text("[project]\nname = 'shared'\n")
    python = Path(".venv/bin/python")
    assert foreign(other / "nested", other / "nested" / python) is False  # inside the opened project
    assert foreign(other / "nested", other / python) is False  # the enclosing project's environment
    assert foreign(plain / "nested", other / python) is True  # another project, e.g. a copy's original
    assert foreign(other, plain / python) is False  # a shared development environment
    assert foreign(other, outside / "python3.14/bin/python") is False  # a standalone runtime


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    root = tmp_path_factory.mktemp("template") / "study"
    return root, build(root)


@pytest.fixture
def study(template, tmp_path):
    source, built = template
    root = tmp_path / "study"
    shutil.copytree(source, root, symlinks=True)
    return root, built


def chain(*nodes):
    return [{"kind": kind, "id": identity} for kind, identity in nodes]


def protections(pruned):
    return {item["result"]: item["protected_by"] for item in pruned["protected"]}


def test_dry_run_names_owner_chains_and_shared_evidence_survives_until_its_final_owner(study, capsys):
    root, built = study
    assert main(["store", "check", "--project", str(root)]) == 0
    assert capsys.readouterr().out.startswith("verified 6 results and 2 analyses")
    with RunStore.open(root / ".simulatte") as store:
        pool = EnvironmentPool(root, store)
        replications = store.replications(built.run_id)
        kept = next(r["attempt"] for r in replications if (r["configuration"], r["index"]) == (0, 0))
        request = next(r["request_id"] for r in replications if r["attempt"] == kept)
        single = analyse(store, pool, Selection(run_ids=(kept,)), built.generation)
        presentations = PresentationStore(store)
        items = [evidence(store, single), evidence(store, AnalysisStore(store).execution(built.full))]
        other = presentations.create(presentation_manifest(store, built.interactive, items))
        plain = store.prune(dry_run=True)
        assert plain["results"] == [] and "protected" not in plain
    # The dry run names, for each kept result, every owner chain from its root owner down to it.
    assert main(["store", "prune", "--dry-run", "--project", str(root), "--json"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["results"] == []
    owners = protections(dry)
    assert set(owners) == {r["attempt"] for r in replications if r["status"] == "succeeded"} | {built.interactive}
    for expected in (
        chain(("experiment", built.run_id), ("replication", f"{built.run_id}/0/0")),
        chain(("presentation-revision", f"{built.presentation}:1"), ("analysis", built.partial), ("result", kept)),
        chain(("presentation-revision", f"{built.presentation}:2"), ("analysis", built.full), ("result", kept)),
        chain(("presentation-revision", f"{other}:1"), ("analysis", single.id), ("result", kept)),
        chain(("presentation-revision", f"{other}:1"), ("analysis", built.full), ("request", request)),
    ):
        assert expected in owners[kept]
    interactive = owners[built.interactive]
    assert chain(("presentation-revision", f"{built.presentation}:1"), ("result", built.interactive)) in interactive
    assert chain(("recent-interactive", built.interactive)) in interactive
    assert main(["store", "prune", "--dry-run", "--project", str(root)]) == 0
    text = capsys.readouterr().out
    assert text.startswith("would remove 0 results and ") and "; 6 results kept" in text

    # Explicit deletion goes owner by owner; shared evidence stays protected until its final owner is gone.
    assert main(["experiment", "delete", built.run_id, "--project", str(root), "--json"]) == 2
    assert "referenced by saved analysis" in json.loads(capsys.readouterr().out)["message"]
    with RunStore.open(root / ".simulatte") as store:
        analysis, presentations = AnalysisStore(store), PresentationStore(store)
        job = presentations.admit_export(other, 1, "studio", {}, [])
        with pytest.raises(ValueError, match="active exports"):
            presentations.delete(other)
        presentations.cancel_export(job.id)
        presentations.delete(built.presentation)
        with pytest.raises(ValueError, match="referenced"):
            analysis.delete(built.full)  # the other presentation still selects it
        owners = protections(store.prune(dry_run=True, explain=True))
        assert (
            chain(("presentation-revision", f"{other}:1"), ("analysis", built.full), ("result", kept)) in owners[kept]
        )
        assert not any(owner[0]["id"].startswith(built.presentation) for owner in owners[kept])
        analysis.delete(built.partial)
        presentations.delete(other)
        analysis.delete(built.full)
    assert main(["experiment", "delete", built.run_id, "--project", str(root), "--json"]) == 0
    removed = json.loads(capsys.readouterr().out)["results"]
    assert len(removed) == 4 and kept not in removed
    with RunStore.open(root / ".simulatte") as store:
        owners = protections(store.prune(dry_run=True, explain=True))
        assert owners[kept] == [
            chain(("analysis", single.id), ("attempt", kept)),
            chain(("analysis", single.id), ("request", request)),
            chain(("analysis", single.id), ("result", kept)),
        ]
        assert store.prune()["results"] == []
        AnalysisStore(store).delete(single.id)
        assert store.prune()["results"] == [kept]
        with pytest.raises(KeyError):
            store.result(kept)
        assert store.result(built.interactive)


def test_environment_cleanup_refuses_owners_and_keeps_reconstruction_evidence(study, monkeypatch):
    root, built = study
    (root / "uv.lock").write_text("version = 1\n")
    definition = load_workspace(root)
    before = inventory(root)
    with RunStore.open(root / ".simulatte") as store:
        pool = fake_pool(definition, store, monkeypatch)
        with pytest.raises(ValueError, match="selected"):
            pool.cleanup(built.generation)
        managed = pool.prepare(definition)
        pool.acquire(managed.id, "analysis:active")
        with pytest.raises(ValueError, match="owned"):
            pool.cleanup(managed.id)
        pool.release("analysis:active")
        # A saved analysis that names the generation is a durable owner.
        analysis = AnalysisStore(store)
        execution = admit(store, root, Selection(run_ids=(built.interactive,)), managed.id)
        analysis.cancel(execution.id)
        with pytest.raises(ValueError, match="referenced"):
            pool.cleanup(managed.id)
        analysis.delete(execution.id)
        pool.cleanup(managed.id)
        assert not environment_directory(store.root / "cache/environments", managed.id).exists()
        # Durable reconstruction evidence stays in history; the generation rebuilds to the same identity.
        assert pool.get(managed.id).evidence == managed.evidence
        rebuilt = pool.reconstruct(managed.evidence)
        assert rebuilt.id == managed.id and rebuilt.interpreter.read_bytes() == b"captured interpreter"
    after = inventory(root)
    for part in ("history", "results", "experiments", "analyses", "presentations"):
        assert after[part] == before[part], part


def test_cache_cleanup_never_deletes_durable_study_results(study, capsys):
    root, built = study
    before = inventory(root)
    store = root / ".simulatte"
    (store / "analysis-cache" / built.partial).mkdir(parents=True)
    (store / "analysis-cache" / built.partial / "rows.index").write_bytes(b"derived")
    (store / "cache/environments/rebuildable").mkdir(parents=True)
    assert main(["store", "prune", "--project", str(root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["results"] == []
    assert not (store / "analysis-cache").exists()
    # The environment cache is rebuildable state: removing all of it keeps every study result.
    shutil.rmtree(store / "cache")
    assert inventory(root) == before
    assert main(["store", "check", "--project", str(root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["problems"] == []
    with RunStore.open(store) as writer:
        pool = EnvironmentPool(root, writer)
        assert pool.selected.id == built.generation
        assert pool.reconstruct(pool.selected.evidence).id == built.generation


def test_check_and_dry_run_name_every_file_problem_and_protection(tmp_path, capsys):
    root = tmp_path / ".simulatte"
    shutil.copytree(FIXTURE, root)  # schema 1: one interactive result shown by a view
    with RunStore.open(root) as store:
        revision = store.current_revision()
        assert revision is not None
        keys = []
        for seed in (1, 2, 3):
            key = store.assign(
                replace(seeded(9, seed), layout_revision=revision.number), revision, slot="local-1", pinned=True
            )
            advance(store, key, "finalizing")
            commit(store, key, float(seed))  # its manifest records no trace hash
            keys.append(key)
        original, replay, important = keys
        store.put_replay(replay.request_id, original.request_id, attempt=replay.attempt_id)
        ProjectStore(store).annotate(important.attempt_id, name="", text="", important=True)
        artifacts = root / "results" / original.attempt_id / "artifacts"
        artifacts.mkdir()
        (artifacts / "good.txt").write_bytes(b"ok")
        (artifacts / "short.txt").write_bytes(b"ab")
        (artifacts / "folder").mkdir()
        (artifacts / "link").symlink_to(artifacts / "good.txt")
        ok = hashlib.sha256(b"ok").hexdigest()
        with store._transaction() as db:
            db.executemany(
                "INSERT INTO artifacts VALUES (?,?,?,?)",
                [
                    (original.attempt_id, "good.txt", 2, ok),
                    (original.attempt_id, "short.txt", 3, hashlib.sha256(b"abc").hexdigest()),
                    (original.attempt_id, "folder", 0, "0" * 64),
                    (original.attempt_id, "link", 2, ok),
                ],
            )
            (view,) = db.execute("SELECT revision FROM views WHERE attempt=?", (FIXTURE_ATTEMPT,)).fetchone()
        owners = protections(store.prune(dry_run=True, explain=True))
    assert owners == {
        FIXTURE_ATTEMPT: [chain(("recent-interactive", FIXTURE_ATTEMPT)), chain(("view", str(view)))],
        original.attempt_id: [chain(("replay", replay.request_id), ("request", original.request_id))],
        replay.attempt_id: [chain(("replay", replay.request_id))],
        important.attempt_id: [chain(("important-annotation", important.attempt_id))],
    }
    reader = StoreReader.open(root)
    try:
        assert [(item["name"], item["problem"]) for item in reader.check()] == [
            ("folder", "unsafe"),
            ("link", "unsafe"),
            ("short.txt", "changed"),
        ]
    finally:
        reader.close()
    # A store from before analyses has no analysis outputs to check.
    older = tmp_path / "older"
    shutil.copytree(Path(__file__).parents[1] / "fixtures/stores/project-v2", older / ".simulatte")
    assert main(["store", "check", "--project", str(older), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "succeeded", "problems": []}
    assert main(["store", "check", "--project", str(tmp_path / "nowhere"), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "validation_failed"
    (older / ".simulatte/index.sqlite").write_bytes(b"not a database")
    assert main(["store", "check", "--project", str(older)]) == 1
    assert capsys.readouterr().err


def selected_generation(root: Path, monkeypatch):
    """A selected installation-v1 generation of ``root`` (fake uv and probes, also for the CLI's own pool)."""
    (root / "uv.lock").write_text("version = 1\n")
    with RunStore.open(root / ".simulatte") as store:
        pool = fake_pool(WorkspaceDefinition(root, str(uuid4()), (), (), None), store, monkeypatch)
        generation = pool.prepare(WorkspaceDefinition(root, str(uuid4()), (), (), None))
        pool.activate(generation.id)
        bindings = pool.installation_bindings(generation)
        generation = pool.get(generation.id)
    for name in ("_probe", "_probe_installation"):
        monkeypatch.setattr(EnvironmentPool, name, staticmethod(getattr(pool, name)))
    return generation, bindings


def capture_children(monkeypatch) -> list:
    """Children that advertise installation-runtime-v1, record each command and then lose their stream."""
    from simulatte.execution import coordinator as module
    from tests.execution.test_scheduler import Peer

    sent = []

    def start(*args, **kwargs):
        peer = Peer()
        receive = peer.receive

        def hello(timeout):
            message = receive(timeout)
            message.body["capabilities"].append("installation-runtime-v1")
            return message

        peer.receive = hello
        peer.send = lambda kind, body, **kw: sent.append((kind, body))
        return peer

    monkeypatch.setattr(module.LocalTransport, "start", start)
    return sent


def test_cli_run_in_the_selected_generation_records_its_installation_evidence(tmp_path, monkeypatch, capsys):
    from unittest.mock import patch
    from simulatte.execution.references import read_document
    from tests.execution.test_coordinator import project
    from tests.execution.test_experiment_cli import NO_WEB, envelope

    spec = project(tmp_path)
    generation, bindings = selected_generation(spec.root, monkeypatch)
    sent = capture_children(monkeypatch)
    command = ["run", str(spec.root / "plant.py"), "--project", str(spec.root), "--json"]
    with patch.dict(sys.modules, NO_WEB):
        # Another interpreter keeps its own raw evidence; it never borrows the generation's.
        assert main(command) == 1
        assert envelope(capsys)["status"] == "lost"
        monkeypatch.setattr(sys, "executable", str(generation.interpreter))
        assert main(command) == 1
        assert envelope(capsys)["status"] == "lost"
    other, inside = [body for kind, body in sent if kind == "inspect"]
    assert "kind" not in read_document(other["inspection_ref"], "request")["runtime"]
    assert "installation_bindings" not in other["config"]
    assert read_document(inside["inspection_ref"], "request")["runtime"]["kind"] == "local-installation-v1"
    evidence = read_document(inside["config"]["runtime_ref"], "runtime")
    assert (evidence["format"], evidence["bindings"]) == ("installation-v1", bindings)
    assert inside["config"]["installation_bindings"] == bindings
    assert Path(inside["config"]["installation_prefix"]) == generation.interpreter.parent.parent


def test_cli_replay_in_a_rebuilt_copy_binds_its_verified_rebuilt_artifacts(tmp_path, monkeypatch, capsys):
    from unittest.mock import patch
    from simulatte.execution.hashing import canonical_hash
    from tests.execution.test_coordinator import project
    from tests.execution.test_experiment_cli import NO_WEB, envelope

    spec = project(tmp_path)
    generation, captured = selected_generation(spec.root, monkeypatch)
    # An original request with the generation's installation evidence, committed at the original location.
    with Coordinator(spec) as co:
        prepared = co.prepare(timeout=10)
        identity = {
            "format": "installation-v1",
            "facts": dict(prepared.runtime["facts"]),
            "dependencies": {"packages": {}},
            "warnings": [],
        }
        runtime = {
            "kind": "local-installation-v1",
            "facts": identity["facts"],
            "dependencies_hash": canonical_hash("installation-dependencies-v1", identity["dependencies"]),
            "trusted_runtime_hash": co.runtime.content_hash,
        }
        evidence = {"format": "installation-v1", "identity": identity, "raw": {}, "bindings": captured}
        documents = {**prepared.documents, "runtime_ref": co._document("runtime", evidence)}
        request = co.make_request(replace(prepared, runtime=runtime, documents=documents))
    with RunStore.open(spec.root / ".simulatte") as store:
        key = store.assign(request, prepared.revision)
        advance(store, key, "finalizing")
        commit(store, key, 1.0)
    copy = tmp_path / "copy"
    shutil.copytree(spec.root, copy, ignore=shutil.ignore_patterns("cache"))
    with RunStore.open(copy / ".simulatte") as store:
        pool = fake_pool(WorkspaceDefinition(copy, str(uuid4()), (), (), None), store, monkeypatch)
        rebuilt = pool.reconstruct(generation.evidence)
        bindings = pool.installation_bindings(rebuilt)
        selected = pool.selected.id
    # The rebuilt bindings name other staging files, bound to the same verified captured content.
    assert rebuilt.id == selected == generation.id
    assert set(bindings).isdisjoint(captured) and sorted(bindings.values()) == sorted(captured.values())
    monkeypatch.setattr(sys, "executable", str(rebuilt.interpreter))
    sent = capture_children(monkeypatch)
    with patch.dict(sys.modules, NO_WEB):
        assert main(["replay", key.request_id, "--project", str(copy), "--json"]) == 1
    assert envelope(capsys)["status"] == "lost"
    (config,) = [body["config"] for kind, body in sent if kind == "run"]
    assert config["installation_bindings"] == bindings
    assert Path(config["installation_prefix"]) == rebuilt.interpreter.parent.parent


def test_cli_experiments_in_the_selected_generation_record_its_installation_evidence(tmp_path, monkeypatch, capsys):
    from unittest.mock import patch
    from simulatte.projects import bootstrap
    from simulatte.execution.hashing import canonical_hash
    from simulatte.execution.references import read_document
    from tests.execution.test_coordinator import project
    from tests.execution.test_experiment_cli import NO_WEB, envelope

    spec = project(tmp_path)
    generation, bindings = selected_generation(spec.root, monkeypatch)
    # Studio's launcher, too, hands this project to its managed generation's own interpreter.
    assert bootstrap.selected_interpreter(StudioTarget(spec.root, None)) == generation.interpreter
    path = write_experiment(spec.root, replications={"count": 1}, configurations={"grid": {"n": [3]}})
    # An interrupted run started inside the generation (as Studio does): its preparation names it.
    with Coordinator(spec) as co:
        prepared = co.prepare(timeout=10)
        identity = {"format": "installation-v1", "facts": dict(prepared.runtime["facts"])}
        identity |= {"dependencies": {"packages": {}}, "warnings": []}
        runtime = {
            "kind": "local-installation-v1",
            "facts": identity["facts"],
            "dependencies_hash": canonical_hash("installation-dependencies-v1", identity["dependencies"]),
            "trusted_runtime_hash": co.runtime.content_hash,
        }
        evidence = {"format": "installation-v1", "identity": identity, "raw": {}, "bindings": bindings}
        documents = {**prepared.documents, "runtime_ref": co._document("runtime", evidence)}
        co.environments = EnvironmentPool(spec.root, co.store)
        monkeypatch.setattr(co, "prepare", lambda timeout=None: replace(prepared, runtime=runtime, documents=documents))
        monkeypatch.setattr(co, "_activate_experiment", lambda run_id, timeout=None: None)
        paused = co.start_experiment(load_experiment(path))
    with RunStore.open(spec.root / ".simulatte") as store:
        stored = store.experiment_run(paused)["preparation"]
    assert stored["environment"] == generation.id
    sent = capture_children(monkeypatch)
    resume = ["experiment", "resume", paused, "--project", str(spec.root), "--local-slots", "1", "--json"]
    start = ["experiment", "run", str(path), "--project", str(spec.root), "--local-slots", "1", "--json"]
    with patch.dict(sys.modules, NO_WEB):
        # Another interpreter keeps its own raw evidence and cannot resume the generation's run.
        assert main(resume) == 2
        assert envelope(capsys)["message"].startswith("Resume requires the experiment captured environment")
        assert main(start) == 1
        assert envelope(capsys)["status"] == "lost"
        monkeypatch.setattr(sys, "executable", str(generation.interpreter))
        assert main(start) == 1
        assert envelope(capsys)["status"] == "lost"
        assert main(resume) == 1  # the fake children lose every dispatch
        resumed = envelope(capsys)
        assert (resumed["run_id"], resumed["status"], resumed["counts"]["lost"]) == (paused, "failed", 1)
    other, inside = [body for kind, body in sent if kind == "inspect"]
    assert "kind" not in read_document(other["inspection_ref"], "request")["runtime"]
    assert "installation_bindings" not in other["config"]
    assert read_document(inside["inspection_ref"], "request")["runtime"]["kind"] == "local-installation-v1"
    assert read_document(inside["config"]["runtime_ref"], "runtime")["bindings"] == bindings
    assert inside["config"]["installation_bindings"] == bindings
    # Resume dispatches the stored requests inside the generation and leaves the preparation as stored.
    runs = [body for kind, body in sent if kind == "run"]
    assert runs and all(body["config"]["installation_bindings"] == bindings for body in runs)
    assert all(Path(body["config"]["installation_prefix"]) == generation.interpreter.parent.parent for body in runs)
    assert all(read_document(body["request_ref"], "request")["runtime"] == runtime for body in runs)
    with RunStore.open(spec.root / ".simulatte") as store:
        assert store.experiment_run(paused)["preparation"] == stored
