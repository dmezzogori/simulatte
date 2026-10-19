"""Project setup and history are independent of model inspection and execution."""

from __future__ import annotations

from collections import deque
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import threading
from uuid import uuid4

from simulatte.execution.coordinator import Coordinator
from simulatte.execution.project_store import ProjectStore
from simulatte.execution.schema import _plain
from simulatte.execution.store import RunStore
from simulatte.execution.store_reader import StoreReader
from simulatte.projects import (
    StudioTarget,
    WorkspaceDefinition,
    ModelRegistration,
    load_workspace,
    preview_config,
    apply_config,
    starter_files,
    install_starter,
    ConfigEdit,
)
from simulatte.projects.environments import EnvironmentPool, LockPreviewRequired


class ProjectService:
    def __init__(self, target):
        self.target = target
        self.root = target.root.resolve()
        self.workspace = None
        self.store = None
        self.reader = None
        self.environments = None
        self.coordinator = None
        self.analysis_runner = None
        self.read_only = False
        self.error = None
        self.restart_generation = None
        self.restart_interpreter = None
        self._previews = {}
        self._lock = threading.RLock()
        self._closed = False
        self.initial_options = None
        self._generation = None
        self._preparation_lock = threading.Lock()
        self._preparation_id = None
        self._cancelled_preparations = deque(maxlen=32)

    @classmethod
    def open(cls, target: StudioTarget, *, read_only=False):
        service = cls(target)
        service.read_only = read_only
        root = service.root / ".simulatte"
        try:
            service.workspace = load_workspace(service.root)
            if (root / "index.sqlite").exists():
                service.reader = StoreReader.open(root)
                selected = next((g for g in service.reader.generations() if g["state"] == "selected"), None)
                if selected is not None and not read_only:
                    evidence = selected["evidence"]
                    spec = evidence["spec"]
                    interpreter = (
                        Path(spec["interpreter"])
                        if spec["kind"] == "existing"
                        else root
                        / "cache/environments"
                        / selected["id"]
                        / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
                    )
                    if not interpreter.is_file():
                        raise ValueError(
                            "Project interpreter is unavailable. Reconstruct its environment "
                            "or open with a compatible runtime."
                        )
                    from simulatte.projects.bootstrap import validate_runtime

                    validate_runtime(interpreter)
                    if interpreter.absolute() != Path(sys.executable).absolute():
                        raise ValueError(
                            "Project runtime requires a launcher handoff; reopen this folder with simulatte studio"
                        )
                    service._generation = selected["id"]
            if not service.read_only:
                if service.reader is not None:
                    service.reader.close()
                    service.reader = None
                service.store = RunStore.open(root)
                service.environments = EnvironmentPool(service.root, service.store)
                if service.workspace is not None:
                    ProjectStore(service.store).register(service.workspace)
        except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
            service.error = str(error)
            service.read_only = True
            if service.store is not None:
                service.store.close()
                service.store = None
                service.environments = None
            if service.reader is None and (root / "index.sqlite").is_file():
                try:
                    service.reader = StoreReader.open(root)
                except (ValueError, OSError, sqlite3.Error):
                    pass
        return service

    def _writable(self):
        if self._closed:
            raise ValueError("Project is closed")
        if self.restart_generation is not None:
            raise ValueError("Project restart is pending; new work is unavailable")
        if self.read_only or self.store is None:
            raise ValueError("Project is read-only; repair the environment or close the other coordinator first")

    def _preview(self, kind, value, **display):
        token = secrets.token_urlsafe(24)
        if len(self._previews) >= 16:
            self._previews.clear()
        self._previews[token] = (kind, value)
        return {"ok": True, "preview_id": token, **display}

    def configure(self, body):
        with self._lock:
            self._writable()
            assert self.store is not None and self.environments is not None
            if "apply" in body:
                kind, value = self._previews.pop(body["apply"])
                if kind == "starter":
                    install_starter(self.root, value)
                else:
                    apply_config(value)
                self.workspace = load_workspace(self.root)
                if self.workspace is not None:
                    ProjectStore(self.store).register(self.workspace)
                    if self.coordinator is not None:
                        from simulatte.execution.model_context import ModelContext

                        with self.coordinator._condition:
                            contexts = {}
                            for model in self.workspace.models:
                                old = self.coordinator.contexts.get(model.id)
                                if old is not None and (old.registration.entrypoint, old.registration.layout) == (
                                    model.entrypoint,
                                    model.layout,
                                ):
                                    old.registration = model
                                    contexts[model.id] = old
                                else:
                                    contexts[model.id] = ModelContext(self.coordinator, model)
                            self.coordinator.contexts = contexts
                return {"ok": True, "project": self.state()}
            if "starter" in body:
                files = starter_files(body["starter"])
                return self._preview(
                    "starter", files, files={name: data.decode("utf-8") for name, data in files.items()}
                )
            models = []
            existing = {} if self.workspace is None else {m.key: m for m in self.workspace.models}
            for item in body.get("models", []):
                key = item["key"]
                identity = item.get("id") or (existing[key].id if key in existing else str(uuid4()))
                models.append(
                    ModelRegistration(
                        identity,
                        key,
                        item.get("name", key),
                        item["entrypoint"],
                        item.get("layout", f"layouts/{key}.json"),
                    )
                )
            definition = WorkspaceDefinition(
                self.root,
                self.workspace.id if self.workspace is not None else str(uuid4()),
                tuple(models),
                () if self.workspace is None else self.workspace.analyses,
                body.get(
                    "default_model",
                    next(
                        (m.key for m in models if self.workspace is not None and m.key == self.workspace.default_model),
                        None,
                    ),
                ),
            )
            edit = preview_config(self.root, definition)
            if "include" in body:
                import tomlkit
                import re
                from simulatte.execution.snapshot import _path

                document = tomlkit.parse(edit.proposed_bytes.decode("utf-8"))
                include = body["include"]
                if not isinstance(include, list) or not all(isinstance(item, str) for item in include):
                    raise ValueError("Bundle include must be a list of relative paths")
                for item in include:
                    _path(item)
                bundle = document.setdefault("tool", {}).setdefault("simulatte", {}).setdefault("bundle", {})
                bundle["include"] = list(dict.fromkeys([*bundle.get("include", []), *include]))
                if "project" not in document:
                    document["project"] = {
                        "name": re.sub(r"[^a-z0-9]+", "-", self.root.name.lower()).strip("-") or "simulation-project",
                        "version": "0.1.0",
                        "requires-python": ">=3.11",
                        "dependencies": ["simulatte[studio]>=0.17.0"],
                    }
                edit = ConfigEdit(edit.path, edit.original_sha256, tomlkit.dumps(document).encode("utf-8"))
            return self._preview("config", edit, files={"pyproject.toml": edit.proposed_bytes.decode("utf-8")})

    def cancel_preparation(self, identity):
        if not isinstance(identity, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity) is None:
            raise ValueError("Invalid preparation operation id")
        with self._preparation_lock:
            self._writable()
            if identity not in self._cancelled_preparations:
                self._cancelled_preparations.append(identity)
            if self._preparation_id == identity:
                assert self.environments is not None
                self.environments.cancel()
        return {"ok": True}

    def prepare_environment(self, body):
        self._writable()
        assert self.store is not None and self.environments is not None
        if self.workspace is None:
            raise ValueError("Configure the project first")
        if body.get("cancel"):
            self.environments.cancel()
            return {"ok": True}
        identity = body.get("operation_id", secrets.token_urlsafe(24))
        if not isinstance(identity, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity) is None:
            raise ValueError("Invalid preparation operation id")
        with self._preparation_lock:
            if identity in self._cancelled_preparations:
                raise ValueError("Environment preparation cancelled before starting")
            if self._preparation_id is not None:
                raise ValueError("Environment preparation is busy")
            self.environments._cancelled.clear()
            self._preparation_id = identity
        try:
            if "existing" in body:
                generation = self.environments.use_existing(Path(body["existing"]))
            else:
                try:
                    generation = self.environments.prepare(
                        self.workspace, lock_update=body.get("lock_update", False), reset_cancel=False
                    )
                except LockPreviewRequired as preview:
                    return self._preview(
                        "lock", preview.edit, files={"uv.lock": preview.edit.proposed_bytes.decode("utf-8")}
                    )
            return {"ok": True, "generation": self._generation_state(generation)}
        finally:
            with self._preparation_lock:
                self._preparation_id = None

    def select_environment(self, body):
        with self._lock:
            self._writable()
            assert self.store is not None and self.environments is not None
            generation = self.environments.get(body["id"])
            from simulatte.projects.bootstrap import validate_runtime

            validate_runtime(generation.interpreter)
            co = self.coordinator
            if co is not None and not co.pause_admission():
                return {"ok": True, "status": "busy"}
            try:
                outcome = self.environments.select(generation.id)
                if outcome == "busy":
                    return {"ok": True, "status": "busy"}
                if generation.interpreter.absolute() == Path(sys.executable).absolute():
                    self.environments.activate(generation.id)
                    self._generation = generation.id
                    return {"ok": True, "status": "selected"}
                if body.get("restart"):
                    self.restart_generation = generation.id
                    self.restart_interpreter = generation.interpreter
                return {"ok": True, "status": "restart_required", "generation": generation.id}
            finally:
                if co is not None and self.restart_generation is None:
                    co.resume_admission()

    def ensure_coordinator(self):
        # Construction, publication, configuration and close share lifecycle ownership.
        # Never publish a partially configured coordinator to another connection.
        with self._lock:
            self._writable()
            assert self.store is not None and self.environments is not None
            if self.coordinator is None:
                coordinator = Coordinator.for_workspace(self.root, store=self.store, environments=self.environments)
                from simulatte.execution.workers import load_workers

                try:
                    try:
                        coordinator._add_workers(tuple(load_workers().values()))
                    except ValueError as error:
                        self.error = str(error)
                except BaseException:
                    coordinator.close()
                    raise
                self.coordinator = coordinator
            return self.coordinator

    @property
    def timeout(self):
        return getattr(self.initial_options, "flags", {}).get("timeout")

    def prepare_model(self, model_id):
        co = self.ensure_coordinator()
        prepared = co.prepare_model(model_id, timeout=30 if self.timeout is None else self.timeout)
        owner = co.inputs_for(model_id)
        initial = self.initial_options(prepared) if owner.state is None and self.initial_options is not None else None
        owner.bind(prepared, initial=initial)
        return {"ok": True, "project": self.state()}

    @staticmethod
    def _generation_state(generation):
        return {
            "id": generation.id,
            "interpreter": str(generation.interpreter),
            "state": generation.state,
            "kind": generation.evidence["spec"]["kind"],
            "runtime": _plain(generation.evidence["probe"]["runtime"]["facts"]),
        }

    def state(self):
        with self._lock:
            models = []
            for model in () if self.workspace is None else self.workspace.models:
                context = None if self.coordinator is None else self.coordinator.contexts.get(model.id)
                inputs = None if context is None else context.inputs.state
                metadata = None if context is None or context.prepared is None else _plain(context.prepared.metadata)
                models.append(
                    {
                        **asdict(model),
                        "revision": None if inputs is None else asdict(inputs.revision),
                        "inputs": None
                        if inputs is None
                        else {"options": _plain(inputs.options), "issues": _plain(inputs.issues)},
                        "metadata": metadata,
                        "auto_session": None if context is None else context.auto_session,
                    }
                )
            history = []
            if self.store is not None:
                history = list(ProjectStore(self.store).history(None, after=None).items)
            elif self.reader is not None:
                history = list(self.reader.history(None, after=None).items)
            active = None
            if self.coordinator is not None:
                assert self.store is not None
                with self.coordinator._condition:
                    owner = self.coordinator._owner()
                    if owner is not None:
                        active = {**self.store.attempt(owner), "key": asdict(owner)}
                        with self.store._transaction() as db:
                            row = db.execute(
                                "SELECT model FROM attempt_models WHERE attempt=?", (owner.attempt_id,)
                            ).fetchone()
                        active["model_id"] = None if row is None else row[0]
            selected = None if self.environments is None else self.environments.selected
            generations = []
            if self.environments is not None:
                assert self.store is not None and self.store._db is not None
                with self.store._mutex:
                    ids = [row[0] for row in self.store._db.execute("SELECT id FROM environment_generations")]
                generations = [self._generation_state(self.environments.get(identity)) for identity in ids]
            return {
                "name": self.root.name,
                "id": None if self.workspace is None else self.workspace.id,
                "phase": "recovery" if self.read_only else "ready" if selected is not None else "setup",
                "read_only": self.read_only,
                "error": self.error,
                "models": models,
                "history": history,
                "active_run": active,
                "generations": generations,
                "environment": None if selected is None else self._generation_state(selected),
                "preparation_progress": "" if self.environments is None else self.environments.progress,
                "legacy_reference": self._legacy_reference(),
                "legacy_layout": getattr(self.initial_options, "flags", {}).get("layout", "layout.json")
                if self.target.legacy_reference is not None
                else None,
            }

    def _legacy_reference(self):
        reference = self.target.legacy_reference
        if reference is None:
            return None
        path, separator, name = reference.rpartition(":")
        if not separator or not path.endswith(".py"):
            path, name = reference, "build"
        return str(Path(path).relative_to(self.root).as_posix()) + ":" + name

    def analyses(self):
        """One local analysis slot, independent of simulation and inspection capacity."""
        with self._lock:
            self._writable()
            if self.analysis_runner is None:
                from simulatte.analysis.execution import AnalysisRunner

                self.analysis_runner = AnalysisRunner(self.store, environments=self.environments)
            return self.analysis_runner

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            # Callbacks run in reverse: runner, coordinator, reader, store; a failure skips none.
            with ExitStack() as stack:
                for component in (self.store, self.reader, self.coordinator, self.analysis_runner):
                    if component is not None:
                        stack.callback(component.close)
