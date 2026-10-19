"""Local execution over slots, shared by the headless CLI and Studio.

Only the child imports model code. The coordinator owns immutable inputs,
assignment fences, durable receipt credit and the result visibility point.
Each slot (``interactive``, ``local-<n>`` experiment slots and ``<worker>-<n>`` remote
slots) is one actor thread owning one transport; the interactive slot keeps SP4
supersession, experiment slots are fenced by their run's state, never by project
revisions. Remote slots stage the snapshot and documents and provision a uv
environment before the unchanged ``run`` and publication loop.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
import functools
import hashlib
import errno
import platform
import sqlite3
import subprocess
from pathlib import Path
import sysconfig
import tempfile
import threading
import time
from typing import Any

from simulatte._wire import canonical_pack, freeze, unpack
from simulatte.execution.hashing import canonical_hash
from simulatte.execution.layout_files import read_layout_file
from simulatte.execution.publication import ResultProposal, publish
from simulatte.execution.receipts import SegmentReceiver, validate_descriptor
from simulatte.execution.replay import Verification, verify_replay
from simulatte.execution.references import write_document, read_document
from simulatte.execution.runtime import describe_runtime, provisioning_spec_hash
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
from simulatte.execution.bootstrap_remote import RUNTIME_MISSING, UV_MISSING
from simulatte.execution.ssh import BootstrapError, SshTransport
from simulatte.execution.scheduler import Scheduler, report as _report, run_status as _run_status
from simulatte.execution.slots import SlotConfig, remote_slots
from simulatte.execution.staging import RemoteStepError, provision, stage_blob
from simulatte.execution.store import INTERACTIVE, RunStore, StaleAttempt, RevisionConflict, _blob, _read, _remove
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


class WorkerUnreachable(WorkerLost):
    """The probe or handshake failed: no request reached the worker, and the slot backs off."""


def _exit_status(transport) -> int | None:
    """The transport process's exit status once it ended (within 1 s), else ``None``."""
    process = getattr(transport, "process", None)
    if process is None:
        return None
    try:
        return process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        return None


@dataclass(frozen=True, slots=True)
class ReplayResult:
    key: AttemptKey  # The replay's published attempt; ``key.request_id`` is the replay request.
    verification: Verification


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
    preview: dict | None = None
    experiment: str | None = None  # The run id when the job executes a replication.


class Coordinator:
    def __init__(
        self,
        project: ProjectSpec,
        *,
        reference: str | None = None,
        slots: SlotConfig | None = None,
        _store_root=None,
        _runtime=None,
        _store=None,
    ):
        self.project = project
        self.reference = reference or project.model
        self.slots = SlotConfig() if slots is None else slots
        if type(self.slots) is not SlotConfig:
            raise ValueError("slots must be a SlotConfig")
        self._owns_store = _store is None
        self.store = (
            _store
            if _store is not None
            else RunStore.open(project.root / ".simulatte" if _store_root is None else _store_root)
        )
        self.contexts = {}
        self.environments = None
        self._inspection_lock = threading.RLock()
        self._auto_owners = {}
        self._inspection_cache = {}
        try:
            self.runtime = (
                freeze_runtime(Path(__file__).resolve().parents[1], project.root) if _runtime is None else _runtime
            )
            self.distribution_roots = list(dict.fromkeys(sysconfig.get_path(name) for name in ("purelib", "platlib")))
            self._condition = threading.Condition()
            self._closed = False
            self._fatal: ExecutionFailure | None = None
            self._inputs = None
            self._preview = None
            self._preview_lock = threading.Lock()
            self._preview_done = threading.Event()
            self._preview_done.set()
            self._events: deque[dict] = deque()
            self._event_bytes = 0
            self._scheduler = Scheduler(self)
            # This interpreter's version; preflight derives a remote Python request from it (D105).
            self.python = platform.python_version()
            # One transport factory per slot kind. ``uv`` (remote) factories take the WorkerSpec.
            self._transports: dict[str, Any] = {"local": self._local_transport, "uv": self._ssh_transport}
            self._actors = {
                name: SlotActor(self, name, self._transports["local"]) for name in (INTERACTIVE, *self.slots.names())
            }
            # The preflight warnings of the newest remote experiment start (``private_index:<url>`` ...).
            self.preflight_warnings: list[str] = []
            for actor in self._actors.values():
                actor.thread.start()
            self._add_workers(self.slots.workers)
        except BaseException:
            self._closed = True
            if self._owns_store:
                self.store.close()
            raise

    @classmethod
    def for_workspace(cls, root, *, store, environments):
        from simulatte.projects import load_workspace
        from simulatte.execution.model_context import ModelContext
        from simulatte.execution.project_store import ProjectStore

        workspace = load_workspace(root)
        if workspace is None:
            raise ValueError("Configure a workspace before creating its coordinator")
        selected = environments.selected
        import sys

        if selected is None:
            raise ValueError("Prepare and select a project environment before execution")
        if selected.interpreter.absolute() != Path(sys.executable).absolute():
            raise ValueError("Restart Studio in the selected project interpreter before execution")
        environments._verify(selected)
        ProjectStore(store).register(workspace)
        project = ProjectSpec(root, "__workspace__.py", "layout.json", (), (), (".",), False)
        coordinator = cls(project, _store=store)
        try:
            coordinator.environments = environments
            coordinator.contexts = {m.id: ModelContext(coordinator, m) for m in workspace.models}
            actor = SlotActor(coordinator, "inspection", coordinator._transports["local"])
            coordinator._actors["inspection"] = actor
            actor.thread.start()
            return coordinator
        except BaseException:
            coordinator.close()
            raise

    def prepare_model(self, model_id, *, timeout=None):
        with self._inspection_lock:
            context = self.contexts[model_id]
            from simulatte.execution.snapshot import discover_project

            context.project = discover_project(
                context.project.root / context.project.model, context.project.root, layout=context.registration.layout
            )
            return context.prepare(timeout=timeout)

    def inputs_for(self, model_id):
        return self.contexts[model_id].inputs

    def enable_auto(self, model_id):
        import secrets

        with self._condition:
            token = secrets.token_hex(24)
            self.contexts[model_id].auto_session = token
            return token

    def disable_auto(self, model_id):
        with self._condition:
            self.contexts[model_id].auto_session = None

    def _owner(self):
        for job in (self._pending, self._current):
            if job is not None:
                return job.key
        return None

    def admit_run(self, model_id, request, revision, *, auto_session=None, timeout=None):
        from simulatte.execution.admission import Admission

        with self._condition:
            context = self.contexts[model_id]
            if (
                context.revision != revision
                or request.model != context.reference
                or request.layout_revision != revision.number
            ):
                return Admission("stale")
            if auto_session is not None and context.auto_session != auto_session:
                return Admission("stale")
            owner = self._owner()
            if owner is not None:
                if auto_session is None or self._auto_owners.get(owner.attempt_id) != (model_id, auto_session):
                    return Admission("busy", owner=owner)
            else:
                snapshot, documents = self._preparation(request)
                key = self._schedule(request, revision, snapshot, documents, timeout, model_id=model_id)
                if auto_session is not None:
                    self._auto_owners[key.attempt_id] = (model_id, auto_session)
                return Admission("started", key=key)
        return self.stop_and_run(owner, model_id, request, revision, _auto_session=auto_session, timeout=timeout)

    def stop_and_run(self, owner, model_id, request, revision, *, _auto_session=None, timeout=None):
        from simulatte.execution.admission import Admission

        with self._condition:
            context = self.contexts[model_id]
            if (
                context.revision != revision
                or request.layout_revision != revision.number
                or request.model != context.reference
            ):
                return Admission("stale")
            if self._owner() != owner:
                return Admission("busy", owner=self._owner())
            generation = self.store.slots()[INTERACTIVE]["generation"]
            if not self.cancel(owner):
                return Admission("busy", owner=owner)
        deadline = time.monotonic() + 10
        with self._condition:
            while self._current is not None and self._current.key == owner:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return Admission("busy", owner=owner)
                self._condition.wait(remaining)
            if self.store.slots()[INTERACTIVE]["generation"] != generation or self._owner() is not None:
                return Admission("busy", owner=self._owner())
            return self.admit_run(model_id, request, revision, auto_session=_auto_session, timeout=timeout)

    # The interactive slot's actor state, under the names Studio's lifecycle uses.
    @property
    def _pending(self) -> _Job | None:
        return self._actors[INTERACTIVE].pending

    @_pending.setter
    def _pending(self, job):
        self._actors[INTERACTIVE].pending = job

    @property
    def _current(self) -> _Job | None:
        return self._actors[INTERACTIVE].current

    @_current.setter
    def _current(self, job):
        self._actors[INTERACTIVE].current = job

    @property
    def _worker(self):
        return self._actors[INTERACTIVE].worker

    @_worker.setter
    def _worker(self, worker):
        self._actors[INTERACTIVE].worker = worker

    @property
    def _thread(self):
        return self._actors[INTERACTIVE].thread

    @_thread.setter
    def _thread(self, thread):
        self._actors[INTERACTIVE].thread = thread

    def _local_transport(self):
        return LocalTransport.start(self.runtime.root, external_roots=self.distribution_roots)

    def _ssh_transport(self, spec, *, python=None):
        # D105: the supervisor runs on the provisioning spec's Python request, as uv sync does.
        requested = ".".join(self.python.split(".")[:2]) if python is None else python
        return SshTransport.start(spec, runtime=self.runtime, python=requested)

    def _worker_specs(self, names):
        """WorkerSpecs by name: this coordinator's first, then ``workers.toml`` (unknown: ``ValueError``)."""
        configured = {spec.name: spec for spec in self.slots.workers}
        missing = [name for name in names if name not in configured]
        if missing:
            from simulatte.execution.workers import load_workers, resolve_workers

            configured.update((spec.name, spec) for spec in resolve_workers(missing, load_workers()))
        return configured

    def _add_workers(self, specs):
        """Start ``<worker>-1`` .. ``<worker>-<slots>`` actors of kind ``uv`` for workers not present yet."""
        with self._condition:
            if self._closed:
                raise RuntimeError("Coordinator is closed")
            started = []
            for spec in specs:
                for name in remote_slots(spec):
                    if name in self._actors:
                        continue
                    factory = functools.partial(self._transports["uv"], spec)
                    actor = SlotActor(self, name, factory, kind="uv", host=spec.name, worker=spec)
                    self._actors[name] = actor
                    started.append(actor)
            for actor in started:
                actor.thread.start()

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
            if kind == "experiment_progress":
                # Coalesce per run: only the newest counts of each run are retained.
                for old in [item for item in self._events if item["type"] == kind]:
                    if old["body"]["run_id"] == event["body"]["run_id"]:
                        self._events.remove(old)
                        self._event_bytes -= len(canonical_pack(freeze(old)))
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
        self, *, timeout=None, expected: Revision | None = None, _on_revision=None, _project=None, _context=None
    ) -> PreparedModel:
        store = self.store if _context is None else _context.store
        if self._closed:
            raise RuntimeError("Coordinator is closed")
        if expected is not None and store.current_revision() != expected:
            raise RevisionConflict("Preparation belongs to an obsolete revision")
        project = (self.project if _context is None else _context.project) if _project is None else _project
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
        inspection = InspectionRequest(
            model=self.reference if _context is None else _context.reference,
            source_hash=source.content_hash,
            runtime=runtime,
        )
        with self._condition:
            revision = store.create_revision(
                {"source": source.content_hash, "layout": layout_hash("file", layer)}, expected=expected
            )
            if _on_revision is not None:
                _on_revision(revision)
            from simulatte.execution.schema import inspection_id

            cache_key = inspection_id(inspection)
            metadata = None if _context is None else self._inspection_cache.get(cache_key)
            if metadata is None:
                key = self._schedule(
                    inspection,
                    revision,
                    source_only,
                    documents,
                    self._remaining(deadline),
                    slot=INTERACTIVE if _context is None else "inspection",
                    model_id=None if _context is None else _context.registration.id,
                )
        if metadata is None:
            metadata = self.wait(key, timeout=None if timeout is None else timeout + 5)
            if _context is not None:
                if len(self._inspection_cache) >= 64:
                    self._inspection_cache.clear()
                self._inspection_cache[cache_key] = metadata
        selected = selected_model(metadata)
        snapshot = capture_inputs(source, tuple(selected["inputs"]))
        self._remaining(deadline)
        # Input discovery changes the source/data partition. Record that final
        # captured input state before a request can be assigned.
        with self._condition:
            revision = store.create_revision(
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

    def preview(self, prepared, request, layer, *, timeout=30):
        """Build in one disposable worker, cancel at INITIAL, never publish or replace a run."""
        timeout = 30 if timeout is None else timeout
        deadline = self._deadline(timeout)
        with self._condition:
            if self._closed:
                raise RuntimeError("Coordinator is closed")
            if not self._preview_lock.acquire(blocking=False):
                raise RuntimeError("A layout preview is already running")
            self._preview_done.clear()
        try:
            with tempfile.TemporaryDirectory(prefix="simulatte-preview-") as temporary:
                with Coordinator(
                    self.project, slots=SlotConfig(local=0), _store_root=Path(temporary), _runtime=self.runtime
                ) as preview:
                    with self._condition:
                        self._preview = preview
                    revision = preview.store.create_revision({"preview": request_id(request)})
                    documents = {**prepared.documents, "layout_ref": preview._document("layout", layer)}
                    outcome = {}
                    key = preview._schedule(
                        request, revision, prepared.snapshot, documents, self._remaining(deadline), _preview=outcome
                    )
                    while True:
                        with self._condition:
                            from simulatte.execution.project_store import revision_is_current

                            with self.store._transaction() as db:
                                current = revision_is_current(self.store, db, prepared.revision)
                            if self._closed or not current:
                                preview.cancel(key)
                                raise RevisionConflict("Layout preview was superseded")
                        poll_timeout = min(0.1, self._remaining(deadline))
                        try:
                            preview.wait(key, timeout=poll_timeout)
                        except TimeoutError:
                            continue
                        except ExecutionFailure as error:
                            if error.status == "cancelled" and outcome.get("complete"):
                                return outcome["bundle"]
                            raise
        finally:
            with self._condition:
                self._preview = None
                self._preview_lock.release()
                self._preview_done.set()

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
            from simulatte.execution.project_store import revision_is_current

            if not revision_is_current(self.store, db, prepared.revision):
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

    def submit(
        self, request: ExecutionRequest, revision: Revision, *, timeout=None, slot: str = INTERACTIVE
    ) -> AttemptKey:
        """Queue one request. An idle experiment slot runs it at the given (pinned) revision."""
        snapshot, documents = self._preparation(request)
        return self._schedule(request, revision, snapshot, documents, timeout, slot=slot)

    @staticmethod
    def _deadline(timeout):
        if timeout is not None and (type(timeout) not in (int, float) or not 0 < timeout < float("inf")):
            raise ValueError("Timeout must be positive and finite")
        return None if timeout is None else time.monotonic() + timeout

    def _schedule(
        self, request, revision, snapshot, documents, timeout, *, _preview=None, slot=INTERACTIVE, model_id=None
    ):
        deadline = self._deadline(timeout)
        with self._condition:
            if self._closed or self._fatal is not None:
                raise RuntimeError("Coordinator is closed or has a storage failure; restart it")
            actor = self._actors.get(slot)
            if actor is None or (slot not in (INTERACTIVE, "inspection") and isinstance(request, InspectionRequest)):
                raise ValueError(f"Unknown execution slot {slot!r}")
            if isinstance(request, InspectionRequest):
                key = self.store.assign_inspection(request, revision, model_id=model_id)
            elif slot == INTERACTIVE:
                key = self.store.assign(request, revision, model_id=model_id)
            else:
                # Never supersede a replication: an experiment slot takes one request only when idle.
                if actor.current is not None or actor.pending is not None:
                    raise RuntimeError(f"Slot {slot} is busy")
                key = self.store.assign(request, revision, slot=slot, worker=actor.host, pinned=True)
            if self.environments is not None and self.environments.selected is not None:
                try:
                    self.environments.acquire(self.environments.selected.id, key.attempt_id)
                except Exception:
                    self.store.revoke(key, "failed")
                    raise
            actor.pending = _Job(key, request, snapshot, documents, deadline, _preview)
            self._condition.notify_all()
        self._emit("queued", key, {} if slot == INTERACTIVE else {"slot": slot})
        return key

    def cancel(self, key):
        return self._stop(key, "cancelled")

    def _stop(self, key, reason, details=None):
        """Revoke an attempt and ask its slot's child to stop; ``False`` when it was no longer active."""
        event, details = reason, details or {}
        with self._condition:
            try:
                self.store.revoke(key, reason, details=details or None)
            except StaleAttempt:
                return False
            except (OSError, sqlite3.Error) as error:
                event = "disk_full" if _disk_full(error) else "failed"
                details = {"message": str(error)[:4096], "index_terminal_state": "not_durable"}
                self._fatal = ExecutionFailure(key, event, details)
                self._pending = None
            for actor in self._actors.values():
                if actor.current is not None and actor.current.key == key and actor.worker is not None:
                    try:
                        actor.worker.send("cancel", {}, key=key)
                    except (OSError, BufferError):
                        pass  # The actor will close the failed transport.
                if actor.pending is not None and actor.pending.key == key:
                    actor.pending = None
                    if self.environments is not None:
                        self.environments.release(key.attempt_id)
            self._condition.notify_all()
        self._emit(event, key, details)
        return True

    def _fail_admission(self, error):
        """A scheduler failure leaves durable state uncertain: close admission for all slots."""
        status = "disk_full" if _disk_full(error) else "failed"
        details = {"message": str(error)[:4096]}
        if isinstance(error, (OSError, sqlite3.Error)):
            details["index_terminal_state"] = "not_durable"
        with self._condition:
            self._fatal = ExecutionFailure(None, status, details)
            self._pending = None
            self._condition.notify_all()
        self._emit(status, None, details)

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

    def replay(self, identity: str, *, worker: str | None = None, timeout=None) -> ReplayResult:
        """Re-execute a committed request at ``full`` level, linked ``replay-of``, and verify it (D100).

        Without ``worker`` the replay runs only on the original attempt's worker (local
        originals on local slots; a remote worker's slots are started from ``workers.toml``
        as ``start_experiment`` does) and never moves to another host. An explicit
        ``worker`` (``"local"`` is the local pool) is preferred, then any slot of the same
        kind. It fails with ``no_compatible_worker`` before scheduling when no slot
        qualifies, and raises ``RuntimeError`` when every qualifying slot is busy. It runs on an idle
        experiment slot at the original attempt's pinned revision, or on the interactive
        slot when no experiment slot is configured. Unverified replays stay published.
        """
        self._deadline(timeout)
        with self.store._transaction() as db:
            # A replay's own request is the full form of its original: replaying it replays the original.
            link = db.execute("SELECT original FROM replays WHERE request_id=?", (identity,)).fetchone()
        identity = identity if link is None else link[0]
        request = self.store.request(identity)
        with self.store._transaction() as db:
            row = db.execute(
                "SELECT r.attempt,a.worker,v.number,v.digest FROM results r JOIN attempts a ON a.id=r.attempt "
                "JOIN revisions v ON v.number=a.revision WHERE a.request_id=? ORDER BY r.created,r.attempt LIMIT 1",
                (identity,),
            ).fetchone()
            prepared = db.execute(
                "SELECT manifest FROM snapshots WHERE id=? AND kind='preparation'", (identity,)
            ).fetchone()
        if row is None:
            raise ValueError("No committed result to replay")
        original = self.store.result(row["attempt"])
        host = row["worker"] if worker is None else None if worker == "local" else worker
        # A slot's kind follows its worker: ``local`` without one, else ``uv`` (remote).
        kind = "local" if host is None else "uv"
        if host is not None:
            # The original (or requested) worker's slots, from workers.toml as start_experiment does.
            try:
                spec = self._worker_specs([host])[host]
            except ValueError as error:
                raise ExecutionFailure(
                    None, "no_compatible_worker", {"message": f"Worker {host} is not configured: {error}"}
                ) from None
            self._add_workers([spec])
        with self._condition:
            actors = list(self._actors.items())
        # Without an explicit worker, only the original host qualifies (D100): never silently elsewhere.
        slots = sorted(
            (
                name
                for name, actor in actors
                if name != INTERACTIVE and actor.kind == kind and (worker is not None or actor.host == host)
            ),
            key=lambda name: self._actors[name].host != host,
        )
        if not slots and kind == "local" and not self.slots.local:
            slots = [INTERACTIVE]
        if not slots:
            raise ExecutionFailure(
                None, "no_compatible_worker", {"message": f"No slot can replay on worker {host or 'local'}"}
            )
        if prepared is None:
            raise ValueError("Captured preparation is unavailable for this request")
        target = replace(request, level="full")
        manifest = {**_read(prepared[0]), "request": target.to_dict()}
        with self.store._transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO snapshots VALUES (?,?,?)", (request_id(target), "preparation", _blob(manifest))
            )
        snapshot, documents = self._preparation(target)
        pinned = Revision(row["number"], row["digest"])
        with self._condition:
            for slot in slots:
                revision = self.store.create_revision({"replay": identity}) if slot == INTERACTIVE else pinned
                try:
                    key = self._schedule(target, revision, snapshot, documents, timeout, slot=slot)
                except RuntimeError as error:
                    if "busy" not in str(error):
                        raise
                    continue
                break
            else:
                raise RuntimeError("Every compatible slot is busy; replay again when one is idle")
            # Recorded before the slot actor can take the job: the condition is still held.
            self.store.put_replay(key.request_id, identity, attempt=key.attempt_id)
        self._replay_state(key, identity, "running", None)
        try:
            result = self.wait(key, timeout=None if timeout is None else timeout + 5)
        except BaseException as error:
            if isinstance(error, ExecutionFailure):
                status = error.status
            else:
                self.cancel(key)
                status = "cancelled"
            self.store.set_replay(key.request_id, key.attempt_id, status, details={"message": str(error)[:4096]})
            self._replay_state(key, identity, status, None)
            raise
        from simulatte.trace import Trace

        verification = verify_replay(original, result, Trace.open(result.path / "run.simtrace").verify())
        self.store.set_replay(
            key.request_id,
            key.attempt_id,
            "succeeded",
            verified=verification.verified,
            details={"reason": verification.reason},
        )
        self._replay_state(key, identity, "succeeded", verification.verified)
        return ReplayResult(result.key, verification)

    def _replay_state(self, key, original, status, verified):
        body = {"request_id": key.request_id, "original": original, "status": status, "verified": verified}
        self._emit("replay_state", key, body)

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

    def start_experiment(self, experiment, *, workers=None, timeout=None, model_id=None) -> str:
        """Expand an experiment at a pinned preparation and dispatch it to the local and remote slots.

        ``workers`` names ``local`` and/or workers of ``workers.toml`` (default: the file's, else
        local); remote workers get ``<worker>-<n>`` slots and the project is preflighted once
        (design 9). Reuses the preparation bound through ``inputs`` when there is one, else
        prepares. ``timeout`` bounds the preparation and each replication attempt. Replications
        whose content already has a committed result are ``reused``. Returns the run id; the same
        definition on unchanged inputs is the same run (D103).
        """
        from simulatte.experiments.definition import Experiment
        from simulatte.experiments.expansion import expand

        if type(experiment) is not Experiment:
            raise ValueError("start_experiment needs an Experiment")
        if isinstance(workers, str):
            raise ValueError("workers must be a list of worker names")
        names = tuple(experiment.workers if workers is None else workers) or ("local",)
        if any(type(name) is not str for name in names):
            raise ValueError("workers must be a list of worker names")
        remote = [name for name in dict.fromkeys(names) if name != "local"]
        self._worker_specs(remote)
        if "local" in names and not self.slots.local:
            raise ValueError("No local experiment slots are configured")
        self._deadline(timeout)
        bound = (
            (None if self._inputs is None else self._inputs._prepared)
            if model_id is None
            else self.inputs_for(model_id)._prepared
        )
        if bound is None:
            prepared = (
                self.prepare(timeout=timeout) if model_id is None else self.prepare_model(model_id, timeout=timeout)
            )
        else:
            prepared = bound
        runtimes = {}
        if remote:
            from simulatte.execution.preflight import preflight

            checked = preflight(
                prepared.snapshot.source.project,
                self.project.root / "uv.lock",
                coordinator_python=self.python,
                trusted_runtime_hash=self.runtime.content_hash,
            )
            self.preflight_warnings = list(checked.warnings)
            runtimes["uv"] = checked.spec
        if "local" in names:
            runtimes["local"] = prepared.runtime
        project = prepared.snapshot.source.project
        layouts = {}
        for variant in experiment.layouts:
            if variant.file == project.layout:
                continue
            captured = read_layout_file(project.root, variant.file)
            if captured.raw is None:
                raise ValueError(f"Layout variant file {variant.file!r} does not exist")
            layer = _plain(captured.layer().to_dict())
            layouts[variant.label] = (layout_hash("file", layer), self._document("layout", layer))
        expanded = expand(experiment, prepared, layouts, runtimes)
        template = {
            name: value
            for name, value in expanded.requests[(0, 0, min(runtimes))].to_dict().items()
            if name not in ("layout_hash", "parameters", "seed", "runtime")
        }
        preparation = {
            "revision": asdict(prepared.revision),
            "request": template,
            "runtimes": {kind: _plain(runtime) for kind, runtime in runtimes.items()},
            "documents": {name: value for name, value in _plain(prepared.documents).items() if name != "layout_ref"},
            "project": {**asdict(project), "root": str(self.project.root)},
            "workers": list(names),
        }
        if self.environments is not None and self.environments.selected is not None:
            preparation["environment"] = self.environments.selected.id
            preparation["environment_evidence"] = _plain(self.environments.selected.evidence)
        if model_id is not None:
            preparation["model_id"] = model_id
        with self._condition:
            if self._closed or self._fatal is not None:
                raise RuntimeError("Coordinator is closed or has a storage failure; restart it")
            identity = self.store.put_experiment(experiment.to_dict())
            run_id = self.store.create_experiment_run(
                identity,
                preparation,
                [configuration.to_dict() for configuration in expanded.configurations],
                expanded.replications,
            )
            # An existing run keeps its stored preparation; a paused one resumes.
            self._activate_experiment(run_id, timeout=timeout)
        return run_id

    def _activate_experiment(self, run_id: str, *, timeout=None) -> None:
        """Restore the run's stored worker selection before admitting its replications.

        Called under the coordinator condition: missing workers leave a paused run
        paused, and actor threads cannot dispatch until activation finishes.
        """
        record = self.store.experiment_run(run_id)
        if record["status"] not in ("running", "paused"):
            return
        names = record["preparation"]["workers"]
        if "local" in names and not self.slots.local:
            raise ValueError("No local experiment slots are configured")
        remote = [name for name in dict.fromkeys(names) if name != "local"]
        configured = self._worker_specs(remote)
        self._add_workers([configured[name] for name in remote])
        generation = record["preparation"].get("environment")
        if generation is not None:
            if (
                self.environments is None
                or self.environments.selected is None
                or self.environments.selected.id != generation
            ):
                raise ValueError("Resume requires the experiment captured environment; reconstruct and select it first")
            self.environments.acquire(generation, "experiment:" + run_id)
        try:
            self._scheduler.activate(run_id, timeout=timeout)
        except BaseException:
            if self.environments is not None:
                self.environments.release("experiment:" + run_id)
            raise

    def resume_experiment(self, run_id: str, *, timeout=None) -> None:
        """Dispatch a paused run's pending replications again (explicit only, D97)."""
        self._deadline(timeout)
        with self._condition:
            if self._closed or self._fatal is not None:
                raise RuntimeError("Coordinator is closed or has a storage failure; restart it")
            status = _run_status(self.store, run_id)
            if status != "paused":
                raise ValueError(f"Only a paused experiment can resume; this one is {status}")
            self._activate_experiment(run_id, timeout=timeout)

    def cancel_experiment(self, run_id: str) -> None:
        """Cancel active attempts through attempt cancellation, then every pending replication."""
        self._scheduler.cancel(run_id)

    def wait_experiment(self, run_id: str, timeout=None) -> dict:
        """Wait until the run is no longer running here; returns counts per state and configuration."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while _run_status(self.store, run_id) == "running" and run_id in self._scheduler._runs:
            if self._fatal is not None:
                raise ExecutionFailure(None, self._fatal.status, self._fatal.details)
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("Waiting for experiment timed out")
            with self._condition:
                self._condition.wait(0.05)
        return _report(self.store, run_id)

    def delete_experiment(self, run_id: str) -> dict:
        """Remove a run that is not running, and the results only it referenced."""
        with self._condition:
            return self.store.delete_experiment_run(run_id)

    def close(self):
        with self._condition:
            if not self._closed:
                for actor in self._actors.values():
                    for job in (actor.current, actor.pending):
                        if job is None:
                            continue
                        if job.experiment is None:
                            self.cancel(job.key)
                        elif self._stop(job.key, "lost", {"message": "Coordinator closed"}) and self._fatal is None:
                            # Not a failure: the replication is pending again, without spending a dispatch.
                            try:
                                self._scheduler.release(job.key)
                            except (OSError, sqlite3.Error):
                                pass  # Recovery treats it as lost at the next open (counted).
                try:
                    self._scheduler.pause()
                except (OSError, sqlite3.Error):
                    pass  # Recovery pauses the runs at the next open.
                self._closed = True
                self._condition.notify_all()
        deadline = time.monotonic() + 10
        for actor in self._actors.values():
            actor.thread.join(max(0.0, deadline - time.monotonic()))
        if any(actor.thread.is_alive() for actor in self._actors.values()):
            raise RuntimeError("Coordinator shutdown did not finish")
        if not self._preview_done.wait(10):
            raise RuntimeError("Layout preview shutdown did not finish")
        if self._owns_store:
            self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class SlotActor:
    """One execution slot: a thread owning one transport and at most one attempt at a time.

    ``_run_job`` is the SP4 worker message loop, keyed on this slot's transport. The
    interactive slot runs queued jobs; an experiment slot also asks the scheduler for
    the next replication whenever it is idle. A ``uv`` (remote) slot first stages the
    request's archives and documents and provisions its environment on the worker.

    A slot whose worker is unreachable (probe or handshake failure) waits before asking
    the scheduler again: ``BACKOFF`` seconds first, doubling up to the cap, reset by the
    next successful hello. Explicitly scheduled jobs and ``close()`` are not delayed.
    """

    BACKOFF = (15.0, 300.0)  # First delay and cap, in seconds.

    def __init__(
        self,
        coordinator: Coordinator,
        slot: str,
        transport_factory,
        *,
        kind: str = "local",
        host: str | None = None,
        worker=None,
    ):
        self.coordinator = coordinator
        self.slot = slot
        self.kind = kind
        self.host = host  # The worker name recorded on attempts (``attempts.worker``); ``None`` is local.
        self.spec = worker  # The remote slot's WorkerSpec (``provision_timeout``).
        self.transport_factory = transport_factory
        self.pending: _Job | None = None
        self.current: _Job | None = None
        self.worker: Any = None
        self.python: str | None = None  # The Python a remote supervisor was started on.
        self.backoff = 0.0  # The last backoff delay; 0 once a hello succeeds.
        self.backoff_until = 0.0  # The time.monotonic() before which the slot takes no replication.
        self.backoff_reason: str | None = None
        self._unreachable: str | None = None  # Set when the last job ended in WorkerUnreachable.
        self._reached = False  # Whether the last job's request was sent to the worker.
        self.thread = threading.Thread(target=self._loop, name=f"simulatte-slot-{slot}", daemon=True)

    def _start_worker(self, python: str | None = None):
        if self.worker is not None and python is not None and python != self.python:
            self.worker.close()  # D105: one Python request per supervisor and environment.
            self.worker = None
        if self.worker is None:
            try:
                self.worker = self.transport_factory() if python is None else self.transport_factory(python=python)
            except BootstrapError as error:
                if error.code == "probe_failed":  # ssh exit 255 or a probe timeout: the host is unreachable.
                    raise WorkerUnreachable(str(error)) from error
                raise
            self.python = python
            try:
                hello = self.worker.receive(10)
            except BootstrapError:
                raise
            except OSError as error:  # Includes the 10 s pre-handshake budget (TimeoutError).
                code = _exit_status(self.worker) if self.kind == "uv" else None
                if code is not None and code != 255:
                    # The remote command ended before hello: a permanent bootstrap failure, not loss.
                    reason = {UV_MISSING: "uv_missing", RUNTIME_MISSING: "runtime_missing"}.get(
                        code, "bootstrap_failed"
                    )
                    raise BootstrapError(reason, f"Remote bootstrap exited with status {code}: {error}") from error
                raise WorkerUnreachable("Worker handshake failed") from error
            # Protocol 1.0 supervisors predate remote staging; they serve local slots only.
            accepted = ((1, 0), (1, 1)) if self.kind == "local" else ((1, 1),)
            if (
                hello.type != "hello"
                or tuple(hello.body["protocol"]) not in accepted
                or "metadata-artifacts" not in hello.body["capabilities"]
            ):
                raise ValueError("Worker protocol/capabilities are incompatible")
            if self.kind == "uv":
                missing = {"staging-v1", "uv-provisioning-v1"} - set(hello.body["capabilities"])
                if missing:
                    message = "Worker lacks capabilities: " + ", ".join(sorted(missing))
                    raise RemoteStepError("worker_capabilities", {"message": message})
                if hello.body.get("trusted_runtime_hash") != self.coordinator.runtime.content_hash:
                    raise RemoteStepError("runtime_mismatch", {"message": "Worker runs another trusted runtime"})
            self.backoff = self.backoff_until = 0.0  # The worker answered: no more backoff.
        return self.worker

    def _remote(self, job):
        """Stage, provision and return the ``run`` body for a ``uv`` request (design 9).

        Blobs are named by SHA-256 and documents keep their D84 envelope; the worker turns
        them into host-local paths. ``runtime_ref`` is replaced by the environment's evidence.
        """
        c, key, request = self.coordinator, job.key, job.request
        runtime = request.runtime
        if runtime.get("kind") != "uv":
            raise ValueError("Remote slots run uv requests only")
        worker = self._start_worker(python=runtime["python"])

        def check():
            c.store.require_active(key)  # StaleAttempt once cancelled: the transport is closed.

        def notify(kind, body):
            c._emit(kind, key, body)

        steps = {"check": check, "notify": notify}
        source, inputs = job.snapshot.source, job.snapshot.inputs
        reference = c._document("request", request.to_dict())
        try:
            stage_blob(worker, "source", source.archive, source.archive_hash, key, **steps)
            stage_blob(worker, "inputs", inputs.archive, inputs.archive_hash, key, **steps)
            documents = {}
            for name in ("layout_ref", "expected_metadata_ref"):
                document = job.documents[name]
                stage_blob(worker, "document", Path(document["path"]), document["sha256"], key, **steps)
                documents[name] = {"sha256": document["sha256"], "length": document["length"]}
            stage_blob(worker, "document", Path(reference["path"]), reference["sha256"], key, **steps)
            remaining = c._remaining(job.deadline)
            limit = self.spec.provision_timeout if self.spec is not None else 600.0
            timeout = limit if remaining is None else min(limit, remaining)
            environment = provision(worker, runtime, source.archive_hash, key, timeout=timeout, **steps)
        except ConnectionError as error:
            raise WorkerLost(str(error)) from error
        if environment["spec_hash"] != provisioning_spec_hash(runtime):
            raise ValueError("Worker provisioned another environment")
        config = {
            "reference": request.model,
            "metadata_artifacts": True,
            "staged_snapshot": {
                "source_sha256": source.archive_hash,
                "inputs_sha256": inputs.archive_hash,
                "python_path": list(source.project.python_path),
            },
            "environment": {"spec_hash": environment["spec_hash"], "evidence_sha256": environment["evidence_sha256"]},
            **documents,
        }
        body = {
            "config": config,
            "timeout": c._remaining(job.deadline),
            "request_ref": {"sha256": reference["sha256"], "length": reference["length"]},
        }
        return worker, body

    def _run_job(self, job):
        c = self.coordinator
        key = job.key
        receiver = None
        snapshot_path = c.store.root / "staging" / key.attempt_id / "snapshot"
        self._reached = False
        try:
            c.store.require_active(key)
            c.store.transition(key, "preparing")
            receiver = SegmentReceiver(c.store, key)
            c._remaining(job.deadline)
            inspect = isinstance(job.request, InspectionRequest)
            if self.kind == "uv" and not inspect:
                worker, body = self._remote(job)
            else:
                paths = materialize(job.snapshot, snapshot_path)
                c._remaining(job.deadline)
                worker = self._start_worker()
                config = {
                    "project_root": str(c.project.root),
                    "snapshot_root": str(paths.root),
                    "snapshot_roots": list(map(str, paths.python_path)),
                    "external_roots": [str(c.runtime.root), *c.distribution_roots],
                    "distribution_roots": c.distribution_roots,
                    "reference": job.request.model,
                    "metadata_artifacts": True,
                    **job.documents,
                }
                body = {
                    "config": config,
                    "timeout": c._remaining(job.deadline),
                    "inspection_ref" if inspect else "request_ref": c._document("request", job.request.to_dict()),
                }
            c.store.require_active(key)
            self._reached = True  # From here a result may exist: a re-dispatch keeps this request.
            c._send(worker, "inspect" if inspect else "run", body, key=key)
            ready = None
            last_cursor = None
            cancelled = False
            acknowledged = False
            while True:
                active = c._active(key)
                if not active and not cancelled:
                    c._send(worker, "cancel", {}, key=key)
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
                    if job.preview is not None and message.type == "cancelled" and acknowledged:
                        job.preview["complete"] = True
                    if active:
                        details = _plain(message.body)
                        status = (
                            "cancelled"
                            if message.type == "cancelled"
                            else "disk_full"
                            if details.get("code") == "disk_full"
                            else "failed"
                            if details.get("code") in ("timeout", "runtime_changed")
                            else "validation_failed"
                            if details.get("phase") in ("preparing", "inspecting", "ready")
                            else "failed"
                        )
                        c._record_failure(key, status, details)
                    return
                if not active:
                    if message.type in ("result", "model_info"):
                        return
                    continue
                if message.type in ("trace_chunk", "artifact_fragment"):
                    receipt = receiver.receive(
                        _plain(message.body["segment"]), _plain(message.body["fragment_offset"]), message.payload
                    )
                    c._send(worker, "ack", asdict(receipt), key=key)
                    if receipt.segment_complete and message.type == "trace_chunk":
                        prefix = receiver.prefix()
                        assert prefix is not None
                        from simulatte.execution.cursor_hint import committed_cursor

                        if not receipt.duplicate:
                            last_cursor = committed_cursor(
                                receiver.segment_path(receipt.id),
                                first=message.body["segment"]["offset"] == 0,
                                previous=last_cursor,
                            )
                        c._emit(
                            "trace_available",
                            key,
                            {
                                "generation": prefix.generation,
                                "length": prefix.length,
                                "sha256": prefix.sha256,
                                "last_cursor": last_cursor,
                            },
                        )
                elif message.type == "ready":
                    if inspect or ready is not None:
                        raise ValueError("Unexpected duplicate readiness")
                    ready = _plain(message.body)
                elif message.type == "model_info":
                    if not inspect:
                        raise ValueError("Unexpected inspection result")
                    metadata = c._metadata(receiver, message.body["metadata_ref"], "model-info")
                    selected_model(metadata)
                    document = c._document("model-info", metadata)
                    c.store.complete_inspection(
                        key,
                        _plain(message.body["metadata_ref"]),
                        execution_exited=True,
                        descendants_cleaned=True,
                        document=document,
                    )
                    c._emit("model_info", key, {"document": document})
                    return
                elif message.type == "result":
                    if inspect or not acknowledged or job.preview is not None:
                        raise ValueError("Result before acknowledged readiness or from a preview")
                    data = c._metadata(receiver, message.body["proposal"], "result-proposal")
                    if data.pop("schema") != 1:
                        raise ValueError("Unknown result proposal schema")
                    resolved = data.pop("resolved_manifest")
                    if resolved.pop("schema") != 1:
                        raise ValueError("Unknown resolved manifest schema")
                    proposal = ResultProposal(**data, resolved_manifest=ResolvedManifest(**resolved))
                    c.store.transition(key, "finalizing")
                    result = publish(c.store, key, proposal, execution_exited=True, descendants_cleaned=True)
                    c._emit("result", key, {"attempt_id": result.key.attempt_id})
                    return
                elif message.type in ("heartbeat", "diagnostics", "kpi"):
                    c._emit(message.type, key, message.body)
                else:
                    raise ValueError("Unsupported worker notification")
                if ready is not None and not acknowledged:
                    try:
                        resolved = c._metadata(receiver, ready["resolved"], "resolved-manifest")
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
                    c.store.transition(key, "ready")
                    if job.preview is not None:
                        if trace.layout is None or not trace.layout.reusable:
                            raise ValueError("Preview requires a resolved layout")
                        job.preview["bundle"] = trace.layout
                        c._send(worker, "cancel", {}, key=key)
                        cancelled = acknowledged = True
                        continue
                    # Presentation views belong to the interactive slot (experiment attempts are pinned).
                    if self.slot == INTERACTIVE and trace.layout is not None and trace.layout.reusable:
                        c.store.bind_ready_view(key, trace.layout)
                    c.store.transition(key, "running")
                    c._send(worker, "ready_ack", {}, key=key)
                    acknowledged = True
                    c._emit("ready", key, ready)
        except StaleAttempt:
            if self.worker is not None:
                self.worker.close()
                self.worker = None
        except Exception as error:
            if isinstance(error, WorkerUnreachable):
                self._unreachable = str(error)[:4096]
            if c._active(key):
                status = "disk_full" if _disk_full(error) else "lost" if isinstance(error, WorkerLost) else "failed"
                details = {"message": str(error)[:4096]}
                if isinstance(error, RemoteStepError):
                    # uv_missing, stage_mismatch, provisioning_failed, worker_capabilities, runtime_mismatch...
                    details = {**error.details, "message": str(error)[:4096]}
                    status = "disk_full" if error.code == "disk_full" else "failed"
                elif isinstance(error, BootstrapError):
                    details["code"] = error.code  # uv_missing, runtime_missing, bootstrap_failed...
                diagnostics = getattr(self.worker, "diagnostics", None)
                if self.kind == "uv" and diagnostics is not None:
                    tail = diagnostics.tail("stderr")[-4096:].decode("utf-8", errors="replace").strip()
                    if tail:
                        details["stderr"] = tail  # The remote host's own words, bounded.
                c._record_failure(key, status, details)
            # A receiver/preflight/protocol failure must contain the child before
            # accepting another assignment. No automatic retry of this request.
            if self.worker is not None:
                self.worker.close()
                self.worker = None
        finally:
            if snapshot_path.exists():
                try:
                    _remove(snapshot_path)
                except OSError as error:
                    c._emit("cleanup_error", key, {"message": str(error)[:4096]})
            with c._condition:
                c._condition.notify_all()

    def _next(self):
        try:
            return self.coordinator._scheduler.next(self)
        except Exception as error:
            self.coordinator._fail_admission(error)
            return None

    def _finish(self, job):
        try:
            self.coordinator._scheduler.on_terminal(
                job.key, self.coordinator.store.attempt(job.key)["status"], reached=self._reached
            )
        except Exception as error:
            self.coordinator._fail_admission(error)

    def _back_off(self):
        """Delay the next ``_next()`` after an unreachable worker and announce it (``slot_state``)."""
        c = self.coordinator
        first, cap = self.BACKOFF
        reason, self._unreachable = self._unreachable, None
        with c._condition:
            self.backoff = first if not self.backoff else min(2 * self.backoff, cap)
            self.backoff_until = time.monotonic() + self.backoff
            self.backoff_reason = reason
        if self.slot != INTERACTIVE:
            # ``until`` is the seconds remaining: monotonic deadlines mean nothing to other processes.
            c._emit(
                "slot_state", None, {"slot": self.slot, "status": "backoff", "until": self.backoff, "reason": reason}
            )

    def _loop(self):
        c = self.coordinator
        try:
            while True:
                with c._condition:
                    if c._closed and self.pending is None:
                        return
                    job, self.pending = self.pending, None
                    if (
                        job is None
                        and self.slot not in (INTERACTIVE, "inspection")
                        and not c._closed
                        and c._fatal is None
                        and time.monotonic() >= self.backoff_until
                    ):
                        job = self._next()
                    self.current = job
                if job is not None:
                    self._run_job(job)
                    with c._condition:
                        self.current = None
                        c._auto_owners.pop(job.key.attempt_id, None)
                        if c.environments is not None:
                            c.environments.release(job.key.attempt_id)
                        c._condition.notify_all()
                    if job.experiment is not None:
                        self._finish(job)
                    if self._unreachable is not None:
                        self._back_off()
                elif self.worker is not None:
                    try:
                        self.worker.receive(0.1)  # Drain idle heartbeats; never grow a retained idle queue.
                    except TimeoutError:
                        if time.monotonic() - self.worker.channel.last_heartbeat >= 5:
                            self.worker.close()
                            self.worker = None
                    except OSError:
                        self.worker.close()
                        self.worker = None
                else:
                    with c._condition:
                        c._condition.wait(0.1)
        finally:
            if self.worker is not None:
                self.worker.close()
                self.worker = None
