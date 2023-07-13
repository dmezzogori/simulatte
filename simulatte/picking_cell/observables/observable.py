from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.events.logged_event import LoggedEvent

if TYPE_CHECKING:
    from simulatte.logger.logger import EventPayload
    from simulatte.system import SystemController


class Observable:
    """
    Implements the observer/observable pattern.

    An observable is observed by an observer.
    The observer uses the observable signal event to act accordingly.
    """

    def __init__(self, *, system: SystemController) -> None:
        self.system = system
        self.signal_event = self._init_signal_event()

    def _init_signal_event(self) -> LoggedEvent:
        """
        Initialize the observable signal event.
        """
        return LoggedEvent(env=self.system.env)

    @property
    def _logged_event_title(self) -> str:
        return self.__class__.__name__

    def trigger_signal_event(self, *, payload: EventPayload) -> None:
        """
        Trigger the observer signal event and then reset it.
        """
        payload["time"] = self.system.env.now
        if hasattr(self, "cell"):
            payload["cell"] = self.cell.name

        self.signal_event.succeed(value=payload)
        self.signal_event = self._init_signal_event()
