from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eagle_trays.agv.ant import Ant


class AGVSelectionPolicy:
    def __call__(self, *, ants: Sequence[Ant], exceptions: Sequence[Ant] | None = None) -> Ant:
        raise NotImplementedError


class MinorWorkloadAntsPolicy(AGVSelectionPolicy):
    @staticmethod
    def sorter(ant: Ant) -> tuple[int, int, int]:
        return len(ant.users), len(ant.queue), ant.resource_requested_timestamp

    def __call__(self, *, ants: Sequence[Ant], exceptions: Sequence[Ant] | None = None) -> Ant | None:
        exceptions = exceptions or set()
        sorted_ants = sorted((ant for ant in ants if ant not in exceptions), key=self.sorter)
        best_ant = sorted_ants[0] if sorted_ants else None
        return best_ant
