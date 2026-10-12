"""Authenticated local Studio application; user Python runs only in execution children."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
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

from simulatte.execution.layout_files import read_layout_file
from simulatte.execution.references import read_document
from simulatte.execution.schema import AttemptKey, Revision, _plain
from simulatte.execution.store import RevisionConflict, StaleAttempt, _read
from simulatte.layout.io import parse_layout
from simulatte.layout.types import LayoutLimits
from simulatte.studio.auth import SessionAuth, response_headers
from simulatte.studio.http import ImmutableFile, RangeSource, References, parse_range
from simulatte.studio.lifecycle import ProjectSession
from simulatte.studio.websocket import CommandCache, ConnectionHub, ProtocolError, encode_message, json_values


class StudioServer(SessionAuth):
    def __init__(self, coordinator, port, static, *, timeout=None, configure=None, watcher=None):
        super().__init__(port, mode="studio")
        self.coordinator = coordinator
        self.static = Path(static)
        self.allowed = set(json.loads((self.static / "manifest.json").read_text())["assets"])
        self.timeout = timeout
        self.references = References()
        self.session = ProjectSession(coordinator, timeout=timeout, configure=configure, watcher=watcher)
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

    def _prefix(self, db, attempt):
        row = db.execute(
            "SELECT sequence,offset+length AS length,prefix_hash FROM receipts "
            "WHERE attempt=? AND stream='trace' ORDER BY sequence DESC LIMIT 1",
            (attempt,),
        ).fetchone()
        if row is None:
            return None
        return {
            "attempt": attempt,
            "generation": row["sequence"],
            "length": row["length"],
            "sha256": row["prefix_hash"],
            "url": f"/api/prefix/{attempt}/{row['sequence']}",
        }

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
                    "SELECT a.* FROM attempts a JOIN state s ON s.active=a.id WHERE s.singleton=1"
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
                        "created": item["created"],
                        "trace": self._prefix(db, item["id"]),
                    }
                    for item in db.execute(
                        "SELECT a.*,r.created FROM results r JOIN attempts a ON a.id=r.attempt "
                        "ORDER BY a.generation DESC LIMIT 20"
                    ).fetchall()
                ]
            state = {
                "revision": None if revision is None else asdict(revision),
                "inputs": None
                if inputs is None
                else {"options": _plain(inputs.options), "issues": _plain(inputs.issues)},
                "startup": dict(self.session.status),
                "active": active,
                "results": results,
                "fatal": None if co._fatal is None else {"status": co._fatal.status, "details": co._fatal.details},
            }
            view = co.store.presentation_view(revision=revision) if revision is not None else None
            last_good = co.store.presentation_view(committed_only=True)
            selected = None if peer is None else peer.selected_view
            selected_view = (
                None if selected is None else co.store.presentation_view(revision=selected, committed_only=True)
            )
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
                    captured = read_layout_file(co.project.root, co.project.layout)
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
            validation = co.inputs.validate_layout(base, body)
            return {
                "ok": True,
                "revision": asdict(validation.revision),
                "requires_rerun": validation.requires_rerun,
                "layout": None if validation.bundle is None else self.references.put(validation.bundle.to_dict()),
                "changed_paths": validation.changed_paths,
            }
        if kind == "layout.write":
            state = co.inputs.write_layout(base, body, timeout=self.timeout)
            return {"ok": True, "revision": asdict(state.revision)}
        raise ValueError("Unsupported Studio command")

    async def command(self, message, peer):
        async with self.commands:
            try:
                return self._body(await asyncio.to_thread(self._command, message, peer))
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
            for event in events:
                kind, body, key = event["type"], event["body"], event["key"]
                if key is None:
                    current = co.store.current_revision()
                    revision = None if current is None else asdict(current)
                else:
                    with co.store._transaction() as db:
                        row = db.execute(
                            "SELECT r.number,r.digest FROM revisions r JOIN attempts a ON a.revision=r.number "
                            "WHERE a.id=? AND a.request_id=? AND a.generation=?",
                            (key["attempt_id"], key["request_id"], key["generation"]),
                        ).fetchone()
                    if row is None:
                        continue
                    revision = dict(row)
                if kind == "trace_available":
                    body = {
                        **body,
                        "attempt": key["attempt_id"],
                        "url": f"/api/prefix/{key['attempt_id']}/{body['generation']}",
                    }
                elif kind == "heartbeat":
                    kind = "progress"
                elif kind == "model_info":
                    metadata = await asyncio.to_thread(read_document, body["document"], "model-info")
                    body = {"metadata": self.references.put(metadata)}
                elif kind in (
                    "queued",
                    "ready",
                    "result",
                    "cancelled",
                    "failed",
                    "lost",
                    "disk_full",
                    "validation_failed",
                ):
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
            if changed and self.hub.connections:
                self.hub.notify("state", await self.state(None))
            await asyncio.sleep(0.05)

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
        if request.url.query:
            return response({"error": "Unknown resource"}, 404)
        if path.startswith("api/"):
            if not self.authenticated(request):
                return response({"error": "Open a fresh terminal link"}, 403)
            try:
                if path == "api/state" and request.method in ("GET", "HEAD"):
                    return response(await asyncio.to_thread(self.snapshot))
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
                if path == "api/layout" and request.method == "PUT":
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
                        "type": "layout.write",
                        "id": request.headers.get("x-simulatte-command", ""),
                        "revision": json.loads(request.headers.get("x-simulatte-revision", "null")),
                        "body": layer,
                    }
                    if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", message["id"]) is None:
                        raise ValueError("Layout PUT requires a command id")
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
