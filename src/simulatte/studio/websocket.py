"""Bounded JSON control messages and per-connection state; no execution ownership."""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from collections.abc import Mapping
import hashlib
import json
import math
import re

MAX_MESSAGE = 256 << 10
MAX_MESSAGES = 256
MAX_QUEUE_BYTES = 4 << 20
# Experiment commands fence on run ids and store state, not on the project revision (design 7):
# their envelope may omit ``revision``, and a present one is validated but never compared.
EXPERIMENT_COMMANDS = frozenset(
    (
        "experiment.define",
        "experiment.start",
        "experiment.cancel",
        "experiment.resume",
        "experiment.delete",
        "comparison.get",
        "series.get",
        "replay.start",
        "workers.get",
    )
)
COMMANDS = frozenset(
    (
        "state.get",
        "project.rescan",
        "params.set",
        "run.start",
        "run.cancel",
        "layout.get",
        "layout.validate",
        "layout.write",
        "view.select",
        *EXPERIMENT_COMMANDS,
    )
)


class ProtocolError(ValueError):
    def __init__(self, message, code=1002):
        self.code = code
        super().__init__(message)


class SlowClient(RuntimeError):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        key.encode("utf-8")
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Nonfinite control number")


def _check_tree(value):
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 64:
            raise ProtocolError("Control nesting limit exceeded")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            item.encode("utf-8")
        elif type(item) is int and abs(item) > (1 << 53) - 1:
            raise ProtocolError("Control integer exceeds safe range")
        elif type(item) is float and not math.isfinite(item):
            raise ProtocolError("Nonfinite control number")


def decode_message(raw, *, auth=False):
    if type(raw) is not str:
        raise ProtocolError("Only text control frames are supported")
    try:
        size = len(raw.encode("utf-8"))
        if size > MAX_MESSAGE:
            raise ProtocolError("Control frame too large", 1009)
        message = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
        _check_tree(message)
    except (ValueError, RecursionError, UnicodeError) as error:
        if isinstance(error, ProtocolError):
            raise
        raise ProtocolError("Invalid control JSON") from error
    if not isinstance(message, dict) or type(message.get("v")) is not int or message["v"] != 1:
        raise ProtocolError("Unsupported control version")
    if auth:
        if (
            set(message) != {"v", "type", "secret"}
            or message["type"] != "auth"
            or type(message["secret"]) is not str
            or len(message["secret"]) > 4096
        ):
            raise ProtocolError("Expected authentication message")
        return message
    fenced = type(message.get("type")) is not str or message["type"] not in EXPERIMENT_COMMANDS
    required = {"v", "type", "id", "body"} | ({"revision"} if fenced else set())
    if (
        not required <= message.keys()
        or message.keys() - {"v", "type", "id", "revision", "body", "attempt_id", "generation"}
        or type(message["type"]) is not str
        or message["type"] not in COMMANDS
        or type(message["id"]) is not str
        or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", message["id"]) is None
        or not isinstance(message["body"], dict)
    ):
        raise ProtocolError("Invalid command envelope")
    revision = message.get("revision")
    if revision is not None and (
        not isinstance(revision, dict)
        or set(revision) != {"number", "digest"}
        or type(revision["number"]) is not int
        or revision["number"] < 1
        or type(revision["digest"]) is not str
        or re.fullmatch(r"[a-f0-9]{64}", revision["digest"]) is None
    ):
        raise ProtocolError("Invalid input revision")
    if "attempt_id" in message and (
        type(message["attempt_id"]) is not str or re.fullmatch(r"[a-f0-9]{32}", message["attempt_id"]) is None
    ):
        raise ProtocolError("Invalid attempt id")
    if "generation" in message and (type(message["generation"]) is not int or message["generation"] < 1):
        raise ProtocolError("Invalid attempt generation")
    return message


def json_values(value):
    """Numeric fields use tagged float specials; arbitrary maps are never decoded as tags."""
    if isinstance(value, Mapping):
        return {key: json_values(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_values(item) for item in value]
    if type(value) is float and not math.isfinite(value):
        return {"$float": "nan" if math.isnan(value) else "+inf" if value > 0 else "-inf"}
    return value


def encode_message(message):
    raw = json.dumps(json_values(message), ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    if len(raw) > MAX_MESSAGE:
        raise ProtocolError("Control frame too large; use an HTTP reference", 1009)
    return raw


class Outbox:
    def __init__(self):
        self._items = deque()
        self._bytes = 0
        self._wake = asyncio.Event()
        self._closed = False

    @property
    def count(self):
        return len(self._items)

    @property
    def bytes(self):
        return self._bytes

    def put(self, message, *, key=None, first=False):
        if self._closed:
            raise SlowClient("Connection closed")
        raw = encode_message(message)
        if key is not None:
            for old in self._items:
                if old[0] == key:
                    self._items.remove(old)
                    self._bytes -= len(old[1])
                    break
        if len(self._items) >= MAX_MESSAGES or self._bytes + len(raw) > MAX_QUEUE_BYTES:
            raise SlowClient("Slow client must resynchronize")
        (self._items.appendleft if first else self._items.append)((key, raw))
        self._bytes += len(raw)
        self._wake.set()

    async def get(self):
        while not self._closed:
            if self._items:
                _, raw = self._items.popleft()
                self._bytes -= len(raw)
                return raw
            self._wake.clear()
            await self._wake.wait()
        raise SlowClient("Connection closed")

    def close(self):
        self._closed = True
        self._items.clear()
        self._bytes = 0
        self._wake.set()


class CommandCache:
    """Bounded connection-local outcomes; reconnect must reconcile before retrying."""

    def __init__(self):
        self._outcomes = OrderedDict()

    def __len__(self):
        return len(self._outcomes)

    @staticmethod
    def _identity(request):
        return hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).digest()

    def get(self, request):
        outcome = self._outcomes.get(request["id"])
        if outcome is None:
            return None
        identity, raw = outcome
        if identity != self._identity(request):
            raise ProtocolError("Command id reused for different content")
        return json.loads(raw)

    def put(self, request, response):
        raw = encode_message(response)
        self._outcomes.pop(request["id"], None)
        self._outcomes[request["id"]] = (self._identity(request), raw)
        while len(self._outcomes) > 256:
            self._outcomes.popitem(last=False)


class Peer:
    def __init__(self, socket):
        self.socket = socket
        self.outbox = Outbox()
        self.cache = CommandCache()
        self.close_code = 1000
        self.selected_view = None
        self.stopped = asyncio.Event()

    def close(self, code=None):
        if code is not None:
            self.close_code = code
        self.outbox.close()
        self.stopped.set()


class ConnectionHub:
    """Single-event-loop transport. Browser disconnects never cancel execution."""

    auth_timeout = 5.0

    def __init__(self, auth, dispatch, state):
        self.auth, self.dispatch, self.state = auth, dispatch, state
        self.connections = set()
        self.pending = 0
        self.event_seq = 0

    def envelope(self, kind, body, **fields):
        self.event_seq += 1
        return {"v": 1, "type": kind, "event_seq": self.event_seq, "body": body, **fields}

    def notify(self, kind, body, *, revision=None, key=None, run_id=None):
        message = self.envelope(
            kind,
            body,
            revision=revision,
            **({} if key is None else {"attempt_id": key["attempt_id"], "generation": key.get("generation")}),
        )
        coalesce = None
        if kind in ("progress", "kpi", "trace_available"):
            coalesce = (kind, None if key is None else key["attempt_id"], body.get("key"))
        elif kind == "experiment_progress":
            # A large progress body is an HTTP reference; keep its run identity outside it.
            coalesce = (kind, body["run_id"] if run_id is None else run_id)
        elif kind == "workers_state":
            coalesce = (kind,)
        for peer in tuple(self.connections):
            try:
                peer.outbox.put(message, key=coalesce)
            except (SlowClient, ProtocolError):
                peer.close(1013)

    def send(self, peer, kind, body, *, revision=None):
        try:
            peer.outbox.put(self.envelope(kind, body, revision=revision))
        except (SlowClient, ProtocolError):
            peer.close(1013)

    @staticmethod
    async def _message(socket):
        from starlette.websockets import WebSocketDisconnect

        message = await socket.receive()
        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(message.get("code", 1000))
        if "text" not in message:
            raise ProtocolError("Only text control frames are supported")
        return message["text"]

    async def _receive(self, peer):
        while True:
            request = decode_message(await self._message(peer.socket))
            response = peer.cache.get(request)
            if response is None:
                body = await self.dispatch(request, peer)
                response = {"v": 1, "type": "response", "id": request["id"], "body": body}
                peer.cache.put(request, response)
            peer.outbox.put(response)

    @staticmethod
    async def _send(peer):
        while True:
            await peer.socket.send_text(await peer.outbox.get())

    async def handle(self, socket):
        from contextlib import suppress

        from starlette.websockets import WebSocketDisconnect

        if (
            not self.auth.headers_valid(socket, require_origin=True)
            or not self.auth.cookie_valid(socket)
            or socket.url.query
            or self.pending >= 4
        ):
            await socket.close(code=1008)
            return
        self.pending += 1
        pending = True
        peer = None
        tasks = []
        code = 1000
        try:
            await socket.accept()
            try:
                raw = await asyncio.wait_for(self._message(socket), self.auth_timeout)
            except TimeoutError:
                code = 1008
                return
            message = decode_message(raw, auth=True)
            if not self.auth.authenticated(socket, secret=message["secret"]):
                code = 1008
                return
            if len(self.connections) >= 16:
                code = 1013
                return
            self.pending -= 1
            pending = False
            peer = Peer(socket)
            self.connections.add(peer)
            initial = self.envelope("state", None)
            initial["body"] = await self.state(peer)
            peer.outbox.put(initial, first=True)
            tasks = [
                asyncio.create_task(self._send(peer)),
                asyncio.create_task(self._receive(peer)),
                asyncio.create_task(peer.stopped.wait()),
            ]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except ProtocolError as error:
            code = error.code
        except SlowClient:
            code = 1013
        except WebSocketDisconnect:
            pass
        except Exception:
            code = 1011
        finally:
            if pending:
                self.pending -= 1
            if peer is not None:
                self.connections.discard(peer)
                peer.close()
                code = peer.close_code if peer.close_code != 1000 else code
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError, Exception):
                    await task
            with suppress(RuntimeError, WebSocketDisconnect):
                await socket.close(code=code)

    async def close(self):
        for peer in tuple(self.connections):
            peer.close(1001)
