from __future__ import annotations

from typing import TYPE_CHECKING

from .base_observer import Observer
from ..areas import FeedingArea

if TYPE_CHECKING:
    from simulatte.requests import Request


class FeedingObserver(Observer[FeedingArea]):
    def next(self) -> Request:
        return self.observable_area.cell.picking_requests_queue.popleft()

    def _main_process(self) -> None:
        self.system.start_feeding_operation(cell=self.cell)
