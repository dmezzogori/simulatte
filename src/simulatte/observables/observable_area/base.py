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
        env,
    ) -> None:
        Area.__init__(self, capacity=capacity, owner=owner, env=env)
        Observable.__init__(self, env=self.env)

        if isinstance(signal_at, str):
            self.signal_at = {signal_at}
        else:
            self.signal_at = set(signal_at)

    def append(self, item: object, /) -> None:  # type: ignore[override]
        super().append(item)
        if "append" in self.signal_at:
            payload = EventPayload(message=f"{self.owner} {self.__class__.__name__} - appending {item}")
            self.trigger_signal_event(payload=payload)

    def append_exceed(self, item: object, /) -> None:
        super().append_exceed(item)
        if "append" in self.signal_at:
            payload = EventPayload(message=f"{self.owner} {self.__class__.__name__} - appending {item}")
            self.trigger_signal_event(payload=payload)

    def remove(self, item: object, /) -> None:  # type: ignore[override]
        super().remove(item)
        if "remove" in self.signal_at:
            payload = EventPayload(message=f"{self.owner} {self.__class__.__name__} - removing {item}")
            self.trigger_signal_event(payload=payload)
