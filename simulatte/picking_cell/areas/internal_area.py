from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.operations import FeedingOperation

from .observable_area import ObservableArea
from .position import Position

if TYPE_CHECKING:
    from simulatte.picking_cell import PickingCell


class InternalArea(ObservableArea[FeedingOperation]):
    """
    Represent the area inside a picking cell, where ants are placed to be unloaded.
    Manage both unloading and pre-unloading positions.
    """

    def __init__(self, *, cell: PickingCell, capacity: int, pre_unload: bool = False) -> None:
        super().__init__(cell=cell, capacity=capacity)

        if pre_unload:
            self.unload_positions = tuple(
                Position(name=f"UnloadPosition{i}", env=cell.system.env, capacity=1) for i in range(capacity // 2)
            )
            self.pre_unload_positions = tuple(
                Position(name=f"PreUnloadPosition{i}", env=cell.system.env, capacity=1) for i in range(capacity // 2)
            )
        else:
            self.unload_positions = tuple(
                Position(name=f"UnloadPosition{i}", env=cell.system.env, capacity=1) for i in range(capacity)
            )
            self.pre_unload_positions = tuple()

    def append(self, feeding_operation: FeedingOperation) -> None:
        super().append(feeding_operation)
        feeding_operation.enter_internal_area()

    def remove(self, feeding_operation: FeedingOperation) -> None:
        super().remove(feeding_operation)
        feeding_operation.unloaded()
