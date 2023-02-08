from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.logger.logger import EventPayload
from ..observables import Observable
from .base_area import Area, T

if TYPE_CHECKING:
    from simulatte.picking_cell import PickingCell


class ObservableArea(Area[T], Observable):
    def __init__(self, *, cell: PickingCell, capacity: int = float("inf")):
        Area.__init__(self, cell=cell, capacity=capacity)
        Observable.__init__(self, system=cell.system)

    def remove(self, item: T) -> None:
        Area.remove(self, item)
        payload = EventPayload(event=f"ACTIVATING {self.__class__.__name__} SIGNAL", type=1)
        self.trigger_signal_event(payload=payload)
