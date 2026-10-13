"""Execution slots of one coordinator (design 4.3, D102).

The coordinator always owns the ``interactive`` slot (SP4 semantics, also used by
``simulatte run``) and ``local-1`` .. ``local-N`` experiment slots. Each slot owns
one transport: one supervisor and at most one execution child at a time. Remote
``<worker>-1`` .. ``<worker>-<slots>`` slots of kind ``uv`` run over SSH (design 8, 9).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os

from simulatte.execution.workers import WorkerSpec

# A bound against configuration mistakes, not a performance limit.
MAX_LOCAL_SLOTS = 64


def default_local_slots() -> int:
    """Four local experiment slots, capped at ``cpu_count - 1`` (at least one).

    Source: specs/research/2026-10-12-sp5-throughput.md, section "Task 4 re-run". On eva
    (10 cores, CPython 3.14) 32 replications of 20,000 events ran at 44.8, 83.3 and 125.8
    per minute on 1, 2 and 4 slots, with the store commit unchanged (0.33 to 0.35 s);
    four slots kept about two cores busy. Task 1 proposed 2 unless 4 was clearly faster.
    """
    return min(4, max(1, (os.cpu_count() or 1) - 1))


@dataclass(frozen=True, slots=True)
class SlotConfig:
    """Number of local experiment slots (``0`` leaves only the interactive slot) and remote workers."""

    local: int = field(default_factory=default_local_slots)
    workers: tuple[WorkerSpec, ...] = ()

    def __post_init__(self):
        if type(self.local) is not int or not 0 <= self.local <= MAX_LOCAL_SLOTS:
            raise ValueError(f"Local slots must be an integer between 0 and {MAX_LOCAL_SLOTS}")
        object.__setattr__(self, "workers", tuple(self.workers))
        names = [spec.name for spec in self.workers if type(spec) is WorkerSpec]
        if len(names) != len(self.workers) or len(set(names)) != len(names):
            raise ValueError("Workers must be distinct WorkerSpec entries")

    def names(self) -> tuple[str, ...]:
        """The local experiment slots."""
        return tuple(f"local-{index}" for index in range(1, self.local + 1))


def remote_slots(spec: WorkerSpec) -> tuple[str, ...]:
    return tuple(f"{spec.name}-{index}" for index in range(1, spec.slots + 1))
