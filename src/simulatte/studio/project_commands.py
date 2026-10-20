"""Authenticated project commands and recovery routes on the existing Studio transport."""

from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import re

from starlette.applications import Starlette
from starlette.responses import Response, StreamingResponse, JSONResponse
from starlette.routing import Route, WebSocketRoute

from simulatte.execution.schema import AttemptKey, Revision
from simulatte.execution.store import RevisionConflict, _read
from simulatte.studio import analysis_http
from simulatte.studio.analysis_commands import AnalysisCommands
from simulatte.studio.app import StudioServer, _RUN_STATES
from simulatte.studio.auth import FRAME_CSP, SessionAuth, response_headers
from simulatte.studio.http import References, ImmutableFile, RangeSource, parse_range
from simulatte.studio.websocket import CommandCache, ConnectionHub, EXPERIMENT_COMMANDS


class ProjectServer(StudioServer):
    def __init__(self, service, port, static):
        SessionAuth.__init__(self, port, mode="studio")
        self.service = service
        self.static = Path(static)
        self.allowed = set(json.loads((self.static / "manifest.json").read_text())["assets"])
        self.references = References()
        self.commands = asyncio.Lock()
        self.http_commands = CommandCache()
        self.hub = ConnectionHub(self, self.command, self.state)
        self.on_restart = None
        self._monitors = {}
        self._tasks = []
        self._experiments = {}
        self.analysis = AnalysisCommands(service)
        self.app = Starlette(
            routes=[
                WebSocketRoute("/ws", self.hub.handle),
                Route("/{path:path}", self.request, methods=["GET", "HEAD", "PUT"]),
            ],
            lifespan=self.lifespan,
        )

    @property
    def coordinator(self):
        return self.service.coordinator or SimpleNamespace(store=self.service.store or self.service.reader)

    def snapshot(self, peer=None):
        project = self.service.state()
        extra = {}
        co = self.service.coordinator
        store = self.service.store or self.service.reader
        if store is not None:
            from simulatte.studio.experiments import prefix, replays

            with store._transaction() as db:
                extra["results"] = [
                    {
                        "key": {
                            "request_id": item["request_id"],
                            "attempt_id": item["id"],
                            "generation": item["generation"],
                        },
                        "revision": item["revision"],
                        "created": item["created"],
                        "trace": prefix(db, item["id"]),
                    }
                    for item in project["history"]
                    if item["has_result"]
                ]
                extra["replays"] = replays(db, prefix)
        if store is not None:
            from simulatte.execution import scheduler

            extra["experiments"] = scheduler.runs(store, limit=20)
        if co is not None:
            from simulatte.studio.experiments import ExperimentCommands
            from simulatte.execution.snapshot import non_source_members

            extra["workers"] = ExperimentCommands(co, workers_error=self.service.error).workers()
            for model in project["models"]:
                model["metadata_reference"] = (
                    None if model["metadata"] is None else self.references.put(model["metadata"])
                )
                context = co.contexts[model["id"]]
                model["view"] = (
                    self._view(co.store.presentation_view(revision=context.revision)) if context.revision else None
                )
                try:
                    model["experiment_files"] = [
                        name for name in non_source_members(context.project) if name.endswith(".experiment.json")
                    ]
                except (ValueError, OSError):
                    model["experiment_files"] = []
        return {
            "project": project,
            "revision": None,
            "results": [],
            "startup": {"phase": project["phase"], "error": project["error"]},
            "active": None,
            "inputs": None,
            "metadata": None,
            "view": None,
            "last_good": None,
            "fatal": None,
            **extra,
        }

    @asynccontextmanager
    async def lifespan(self, app):
        pump = asyncio.create_task(self._pump_project())
        try:
            yield
        finally:
            for monitor in self._monitors.values():
                monitor.close()
            pump.cancel()
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(pump, *self._tasks, return_exceptions=True)
            await self.hub.close()
            await asyncio.to_thread(self.service.close)

    async def _pump_project(self):
        while True:
            co = self.service.coordinator
            if co is not None:
                for model_id, monitor in list(self._monitors.items()):
                    if co.contexts.get(model_id) is not monitor.coordinator:
                        monitor.close()
                        del self._monitors[model_id]
                for model_id, context in tuple(co.contexts.items()):
                    if context.inputs.state is not None and model_id not in self._monitors:
                        from simulatte.studio.monitor import ProjectMonitor

                        monitor = ProjectMonitor(context)
                        self._monitors[model_id] = monitor
                        self._tasks.append(asyncio.create_task(monitor.run()))
                for event in co.events():
                    kind, body = event["type"], event["body"]
                    if kind in _RUN_STATES:
                        # The viewer contract names run lifecycle changes ``run_state``; keep the raw kind in the body.
                        kind, body = (
                            "run_state",
                            {"kind": kind, "status": "succeeded" if kind == "result" else kind, "details": body},
                        )
                    self.hub.notify(kind, self._body(body), key=event["key"])
            if self.analysis.watching():
                for body in await asyncio.to_thread(self.analysis.changes):
                    self.hub.notify("analysis_state", body)
            if self.hub.connections:
                await self._broadcast_state()
            if self.service.restart_generation is not None and self.on_restart is not None:
                self.on_restart()
                return
            await asyncio.sleep(0.25)

    def _command(self, message, peer):
        kind, body = message["type"], message["body"]
        service = self.service
        if kind in ("project.get", "state.get"):
            return {"ok": True, "state": self.snapshot(peer)}
        if kind == "history.get":
            if service.store is not None:
                from simulatte.execution.project_store import ProjectStore

                page = ProjectStore(service.store).history(
                    body.get("model_id"), after=body.get("after"), limit=body.get("limit", 50)
                )
            elif service.reader is not None:
                page = service.reader.history(
                    body.get("model_id"), after=body.get("after"), limit=body.get("limit", 50)
                )
            else:
                raise ValueError("Saved history is unavailable")
            return {"ok": True, **asdict(page)}
        if kind == "run.get":
            from simulatte.studio.experiments import prefix

            store = service.store or service.reader
            if store is None:
                raise ValueError("Saved history is unavailable")
            attempt = body["attempt_id"]
            with store._transaction() as db:
                row = db.execute(
                    "SELECT a.*,q.body FROM attempts a JOIN requests q ON q.id=a.request_id WHERE a.id=?", (attempt,)
                ).fetchone()
                if row is None:
                    raise KeyError(attempt)
                run = dict(row)
                annotation = None
                if getattr(store, "version", 3) >= 3:
                    annotation = db.execute(
                        "SELECT name,text,important FROM history_annotations WHERE attempt=?", (attempt,)
                    ).fetchone()
                run.update(
                    {"name": "", "text": "", "important": False}
                    if annotation is None
                    else {
                        "name": annotation["name"],
                        "text": annotation["text"],
                        "important": bool(annotation["important"]),
                    }
                )
                run["request"] = _read(run.pop("body"))
                run["details"] = _read(run["details"])
                run["trace"] = prefix(db, attempt)
                run["artifacts"] = [
                    dict(r) for r in db.execute("SELECT path,length,sha256 FROM artifacts WHERE attempt=?", (attempt,))
                ]
                view = db.execute(
                    "SELECT layout FROM views WHERE attempt=? ORDER BY revision DESC LIMIT 1", (attempt,)
                ).fetchone()
                run["layout"] = None if view is None else self.references.put(_read(view[0]))
            return {"ok": True, "run": run}
        if kind in ("comparison.get", "series.get"):
            from simulatte.studio.experiments import ExperimentCommands

            store = service.store or service.reader
            if store is None:
                raise ValueError("Saved experiments are unavailable")
            return ExperimentCommands(SimpleNamespace(store=store))(
                kind, {key: value for key, value in body.items() if key != "model_id"}
            )
        if kind.startswith("analysis."):
            return self.analysis(kind, body)
        service._writable()
        if kind == "history.annotate":
            from simulatte.execution.project_store import ProjectStore

            ProjectStore(service.store).annotate(
                body["attempt_id"],
                name=body.get("name", ""),
                text=body.get("text", ""),
                important=body.get("important", False),
            )
            return {"ok": True}
        if kind == "project.configure":
            return service.configure(body)
        if kind == "environment.prepare":
            return service.prepare_environment(body)
        if kind == "environment.select":
            return service.select_environment(body)
        if kind == "model.prepare":
            return service.prepare_model(body["model_id"])
        co = service.ensure_coordinator()
        if kind == "run.cancel":
            key = AttemptKey(**body["owner"])
            return {"ok": True, "cancelled": co.cancel(key)}
        model_id = body["model_id"]
        owner = co.inputs_for(model_id)
        context = co.contexts[model_id]
        if kind in EXPERIMENT_COMMANDS:
            from simulatte.studio.experiments import ExperimentCommands

            commands = self._experiments.get(model_id)
            if commands is None or commands.coordinator is not context:
                commands = ExperimentCommands(context, workers_error=service.error)
                self._experiments[model_id] = commands
            return commands(kind, {key: value for key, value in body.items() if key != "model_id"})
        if kind == "model.auto":
            token = co.enable_auto(model_id) if body["enabled"] else co.disable_auto(model_id)
            return {"ok": True, "auto_session": token}
        raw = message.get("revision")
        base = None if raw is None else Revision(**raw)
        if base is None or context.revision != base:
            raise RevisionConflict("Model inputs changed; reload before editing or running")
        if kind == "params.set":
            state = owner.apply(base, body["changes"], auto_run=False)
            return {"ok": True, "revision": asdict(state.revision)}
        if kind in ("run.start", "run.stop_and_run"):
            state = owner.state
            if state is None or state.request is None:
                raise ValueError("Prepare and configure valid inputs before running")
            admission = (
                co.admit_run(
                    model_id, state.request, base, auto_session=body.get("auto_session"), timeout=service.timeout
                )
                if kind == "run.start"
                else co.stop_and_run(
                    AttemptKey(**body["owner"]), model_id, state.request, base, timeout=service.timeout
                )
            )
            return {"ok": True, **asdict(admission)}
        if kind == "layout.get":
            from simulatte.execution.layout_files import read_layout_file

            captured = read_layout_file(context.project.root, context.project.layout)
            try:
                document = {"valid": True, "layer": captured.layer().to_dict()}
            except ValueError as error:
                document = {
                    "valid": False,
                    "raw": (captured.raw or b"").decode("utf-8", errors="replace"),
                    "error": str(error),
                }
            return {"ok": True, "revision": asdict(base), "layout": self.references.put(document)}
        if kind == "layout.validate":
            validation = owner.preview_layout(base, body["layout"], timeout=30)
            return {
                "ok": True,
                "requires_rerun": validation.requires_rerun,
                "layout": None if validation.bundle is None else self.references.put(validation.bundle.to_dict()),
            }
        if kind == "layout.write":
            from simulatte.execution.layout_files import _path
            from simulatte.execution.store import _directory

            target = _path(context.project.root, context.project.layout)
            parent = context.project.root
            for component in target.parent.relative_to(parent).parts:
                parent = parent / component
                _directory(parent)
            state = owner.write_layout(base, body["layout"], auto_run=False, timeout=30)
            return {"ok": True, "revision": asdict(state.revision)}
        raise ValueError("Unsupported project command")

    async def command(self, message, peer):
        try:
            # Preparation is cancellable by another connection; serialization belongs
            # to the environment/inspection owners, not the HTTP event loop.
            return self._body(await asyncio.to_thread(self._command, message, peer))
        except (ValueError, KeyError, TypeError, OSError, RuntimeError, sqlite3.Error) as error:
            return {
                "ok": False,
                "error": {
                    "code": "revision_conflict" if isinstance(error, RevisionConflict) else "failed",
                    "message": str(error)[:4096],
                },
            }

    async def request(self, request):
        path = request.path_params["path"]
        if request.method == "PUT" and path.startswith("api/environment/cancel/"):
            headers = response_headers()
            if not self.headers_valid(request, require_origin=True) or not self.authenticated(request):
                return Response("Authentication required", status_code=403, headers=headers)
            async for chunk in request.stream():
                if chunk:
                    return Response("Cancellation takes no body", status_code=413, headers=headers)
            try:
                result = await asyncio.to_thread(
                    self.service.cancel_preparation, path.removeprefix("api/environment/cancel/")
                )
                return JSONResponse(result, headers=headers)
            except ValueError as error:
                return JSONResponse({"ok": False, "error": {"message": str(error)}}, status_code=400, headers=headers)
        if request.method == "PUT" and path in ("api/layout", "api/layout/validate"):
            from simulatte.layout.types import LayoutLimits
            from simulatte.layout.io import parse_layout

            headers = response_headers()
            if not self.headers_valid(request, require_origin=True) or not self.authenticated(request):
                return Response("Authentication required", status_code=403, headers=headers)
            try:
                for name in ("x-simulatte-command", "x-simulatte-revision", "x-simulatte-model"):
                    if len(request.headers.getlist(name)) != 1:
                        raise ValueError("Model layout PUT requires unique model, command and revision headers")
                identity = request.headers["x-simulatte-command"]
                if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity) is None:
                    raise ValueError("Invalid command id")
                raw = bytearray()
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > LayoutLimits().max_file_bytes:
                        return Response("Layout byte limit exceeded", status_code=413, headers=headers)
                    raw.extend(chunk)
                message = {
                    "v": 1,
                    "type": "layout.validate" if path.endswith("/validate") else "layout.write",
                    "id": identity,
                    "revision": json.loads(request.headers["x-simulatte-revision"]),
                    "body": {
                        "model_id": request.headers["x-simulatte-model"],
                        "layout": parse_layout(bytes(raw)).to_dict(),
                    },
                }
                async with self.commands:
                    result = self.http_commands.get(message)
                    if result is None:
                        result = await self.command(message, None)
                        self.http_commands.put(message, result)
                return JSONResponse(result, headers=headers)
            except (ValueError, KeyError, TypeError) as error:
                return JSONResponse({"ok": False, "error": {"message": str(error)}}, status_code=400, headers=headers)
        if request.method in ("GET", "HEAD") and path.startswith("api/analysis/"):
            return await analysis_http.handle(self, request, path)
        if request.method in ("GET", "HEAD") and path.rpartition("/")[2] == "plotly-frame.html":
            # The sandboxed figure frame may be embedded by Studio; it loads only the local renderer.
            response = await super().request(request)
            if response.status_code == 200:
                response.headers["Content-Security-Policy"] = FRAME_CSP
            return response
        if path.startswith("api/artifacts/"):
            headers = response_headers()
            if not self.headers_valid(request) or not self.authenticated(request):
                return Response("Authentication required", status_code=403, headers=headers)
            try:
                attempt, name = path.removeprefix("api/artifacts/").split("/", 1)
                service = self.service
                if service.reader is not None:
                    reader = service.reader
                    info = next(item for item in reader.artifacts(attempt) if item["path"] == name)
                    target = reader.artifact(attempt, name)
                else:
                    with service.store._transaction() as db:
                        row = db.execute(
                            "SELECT path,length,sha256 FROM artifacts WHERE attempt=? AND path=?", (attempt, name)
                        ).fetchone()
                    if row is None:
                        raise KeyError(name)
                    info = dict(row)
                    target = service.store.result(attempt).path / "artifacts" / name
                source = RangeSource([ImmutableFile(target, info["length"], info["sha256"])], info["length"])
                start, end, status = parse_range(request.headers.get("range"), source.length)
                source.validate(start, end)
                headers.update(
                    {
                        "Content-Type": "application/octet-stream",
                        "Content-Disposition": "attachment",
                        "Content-Length": str(end - start + 1),
                    }
                )
                headers["Accept-Ranges"] = "bytes"
                if status == 206:
                    headers["Content-Range"] = f"bytes {start}-{end}/{source.length}"
                if request.method == "HEAD":
                    return Response(status_code=status, headers=headers)
                return StreamingResponse(source.chunks(start, end), status_code=status, headers=headers)
            except (KeyError, ValueError, OSError, StopIteration):
                return Response("Saved artifact unavailable", status_code=404, headers=headers)
        if request.method == "PUT":
            return Response("Use authenticated model commands", status_code=405, headers=response_headers())
        return await super().request(request)
