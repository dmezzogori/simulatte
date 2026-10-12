"""Single-slot local execution, shared by the headless CLI and Studio.

Only the child imports model code. The coordinator owns immutable inputs,
assignment fences, durable receipt credit and the result visibility point.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import asdict, dataclass
import hashlib
import errno
import sqlite3
from pathlib import Path
import sysconfig
import threading
import time
from typing import Any

from simulatte._wire import canonical_pack, freeze, unpack
from simulatte.execution.hashing import canonical_hash
from simulatte.execution.publication import ResultProposal, publish
from simulatte.execution.receipts import SegmentReceiver, validate_descriptor
from simulatte.execution.references import write_document, read_document
from simulatte.execution.runtime import describe_runtime
from simulatte.execution.schema import (
    AttemptKey,
    ExecutionRequest,
    InspectionRequest,
    ResolvedManifest,
    Revision,
    request_id,
    _plain,
    _mapping,
)
from simulatte.execution.snapshot import (
    ProjectSpec,
    Snapshot,
    capture_source,
    capture_inputs,
    materialize,
    load_source,
    freeze_runtime,
)
from simulatte.execution.store import RunStore, StaleAttempt, RevisionConflict, _blob, _read, _remove
from simulatte.execution.transport import LocalTransport
from simulatte.layout.identity import layout_hash
from simulatte.layout.io import load_layout
from simulatte.model import ParameterSchema, validate_parameters


class ExecutionFailure(RuntimeError):
    def __init__(self, key, status, details):
        self.key, self.status, self.details = key, status, details
        super().__init__(details.get("message", status))


class ReproductionMismatch(ExecutionFailure):
    pass


class WorkerLost(RuntimeError):
    pass


def _disk_full(error):
    return (isinstance(error, OSError) and error.errno in (errno.ENOSPC, errno.EDQUOT)) or (
        isinstance(error, sqlite3.Error) and "full" in str(error).lower()
    )


def selected_model(metadata):
    """Validate data-only inspection metadata before committing its visibility."""
    if not isinstance(metadata, Mapping) or not isinstance(metadata.get("models"), (list, tuple)):
        raise ValueError("Invalid model metadata")
    models = metadata["models"]
    names = set()
    selected = None
    for item in models:
        if not isinstance(item, Mapping) or not {"name", "parameters", "inputs", "stopping"} <= item.keys():
            raise ValueError("Invalid model metadata fields")
        name = item["name"]
        if type(name) is not str or not name.isidentifier() or name in names:
            raise ValueError("Invalid or duplicate model name")
        names.add(name)
        ParameterSchema.from_dict(item["parameters"])
        if not isinstance(item["inputs"], (list, tuple)) or any(type(path) is not str for path in item["inputs"]):
            raise ValueError("Invalid declared inputs")
        if not isinstance(item["stopping"], Mapping):
            raise ValueError("Invalid stopping metadata")
        if name == metadata.get("selected"):
            selected = item
    if selected is None:
        raise ValueError("Selected model is missing from metadata")
    return selected


@dataclass(frozen=True, slots=True)
class PreparedModel:
    snapshot: Snapshot
    revision: Revision
    model: str
    metadata: Mapping[str, Any]
    layout: Mapping[str, Any]
    runtime: Mapping[str, Any]
    documents: Mapping[str, Any]

    def __post_init__(self):
        for name in ("metadata", "layout", "runtime", "documents"):
            object.__setattr__(self, name, _mapping(getattr(self, name)))


@dataclass(frozen=True, slots=True)
class _Job:
    key: AttemptKey
    request: ExecutionRequest | InspectionRequest
    snapshot: Snapshot
    documents: dict
    deadline: float | None


class Coordinator:
    def __init__(self, project: ProjectSpec, *, reference: str | None = None):
        self.project = project
        self.reference = reference or project.model
        self.store = RunStore.open(project.root / ".simulatte")
        try:
            self.runtime = freeze_runtime(Path(__file__).resolve().parents[1], project.root)
            self.distribution_roots = list(dict.fromkeys(sysconfig.get_path(name) for name in ("purelib", "platlib")))
            self._condition = threading.Condition()
            self._pending: _Job | None = None
            self._current: _Job | None = None
            self._worker = None
            self._closed = False
            self._fatal: ExecutionFailure | None = None
            self._inputs = None
            self._events: deque[dict] = deque()
            self._event_bytes = 0
            self._thread = threading.Thread(target=self._loop, name="simulatte-coordinator", daemon=True)
            self._thread.start()
        except BaseException:
            self.store.close()
            raise

    @classmethod
    def from_store(cls, root: Path, identity: str):
        """Open captured project metadata without inspecting current project files."""
        root = root.resolve()
        if not (root / ".simulatte" / "index.sqlite").is_file():
            raise ValueError("Run store is unavailable for reproduction")
        with RunStore.open(root / ".simulatte") as store:
            request = store.request(identity)
            with store._transaction() as db:
                row = db.execute(
                    "SELECT manifest FROM snapshots WHERE id=? AND kind='preparation'", (identity,)
                ).fetchone()
            if row is None:
                raise ValueError("Captured preparation is unavailable for this request")
            data = _read(row[0])["project"]
        project: Any = {**data, "root": root}
        return cls(ProjectSpec(**project), reference=request.model)

    def _document(self, kind, value):
        return write_document(self.store.root / "documents", kind, value)

    @property
    def inputs(self):
        from simulatte.execution.inputs import InputOwner

        with self._condition:
            if self._inputs is None:
                self._inputs = InputOwner(self)
            return self._inputs

    def _relocate(self, reference):
        return {
            **reference,
            "path": str((self.store.root / "documents" / (reference["sha256"] + ".msgpack")).resolve()),
        }

    def _emit(self, kind, key, body):
        event = {"type": kind, "key": None if key is None else asdict(key), "body": _plain(body)}
        size = len(canonical_pack(freeze(event)))
        with self._condition:
            if len(self._events) >= 256 or self._event_bytes + size > 4 << 20:
                self._events.clear()
                event = {"type": "resync", "key": None, "body": {}}
                size = len(canonical_pack(freeze(event)))
                self._event_bytes = 0
            self._events.append(event)
            self._event_bytes += size
            self._condition.notify_all()

    def events(self):
        with self._condition:
            result = list(self._events)
            self._events.clear()
            self._event_bytes = 0
            return result

    def prepare(
        self, *, timeout=None, expected: Revision | None = None, _on_revision=None, _project=None
    ) -> PreparedModel:
        if self._closed:
            raise RuntimeError("Coordinator is closed")
        if expected is not None and self.store.current_revision() != expected:
            raise RevisionConflict("Preparation belongs to an obsolete revision")
        project = self.project if _project is None else _project
        deadline = self._deadline(timeout)
        source = capture_source(project)
        self._remaining(deadline)
        source_only = capture_inputs(source, ())
        layer_path = project.root / project.layout
        layer = _plain(load_layout(layer_path).data) if layer_path.exists() else {"schema_version": "1.0"}
        evidence = describe_runtime(self.distribution_roots)
        runtime = {
            "facts": evidence["facts"],
            "dependencies_hash": canonical_hash("dependencies-v1", evidence["dependencies"]),
            "trusted_runtime_hash": self.runtime.content_hash,
        }
        documents = {"runtime_ref": self._document("runtime", evidence), "layout_ref": self._document("layout", layer)}
        self._remaining(deadline)
        inspection = InspectionRequest(model=self.reference, source_hash=source.content_hash, runtime=runtime)
        with self._condition:
            revision = self.store.create_revision(
                {"source": source.content_hash, "layout": layout_hash("file", layer)}, expected=expected
            )
            if _on_revision is not None:
                _on_revision(revision)
            key = self._schedule(inspection, revision, source_only, documents, self._remaining(deadline))
        metadata = self.wait(key, timeout=None if timeout is None else timeout + 5)
        selected = selected_model(metadata)
        snapshot = capture_inputs(source, tuple(selected["inputs"]))
        self._remaining(deadline)
        # Input discovery changes the source/data partition. Record that final
        # captured input state before a request can be assigned.
        with self._condition:
            revision = self.store.create_revision(
                {
                    "source": snapshot.source.content_hash,
                    "inputs": snapshot.inputs.content_hash,
                    "layout": layout_hash("file", layer),
                },
                expected=revision,
            )
            if _on_revision is not None:
                _on_revision(revision)
        documents["expected_metadata_ref"] = self._document("model-info", metadata)
        return PreparedModel(
            snapshot, revision, project.model + ":" + metadata["selected"], metadata, layer, runtime, documents
        )

    def make_request(
        self, prepared: PreparedModel, *, parameters=None, seed=0, stopping=None, level="full", kpis=()
    ) -> ExecutionRequest:
        request = self._build_request(
            prepared, parameters=parameters, seed=seed, stopping=stopping, level=level, kpis=kpis
        )
        self._remember(prepared, request)
        return request

    @staticmethod
    def _build_request(prepared, *, parameters=None, seed=0, stopping=None, level="full", kpis=()):
        selected = selected_model(prepared.metadata)
        parameters = validate_parameters(ParameterSchema.from_dict(selected["parameters"]), parameters or {})
        return ExecutionRequest(
            model=prepared.model,
            source_hash=prepared.snapshot.source.content_hash,
            input_hash=prepared.snapshot.inputs.content_hash,
            layout_hash=layout_hash("file", prepared.layout),
            layout_revision=prepared.revision.number,
            parameters=parameters,
            seed=seed,
            stopping=selected["stopping"] if stopping is None else stopping,
            level=level,
            kpis=kpis,
            runtime=prepared.runtime,
        )

    def _preparation_manifest(self, prepared, request):
        return {
            "request": request.to_dict(),
            "documents": prepared.documents,
            "project": {**asdict(prepared.snapshot.source.project), "root": str(self.project.root)},
        }

    def _remember(self, prepared, request):
        manifest = self._preparation_manifest(prepared, request)
        with self.store._transaction() as db:
            if prepared.revision != self.store._current_revision(db):
                raise RevisionConflict("Prepared model belongs to an obsolete revision")
            db.execute(
                "INSERT OR REPLACE INTO snapshots VALUES (?,?,?)", (request_id(request), "preparation", _blob(manifest))
            )

    def _preparation(self, request):
        with self.store._transaction() as db:
            row = db.execute(
                "SELECT manifest FROM snapshots WHERE id=? AND kind='preparation'", (request_id(request),)
            ).fetchone()
        if row is None:
            raise ValueError("Captured preparation is unavailable for this request")
        manifest = _read(row[0])
        if ExecutionRequest.from_dict(manifest["request"]) != request:
            raise ValueError("Captured preparation request mismatch")
        if request.runtime["trusted_runtime_hash"] != self.runtime.content_hash:
            raise ValueError("Requested runtime differs from this installation; no automatic provisioning")
        project_data: Any = {**manifest["project"], "root": self.project.root}
        spec = ProjectSpec(**project_data)
        snapshot = Snapshot(
            load_source(spec, request.source_hash), load_source(spec, request.input_hash, kind="inputs")
        )
        documents = {name: self._relocate(reference) for name, reference in manifest["documents"].items()}
        return snapshot, documents

    def submit(self, request: ExecutionRequest, revision: Revision, *, timeout=None) -> AttemptKey:
        snapshot, documents = self._preparation(request)
        return self._schedule(request, revision, snapshot, documents, timeout)

    @staticmethod
    def _deadline(timeout):
        if timeout is not None and (type(timeout) not in (int, float) or not 0 < timeout < float("inf")):
            raise ValueError("Timeout must be positive and finite")
        return None if timeout is None else time.monotonic() + timeout

    def _schedule(self, request, revision, snapshot, documents, timeout):
        deadline = self._deadline(timeout)
        with self._condition:
            if self._closed or self._fatal is not None:
                raise RuntimeError("Coordinator is closed or has a storage failure; restart it")
            key = (
                self.store.assign_inspection(request, revision)
                if isinstance(request, InspectionRequest)
                else self.store.assign(request, revision)
            )
            self._pending = _Job(key, request, snapshot, documents, deadline)
            self._condition.notify_all()
        self._emit("queued", key, {})
        return key

    def cancel(self, key):
        event, details = "cancelled", {}
        with self._condition:
            try:
                self.store.revoke(key, "cancelled")
            except StaleAttempt:
                return False
            except (OSError, sqlite3.Error) as error:
                event = "disk_full" if _disk_full(error) else "failed"
                details = {"message": str(error)[:4096], "index_terminal_state": "not_durable"}
                self._fatal = ExecutionFailure(key, event, details)
                self._pending = None
            if self._current is not None and self._current.key == key and self._worker is not None:
                try:
                    self._worker.send("cancel", {}, key=key)
                except (OSError, BufferError):
                    pass  # The actor will close the failed transport.
            if self._pending is not None and self._pending.key == key:
                self._pending = None
            self._condition.notify_all()
        self._emit(event, key, details)
        return True

    def wait(self, key, *, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            attempt = self.store.attempt(key)
            if attempt["status"] == "succeeded":
                if attempt["details"].get("kind") == "inspection":
                    return read_document(self._relocate(attempt["details"]["document"]), "model-info")
                return self.store.result(key.attempt_id)
            if self._fatal is not None:
                raise ExecutionFailure(key, self._fatal.status, self._fatal.details)
            if attempt["status"] not in ("queued", "preparing", "ready", "running", "finalizing"):
                raise ExecutionFailure(key, attempt["status"], attempt["details"])
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("Waiting for attempt timed out")
            with self._condition:
                self._condition.wait(0.05)

    def reproduce(self, identity, *, timeout=None):
        request = self.store.request(identity)
        with self.store._transaction() as db:
            row = db.execute(
                "SELECT r.attempt FROM results r JOIN attempts a ON a.id=r.attempt "
                "WHERE a.request_id=? ORDER BY r.created LIMIT 1",
                (identity,),
            ).fetchone()
        if row is None:
            raise ValueError("No committed baseline for reproduction")
        baseline = self.store.result(row[0])
        revision = self.store.create_revision({"reproduce": identity})
        key = self.submit(request, revision, timeout=timeout)
        result = self.wait(key, timeout=None if timeout is None else timeout + 5)
        if not result.manifest["simulation"]["complete"] or result.manifest["resolved"]["warnings"]:
            raise ReproductionMismatch(key, "unverifiable", {"message": "Run provenance is incomplete"})
        if result.fingerprint != baseline.fingerprint or result.manifest["artifacts"] != baseline.manifest["artifacts"]:
            raise ReproductionMismatch(key, "mismatch", {"message": "Reproduced fingerprint or artifacts differ"})
        return result

    def _start_worker(self):
        if self._worker is None:
            self._worker = LocalTransport.start(self.runtime.root, external_roots=self.distribution_roots)
            hello = self._worker.receive(10)
            if (
                hello.type != "hello"
                or tuple(hello.body["protocol"]) != (1, 0)
                or "metadata-artifacts" not in hello.body["capabilities"]
            ):
                raise ValueError("Worker protocol/capabilities are incompatible")
        return self._worker

    def _active(self, key):
        if self._fatal is not None:
            return False
        try:
            self.store.require_active(key)
            return True
        except StaleAttempt:
            return False

    def _record_failure(self, key, status, details):
        try:
            self.store.revoke(key, status, details=details)
        except StaleAttempt:
            return
        except (OSError, sqlite3.Error) as error:
            # If the index itself is unwritable, retain a bounded live failure.
            # Never claim durable terminal state; startup recovery marks it lost.
            status = "disk_full" if _disk_full(error) else "failed"
            details = {"message": str(error)[:4096], "index_terminal_state": "not_durable"}
            with self._condition:
                self._fatal = ExecutionFailure(key, status, details)
                self._pending = None
        self._emit(status, key, details)

    def _send(self, worker, kind, body, *, key):
        try:
            worker.send(kind, body, key=key)
        except (OSError, BufferError) as error:
            raise WorkerLost("Worker control transport failed") from error

    def _metadata(self, receiver, reference, stream):
        reference = validate_descriptor(_plain(reference))
        if reference["stream"] != "metadata/" + stream or reference["offset"] != 0:
            raise ValueError("Wrong metadata stream")
        path = receiver.segment_path(reference["id"])
        with self.store._transaction() as db:
            self.store._require_active(db, receiver.key)
            row = db.execute(
                "SELECT * FROM receipts WHERE attempt=? AND id=?", (receiver.key.attempt_id, reference["id"])
            ).fetchone()
            if row is None or any(row[name] != value for name, value in reference.items()):
                raise ValueError("Metadata descriptor differs from durable receipt")
        data = path.read_bytes()
        if len(data) != reference["length"] or hashlib.sha256(data).hexdigest() != reference["sha256"]:
            raise ValueError("Metadata reference differs from durable receipt")
        return _plain(unpack(data))

    @staticmethod
    def _remaining(deadline):
        if deadline is None:
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Attempt deadline expired during preparation")
        return remaining

    def _run_job(self, job):
        key = job.key
        receiver = None
        snapshot_path = self.store.root / "staging" / key.attempt_id / "snapshot"
        try:
            self.store.require_active(key)
            self.store.transition(key, "preparing")
            receiver = SegmentReceiver(self.store, key)
            self._remaining(job.deadline)
            paths = materialize(job.snapshot, snapshot_path)
            self._remaining(job.deadline)
            worker = self._start_worker()
            config = {
                "project_root": str(self.project.root),
                "snapshot_root": str(paths.root),
                "snapshot_roots": list(map(str, paths.python_path)),
                "external_roots": [str(self.runtime.root), *self.distribution_roots],
                "distribution_roots": self.distribution_roots,
                "reference": job.request.model,
                "metadata_artifacts": True,
                **job.documents,
            }
            inspect = isinstance(job.request, InspectionRequest)
            body = {
                "config": config,
                "timeout": self._remaining(job.deadline),
                "inspection_ref" if inspect else "request_ref": self._document("request", job.request.to_dict()),
            }
            self.store.require_active(key)
            self._send(worker, "inspect" if inspect else "run", body, key=key)
            ready = None
            cancelled = False
            acknowledged = False
            while True:
                active = self._active(key)
                if not active and not cancelled:
                    self._send(worker, "cancel", {}, key=key)
                    cancelled = True
                try:
                    message = worker.receive(0.1)
                except TimeoutError:
                    if time.monotonic() - worker.channel.last_heartbeat >= 5:
                        raise WorkerLost("Worker heartbeat lost")
                    continue
                except OSError as error:
                    raise WorkerLost("Worker transport closed") from error
                if message.envelope["attempt_id"] is None and message.type == "heartbeat":
                    continue
                if (message.envelope["request_id"], message.envelope["attempt_id"], message.envelope["generation"]) != (
                    key.request_id,
                    key.attempt_id,
                    key.generation,
                ):
                    raise ValueError("Worker message belongs to another assignment")
                if message.type in ("error", "cancelled"):
                    if active:
                        details = _plain(message.body)
                        status = (
                            "cancelled"
                            if message.type == "cancelled"
                            else "disk_full"
                            if details.get("code") == "disk_full"
                            else "failed"
                            if details.get("code") == "timeout"
                            else "validation_failed"
                            if details.get("phase") in ("preparing", "inspecting", "ready")
                            else "failed"
                        )
                        self._record_failure(key, status, details)
                    return
                if not active:
                    if message.type in ("result", "model_info"):
                        return
                    continue
                if message.type in ("trace_chunk", "artifact_fragment"):
                    receipt = receiver.receive(
                        _plain(message.body["segment"]), _plain(message.body["fragment_offset"]), message.payload
                    )
                    self._send(worker, "ack", asdict(receipt), key=key)
                    if receipt.segment_complete and message.type == "trace_chunk":
                        prefix = receiver.prefix()
                        assert prefix is not None
                        self._emit(
                            "trace_available",
                            key,
                            {"generation": prefix.generation, "length": prefix.length, "sha256": prefix.sha256},
                        )
                elif message.type == "ready":
                    if inspect or ready is not None:
                        raise ValueError("Unexpected duplicate readiness")
                    ready = _plain(message.body)
                elif message.type == "model_info":
                    if not inspect:
                        raise ValueError("Unexpected inspection result")
                    metadata = self._metadata(receiver, message.body["metadata_ref"], "model-info")
                    selected_model(metadata)
                    document = self._document("model-info", metadata)
                    self.store.complete_inspection(
                        key,
                        _plain(message.body["metadata_ref"]),
                        execution_exited=True,
                        descendants_cleaned=True,
                        document=document,
                    )
                    self._emit("model_info", key, {"document": document})
                    return
                elif message.type == "result":
                    if inspect or not acknowledged:
                        raise ValueError("Result before acknowledged readiness")
                    data = self._metadata(receiver, message.body["proposal"], "result-proposal")
                    if data.pop("schema") != 1:
                        raise ValueError("Unknown result proposal schema")
                    resolved = data.pop("resolved_manifest")
                    if resolved.pop("schema") != 1:
                        raise ValueError("Unknown resolved manifest schema")
                    proposal = ResultProposal(**data, resolved_manifest=ResolvedManifest(**resolved))
                    self.store.transition(key, "finalizing")
                    result = publish(self.store, key, proposal, execution_exited=True, descendants_cleaned=True)
                    self._emit("result", key, {"attempt_id": result.key.attempt_id})
                    return
                elif message.type in ("heartbeat", "diagnostics", "kpi"):
                    self._emit(message.type, key, message.body)
                else:
                    raise ValueError("Unsupported worker notification")
                if ready is not None and not acknowledged:
                    try:
                        resolved = self._metadata(receiver, ready["resolved"], "resolved-manifest")
                    except KeyError:
                        continue
                    prefix = receiver.prefix()
                    if prefix is None or prefix.length < ready["trace_length"]:
                        continue
                    from simulatte.trace import Trace

                    trace = Trace.open(prefix.path)
                    execution = _plain(trace.execution)
                    if (
                        execution is None
                        or execution["initial"] is None
                        or execution["initial"]["resolved"] != resolved
                        or execution["header"]["request_id"] != key.request_id
                    ):
                        raise ValueError("Ready metadata differs from durable INITIAL")
                    self.store.transition(key, "ready")
                    if trace.layout is not None and trace.layout.reusable:
                        self.store.bind_ready_view(key, trace.layout)
                    self.store.transition(key, "running")
                    self._send(worker, "ready_ack", {}, key=key)
                    acknowledged = True
                    self._emit("ready", key, ready)
        except StaleAttempt:
            if self._worker is not None:
                self._worker.close()
                self._worker = None
        except Exception as error:
            if self._active(key):
                status = "disk_full" if _disk_full(error) else "lost" if isinstance(error, WorkerLost) else "failed"
                self._record_failure(key, status, {"message": str(error)[:4096]})
            # A receiver/preflight/protocol failure must contain the child before
            # accepting another assignment. No automatic retry of this request.
            if self._worker is not None:
                self._worker.close()
                self._worker = None
        finally:
            if snapshot_path.exists():
                try:
                    _remove(snapshot_path)
                except OSError as error:
                    self._emit("cleanup_error", key, {"message": str(error)[:4096]})
            with self._condition:
                self._condition.notify_all()

    def _loop(self):
        try:
            while True:
                with self._condition:
                    if self._closed and self._pending is None:
                        return
                    job, self._pending = self._pending, None
                    self._current = job
                if job is not None:
                    self._run_job(job)
                    with self._condition:
                        self._current = None
                elif self._worker is not None:
                    try:
                        self._worker.receive(0.1)  # Drain idle heartbeats; never grow a retained idle queue.
                    except TimeoutError:
                        if time.monotonic() - self._worker.channel.last_heartbeat >= 5:
                            self._worker.close()
                            self._worker = None
                    except OSError:
                        self._worker.close()
                        self._worker = None
                else:
                    with self._condition:
                        self._condition.wait(0.1)
        finally:
            if self._worker is not None:
                self._worker.close()
                self._worker = None

    def close(self):
        with self._condition:
            if not self._closed:
                for job in (self._current, self._pending):
                    if job is not None:
                        self.cancel(job.key)
                self._closed = True
                self._condition.notify_all()
        self._thread.join(10)
        if self._thread.is_alive():
            raise RuntimeError("Coordinator shutdown did not finish")
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
