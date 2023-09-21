from __future__ import annotations

from typing import Literal

from simulatte.logger.event_payload import EventPayload
from simulatte.observables.area.base import Area, Item, Owner
from simulatte.observables.observable.base import Observable


class ObservableArea(Area[Item, Owner], Observable):
    """
    Implement an observable area.
    Extends Area to add the observer/observable pattern.
    """

    def __init__(
        self,
        *,
        capacity: int = float("inf"),
        owner: Owner | None = None,
        signal_at: Literal["append", "remove"],
    ):
        Area.__init__(self, capacity=capacity, owner=owner)
        Observable.__init__(self)
        self.signal_at = signal_at

    def append(self, item: Item, exceed=False):
        super().append(item=item, exceed=exceed)
        if self.signal_at == "append":
            payload = EventPayload(event=f"ACTIVATING {self.__class__.__name__} SIGNAL", type=1)
            self.trigger_signal_event(payload=payload)

    def remove(self, item: Item) -> None:
        super().remove(item)
        if self.signal_at == "remove":
            payload = EventPayload(event=f"ACTIVATING {self.__class__.__name__} SIGNAL", type=1)
            self.trigger_signal_event(payload=payload)
