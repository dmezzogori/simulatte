"""End-to-end durable credit with a bounded sender window and cancellable waits."""

from dataclasses import asdict
import importlib
import importlib.util
import time

import pytest

from simulatte.execution.control import Cancelled
from simulatte.execution.protocol import MAX_FRAGMENT
from simulatte.execution.receipts import SegmentReceiver
from simulatte.execution.spool import Spool
from tests.execution.test_receipts import setup


def api():
    assert importlib.util.find_spec("simulatte.execution.transfer"), "bounded transfer sender missing"
    return importlib.import_module("simulatte.execution.transfer")


def segment(spool, data):
    reservation = spool.reserve(len(data))
    reservation.write(data)
    return spool.commit(reservation, stream="trace", offset=0)


def until(predicate):
    end = time.monotonic() + 3
    while not predicate() and time.monotonic() < end:
        time.sleep(0.005)
    assert predicate()


def test_fragment_credit_transfers_segment_larger_than_window(tmp_path):
    m = api()
    store, key = setup(tmp_path)
    spool = Spool(tmp_path / "spool")
    data = b"x" * (17 * MAX_FRAGMENT + 3)
    original = segment(spool, data)
    with store:
        receiver = SegmentReceiver(store, key)
        acknowledgements = []

        def send(kind, body, *, payload):
            assert kind == "trace_chunk"
            receipt = receiver.receive(body["segment"], body["fragment_offset"], payload)
            acknowledgements.append(asdict(receipt))
            sender.ack(asdict(receipt))

        sender = m.SegmentSender(spool, send, window=2 * MAX_FRAGMENT)
        sender.start()
        sender.finish()
        sender.wait(5)
        prefix = receiver.prefix()
        assert prefix is not None
        assert prefix.path.read_bytes() == data
        assert not original.path.exists()
        assert spool.stats()["bytes"] == 0
        assert sender.stats()["peak_in_flight"] <= 2 * MAX_FRAGMENT
        assert sender.ack(acknowledgements[0]) is False
        assert sender.ack(acknowledgements[-1]) is False
        wrong = acknowledgements[-1] | {"sha256": "0" * 64}
        with pytest.raises(ValueError):
            sender.ack(wrong)


def test_full_window_waits_for_credit_and_cancel_needs_no_ack(tmp_path):
    m = api()
    spool = Spool(tmp_path / "spool")
    original = segment(spool, b"x" * (3 * MAX_FRAGMENT))
    sent = []
    sender = m.SegmentSender(spool, lambda kind, body, **kw: sent.append(body), window=2 * MAX_FRAGMENT)
    sender.start()
    until(lambda: sender.stats()["in_flight"] == 2 * MAX_FRAGMENT)
    assert len(sent) == 2 and original.path.exists()
    spool.control.cancel()
    with pytest.raises(Cancelled):
        sender.wait(2)
    assert not sender.running
    assert spool.stats()["bytes"] == 3 * MAX_FRAGMENT


def test_wrong_or_out_of_order_ack_does_not_release_credit(tmp_path):
    m = api()
    spool = Spool(tmp_path / "spool")
    original = segment(spool, b"x" * (3 * MAX_FRAGMENT))
    sender = m.SegmentSender(spool, lambda *a, **k: None, window=2 * MAX_FRAGMENT)
    sender.start()
    until(lambda: sender.stats()["in_flight"] == 2 * MAX_FRAGMENT)
    ack = {
        "id": original.id,
        "offset": 0,
        "length": MAX_FRAGMENT,
        "sha256": original.sha256,
        "segment_complete": False,
        "duplicate": False,
    }
    try:
        for changed in ({"offset": MAX_FRAGMENT}, {"length": 1}, {"segment_complete": True}, {"id": "unknown"}):
            with pytest.raises(ValueError):
                sender.ack(ack | changed)
            assert sender.stats()["in_flight"] == 2 * MAX_FRAGMENT
        assert sender.ack(ack)
        assert sender.ack(ack) is False
        until(lambda: sender.stats()["in_flight"] == 2 * MAX_FRAGMENT)
    finally:
        spool.control.cancel()
        with pytest.raises(Cancelled):
            sender.wait(2)


def test_temporary_channel_backpressure_retries_without_new_reservation(tmp_path):
    m = api()
    spool = Spool(tmp_path / "spool")
    original = segment(spool, b"abc")
    attempts = []

    def send(kind, body, *, payload):
        attempts.append(payload)
        if len(attempts) < 3:
            raise BufferError("queue full")
        sender.ack(
            {
                "id": original.id,
                "offset": 0,
                "length": 3,
                "sha256": original.sha256,
                "segment_complete": True,
                "duplicate": False,
            }
        )

    sender = m.SegmentSender(spool, send)
    sender.start()
    sender.finish()
    sender.wait(2)
    assert attempts == [b"abc"] * 3
    assert sender.stats()["peak_in_flight"] == 3
    assert spool.stats()["files"] == 0


def test_partial_durable_credit_keeps_full_spool_alive_without_reclaim(tmp_path):
    import threading

    m = api()
    clock = [0.0]
    spool = Spool(tmp_path / "spool", max_files=1, clock=lambda: clock[0])
    original = segment(spool, b"x" * (3 * MAX_FRAGMENT))
    sent = []
    sender = m.SegmentSender(spool, lambda kind, body, **kw: sent.append(body), window=MAX_FRAGMENT)
    sender.start()
    until(lambda: len(sent) == 1)
    reservations = []
    waiting = threading.Thread(target=lambda: reservations.append(spool.reserve(1)))
    waiting.start()
    until(lambda: spool.stats()["backpressure"])
    try:
        for index, tick in enumerate((59, 118, 177)):
            clock[0] = tick
            sender.ack(
                {
                    "id": original.id,
                    "sha256": original.sha256,
                    "offset": index * MAX_FRAGMENT,
                    "length": MAX_FRAGMENT,
                    "segment_complete": index == 2,
                    "duplicate": False,
                }
            )
            if index < 2:
                until(lambda: len(sent) == index + 2)
                assert spool.stats()["files"] == 1 and spool.stats()["bytes"] == 3 * MAX_FRAGMENT
                assert waiting.is_alive()
        waiting.join(2)
        assert not waiting.is_alive() and len(reservations) == 1
        reservations[0].abort()
        sender.finish()
        sender.wait(2)
        assert spool.stats()["bytes"] == 0
    finally:
        spool.control.cancel()
        waiting.join(2)


def test_sender_validation_timeout_and_transport_failure(tmp_path):
    m = api()
    spool = Spool(tmp_path / "spool")
    original = segment(spool, b"abc")
    for window in (True, 0, MAX_FRAGMENT - 1, (16 << 20) + 1):
        with pytest.raises(ValueError, match="window"):
            m.SegmentSender(spool, lambda *a, **k: None, window=window)
    sender = m.SegmentSender(spool, lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="not started"):
        sender.wait(0)
    with pytest.raises(ValueError, match="fields"):
        sender.ack({})
    with pytest.raises(ValueError, match="unsent"):
        sender.ack(
            {
                "id": original.id,
                "sha256": original.sha256,
                "offset": 0,
                "length": 3,
                "segment_complete": True,
                "duplicate": False,
            }
        )
    sender.start()
    try:
        with pytest.raises(RuntimeError, match="already started"):
            sender.start()
        with pytest.raises(TimeoutError):
            sender.wait(0.01)
    finally:
        spool.control.cancel()
        with pytest.raises(Cancelled):
            sender.wait(2)

    failed = Spool(tmp_path / "failed")
    segment(failed, b"abc")
    broken = m.SegmentSender(failed, lambda *a, **k: (_ for _ in ()).throw(OSError("disconnected")))
    broken.start()
    with pytest.raises(OSError, match="disconnected"):
        broken.wait(2)
    with pytest.raises(OSError, match="disconnected"):
        failed.reserve()


def test_sender_rejects_truncated_spool_file(tmp_path):
    m = api()
    spool = Spool(tmp_path / "spool")
    original = segment(spool, b"abc")
    original.path.write_bytes(b"ab")
    sender = m.SegmentSender(spool, lambda *a, **k: None)
    sender.start()
    with pytest.raises(ValueError, match="truncated"):
        sender.wait(2)
    assert not sender.running
