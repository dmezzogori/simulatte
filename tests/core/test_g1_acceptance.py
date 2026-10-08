"""Gate G1 acceptance (spec §2): the vertical slice records, seeks, replays and digests a reference job shop.

The reference shop is a LumsCor job shop plus a ``lathe`` server used directly by a process, without a
``ShopFloor``: the first request of each round finds the lathe idle and is granted inside its constructor, the
others queue at the same simulated time. The criteria:

- the shop records a ``full`` trace;
- :meth:`Trace.state_at` equals the uninterrupted replay of the live events at every chunk boundary and at
  sampled intermediate cursors, including same-time events and the lathe's immediate grants;
- the semantic digest is identical across observer configurations and across ``PYTHONHASHSEED`` values in
  fresh processes, and :meth:`Trace.verify` is true.
"""

from __future__ import annotations

import os
import platform
import random
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from simulatte.builders import build_lumscor_system
from simulatte.environment import Environment
from simulatte.events import DomainEvent, Event, apply_deltas
from simulatte.job import ProductionJob
from simulatte.scenario import Scenario
from simulatte.server import JobGranted, JobQueued, Server
from simulatte.trace import ChunkLimits, Cursor, Trace, TraceRecorder

if TYPE_CHECKING:
    from simulatte.typing import ProcessGenerator

SEED = 20260508
HORIZON = 300.0
CHUNK_EVENTS = 200


def build_reference_shop(env: Environment) -> None:
    """The G1 reference shop: a 4-machine LumsCor job shop and a directly used ``lathe``."""
    build_lumscor_system(
        env=env, scenario=Scenario(n_servers=4), check_timeout=5.0, wl_norm_level=6.0, allowance_factor=2
    )
    lathe = Server(env=env, capacity=1, name="lathe")

    def hold(job: ProductionJob) -> ProcessGenerator:
        with lathe.request(job=job) as request:
            yield request
            yield env.timeout(2.0)

    def direct_user() -> ProcessGenerator:
        round_ = 0
        while True:
            for _ in range(1 + round_ % 3):  # 1 to 3 requests at the same time; the lathe is idle at each round
                job = ProductionJob(env=env, sku="D", servers=[lathe], processing_times=[2.0], due_date=env.now + 20)
                env.process(hold(job))
            round_ += 1
            yield env.timeout(7.0)

    env.process(direct_user())


def run_digest() -> str | None:
    """The digest of the reference shop with only the digest observing (used by the hash-seed subprocesses)."""
    env = Environment(seed=SEED)
    env.enable_digest()
    build_reference_shop(env)
    env.run(until=HORIZON)
    return env.fingerprint().digest


class LiveLog:
    """Every domain event of the projection as it is emitted, independent of the trace file."""

    def __init__(self, env: Environment) -> None:
        self.events: list[DomainEvent] = []
        env.bus.subscribe(self, "*")

    def __call__(self, event: DomainEvent) -> None:
        if event.ordinal is not None:
            self.events.append(event)


def _cursor(event: Event) -> Cursor:
    return (float(event.t), event.seq)


def _digest_with(observe: Callable[[Environment, Path], None], path: Path) -> str | None:
    env = Environment(seed=SEED)
    observe(env, path)
    build_reference_shop(env)
    env.run(until=HORIZON)
    env.close()
    return env.fingerprint().digest


def test_g1_acceptance(tmp_path: Path) -> None:
    # 1. The reference shop records a full trace (small chunks, so there are many boundaries).
    path = tmp_path / "reference.simtrace"
    env = Environment(seed=SEED)
    log = LiveLog(env)
    TraceRecorder(env, path, chunk_limits=ChunkLimits(max_events=CHUNK_EVENTS))
    build_reference_shop(env)
    env.run(until=HORIZON)
    env.close()

    trace = Trace.open(path)
    assert trace.level == "full"
    assert trace.outcome == "completed"
    assert not trace.truncated
    assert len(trace.index) >= 20
    assert [(e.t, e.seq) for e in trace.events()] == [_cursor(e) for e in log.events]
    trace.check()

    # 2. Seek equals uninterrupted replay at every chunk boundary and at sampled intermediate cursors.
    cursors = [_cursor(e) for e in log.events]
    lathe_queued = {e.job for e in log.events if isinstance(e, JobQueued) and e.server == "lathe"}
    immediate = [
        _cursor(e)
        for before, e in zip(log.events, log.events[1:], strict=False)
        if isinstance(e, JobGranted)
        and e.server == "lathe"
        and isinstance(before, JobQueued)
        and before.job == e.job
        and before.queue_length == 1
    ]
    same_time = [a for a, b in zip(cursors, cursors[1:], strict=False) if a[0] == b[0]]
    assert len(lathe_queued) >= 50 and len(immediate) >= 20 and len(same_time) >= 1000
    rnd = random.Random(SEED)
    boundaries = [c for info in trace.index for c in (info.first, info.last)]
    activation = (0.0, -1)
    targets = sorted(
        {
            activation,
            *boundaries,
            *rnd.sample(cursors, 150),
            *rnd.sample(same_time, 100),
            *immediate,
            *(((a[0] + b[0]) / 2, 0) for a, b in rnd.sample(list(zip(cursors, cursors[1:], strict=False)), 50)),
        }
    )
    state: dict[str, dict[str, Any]] = {entity: dict(fields) for entity, fields in env.initial_state.items()}
    position = 0
    for target in targets:
        while position < len(log.events) and cursors[position] <= target:
            apply_deltas(state, log.events[position].deltas)
            position += 1
        assert trace.state_at(target) == state, target
    for event in log.events[position:]:
        apply_deltas(state, event.deltas)
    assert state == env.entities.snapshot()
    end = trace.cursor_range
    assert end is not None and trace.state_at(end[1]) == state

    # 3. The digest is identical across observer configurations ...
    recorded = env.fingerprint().digest
    assert recorded is not None and trace.fingerprint is not None and trace.fingerprint.digest == recorded
    seen: list[Event] = []

    def digest_only(env: Environment, path: Path) -> None:
        env.enable_digest()

    def extra_subscribers(env: Environment, path: Path) -> None:
        env.enable_digest()
        env.bus.subscribe(seen.append, "**")
        env.bus.subscribe(lambda event: len(event.deltas), "*")

    def kpi_recorder(env: Environment, path: Path) -> None:
        TraceRecorder(env, path, level="kpi")

    def full_recorder(env: Environment, path: Path) -> None:
        TraceRecorder(env, path)  # default chunk limits

    digests = {
        "digest only": _digest_with(digest_only, tmp_path / "unused"),
        "extra subscribers": _digest_with(extra_subscribers, tmp_path / "unused"),
        "kpi recorder": _digest_with(kpi_recorder, tmp_path / "kpi.simtrace"),
        "full recorder": _digest_with(full_recorder, tmp_path / "default.simtrace"),
    }
    assert len(seen) > len(log.events)
    assert set(digests.values()) == {recorded}, digests
    assert Trace.open(tmp_path / "default.simtrace").verify() is True
    assert Trace.open(tmp_path / "kpi.simtrace").verify() == "not_verifiable"

    # ... and across PYTHONHASHSEED values in fresh processes.
    script = (
        f"import sys; sys.path.insert(0, {str(Path(__file__).parent)!r}); "
        "from test_g1_acceptance import run_digest; print(run_digest())"
    )
    for hash_seed in ("0", "1", "123"):
        completed = subprocess.run(
            [sys.executable, "-c", script],
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == recorded, hash_seed

    # 4. The trace verifies: the digest recomputed from the file equals the recorded one.
    assert trace.verify() is True


# The G1 reference digest (specs/research/sp1-g1-report.md) and the canonical content of its full trace with
# CHUNK_EVENTS-event chunks. Pinned on macOS arm64; RNG samples go through the platform's libm (log, exp), whose
# last-bit results may differ elsewhere, so other platforms only check that the values are stable in-process.
GOLDEN_REFERENCE_DIGEST = "9032ec344577870a45a32b7aa251f3db06145d9d1dd499b94664c5bd4e29c51f"
GOLDEN_REFERENCE_CONTENT = "6514ec161aa0812a8d7fba6d1f8414d9cc34f951c06e4a7496d3d9e2af2fd973"
_GOLDEN_PLATFORM = sys.platform == "darwin" and platform.machine() == "arm64"


def test_golden_reference_digest_and_trace_content(tmp_path: Path, trace_content: Callable[[Path], str]) -> None:
    path = tmp_path / "golden.simtrace"
    env = Environment(seed=SEED)
    TraceRecorder(env, path, chunk_limits=ChunkLimits(max_events=CHUNK_EVENTS))
    build_reference_shop(env)
    env.run(until=HORIZON)
    env.close()
    digest = env.fingerprint().digest
    assert digest == run_digest()
    content = trace_content(path)
    if not _GOLDEN_PLATFORM:
        pytest.skip("golden values are pinned on macOS arm64 (libm-dependent samples)")
    assert (digest, content) == (GOLDEN_REFERENCE_DIGEST, GOLDEN_REFERENCE_CONTENT)
