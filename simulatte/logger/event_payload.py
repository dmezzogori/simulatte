from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from simulatte.operations.feeding_operation import FeedingOperation
    from simulatte.picking_cell.cell import PickingCell


class EventPayload(TypedDict, total=False):
    time: float
    cell: PickingCell
    event: str
    type: int
    operation: FeedingOperation | None
