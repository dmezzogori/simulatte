from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from simulatte.operations.feeding_operation import FeedingOperation
    from simulatte.picking_cell.cell import PickingCell


class EventPayload(TypedDict, total=False):
    cell: PickingCell
    message: str
    operation: FeedingOperation | None
