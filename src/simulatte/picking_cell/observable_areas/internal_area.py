from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.observables.observable_area.base import ObservableArea
from simulatte.picking_cell.observable_areas.position import Position

if TYPE_CHECKING:
    from simulatte.operations.feeding_operation import FeedingOperation
    from simulatte.picking_cell.cell import PickingCell


class InternalArea(ObservableArea):
    """
    Represent the area inside a picking cell, where AGVs are placed to be picked from.
    Manage both unloading and pre-unloading positions.
    """

    def __init__(self, *, capacity: int, owner: PickingCell, signal_at, pre_unload: bool = False) -> None:
        super().__init__(capacity=capacity, owner=owner, signal_at=signal_at)

        if pre_unload:
            self.unload_positions = tuple(Position(name=f"UnloadPosition{i}", capacity=1) for i in range(capacity // 2))
            self.pre_unload_positions = tuple(
                Position(name=f"PreUnloadPosition{i}", capacity=1) for i in range(capacity // 2)
            )
        else:
            self.unload_positions = tuple(Position(name=f"UnloadPosition{i}", capacity=1) for i in range(capacity))
            self.pre_unload_positions = tuple()

    def append(self, feeding_operation: FeedingOperation):
        feeding_operation.enter_internal_area()
        return super().append(feeding_operation)

    def remove(self, feeding_operation: FeedingOperation):
        feeding_operation.unloaded()
        return super().remove(feeding_operation)
