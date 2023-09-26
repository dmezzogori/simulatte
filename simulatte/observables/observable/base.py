from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.environment import Environment
from simulatte.events.logged_event import LoggedEvent

if TYPE_CHECKING:
    from simulatte.logger.event_payload import EventPayload


class Observable:
    """
    Implements the observer/observable pattern.

    An observable is observed by an observer.
    The observer uses the observable signal event to act accordingly.
    """

    def __init__(self) -> None:
        self.env = Environment()
        self.signal_event = self._init_signal_event()

    def _init_signal_event(self) -> LoggedEvent:
        """
        Initialize the observable signal event.
        """
        return LoggedEvent(env=self.env)

    @property
    def _logged_event_title(self) -> str:
        return self.__class__.__name__

    def trigger_signal_event(self, *, payload: EventPayload, event: LoggedEvent | None = None) -> LoggedEvent:
        """
        Trigger the observer signal event and then reset it.
        """

        payload["time"] = self.env.now
        if hasattr(self, "cell"):
            payload["cell"] = self.cell.name

        if event is None:
            self.signal_event.succeed(value=payload)
            self.signal_event = self._init_signal_event()
            return self.signal_event
        else:
            event.succeed(value=payload)
            return self._init_signal_event()