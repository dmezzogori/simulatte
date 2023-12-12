from __future__ import annotations

from typing import Protocol

from .has_env import HasEnv


class Timed(HasEnv, Protocol):
    _start_time: float | None
    _end_time: float | None
    lead_time: float | None

    @property
    def lead_time(self) -> float | None:
        if self._start_time is not None and self._end_time is not None:
            return self._end_time - self._start_time
        return None

    def started(self) -> None:
        """Mark as started"""
        self._start_time = self.env.now

    def completed(self) -> None:
        """Mark as completed"""
        self._end_time = self.env.now
