from __future__ import annotations

from typing import Any, Protocol

from simulatte.typings import History


class SupportsOutput(Protocol):
    storage_jobs_counter: int
    storage_jobs_history: History[int]

    def put(self, **kwargs: Any) -> Any:
        ...
