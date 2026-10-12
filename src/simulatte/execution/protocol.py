"""Worker protocol 1.0: bounded MessagePack control plus opaque fragments."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import struct
import time
from typing import Any

from simulatte._wire import FrozenMap, MAX_SAFE_INT, canonical_pack, freeze, unpack

MAGIC = b"SIMWORK\0"
MAX_FRAME = 2 << 20
MAX_ENVELOPE = 256 << 10
MAX_FRAGMENT = 1 << 20
_LENGTH = struct.Struct(">I")
_TYPES = frozenset(
    (
        "hello",
        "inspect",
        "model_info",
        "run",
        "ready",
        "ready_ack",
        "cancel",
        "cancelled",
        "heartbeat",
        "progress",
        "trace_chunk",
        "artifact_fragment",
        "ack",
        "kpi",
        "diagnostics",
        "result",
        "error",
        "shutdown",
        "bye",
    )
)
_FIELDS = frozenset(("v", "type", "seq", "request_id", "attempt_id", "generation", "body"))


@dataclass(frozen=True, slots=True)
class Message:
    envelope: FrozenMap
    payload: bytes = b""

    @property
    def type(self) -> str:
        return str(self.envelope["type"])

    @property
    def body(self) -> FrozenMap:
        body = self.envelope["body"]
        assert isinstance(body, FrozenMap)
        return body


def _validate(envelope: Mapping[str, Any], payload: bytes) -> FrozenMap:
    if set(envelope) != _FIELDS:
        raise ValueError("Invalid worker envelope fields")
    version = envelope["v"]
    if (
        not isinstance(version, (tuple, list))
        or len(version) != 2
        or any(type(x) is not int for x in version)
        or version[0] != 1
        or version[1] < 0
    ):
        raise ValueError("Incompatible worker protocol version")
    if type(envelope["type"]) is not str or envelope["type"] not in _TYPES:
        raise ValueError("Unknown worker message")
    seq = envelope["seq"]
    if type(seq) is not int or not 0 <= seq <= MAX_SAFE_INT:
        raise ValueError("Invalid worker sequence")
    identifiers = (envelope["request_id"], envelope["attempt_id"], envelope["generation"])
    if any(value is not None for value in identifiers):
        from simulatte.execution.schema import AttemptKey

        try:
            AttemptKey(*identifiers)
        except (TypeError, ValueError) as error:
            raise ValueError("Invalid attempt fence") from error
    body = envelope["body"]
    if not isinstance(body, Mapping):
        raise ValueError("Worker body must be a map")
    if len(payload) > MAX_FRAGMENT:
        raise ValueError("Worker fragment exceeds limit")
    if payload and envelope["type"] not in ("artifact_fragment", "trace_chunk"):
        raise ValueError("Binary payload is forbidden for this message")
    if envelope["type"] in ("artifact_fragment", "trace_chunk"):
        if type(body.get("payload_bytes")) is not int or body["payload_bytes"] != len(payload):
            raise ValueError("Fragment length mismatch")
    result = freeze(dict(envelope))
    assert isinstance(result, FrozenMap)
    return result


def encode_message(envelope: Mapping[str, Any], payload: bytes = b"") -> bytes:
    value = _validate(envelope, payload)
    encoded = canonical_pack(value)
    if len(encoded) > MAX_ENVELOPE:
        raise ValueError("Worker envelope exceeds limit")
    # Decode validation applies the same nesting rules to both producers/consumers.
    unpack(encoded, max_depth=64, max_len=MAX_ENVELOPE)
    length = 4 + len(encoded) + len(payload)
    return _LENGTH.pack(length) + _LENGTH.pack(len(encoded)) + encoded + payload


class FrameCodec:
    """Incremental parser; corruption after magic is fatal, never resynchronized."""

    def __init__(self, *, expect_magic: bool = True, clock: Callable[[], float] = time.monotonic):
        self._buffer = bytearray()
        self._magic = not expect_magic
        self._hello = expect_magic
        self._clock = clock
        self._started = clock()
        self._sequence = -1
        self.noise = b""
        self._failed = False

    def feed(self, data: bytes) -> list[Message]:
        if self._failed:
            raise ValueError("Worker protocol parser already failed")
        try:
            return self._feed(data)
        except (ValueError, TypeError, OverflowError, RecursionError) as error:
            self._failed = True
            raise ValueError(str(error)) from error

    def _feed(self, data: bytes) -> list[Message]:
        if self._hello and self._clock() - self._started > 10:
            raise ValueError("Worker handshake timeout")
        if len(data) > MAX_FRAME + 4 or len(self._buffer) + len(data) > 2 * MAX_FRAME + 8:
            raise ValueError("Worker input buffer exceeds limit")
        self._buffer.extend(data)
        if not self._magic:
            offset = self._buffer.find(MAGIC)
            if offset < 0:
                count = max(0, len(self._buffer) - len(MAGIC) + 1)
                if len(self.noise) + count > 65536:
                    raise ValueError("Worker bootstrap noise exceeds limit")
                self.noise += bytes(self._buffer[:count])
                del self._buffer[:count]
                return []
            if len(self.noise) + offset > 65536:
                raise ValueError("Worker bootstrap noise exceeds limit")
            self.noise += bytes(self._buffer[:offset])
            del self._buffer[: offset + len(MAGIC)]
            self._magic = True
        messages = []
        while len(self._buffer) >= 4:
            length = _LENGTH.unpack_from(self._buffer)[0]
            if not 5 <= length <= MAX_FRAME:
                raise ValueError("Invalid worker frame length")
            if len(self._buffer) < length + 4:
                break
            envelope_length = _LENGTH.unpack_from(self._buffer, 4)[0]
            if not 1 <= envelope_length <= MAX_ENVELOPE or envelope_length + 4 > length:
                raise ValueError("Invalid worker envelope length")
            raw = bytes(self._buffer[8 : 8 + envelope_length])
            envelope = unpack(raw, max_depth=64, max_len=MAX_ENVELOPE)
            if not isinstance(envelope, Mapping):
                raise ValueError("Worker envelope must be a map")
            payload = bytes(self._buffer[8 + envelope_length : 4 + length])
            value = _validate(envelope, payload)
            if self._hello and value["type"] != "hello":
                raise ValueError("First worker message must be hello")
            seq = envelope["seq"]
            if seq <= self._sequence:
                raise ValueError("Non-increasing worker sequence")
            self._sequence = seq
            self._hello = False
            messages.append(Message(value, payload))
            del self._buffer[: 4 + length]
        return messages

    def finish(self) -> None:
        if self._buffer or not self._magic or self._hello or self._failed:
            raise ValueError("Truncated or failed worker stream")
