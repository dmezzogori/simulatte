from __future__ import annotations

from typing import Literal

from simulatte.events.event_payload import EventPayload
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
        capacity: float = float("inf"),
        owner: Owner,
        signal_at: Literal["append", "remove"] | tuple[Literal["append", "remove"], ...],
    ) -> None:
        Area.__init__(self, capacity=capacity, owner=owner)
        Observable.__init__(self)

        if isinstance(signal_at, str):
            self.signal_at = {signal_at}
        else:
            self.signal_at = set(signal_at)

    def append(self, item: Item, exceed=False, skip_signal=False):
        super().append(item=item, exceed=exceed)
        if "append" in self.signal_at and not skip_signal:
            payload = EventPayload(message=f"{self.owner} {self.__class__.__name__} - appending {item}")
            self.trigger_signal_event(payload=payload)

    def remove(self, item: Item) -> None:
        super().remove(item)
        if "remove" in self.signal_at:
            payload = EventPayload(message=f"{self.owner} {self.__class__.__name__} - removing {item}")
            self.trigger_signal_event(payload=payload)
