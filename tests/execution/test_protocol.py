"""Framing cannot be confused by chunking, diagnostic noise or oversized claims."""

from __future__ import annotations

import importlib
import importlib.util
import struct

import pytest


def api():
    assert importlib.util.find_spec("simulatte.execution.protocol"), "worker framing missing"
    return importlib.import_module("simulatte.execution.protocol")


def envelope(type="hello", seq=0, **body):
    return {
        "v": [1, 0],
        "type": type,
        "seq": seq,
        "request_id": None,
        "attempt_id": None,
        "generation": None,
        "body": body,
    }


def test_every_fragment_boundary_and_concatenation():
    m = api()
    data = m.MAGIC + m.encode_message(envelope()) + m.encode_message(envelope("heartbeat", 1, time=1.0))
    for split in range(len(data) + 1):
        codec = m.FrameCodec()
        messages = codec.feed(data[:split]) + codec.feed(data[split:])
        codec.finish()
        assert [message.type for message in messages] == ["hello", "heartbeat"]
        assert messages[1].body["time"] == 1.0
    codec = m.FrameCodec()
    messages = []
    for byte in data:
        messages.extend(codec.feed(bytes([byte])))
    assert len(messages) == 2


def test_binary_payload_cannot_be_parsed_as_control():
    m = api()
    codec = m.FrameCodec(expect_magic=False)
    body = envelope("artifact_fragment", payload_bytes=8)
    message = codec.feed(m.encode_message(body, b"SIMWORK\0"))[0]
    assert message.payload == b"SIMWORK\0"
    assert message.type == "artifact_fragment"
    with pytest.raises(ValueError):
        m.encode_message(envelope(), b"noise")


def test_noise_and_handshake_deadline_are_bounded():
    m = api()
    clock = [0.0]
    codec = m.FrameCodec(clock=lambda: clock[0])
    assert codec.feed(b"shell noise\n") == []
    assert codec.feed(m.MAGIC + m.encode_message(envelope()))[0].type == "hello"
    assert codec.noise == b"shell noise\n"
    with pytest.raises(ValueError):
        m.FrameCodec().feed(b"x" * (65536 + 8))
    codec = m.FrameCodec(clock=lambda: clock[0])
    clock[0] = 11
    with pytest.raises(ValueError, match="timeout"):
        codec.feed(b"")


def test_oversize_truncation_unknown_version_and_sequence_fail():
    m = api()
    with pytest.raises(ValueError):
        m.FrameCodec().feed(m.MAGIC + struct.pack(">I", 2**32 - 1))
    with pytest.raises(ValueError):
        m.FrameCodec().feed(m.MAGIC + struct.pack(">I", 0))
    codec = m.FrameCodec()
    codec.feed(m.MAGIC + b"\0")
    with pytest.raises(ValueError):
        codec.finish()
    for bad in [
        envelope() | {"v": [2, 0]},
        envelope("unknown"),
        envelope() | {"seq": True},
        envelope() | {"request_id": "bad"},
        envelope() | {"extra": 1},
    ]:
        with pytest.raises(ValueError):
            m.encode_message(bad)
    codec = m.FrameCodec()
    codec.feed(m.MAGIC + m.encode_message(envelope()))
    with pytest.raises(ValueError):
        codec.feed(m.encode_message(envelope("heartbeat")))


def test_raw_bad_envelope_is_validated_before_dispatch():
    m = api()
    # MessagePack with duplicate map keys must not overwrite a trusted field.
    duplicate = b"\x82\xa4type\xa5hello\xa4type\xa3run"
    payload = struct.pack(">I", len(duplicate)) + duplicate
    with pytest.raises(ValueError):
        m.FrameCodec(expect_magic=False).feed(struct.pack(">I", len(payload)) + payload)
    with pytest.raises(ValueError):
        m.encode_message(envelope("diagnostics", text="x" * (256 << 10)))


def test_invalid_payloads_envelopes_and_parser_poisoning():
    m = api()
    from simulatte._wire import pack, freeze

    invalid = [envelope() | {"body": []}, envelope("trace_chunk", payload_bytes=1)]
    for value in invalid:
        with pytest.raises(ValueError):
            m.encode_message(value)
    with pytest.raises(ValueError, match="fragment"):
        m.encode_message(envelope("trace_chunk", payload_bytes=(1 << 20) + 1), b"x" * ((1 << 20) + 1))
    for raw in [pack(freeze([])), pack(freeze(envelope("ready")))]:
        data = struct.pack(">II", 4 + len(raw), len(raw)) + raw
        codec = m.FrameCodec()
        with pytest.raises(ValueError):
            codec.feed(m.MAGIC + data)
        with pytest.raises(ValueError, match="already failed"):
            codec.feed(b"")
    with pytest.raises(ValueError, match="envelope length"):
        m.FrameCodec(expect_magic=False).feed(struct.pack(">II", 5, 999) + b"x")
    with pytest.raises(ValueError, match="buffer"):
        m.FrameCodec().feed(b"x" * ((2 << 20) + 5))
    with pytest.raises(ValueError, match="noise"):
        m.FrameCodec().feed(b"x" * 65537 + m.MAGIC)
    deep = {}
    for _ in range(70):
        deep = {"nested": deep}
    with pytest.raises(ValueError):
        m.encode_message(envelope("progress", **deep))
