from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from simulatte.logger.logger import EventPayload

from ..observable import Observable
from .base_area import Area, T

if TYPE_CHECKING:
    from ...controllers import SystemController


class ObservableArea(Area[T], Observable):
    """
    Implement an observable area.
    Extends Area to add the observer/observable pattern.
    """

    def __init__(
        self,
        *,
        system_controller: SystemController,
        capacity: int = float("inf"),
        signal_at: Literal["append", "remove"],
    ):
        Area.__init__(self, system_controller=system_controller, capacity=capacity)
        Observable.__init__(self, system_controller=system_controller)
        self.signal_at = signal_at

    def append(self, item: T, exceed=False):
        super().append(item=item, exceed=exceed)
        if self.signal_at == "append":
            payload = EventPayload(event=f"ACTIVATING {self.__class__.__name__} SIGNAL", type=1)
            self.trigger_signal_event(payload=payload)

    def remove(self, item: T) -> None:
        super().remove(item)
        if self.signal_at == "remove":
            payload = EventPayload(event=f"ACTIVATING {self.__class__.__name__} SIGNAL", type=1)
            self.trigger_signal_event(payload=payload)
