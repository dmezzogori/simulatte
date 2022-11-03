from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from simulatte.system.policies import AntSelectionPolicy

if TYPE_CHECKING:
    from simulatte.ant import Ant


class AntsManager:
    def __init__(self, ants: Sequence[Ant], ant_selection_policy: AntSelectionPolicy) -> None:
        self.ants = ants
        self._ant_selection_policy = ant_selection_policy

    def get_best_ant(self) -> Ant:
        return self._ant_selection_policy(ants=self.ants)
