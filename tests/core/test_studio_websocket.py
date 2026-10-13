"""Control framing, bounded outbound queues and reconnect-safe command identities."""

import asyncio
import json

import pytest

from simulatte.studio.websocket import CommandCache, Outbox, ProtocolError, SlowClient, decode_message, encode_message


def command(**changes):
    return {"v": 1, "type": "state.get", "id": "c1", "revision": None, "body": {}, **changes}


def test_control_json_bounds_and_envelope():
    assert decode_message(json.dumps(command())) == command()
    for raw in [
        '{"v":1,"v":1}',
        json.dumps(command(v=True)),
        json.dumps(command(type="experiment.unknown")),
        json.dumps(command(type=["experiment.start"])),
        json.dumps({key: value for key, value in command(type="run.start").items() if key != "revision"}),
        json.dumps({key: value for key, value in command(type="state.get").items() if key != "revision"}),
        json.dumps(command(type="experiment.start", revision={"number": 0, "digest": "a" * 64})),
        json.dumps(command(id="")),
        json.dumps(command(extra=True)),
        json.dumps(command(body=[])),
        json.dumps(command(body={"x": float("nan")})),
        json.dumps(command(body={"x": 1 << 54})),
        "[1]",
        "null",
        "{",
        '["' + "x" * (256 << 10) + '"]',
        "[" * 65 + "0" + "]" * 65,
    ]:
        with pytest.raises(ProtocolError):
            decode_message(raw)
    # Experiment commands fence on run and store state: their revision may be omitted or stale.
    accepted = {key: value for key, value in command(type="experiment.start").items() if key != "revision"}
    assert decode_message(json.dumps(accepted)) == accepted
    stale = command(type="comparison.get", revision={"number": 3, "digest": "b" * 64})
    assert decode_message(json.dumps(stale)) == stale
    assert decode_message('{"v":1,"type":"auth","secret":"x"}', auth=True)["secret"] == "x"
    with pytest.raises(ProtocolError):
        decode_message(json.dumps(command()), auth=True)


def test_send_bounds_and_nonfinite_values():
    encoded = encode_message({"v": 1, "type": "kpi", "body": {"value": float("inf")}})
    assert json.loads(encoded)["body"]["value"] == {"$float": "+inf"}
    with pytest.raises(ProtocolError):
        encode_message({"body": "x" * (256 << 10)})


def test_outbox_coalesces_without_reordering_sequence_or_losing_terminal():
    async def run():
        box = Outbox()
        box.put({"type": "progress", "event_seq": 1}, key=("progress", "a"))
        box.put({"type": "run_state", "event_seq": 2})
        box.put({"type": "progress", "event_seq": 3}, key=("progress", "a"))
        assert json.loads(await box.get())["event_seq"] == 2
        assert json.loads(await box.get())["event_seq"] == 3
        for i in range(256):
            box.put({"type": "run_state", "event_seq": i})
        with pytest.raises(SlowClient):
            box.put({"type": "run_state", "event_seq": 257})
        assert box.count == 256
        box.close()
        with pytest.raises(SlowClient):
            await box.get()

    asyncio.run(run())


def test_outbox_byte_budget_and_waiting_reader():
    async def run():
        box = Outbox()
        reader = asyncio.create_task(box.get())
        await asyncio.sleep(0)
        box.put({"type": "state"})
        assert json.loads(await reader)["type"] == "state"
        for _ in range(16):
            box.put({"body": "x" * (250 << 10)})
        with pytest.raises(SlowClient):
            box.put({"body": "x" * (250 << 10)})
        assert box.bytes <= 4 << 20
        box.close()

    asyncio.run(run())


def test_command_cache_rejects_id_reuse_for_different_mutations_and_is_bounded():
    cache = CommandCache()
    request = command(type="run.start")
    assert cache.get(request) is None
    cache.put(request, {"type": "result", "id": "c1"})
    assert cache.get(request) == {"type": "result", "id": "c1"}
    with pytest.raises(ProtocolError):
        cache.get(command(type="params.set"))
    for i in range(256):
        request = command(id=f"c{i + 2}")
        cache.put(request, {"id": request["id"]})
    assert cache.get(command(type="run.start")) is None
    assert len(cache) == 256


@pytest.mark.parametrize(
    "changes",
    [
        {"revision": {"number": 0, "digest": "a" * 64}},
        {"attempt_id": "bad"},
        {"generation": 0},
        {"generation": True},
    ],
)
def test_invalid_revision_and_attempt_fences(changes):
    with pytest.raises(ProtocolError):
        decode_message(json.dumps(command(**changes)))


def test_malformed_unicode_overflow_numbers_and_closed_outbox():
    for raw in ('{"v":1,"type":"auth","secret":"\\ud800"}', '{"x":1e999}', '{"\\ud800":0}', "[[[" * 1000):
        with pytest.raises(ProtocolError):
            decode_message(raw)
    with pytest.raises(ProtocolError):
        decode_message(b"binary")
    box = Outbox()
    box.put({"value": 1}, key="one")
    box.put({"value": 2}, key="two")
    box.put({"value": 3}, key="two")
    box.close()
    with pytest.raises(SlowClient):
        box.put({})


def test_auth_rejects_unknown_mode():
    pytest.importorskip("starlette")
    from simulatte.studio.auth import SessionAuth

    with pytest.raises(ValueError):
        SessionAuth(8000, mode="unknown")


def test_finite_float_lexemes_preserve_values_above_integer_limit():
    message = decode_message(
        '{"v":1,"type":"params.set","id":"float","revision":null,"body":{"parameters":{"scale":1e+20}}}'
    )
    value = message["body"]["parameters"]["scale"]
    assert type(value) is float and value == 1e20


def test_hub_send_closes_slow_peer():
    from types import SimpleNamespace
    from simulatte.studio.websocket import ConnectionHub

    hub = ConnectionHub(None, None, None)
    closed = []

    def put(message, key=None):
        raise SlowClient()

    peer = SimpleNamespace(outbox=SimpleNamespace(put=put), close=closed.append)
    hub.send(peer, "state", {})
    assert closed == [1013]
