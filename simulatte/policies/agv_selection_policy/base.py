from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from simulatte.agv.agv import AGV


class AGVSelectionPolicy:
    def __call__(self, *, agvs: Collection[AGV], exceptions: Collection[AGV] | None = None) -> AGV:
        """
        Abstract method to select the best AGV according to some logic.
        """

        raise NotImplementedError


class MultiAGVSelectionPolicy:
    def __call__(self, *, agvs: Collection[AGV], exceptions: Collection[AGV] | None = None) -> Sequence[AGV]:
        """
        Abstract method to select the best AGVs according to some logic.
        """

        raise NotImplementedError
