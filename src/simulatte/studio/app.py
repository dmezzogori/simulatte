"""Authenticated local Studio application; user Python runs only in execution children."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, nullcontext
from dataclasses import asdict
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import sqlite3

from starlette.applications import Starlette
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route, WebSocketRoute

from simulatte.execution import scheduler
from simulatte.execution.layout_files import read_layout_file, backup_layout, stage_layout
from simulatte.execution.references import read_document
from simulatte.execution.schema import AttemptKey, Revision, _plain
from simulatte.execution.snapshot import non_source_members
from simulatte.execution.store import INTERACTIVE, RevisionConflict, StaleAttempt, _read
from simulatte.layout.io import parse_layout
from simulatte.layout.layers import FileLayer
from simulatte.layout.types import LayoutLimits
from simulatte.studio.auth import SessionAuth, response_headers
from simulatte.studio.experiments import ExperimentCommands, parse_results_query, prefix, replays, report, run_summary
from simulatte.studio.http import ImmutableFile, RangeSource, References, parse_range
from simulatte.studio.lifecycle import ProjectSession
from simulatte.studio.websocket import (
    EXPERIMENT_COMMANDS,
    CommandCache,
    ConnectionHub,
    ProtocolError,
    encode_message,
    json_values,
)

# Attempt events that become the interactive ``run_state``. On other slots they only refresh ``workers_state``:
# ``experiment_progress`` and ``replay_state`` carry replication and replay outcomes.
_RUN_STATES = ("queued", "ready", "result", "cancelled", "failed", "lost", "disk_full", "validation_failed")


class StudioServer(SessionAuth):
    def __init__(self, coordinator, port, static, *, timeout=None, configure=None, watcher=None, workers_error=None):
        super().__init__(port, mode="studio")
        self.coordinator = coordinator
        self.static = Path(static)
        self.allowed = set(json.loads((self.static / "manifest.json").read_text())["assets"])
        self.timeout = timeout
        self.progress = None
        self._unbound_layout = None
        self.references = References()
        self.session = ProjectSession(coordinator, timeout=timeout, configure=configure, watcher=watcher)
        self.experiments = ExperimentCommands(coordinator, timeout=timeout, workers_error=workers_error)
        self._run_states = {}  # The last announced status of each experiment run.
        self.commands = asyncio.Lock()
        self.http_commands = CommandCache()
        self.hub = ConnectionHub(self, self.command, self.state)
        self.app = Starlette(
            routes=[
                WebSocketRoute("/ws", self.hub.handle),
                Route("/{path:path}", self.request, methods=["GET", "HEAD", "PUT"]),
            ],
            lifespan=self.lifespan,
        )

    @asynccontextmanager
    async def lifespan(self, app):
        watching = asyncio.create_task(self.session.run())
        pumping = asyncio.create_task(self._pump())
        try:
            yield
        finally:
            try:
                self.session.close()
                await self.hub.close()
            finally:
                pumping.cancel()
                try:
                    await asyncio.gather(pumping, watching, return_exceptions=True)
                finally:
                    await asyncio.to_thread(self.coordinator.close)

    def _body(self, value):
        # Leave headroom for the envelope; large authoritative values have HTTP references.
        try:
            if len(encode_message(value)) <= 128 << 10:
                return value
        except ProtocolError:
            pass
        return {"ref": self.references.put(value)}

    def _view(self, view):
        if view is None:
            return None
        return {
            "revision": asdict(view.revision),
            "key": asdict(view.key),
            "committed": view.committed,
            "layout": self.references.put(view.bundle.to_dict()),
        }

    _prefix = staticmethod(prefix)

    def snapshot(self, peer=None):
        co = self.coordinator
        with co._condition, co.store._mutex:
            inputs = co.inputs.state
            revision = co.store.current_revision()
            metadata = self.session.metadata
            if inputs is not None:
                metadata = co.inputs.captured_inputs()[1].metadata
            with co.store._transaction() as db:
                row = db.execute(
                    "SELECT a.* FROM attempts a JOIN slots s ON s.active=a.id WHERE s.name=?", (INTERACTIVE,)
                ).fetchone()
                active = None
                if row is not None:
                    active = {
                        "key": asdict(AttemptKey(row["request_id"], row["id"], row["generation"])),
                        "revision": row["revision"],
                        "status": row["status"],
                        "details": _read(row["details"]),
                        "prefix": self._prefix(db, row["id"]),
                    }
                results = [
                    {
                        "key": asdict(AttemptKey(item["request_id"], item["id"], item["generation"])),
                        "revision": item["revision"],
                        "origin": {"number": item["revision"], "digest": item["revision_digest"]},
                        "created": item["created"],
                        "trace": self._prefix(db, item["id"]),
                    }
                    for item in db.execute(
                        "SELECT a.*,r.created,v.digest AS revision_digest FROM results r "
                        "JOIN attempts a ON a.id=r.attempt JOIN revisions v ON v.number=a.revision "
                        "WHERE a.slot=? ORDER BY a.generation DESC LIMIT 20",
                        (INTERACTIVE,),
                    ).fetchall()
                ]
                # Experiment replications and replays have their own lists; they never become the interactive view.
                latest = db.execute(
                    "SELECT * FROM attempts WHERE slot=? ORDER BY generation DESC LIMIT 1", (INTERACTIVE,)
                ).fetchone()
                latest_attempt = (
                    None
                    if latest is None
                    else {
                        "key": asdict(AttemptKey(latest["request_id"], latest["id"], latest["generation"])),
                        "revision": latest["revision"],
                        "status": latest["status"],
                        "details": _read(latest["details"]),
                    }
                )
                replay_list = replays(db, self._prefix)
            state = {
                "revision": None if revision is None else asdict(revision),
                "inputs": None
                if inputs is None
                else {"options": _plain(inputs.options), "issues": _plain(inputs.issues)},
                "startup": dict(self.session.status),
                "active": active,
                "results": results,
                "latest_attempt": latest_attempt,
                "progress": self.progress
                if active is not None
                and self.progress is not None
                and self.progress["attempt_id"] == active["key"]["attempt_id"]
                else None,
                "fatal": None if co._fatal is None else {"status": co._fatal.status, "details": co._fatal.details},
                "experiments": scheduler.runs(co.store, limit=20),
                "workers": self.experiments.workers(),
                "replays": replay_list,
            }
            view = co.store.presentation_view(revision=revision) if revision is not None else None
            last_good = co.store.presentation_view(committed_only=True)
            selected = None if peer is None else peer.selected_view
            selected_view = (
                None if selected is None else co.store.presentation_view(revision=selected, committed_only=True)
            )
        # Outside the locks: listing reads Git membership. Watcher errors are reported on their own.
        try:
            members = non_source_members(co.project)
        except (OSError, ValueError):
            members = ()
        state["experiment_files"] = [name for name in members if name.endswith(".experiment.json")]
        state["model"] = co.project.model
        state["metadata"] = None if metadata is None else self.references.put(metadata)
        state["view"] = self._view(view)
        state["last_good"] = self._view(last_good)
        if selected is not None:
            state["selected_view"] = self._view(selected_view)
        return state

    async def state(self, peer):
        return self._body(await asyncio.to_thread(self.snapshot, peer))

    def _check_revision(self, raw):
        revision = None if raw is None else Revision(**raw)
        if revision is None or self.coordinator.store.current_revision() != revision:
            raise RevisionConflict("Project revision changed; reload before editing")
        return revision

    def _command(self, message, peer):
        kind, body = message["type"], message["body"]
        co = self.coordinator
        if kind == "state.get":
            return {"ok": True, "state": self.snapshot(peer)}
        if kind == "layout.get":
            with co._condition:
                if co.inputs.state is None:
                    revision = co.store.current_revision()
                    if revision is None or self._unbound_layout is None or self._unbound_layout[0] != revision:
                        self._unbound_layout = (revision, read_layout_file(co.project.root, co.project.layout))
                    captured = self._unbound_layout[1]
                else:
                    state, _, captured = co.inputs.captured_inputs()
                    revision = state.revision
            try:
                layout = {"valid": True, "layer": captured.layer().to_dict(), "sha256": captured.digest}
            except ValueError as error:
                layout = {
                    "valid": False,
                    "raw": (captured.raw or b"").decode("utf-8", errors="replace"),
                    "error": str(error)[:4096],
                }
            return {
                "ok": True,
                "layout": self.references.put(layout),
                "revision": None if revision is None else asdict(revision),
            }
        with co._condition:
            base = self._check_revision(message["revision"])
            if kind == "run.start":
                if body:
                    raise ValueError("run.start takes no execution overrides; use params.set")
                if self.session.status["phase"] == "error":
                    raise ValueError("Review and Apply execution inputs after a startup error")
                state = co.inputs.state
                if state is None or state.revision != base or state.request is None:
                    raise ValueError("Complete valid model inputs before running")
                return {"ok": True, "key": asdict(co.submit(state.request, base, timeout=self.timeout))}
            if kind == "run.cancel":
                if set(body) != {"request_id"}:
                    raise ValueError("Cancel requires the exact execution request and attempt")
                key = AttemptKey(body["request_id"], message.get("attempt_id"), message.get("generation"))
                if not co.cancel(key):
                    raise StaleAttempt("Attempt is no longer active")
                return {"ok": True, "key": asdict(key)}
            if kind == "view.select":
                target = Revision(**body["revision"])
                view = co.store.presentation_view(revision=target, committed_only=True)
                if view is None:
                    raise ValueError("Only an indexed committed view can be selected")
                peer.selected_view = target
                return {"ok": True, "view": self._view(view)}
        if kind == "params.set":
            reviewing = self.session.status["phase"] == "error"
            state = co.inputs.apply(base, body, auto_run=not reviewing, timeout=self.timeout)
            if reviewing:
                co.submit(state.request, state.revision, timeout=self.timeout)
                self.session._status("ready")
            return {"ok": True, "revision": asdict(state.revision)}
        if kind == "layout.validate":
            validation = co.inputs.preview_layout(base, body, timeout=self.timeout or 30)
            return {
                "ok": True,
                "revision": asdict(validation.revision),
                "requires_rerun": validation.requires_rerun,
                "layout": None if validation.bundle is None else self.references.put(validation.bundle.to_dict()),
                "changed_paths": validation.changed_paths,
            }
        if kind == "layout.write" and co.inputs.state is None:
            with co._condition:
                self._check_revision(message["revision"])
                if self._unbound_layout is None or self._unbound_layout[0] != base:
                    raise RevisionConflict("Read the failed layout before repairing it")
                captured = self._unbound_layout[1]
                try:
                    captured.layer()
                except ValueError:
                    pass
                else:
                    raise ValueError("Wait for model metadata before editing a valid layout")
                with stage_layout(co.project.root, co.project.layout, FileLayer(body)) as staged:
                    backup_layout(co.store, captured)
                    staged.replace(expected=captured)
                    revision = co.store.create_revision(
                        {"layout_repair": staged.replacement.digest}, expected=base, revoke_active=True
                    )
                self._unbound_layout = None
            return {"ok": True, "revision": asdict(revision)}
        if kind == "layout.write":
            state = co.inputs.write_layout(base, body, timeout=self.timeout)
            return {"ok": True, "revision": asdict(state.revision)}
        raise ValueError("Unsupported Studio command")

    async def command(self, message, peer):
        # Preview is read-only and independently bounded. Do not block run.cancel
        # or another tab's revision change while a model builds its preview.
        # Experiment commands fence on run and store state, not on the project revision.
        unlocked = message["type"] == "layout.validate" or message["type"] in EXPERIMENT_COMMANDS
        async with nullcontext() if unlocked else self.commands:
            try:
                if message["type"] in EXPERIMENT_COMMANDS:
                    return self._body(await asyncio.to_thread(self.experiments, message["type"], message["body"]))
                if message["type"] == "project.rescan":
                    body = message["body"]
                    if set(body) - {"retry"} or type(body.get("retry", False)) is not bool:
                        raise ValueError("Rescan accepts only a retry boolean")
                    base = self._check_revision(message["revision"])
                    await self.session.rescan(retry=body.get("retry", False), expected=base)
                    return {"ok": True}
                outcome = await asyncio.to_thread(self._command, message, peer)
                if message["type"] == "layout.write" and self.coordinator.inputs.state is None:
                    await self.session.rescan(retry=True)
                return self._body(outcome)
            except (ValueError, KeyError, TypeError, OSError, RuntimeError, sqlite3.Error) as error:
                code = (
                    "revision_conflict"
                    if isinstance(error, RevisionConflict)
                    else "stale_attempt"
                    if isinstance(error, StaleAttempt)
                    else "validation_failed"
                    if isinstance(error, (ValueError, KeyError, TypeError))
                    else "failed"
                )
                revision = self.coordinator.store.current_revision()
                return {
                    "ok": False,
                    "error": {"code": code, "message": str(error)[:4096]},
                    "revision": None if revision is None else asdict(revision),
                }

    async def _pump(self):
        co = self.coordinator
        while True:
            events = co.events()
            changed = False
            slots_changed = False
            for event in events:
                kind, body, key = event["type"], event["body"], event["key"]
                slot = INTERACTIVE
                if key is None:
                    current = await asyncio.to_thread(co.store.current_revision)
                    revision = None if current is None else asdict(current)
                else:
                    origin = await asyncio.to_thread(self._attempt_origin, key)
                    if origin is None:
                        continue
                    revision, slot = origin
                if kind in ("experiment_state", "experiment_progress"):
                    slots_changed = slots_changed or kind == "experiment_progress"
                    state = await asyncio.to_thread(self._run_state, body)
                    if state is not None:
                        changed = True
                        self.hub.notify("experiment_state", state, revision=revision)
                    if kind == "experiment_progress":
                        self.hub.notify(kind, self._body(body), revision=revision)
                    continue
                if kind == "replay_state":
                    changed = slots_changed = True
                    self.hub.notify(kind, body, revision=revision, key=key)
                    continue
                if slot != INTERACTIVE:
                    # Replications and replays keep their live trace and KPI events for inspection;
                    # their state and progress reach clients as experiment and replay notifications.
                    if kind == "trace_available":
                        body = {
                            **body,
                            "attempt": key["attempt_id"],
                            "url": f"/api/prefix/{key['attempt_id']}/{body['generation']}",
                            "slot": slot,
                        }
                    elif kind in ("kpi", "diagnostics", "cleanup_error"):
                        body = {**body, "slot": slot}
                        if kind != "kpi":
                            body, kind = {"kind": kind, **body}, "error"
                    else:
                        slots_changed = slots_changed or kind in _RUN_STATES
                        continue
                    self.hub.notify(kind, self._body(body), revision=revision, key=key)
                    continue
                if kind == "trace_available":
                    body = {
                        **body,
                        "attempt": key["attempt_id"],
                        "url": f"/api/prefix/{key['attempt_id']}/{body['generation']}",
                    }
                elif kind == "heartbeat":
                    kind = "progress"
                    self.progress = {"attempt_id": key["attempt_id"], "body": body}
                elif kind == "model_info":
                    metadata = await asyncio.to_thread(read_document, body["document"], "model-info")
                    body = {"metadata": self.references.put(metadata)}
                elif kind in _RUN_STATES:
                    body = {"status": "succeeded" if kind == "result" else kind, "details": body}
                    kind = "run_state"
                elif kind == "resync" or kind == "studio_state":
                    changed = True
                    continue
                elif kind in ("diagnostics", "cleanup_error"):
                    body = {"kind": kind, **body}
                    kind = "error"
                self.hub.notify(kind, self._body(body), revision=revision, key=key)
                changed = changed or kind not in ("progress", "kpi", "trace_available")
            if slots_changed and self.hub.connections:
                workers = await asyncio.to_thread(self.experiments.workers)
                self.hub.notify("workers_state", self._body(workers))
            if changed and self.hub.connections:
                await self._broadcast_state()
            await asyncio.sleep(0.05)

    def _run_state(self, event):
        """The ``experiment_state`` of a command or progress event whose run status was not announced yet."""
        run_id, status = event["run_id"], event["status"]
        if self._run_states.get(run_id) == status:
            return None
        if status == "deleted":
            self._run_states[run_id] = status
            return event
        try:
            summary = run_summary(self.coordinator.store, run_id)
        except KeyError:
            return None  # Deleted meanwhile; the delete announces it.
        if summary["status"] != status:
            return None  # A newer event of this run carries the newer status.
        self._run_states[run_id] = status
        return summary

    def _attempt_origin(self, key):
        """The attempt's original input revision and its slot, or ``None`` for an unknown attempt."""
        with self.coordinator.store._transaction() as db:
            row = db.execute(
                "SELECT r.number,r.digest,a.slot FROM revisions r JOIN attempts a ON a.revision=r.number "
                "WHERE a.id=? AND a.request_id=? AND a.generation=?",
                (key["attempt_id"], key["request_id"], key["generation"]),
            ).fetchone()
        return None if row is None else ({"number": row["number"], "digest": row["digest"]}, row["slot"])

    async def _broadcast_state(self):
        # A pinned selection is per peer; the shared snapshot serves everyone else.
        shared = None
        for peer in tuple(self.hub.connections):
            if peer.selected_view is None:
                shared = await self.state(None) if shared is None else shared
                self.hub.send(peer, "state", shared)
            else:
                self.hub.send(peer, "state", await self.state(peer))

    def _range_source(self, attempt, generation):
        if re.fullmatch(r"[a-f0-9]{32}", attempt) is None or not generation.isdecimal():
            raise KeyError("Unknown trace")
        generation = int(generation)
        store = self.coordinator.store
        with store._transaction() as db:
            row = db.execute("SELECT request_id,generation FROM attempts WHERE id=?", (attempt,)).fetchone()
            if row is None:
                raise KeyError("Unknown attempt")
            key = AttemptKey(row[0], attempt, row[1])
            committed = db.execute("SELECT 1 FROM results WHERE attempt=?", (attempt,)).fetchone() is not None
            if not committed:
                store._require_active(db, key)
            receipt = db.execute(
                "SELECT offset+length,prefix_hash FROM receipts WHERE attempt=? AND sequence=? AND stream='trace'",
                (attempt, generation),
            ).fetchone()
            if receipt is None:
                raise KeyError("Unknown prefix")
            length, digest = receipt
            if committed:
                full = self._prefix(db, attempt)
                files = [
                    ImmutableFile(store.root / "results" / attempt / "run.simtrace", full["length"], full["sha256"])
                ]
            else:
                rows = db.execute(
                    "SELECT id,offset,length,sha256 FROM receipts WHERE attempt=? AND stream='trace' "
                    "AND sequence<=? ORDER BY offset",
                    (attempt, generation),
                ).fetchall()
                files, offset = [], 0
                for item in rows:
                    if item["offset"] != offset:
                        raise ValueError("Trace receipt gap")
                    files.append(
                        ImmutableFile(
                            store.root
                            / "staging"
                            / attempt
                            / "segments"
                            / (hashlib.sha256(item["id"].encode()).hexdigest() + ".segment"),
                            item["length"],
                            item["sha256"],
                        )
                    )
                    offset += item["length"]
                if offset != length:
                    raise ValueError("Trace receipt length mismatch")

        def authorize():
            with store._transaction() as db:
                if db.execute("SELECT 1 FROM results WHERE attempt=?", (attempt,)).fetchone() is None:
                    store._require_active(db, key)

        return RangeSource(files, length, authorize=authorize), f'"{attempt}:{generation}:{length}:{digest}"'

    async def request(self, request):
        headers = response_headers()

        def response(value, status=200):
            return Response(
                json.dumps(json_values(value), allow_nan=False),
                status_code=status,
                media_type="application/json",
                headers=headers,
            )

        if not self.headers_valid(request, require_origin=request.method == "PUT"):
            return response({"error": "Invalid host or origin"}, 403)
        path = request.path_params["path"]
        if path == "bootstrap" and request.method != "PUT":
            return self.bootstrap(request)
        results = path.startswith("api/experiments/") and path.endswith("/results")
        if request.url.query and not results:
            return response({"error": "Unknown resource"}, 404)
        if path.startswith("api/"):
            if not self.authenticated(request):
                return response({"error": "Open a fresh terminal link"}, 403)
            try:
                if path == "api/state" and request.method in ("GET", "HEAD"):
                    return response(await asyncio.to_thread(self.snapshot))
                if results and request.method in ("GET", "HEAD"):
                    run_id = path.removeprefix("api/experiments/").removesuffix("/results")
                    if re.fullmatch(r"[a-f0-9]{64}", run_id) is None:
                        return response({"error": "Unknown experiment run"}, 404)
                    options = parse_results_query(request.query_params)
                    try:
                        # Bounded: the report caps its listed missing replications.
                        document = await asyncio.to_thread(report, self.coordinator.store, run_id, **options)
                    except KeyError:
                        return response({"error": "Unknown experiment run"}, 404)
                    return response(document)
                if path.startswith("api/references/") and request.method in ("GET", "HEAD"):
                    identity = path.removeprefix("api/references/")
                    data = self.references.get(identity)
                    headers.update({"ETag": f'"{identity}"', "Content-Length": str(len(data))})
                    if request.method == "HEAD":
                        return Response(media_type="application/json", headers=headers)
                    return StreamingResponse(
                        (data[offset : offset + (1 << 20)] for offset in range(0, len(data), 1 << 20)),
                        media_type="application/json",
                        headers=headers,
                    )
                if path.startswith("api/prefix/") and request.method in ("GET", "HEAD"):
                    pieces = path.split("/")
                    if len(pieces) != 4:
                        raise KeyError("Unknown prefix")
                    source, etag = await asyncio.to_thread(self._range_source, *pieces[2:])
                    if request.headers.get("if-match", etag) != etag:
                        return response({"error": "Trace identity changed"}, 412)
                    try:
                        start, end, status = parse_range(request.headers.get("range"), source.length)
                    except ValueError:
                        headers["Content-Range"] = f"bytes */{source.length}"
                        return response({"error": "Invalid range"}, 416)
                    await asyncio.to_thread(source.validate, start, end)
                    headers.update(
                        {
                            "ETag": etag,
                            "Content-Length": str(end - start + 1),
                            "Accept-Ranges": "bytes",
                            "Content-Type": "application/octet-stream",
                        }
                    )
                    if status == 206:
                        headers["Content-Range"] = f"bytes {start}-{end}/{source.length}"
                    if request.method == "HEAD":
                        return Response(status_code=status, headers=headers)
                    return StreamingResponse(source.chunks(start, end), status_code=status, headers=headers)
                if path in ("api/layout", "api/layout/validate") and request.method == "PUT":
                    for name in ("x-simulatte-command", "x-simulatte-revision"):
                        if len(request.headers.getlist(name)) != 1:
                            raise ValueError("Layout PUT requires unique command and revision headers")
                    raw = bytearray()
                    async for chunk in request.stream():
                        if len(raw) + len(chunk) > LayoutLimits().max_file_bytes:
                            return response({"error": "Layout byte limit exceeded"}, 413)
                        raw.extend(chunk)
                    layer = parse_layout(bytes(raw)).to_dict()
                    message = {
                        "v": 1,
                        "type": "layout.validate" if path.endswith("/validate") else "layout.write",
                        "id": request.headers.get("x-simulatte-command", ""),
                        "revision": json.loads(request.headers.get("x-simulatte-revision", "null")),
                        "body": layer,
                    }
                    if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", message["id"]) is None:
                        raise ValueError("Layout PUT requires a command id")
                    if message["type"] == "layout.validate":
                        return response(await self.command(message, None))
                    # HTTP retries use the same content-bound identity as websocket mutations.
                    async with self.commands:
                        result = self.http_commands.get(message)
                        if result is None:
                            result = await asyncio.to_thread(self._command, message, None)
                            self.http_commands.put(message, result)
                    return response(result)
            except StaleAttempt:
                return response({"error": "Provisional trace was invalidated"}, 410)
            except RevisionConflict:
                current = self.coordinator.store.current_revision()
                return response(
                    {"error": "revision_conflict", "revision": None if current is None else asdict(current)}, 409
                )
            except KeyError:
                return response({"error": "Reference unavailable; resynchronize state"}, 410)
            except (ValueError, TypeError, OSError) as error:
                return response({"error": str(error)[:4096]}, 400)
            return response({"error": "Unknown resource"}, 404)
        name = path or "index.html"
        if request.method == "PUT" or name not in self.allowed or ".." in Path(name).parts:
            return response({"error": "Unknown resource"}, 404)
        asset = self.static / name
        if not asset.resolve().is_relative_to(self.static.resolve()):
            return response({"error": "Unknown resource"}, 404)
        data = asset.read_bytes()
        headers["Content-Length"] = str(len(data))
        return Response(
            data if request.method == "GET" else b"",
            media_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
            headers=headers,
        )
