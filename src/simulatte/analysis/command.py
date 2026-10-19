"""Headless API and CLI. Project code is inspected only in a contained child."""

from __future__ import annotations
from contextlib import closing
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
from uuid import UUID, uuid4

from simulatte.execution.process import ContainedProcess
from simulatte.execution.snapshot import capture_source, discover_project, _extract
from simulatte.execution.store import RunStore, StoreBusy, _blob
from simulatte.execution.store_reader import StoreReader
from simulatte.model import ParameterSchema, parse_parameters
from simulatte.projects.config import load_workspace
from simulatte.projects.environments import EnvironmentPool
from . import codec
from .context import write_data, MAX_MANIFEST_BYTES
from .definition import AnalysisDefinition, DatasetRequirement
from .execution import AnalysisRunner
from .selection import Selection
from .store import AnalysisStore


def prepare_analysis(store, key, *, environments=None, generation=None, timeout=30):
    """Capture and inspect a registered definition using the shared P1 inspection capacity."""
    workspace = load_workspace(store.root.parent)
    registration = None if workspace is None else next((r for r in workspace.analyses if r.key == key), None)
    if registration is None:
        raise ValueError(f"Unknown analysis registration: {key}")
    assert workspace is not None
    environments = environments or EnvironmentPool(store.root.parent, store)
    generation = generation or environments.selected or environments.use_existing(sys.executable)
    owner = "analysis-inspection-" + str(uuid4())
    with store._inspection_lock:
        generation = environments.acquire(generation.id, owner)
        try:
            project = discover_project(workspace.root / registration.entrypoint.partition(":")[0], workspace.root)
            # Source capture and its temporary reference are indivisible with respect to prune.
            with store._mutex:
                source = capture_source(project)
                with store._transaction() as db:
                    db.execute(
                        "INSERT OR IGNORE INTO snapshots VALUES (?,?,?)",
                        (source.content_hash, "analysis-source", _blob({"source_hash": source.content_hash})),
                    )
                    db.execute(
                        'INSERT INTO "references" VALUES (?,?,?,?)',
                        ("analysis-inspection", owner, "snapshot", source.content_hash),
                    )
            with tempfile.TemporaryDirectory(dir=store.root / "analysis-staging") as temporary:
                directory = Path(temporary)
                captured = directory / "source"
                captured.mkdir()
                _extract(source, captured)
                config = {
                    "inspect": True,
                    "source": str(captured),
                    "python_path": project.python_path,
                    "project_root": str(project.root),
                    "entrypoint": registration.entrypoint,
                    "definition_id": registration.id,
                    "source_hash": source.content_hash,
                    "output": str(directory / "definition.json"),
                }
                write_data(directory / "config.json", config)
                child = ContainedProcess.start(
                    [
                        str(generation.interpreter),
                        "-I",
                        "-B",
                        "-m",
                        "simulatte.analysis.child",
                        str(directory / "config.json"),
                    ],
                    cwd=directory,
                )
                diagnostics, lock = bytearray(), threading.Lock()

                def drain(stream):
                    while block := stream.read(65536):
                        with lock:
                            diagnostics.extend(block)
                            del diagnostics[:-8192]

                drains = [
                    threading.Thread(target=drain, args=(stream,), daemon=True)
                    for stream in (child.stdout, child.stderr)
                ]
                for thread in drains:
                    thread.start()
                try:
                    code = child.wait(timeout=timeout)
                    child.close()
                    for thread in drains:
                        thread.join(5)
                    if code:
                        raise ValueError("Analysis inspection failed: " + diagnostics.decode(errors="replace"))
                    path = directory / "definition.json"
                    if path.is_symlink() or path.stat().st_size > MAX_MANIFEST_BYTES:
                        raise ValueError("Invalid analysis metadata")
                    body = codec.loads(path.read_bytes())
                    if (body["id"], body["entrypoint"], body["source_hash"]) != (
                        registration.id,
                        registration.entrypoint,
                        source.content_hash,
                    ):
                        raise ValueError("Analysis metadata identity mismatch")
                    definition = AnalysisDefinition(
                        body["id"],
                        body["entrypoint"],
                        body["source_hash"],
                        ParameterSchema.from_dict(body["parameters"]),
                        tuple(
                            DatasetRequirement(
                                r["dataset"], tuple(r["fields"]), r["min_replications"], r["require_complete"]
                            )
                            for r in body["requirements"]
                        ),
                    )
                    environments._verify(generation)
                    identity = AnalysisStore(store).put_definition(definition, source=source)
                    return identity, generation.id
                finally:
                    child.close()
                    for thread in drains:
                        thread.join(5)
        finally:
            with store._transaction() as db:
                db.execute('DELETE FROM "references" WHERE owner_kind=? AND owner_id=?', ("analysis-inspection", owner))
            environments.release(owner)


def add_arguments(parser):
    actions = parser.add_subparsers(dest="analysis_action", required=True)
    for action in ("run", "rerun", "refresh", "show", "cancel"):
        command = actions.add_parser(action)
        command.add_argument("identity")
        command.add_argument("--project", type=Path, default=Path.cwd())
        command.add_argument("--json", action="store_true")
        if action == "run":
            command.add_argument("--run", dest="runs", action="append", default=[])
            command.add_argument("--experiment", dest="experiments", action="append", default=[])
            command.add_argument("--configuration", type=int, action="append")
            command.add_argument("--require-complete", action="store_true")
            command.add_argument("--min-replications", type=int, default=0)
            command.add_argument("--param", action="append", default=[])


def _read(root, identity):
    with closing(StoreReader.open(root)) as reader:
        if reader.version != 4:
            raise ValueError("Analysis needs a schema4 store")
        row = reader._db.execute(
            "SELECT id,definition,snapshot,generation,state,details FROM analysis_executions WHERE id=?", (identity,)
        ).fetchone()
        if row is None:
            raise KeyError(identity)
        from .store import AnalysisExecution

        execution = AnalysisExecution(*tuple(row)[:5])
        output = reader._db.execute("SELECT manifest FROM analysis_outputs WHERE execution=?", (identity,)).fetchone()
        return {
            "execution": asdict(execution),
            "details": codec.to_json(codec.loads(row["details"])),
            "output": None if output is None else codec.to_json(codec.loads(output[0])),
        }


def request_cancel(root, identity):
    """A bounded local control request; only the owning writer changes execution state."""
    if str(UUID(identity)) != identity:
        raise ValueError("Invalid execution UUID")
    current = _read(root, identity)
    if current["execution"]["state"] not in ("waiting", "running", "finalizing"):
        return current
    path = root / "analysis-control" / (identity + ".cancel")
    if path.is_symlink():
        raise ValueError("Unsafe cancellation control file")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600) if not path.exists() else None
    if fd is not None:
        os.close(fd)
    return {**current, "cancellation_requested": True}


def command(args):
    root = args.project.resolve() / ".simulatte"
    code, envelope = 0, {}
    try:
        if args.analysis_action == "show":
            envelope = _read(root, args.identity)
        elif args.analysis_action == "cancel":
            try:
                with RunStore.open(root) as store:
                    analysis = AnalysisStore(store)
                    analysis.cancel(args.identity)
            except StoreBusy:
                envelope = request_cancel(root, args.identity)
            else:
                envelope = _read(root, args.identity)
        else:
            with RunStore.open(root) as store:
                analysis, environments = AnalysisStore(store), EnvironmentPool(root.parent, store)
                if args.analysis_action == "run":
                    selection = Selection(
                        tuple(args.runs),
                        tuple(args.experiments),
                        None if args.configuration is None else tuple(args.configuration),
                        args.require_complete,
                        args.min_replications,
                    )
                    definition, generation = prepare_analysis(store, args.identity, environments=environments)
                    raw = []
                    for param in args.param:
                        key, separator, value = param.partition("=")
                        if not separator:
                            raise ValueError("Parameters use NAME=VALUE")
                        raw.append((key, value))
                    parameters = parse_parameters(
                        ParameterSchema.from_dict(analysis.definition(definition)["parameters"]), raw
                    )
                    item = analysis.admit(
                        analysis.preview(selection).id,
                        definition_id=definition,
                        parameters=parameters,
                        generation_id=generation,
                    )
                elif args.analysis_action == "rerun":
                    item = analysis.rerun(args.identity)
                else:
                    old = analysis.execution(args.identity)
                    with store._transaction() as db:
                        parameters = codec.loads(
                            db.execute("SELECT parameters FROM analysis_executions WHERE id=?", (old.id,)).fetchone()[0]
                        )
                    item = analysis.admit(
                        analysis.refresh(old.id).id,
                        definition_id=old.definition_id,
                        parameters=parameters,
                        generation_id=old.generation_id,
                    )
                with AnalysisRunner(store, environments=environments) as runner:
                    runner.submit(item.id)
                    try:
                        result = runner.wait(item.id)
                    except KeyboardInterrupt:
                        runner.cancel(item.id)
                        result = runner.wait(item.id)
                code = 0 if result.state == "succeeded" else 130 if result.state == "cancelled" else 1
            envelope = _read(root, item.id)
    except (ValueError, KeyError, ImportError) as error:
        code, envelope = 2, {"status": "validation_failed", "message": str(error)[-8192:]}
    except (OSError, RuntimeError, sqlite3.Error, TimeoutError) as error:
        code, envelope = 1, {"status": "failed", "message": str(error)[-8192:]}
    if args.json:
        print(json.dumps(envelope, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
    else:
        print(envelope.get("message") or json.dumps(envelope, ensure_ascii=True, allow_nan=False), file=sys.stderr)
    return code
