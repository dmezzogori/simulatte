from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from simulatte._wire import canonical_pack
from simulatte.environment import Environment
from simulatte.trace import Trace


@pytest.fixture
def env() -> Environment:
    """Provide a fresh Environment for each test."""
    return Environment()


def trace_content_digest(path: Path) -> str:
    """BLAKE2b of the canonical encoding of what a ``full`` trace holds, independent of byte layout.

    Covers the level, outcome and cursor range, the initial state, every chunk (cursors, times, epoch, snapshot),
    every recorded event and the footer fingerprint. The manifest (Python version, platform, installed packages)
    and volatile metadata are left out so the value does not depend on the machine, the catalog because it holds
    every event type registered in the process, and file offsets because they depend on compressed sizes.
    """
    trace = Trace.open(path)
    cursor_range = trace.cursor_range
    assert cursor_range is not None
    events = list(trace.events())
    chunks = [trace._chunk(i) for i in range(len(trace.index))]
    content: Any = (
        trace.level,
        trace.outcome,
        cursor_range,
        trace.state_at(cursor_range[0]),
        tuple(
            (info.first, info.last, info.t_start, info.t_end, info.epoch, chunk.snapshot)
            for info, chunk in zip(trace.index, chunks, strict=True)
        ),
        tuple(tuple(event) for event in events),
        None if trace.fingerprint is None else trace.fingerprint.digest,
    )
    return hashlib.blake2b(canonical_pack(content), digest_size=32).hexdigest()


@pytest.fixture
def trace_content() -> Callable[[Path], str]:
    """:func:`trace_content_digest`, for golden tests of the recorder."""
    return trace_content_digest
