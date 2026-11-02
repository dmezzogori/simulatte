import hashlib
import struct

import pytest

from prototypes.native_engine.model import Job
from prototypes.native_engine_dynamic.compiled import CompiledStream
from prototypes.native_engine_dynamic.native_kpis import NativeKPIStream
from prototypes.native_engine_dynamic.state_codec import decode

NONE = 2**32 - 1


def test_empty_state_matches_hand_encoded_bytes():
    stream = CompiledStream(2, wip_cap=3, policy="fifo")
    try:
        expected = struct.pack("<8s7Id7Qd", b"NSTA0001", 2, 3, 0, 0, 0, 0, 0, 0.0, 0, 0, 0, 0, 0, 0, 0, 0.0)
        expected += struct.pack("<7I", NONE, NONE, 0, NONE, NONE, 0, 0)
        assert stream.state_bytes() == expected
        assert decode(expected)["offsets"] == [0]
    finally:
        stream.close()


def state_at_one(*, due=4, duration=1, warmup=0):
    stream = NativeKPIStream(1, wip_cap=1, policy="fifo", warmup=warmup)
    try:
        stream.add(Job(0, (0,), (2,), 1))
        stream.add(Job(0, (0,), (duration,), due))
        stream.advance(1)
        before = stream.snapshot()
        raw = stream.state_bytes()
        assert stream.state_bytes() == raw and stream.snapshot() == before
        stream.run()
        return raw, before, stream.result()
    finally:
        stream.close()


def test_pending_state_matches_hand_derived_calendar_and_pool():
    raw, _, _ = state_at_one()
    state = decode(raw)
    assert state["now"] == 1 and state["floor"] == 1 and state["sequence"] == 5
    assert state["events"] == 4 and state["dispatched"] == 1 and state["completed"] == 0
    assert state["resources"] == [(NONE, NONE, 1)]
    assert state["jobs"] == [(0, 1, 0, 0, 2), (0, 4, 0, 0, 1)]
    assert state["offsets"] == [0, 1, 2] and state["dispatch"] == [0]
    assert state["operations"] == [(0, 0, NONE, 2, 0, 0, 0), (1, 0, NONE, 1, 0, 0, 0)]
    assert state["pool"] == [1] and state["calendar"] == [(2, 4, 2, 0)]


@pytest.mark.parametrize("change", [{"due": 100}, {"duration": 3}])
def test_unfinished_input_changes_hash_without_changing_current_kpis(change):
    original, kpis, _ = state_at_one()
    changed, same, _ = state_at_one(**change)
    assert kpis == same
    assert hashlib.sha256(original).digest() != hashlib.sha256(changed).digest()


def test_warmup_and_legacy_observation_modes_do_not_change_model_state(tmp_path):
    expected = None
    for mode in ("none", "kpi", "record", "studio", "stock-zero", "stock-later"):
        options = dict(wip_cap=2, policy="spt")
        if mode.startswith("stock"):
            stream = NativeKPIStream(3, warmup=0 if mode.endswith("zero") else 2, **options)
        else:
            stream = CompiledStream(
                3, mode=mode, trace_path=tmp_path / mode if mode in ("record", "studio") else None, **options
            )
        try:
            for i in range(10):
                stream.add(Job(i / 4, (i % 3, (i + 1) % 3), (0.5, 0.25), i % 7))
            snapshots = []
            for t in (0, 0.5, 1, 2, 4, 10):
                stream.advance(t)
                snapshots.append(stream.state_bytes())
            stream.run()
            snapshots.append(stream.state_bytes())
            assert expected is None or snapshots == expected
            expected = snapshots
            for raw in snapshots:
                state = decode(raw)
                assert state["pool"] == sorted(state["pool"])
                assert state["calendar"] == sorted(
                    state["calendar"], key=lambda r: (r[0], 0 if r[2] in (0, 5) else 1, r[1])
                )
        finally:
            stream.close()


def test_hash_queries_do_not_change_trajectory_and_lifecycle():
    results = []
    for query in (False, True):
        stream = CompiledStream(2, wip_cap=2, policy="spt")
        for i in range(50):
            stream.advance(i / 8)
            stream.add(Job(i / 8, (i % 2, (i + 1) % 2), (0.5, 0.25), i))
            if query:
                raw = stream.state_bytes()
                assert raw == stream.state_bytes()
        stream.run()
        assert decode(stream.state_bytes())["lifecycle"] == 2
        results.append(stream.result())
        stream.close()
        assert decode(raw)["lifecycle"] == 0 if query else True
        with pytest.raises(RuntimeError):
            stream.state_bytes()
    assert results[0] == results[1]


def test_zero_normalization_and_decoder_rejects_truncation():
    states = []
    for zero in (-0.0, 0.0):
        stream = CompiledStream(1)
        try:
            stream.add(Job(zero, (0,), (1,), zero))
            states.append(stream.state_bytes())
        finally:
            stream.close()
    assert states[0] == states[1]
    for invalid in (b"", states[0][:-1], b"badmagic" + states[0][8:], states[0] + b"extra"):
        with pytest.raises(ValueError):
            decode(invalid)
