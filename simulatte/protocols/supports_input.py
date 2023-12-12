from __future__ import annotations

from typing import Protocol

from simulatte.operations import FeedingOperation
from simulatte.typings import History


class SupportsInput(Protocol):
    retrieval_jobs_counter: int
    retrieval_jobs_history: History[int]

    def get(self, *, feeding_operation: FeedingOperation):
        ...
