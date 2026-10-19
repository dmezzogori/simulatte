"""Immutable analytical evidence and atomic admission through the project writer."""

from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
import time
from uuid import uuid4

from simulatte.execution.project_store import require_reference
from simulatte.execution.store import RunStore, _read, _remove
from simulatte.model import ParameterSchema, validate_parameters
from . import codec
from .datasets import DatasetManifest
from .definition import AnalysisDefinition
from .selection import Selection
from .types import Observation


# Replications named in an admission refusal message; details keep all of them.
LISTED_REPLICATIONS = 10


class InsufficientInput(ValueError):
    """Admission refusal with the per-dataset evidence that explains it."""

    def __init__(self, message: str, details: dict):
        super().__init__(message)
        self.details = details


@dataclass(frozen=True, slots=True)
class InputPreview:
    id: str
    snapshot_hash: str
    available: int
    planned: int
    missing: int
    partial: bool


@dataclass(frozen=True, slots=True)
class AnalysisExecution:
    id: str
    definition_id: str
    snapshot_hash: str
    generation_id: str
    state: str


class AnalysisStore:
    def __init__(self, store: RunStore):
        self.store = store

    def put_definition(self, definition: AnalysisDefinition, *, source=None) -> str:
        body = definition.to_dict()
        if source is not None:
            if (
                source.content_hash != definition.source_hash
                or source.project.root.resolve() != self.store.root.parent.resolve()
            ):
                raise ValueError("Analysis source identity/project mismatch")
            body["capture"] = {"python_path": list(source.project.python_path)}
        identity = codec.identity(body)
        with self.store._transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO analysis_definitions VALUES (?,?,?)",
                (identity, definition.id, codec.dumps(body)),
            )
            if source is not None:
                from simulatte.execution.store import _blob

                db.execute(
                    "INSERT OR IGNORE INTO snapshots VALUES (?,?,?)",
                    (source.content_hash, "analysis-source", _blob({"source_hash": source.content_hash})),
                )
                db.execute(
                    'INSERT OR IGNORE INTO "references" VALUES (?,?,?,?)',
                    ("analysis-definition", identity, "snapshot", source.content_hash),
                )
        return identity

    def output(self, identity):
        with self.store._transaction() as db:
            row = db.execute("SELECT manifest FROM analysis_outputs WHERE execution=?", (identity,)).fetchone()
            if row is None:
                raise KeyError(identity)
            return Observation(codec.loads(row[0]))

    def definition(self, identity):
        with self.store._transaction() as db:
            return self._body(db, "analysis_definitions", identity)

    @staticmethod
    def _body(db, table, identity):
        row = db.execute(f"SELECT body FROM {table} WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise KeyError(identity)
        return codec.loads(row[0])

    def snapshot(self, identity):
        with self.store._transaction() as db:
            return Observation(self._body(db, "analysis_input_snapshots", identity))

    def _result(self, db, attempt):
        row = db.execute(
            "SELECT r.manifest,r.fingerprint,a.request_id,q.body FROM results r "
            "JOIN attempts a ON a.id=r.attempt JOIN requests q ON q.id=a.request_id WHERE r.attempt=?",
            (attempt,),
        ).fetchone()
        if row is None:
            raise ValueError("Evidence removed; select a fresh preview")
        root = self.store.root / "results" / attempt
        if not root.is_dir() or root.is_symlink():
            raise ValueError("Evidence files removed; select a fresh preview")
        artifacts, datasets, failures = [], {}, {}
        for item in db.execute("SELECT path,length,sha256 FROM artifacts WHERE attempt=? ORDER BY path", (attempt,)):
            descriptor = dict(item)
            relative = PurePosixPath(item["path"])
            path = (root / "artifacts").joinpath(*relative.parts)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or path.is_symlink()
                or not path.resolve().is_relative_to(root.resolve())
            ):
                raise ValueError("Unsafe evidence path")
            # Metadata/file identity checked before admission; chunk contents are verified by readers.
            if not path.is_file() or path.stat().st_size != item["length"]:
                raise ValueError("Evidence files removed; select a fresh preview")
            artifacts.append(descriptor)
            if relative.name in ("manifest.json", "failure.json") and "datasets" in relative.parts:
                if item["length"] > 4 << 20:
                    raise ValueError("Dataset manifest exceeds 4 MiB")
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                    raise ValueError("Dataset manifest changed; select a fresh preview")
                if relative.name == "failure.json":
                    # A published failure record invalidates the dataset, even beside a manifest.
                    failures[relative.parent.name] = str(json.loads(raw)["failure"])[:4096]
                    continue
                manifest = DatasetManifest.from_dict(json.loads(raw))
                if manifest.schema.name in datasets:
                    raise ValueError("Duplicate dataset name")
                datasets[manifest.schema.name] = {"path": str(relative.parent), "manifest": manifest.to_dict()}
        for name in failures:
            datasets.pop(name, None)
        indexed = {a["path"]: a for a in artifacts}
        for dataset in datasets.values():
            for chunk in dataset["manifest"]["chunks"]:
                path = str(PurePosixPath(dataset["path"]) / chunk["path"])
                if indexed.get(path) != {"path": path, "length": chunk["length"], "sha256": chunk["sha256"]}:
                    raise ValueError("Dataset chunk is not published; select a fresh preview")
        return {
            "request_id": row["request_id"],
            "request": _read(row["body"]),
            "manifest": _read(row["manifest"]),
            "fingerprint": _read(row["fingerprint"]),
            "artifacts": artifacts,
            "datasets": datasets,
            **({"dataset_failures": failures} if failures else {}),
            "scalars": {
                r["name"]: _read(r["value"])
                for r in db.execute("SELECT name,value FROM kpis WHERE attempt=? ORDER BY name", (attempt,))
            },
        }

    def preview(self, selection: Selection) -> InputPreview:
        with self.store._transaction() as db:
            replications, configurations, experiments = [], [], []
            for run in selection.experiment_run_ids:
                record = db.execute(
                    "SELECT r.*,e.name,e.definition FROM experiment_runs r "
                    "JOIN experiments e ON e.id=r.experiment WHERE r.id=?",
                    (run,),
                ).fetchone()
                if record is None:
                    raise KeyError(run)
                experiments.append(
                    {
                        "id": run,
                        "name": record["name"],
                        "status": record["status"],
                        "definition": _read(record["definition"]),
                        "preparation": _read(record["preparation"]),
                    }
                )
                configs = db.execute("SELECT * FROM configurations WHERE run=? ORDER BY idx", (run,)).fetchall()
                if selection.configurations is not None and not set(selection.configurations) <= {
                    c["idx"] for c in configs
                }:
                    raise ValueError("Unknown configuration index")
                for c in configs:
                    if selection.configurations is None or c["idx"] in selection.configurations:
                        configurations.append(
                            {
                                "run": run,
                                "index": c["idx"],
                                "label": c["label"],
                                "parameters": _read(c["parameters"]),
                                "layout_hash": c["layout_hash"],
                                "layout_ref": _read(c["layout_ref"]),
                            }
                        )
                for r in db.execute("SELECT * FROM replications WHERE run=? ORDER BY idx,configuration", (run,)):
                    if selection.configurations is None or r["configuration"] in selection.configurations:
                        replications.append(
                            self._replication(
                                db, run, r["configuration"], r["idx"], int(r["seed"]), r["status"], r["attempt"]
                            )
                        )
            for index, attempt in enumerate(selection.run_ids):
                row = db.execute(
                    "SELECT a.status,q.body FROM attempts a JOIN requests q ON q.id=a.request_id WHERE a.id=?",
                    (attempt,),
                ).fetchone()
                if row is None:
                    raise KeyError(attempt)
                configurations.append(
                    {
                        "run": attempt,
                        "index": 0,
                        "label": attempt,
                        "parameters": _read(row["body"]).get("parameters", {}),
                    }
                )
                replications.append(
                    self._replication(db, attempt, 0, index, _read(row["body"]).get("seed"), row["status"], attempt)
                )
            available = sum(r["available"] for r in replications)
            body = {
                "schema": "analysis-input-v1",
                "selection": selection.to_dict(),
                "experiments": experiments,
                "configurations": configurations,
                "replications": replications,
                "available": available,
                "planned": len(replications),
                "partial": available != len(replications),
            }
            identity, preview = codec.identity(body), str(uuid4())
            db.execute("INSERT OR IGNORE INTO analysis_input_snapshots VALUES (?,?)", (identity, codec.dumps(body)))
            db.execute("INSERT INTO analysis_previews VALUES (?,?)", (preview, identity))
        return InputPreview(
            preview, identity, available, len(replications), len(replications) - available, body["partial"]
        )

    def _replication(self, db, run, configuration, index, seed, status, attempt):
        result = {
            "run": run,
            "configuration": configuration,
            "index": index,
            "seed": seed,
            "status": status,
            "attempt": attempt,
            "available": False,
            "reason": status,
            "scalars": {},
        }
        if status in ("succeeded", "reused"):
            try:
                evidence = self._result(db, attempt)
            except ValueError as error:
                result["reason"] = str(error)
            except (TypeError, KeyError):
                result["reason"] = "invalid dataset manifest"
            else:
                result.update(evidence, available=True, reason=None)
        return result

    def admit(self, preview_id: str, *, definition_id: str, parameters, generation_id: str) -> AnalysisExecution:
        with self.store._transaction() as db:
            row = db.execute("SELECT snapshot FROM analysis_previews WHERE id=?", (preview_id,)).fetchone()
            if row is None:
                raise KeyError(preview_id)
            return self._admit(db, row[0], definition_id, parameters, generation_id)

    def _admit(self, db, snapshot_hash, definition_id, parameters, generation_id):
        body = self._body(db, "analysis_input_snapshots", snapshot_hash)
        definition = self._body(db, "analysis_definitions", definition_id)
        parameters = validate_parameters(ParameterSchema.from_dict(definition["parameters"]), parameters)
        require_reference(db, "environment", generation_id)
        selection = Selection.from_dict(body["selection"])
        if not body["available"] or selection.require_complete and body["partial"]:
            raise ValueError("insufficient input: snapshot is incomplete or empty")
        groups = {(c["run"], c["index"]) for c in body["configurations"]}
        for group in groups:
            rows = [r for r in body["replications"] if (r["run"], r["configuration"]) == group]
            if sum(r["available"] for r in rows) < selection.min_replications:
                raise ValueError("insufficient replication count")
            for requirement in definition["requirements"]:
                name, fields = requirement["dataset"], set(requirement["fields"])
                unavailable = []
                for r in rows:
                    captured = r["datasets"].get(name) if r["available"] else None
                    missing = (
                        sorted(fields - {f["name"] for f in captured["manifest"]["schema"]["fields"]})
                        if captured
                        else sorted(fields)
                    )
                    if captured and not missing:
                        continue
                    if not r["available"]:
                        reason = r["reason"]
                    elif captured is None:
                        reason = r.get("dataset_failures", {}).get(name, "dataset unavailable")
                    else:
                        reason = "fields not retained"
                    unavailable.append(
                        {"index": r["index"], "attempt": r["attempt"], "missing_fields": missing, "reason": reason}
                    )
                valid = len(rows) - len(unavailable)
                complete = bool(requirement["require_complete"] or selection.require_complete)
                required = max(1, requirement["min_replications"], selection.min_replications)
                if complete:
                    required = max(required, len(rows))
                if valid < required:
                    raise _insufficient(name, group, valid, len(rows), required, complete, unavailable)
        references = [
            ("analysis-snapshot", snapshot_hash),
            ("analysis-definition", definition_id),
            ("environment", generation_id),
        ]
        for experiment in body["experiments"]:
            try:
                require_reference(db, "experiment", experiment["id"])
            except KeyError as error:
                raise ValueError("Experiment removed; select a fresh preview") from error
            references.append(("experiment", experiment["id"]))
        for r in body["replications"]:
            if r["attempt"] is not None:
                require_reference(db, "attempt", r["attempt"])
                references.append(("attempt", r["attempt"]))
            if r["available"]:
                current = self._result(db, r["attempt"])
                if codec.identity(current) != codec.identity({key: r.get(key) for key in current}):
                    raise ValueError("Evidence changed; select a fresh preview")
                references.extend((("result", r["attempt"]), ("request", r["request_id"])))
        identity = str(uuid4())
        db.execute(
            "INSERT INTO analysis_executions VALUES (?,?,?,?,?,?,?,?)",
            (
                identity,
                definition_id,
                snapshot_hash,
                generation_id,
                codec.dumps(parameters),
                "waiting",
                codec.dumps({}),
                time.time(),
            ),
        )
        db.executemany(
            'INSERT INTO "references" VALUES (?,?,?,?)',
            [("analysis", identity, kind, target) for kind, target in dict.fromkeys(references)],
        )
        return AnalysisExecution(identity, definition_id, snapshot_hash, generation_id, "waiting")

    def execution(self, identity):
        with self.store._transaction() as db:
            return self._execution(db, identity)

    def _discard(self, directory, name):
        # Never follows a planted symlink; a missing entry is already discarded.
        path = self.store.root / directory / name
        if path.exists() or path.is_symlink():
            _remove(path)

    @staticmethod
    def _execution(db, identity):
        row = db.execute(
            "SELECT id,definition,snapshot,generation,state FROM analysis_executions WHERE id=?", (identity,)
        ).fetchone()
        if row is None:
            raise KeyError(identity)
        return AnalysisExecution(*row)

    def rerun(self, identity):
        with self.store._transaction() as db:
            item = self._execution(db, identity)
            parameters = codec.loads(
                db.execute("SELECT parameters FROM analysis_executions WHERE id=?", (identity,)).fetchone()[0]
            )
            return self._admit(db, item.snapshot_hash, item.definition_id, parameters, item.generation_id)

    def refresh(self, identity):
        item = self.execution(identity)
        return self.preview(Selection.from_dict(self.snapshot(item.snapshot_hash)["selection"]))

    def cancel(self, identity):
        with self.store._transaction() as db:
            self._execution(db, identity)
            db.execute(
                "UPDATE analysis_executions SET state='cancelled' WHERE id=? "
                "AND state IN ('waiting','running','finalizing')",
                (identity,),
            )
        self._discard("analysis-control", identity + ".cancel")
        return self.execution(identity)

    def delete(self, identity):
        with self.store._transaction() as db:
            item = self._execution(db, identity)
            if item.state in ("waiting", "running", "finalizing"):
                raise ValueError("Cancel active analysis before deletion")
            if db.execute(
                'SELECT 1 FROM "references" WHERE target_kind=? AND target_id=?', ("analysis", identity)
            ).fetchone():
                raise ValueError("Analysis is referenced")
            db.execute('DELETE FROM "references" WHERE owner_kind=? AND owner_id=?', ("analysis", identity))
            db.execute("DELETE FROM analysis_annotations WHERE execution=?", (identity,))
            db.execute("DELETE FROM analysis_outputs WHERE execution=?", (identity,))
            db.execute("DELETE FROM analysis_executions WHERE id=?", (identity,))
        # The index commits first; an interrupted removal leaves an unindexed tree for recover().
        self._discard("analysis-results", identity)
        self._discard("analysis-control", identity + ".cancel")

    def annotate(self, identity, text):
        if not isinstance(text, str) or len(text) > 65536:
            raise ValueError("Annotation must be text of at most 65536 characters")
        revision = str(uuid4())
        with self.store._transaction() as db:
            self._execution(db, identity)
            db.execute(
                "INSERT INTO analysis_annotations VALUES (?,?,?,?)",
                (revision, identity, codec.dumps({"text": text}), time.time()),
            )
        return revision


def _insufficient(name, group, valid, total, required, complete, unavailable):
    run, configuration = group
    entries = [
        f"replication {item['index']}"
        + ("" if item["attempt"] is None else f" (attempt {item['attempt']})")
        + (
            f" lacks fields: {', '.join(item['missing_fields'])}"
            if item["reason"] == "fields not retained"
            else f": {item['reason']}"
        )
        for item in unavailable
    ]
    listed = entries[:LISTED_REPLICATIONS]
    if len(entries) > len(listed):
        listed.append(f"{len(entries) - len(listed)} more")
    message = (
        f"insufficient input for dataset {name} in configuration {configuration} of run {run}: "
        f"{valid} of {total} replications valid, {required} required; " + "; ".join(listed)
    )
    details = {
        "dataset": name,
        "run": run,
        "configuration": configuration,
        "valid": valid,
        "replications": total,
        "required": required,
        "require_complete": complete,
        "unavailable": unavailable,
    }
    return InsufficientInput(message, details)
