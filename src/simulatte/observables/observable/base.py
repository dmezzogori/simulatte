from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from simulatte.events.logged_event import LoggedEvent

if TYPE_CHECKING:
    from simulatte.events.event_payload import EventPayload


class Observable:
    """
    Implements the observer/observable pattern.

    An observable is observed by an observer.
    The observer uses the observable signal event to act accordingly.
    """

    def __init__(self) -> None:
        self._callbacks: list[Callable] = []
        self.signal_event = self._init_signal_event()

    @property
    def callbacks(self):
        return self._callbacks

    @callbacks.setter
    def callbacks(self, callbacks):
        self._callbacks = callbacks
        self.signal_event = self._init_signal_event()

    def _init_signal_event(self) -> LoggedEvent:
        """
        Initialize the observable signal event.
        """
        event = LoggedEvent()
        if self.callbacks:
            event.callbacks.extend(self.callbacks)

        return event

    def trigger_signal_event(self, *, payload: EventPayload) -> LoggedEvent:
        """
        Trigger the signal event observed by the observer and then reset it.
        """

        self.signal_event.succeed(value=payload)
        self.signal_event = self._init_signal_event()
        return self.signal_event
