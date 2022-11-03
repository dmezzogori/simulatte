from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from simulatte.ant import Ant


class AntSelectionPolicy:
    def __call__(self, *, ants: Sequence[Ant], exceptions: Sequence[Ant] | None = None) -> Ant:
        raise NotImplementedError


class MinorWorkloadAntsPolicy(AntSelectionPolicy):
    @staticmethod
    def sorter(ant: Ant) -> tuple[int, int]:
        return len(ant.users), len(ant.queue)

    def __call__(self, *, ants: Sequence[Ant], exceptions: Sequence[Ant] | None = None) -> Ant:
        exceptions = exceptions or set()
        return min((ant for ant in ants if ant not in exceptions), key=self.sorter)
