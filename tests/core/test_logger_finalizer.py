"""Regression tests for the loguru handler-removal deadlock.

A weakref finalizer (``_finalize_logger``) can fire — via GC — while the same
thread is already inside a loguru ``add()``/``remove()`` that holds loguru's
non-reentrant ``_core.lock``. Calling ``_logger.remove()`` from the finalizer at
that moment re-acquires the same lock on the same thread and self-deadlocks
(observed on PyPy, where the cyclic GC fires finalizers at arbitrary points).

The fix: while we are inside such a critical section, the finalizer must *defer*
the handler removal to a queue that is drained at a safe point (lock free),
instead of calling ``_logger.remove()`` re-entrantly.
"""

from __future__ import annotations

import pytest

from simulatte import logger as logmod


class _RecordingLogger:
    """Stand-in for the module-level loguru logger that records remove() calls."""

    def __init__(self) -> None:
        self.removed: list[int] = []

    def remove(self, handler_id: int) -> None:
        self.removed.append(handler_id)


@pytest.fixture
def fake_logger(monkeypatch: pytest.MonkeyPatch) -> _RecordingLogger:
    fake = _RecordingLogger()
    monkeypatch.setattr(logmod, "_logger", fake)
    # Start each test with a clean queue and an inactive critical-section flag.
    logmod._pending_handler_removals.clear()
    logmod._loguru_critical.active = False
    return fake


def test_finalizer_defers_handler_removal_during_loguru_critical_section(
    fake_logger: _RecordingLogger,
) -> None:
    # Simulate a finalizer firing while this thread is mid-add() (lock held).
    logmod._loguru_critical.active = True
    try:
        logmod._finalize_logger(handler_id=123, db_store=None)
        # Must NOT call remove() now — that would re-enter _core.lock and deadlock.
        assert fake_logger.removed == []
        assert list(logmod._pending_handler_removals) == [123]
    finally:
        logmod._loguru_critical.active = False

    # Draining at a safe point (lock free) performs the deferred removal.
    logmod._drain_pending_handler_removals()
    assert fake_logger.removed == [123]
    assert len(logmod._pending_handler_removals) == 0


def test_finalizer_removes_handler_directly_outside_critical_section(
    fake_logger: _RecordingLogger,
) -> None:
    # No critical section active: removing directly is safe (lock uncontended).
    logmod._finalize_logger(handler_id=456, db_store=None)
    assert fake_logger.removed == [456]
    assert len(logmod._pending_handler_removals) == 0
