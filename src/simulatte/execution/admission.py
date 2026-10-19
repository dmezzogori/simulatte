"""Explicit admission outcomes; a busy slot never silently queues manual work."""

from dataclasses import dataclass
from typing import Literal
from simulatte.execution.schema import AttemptKey


@dataclass(frozen=True, slots=True)
class Admission:
    status: Literal["started", "busy", "stale"]
    key: AttemptKey | None = None
    owner: AttemptKey | None = None
