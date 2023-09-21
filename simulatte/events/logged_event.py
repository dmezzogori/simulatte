from __future__ import annotations

from typing import TYPE_CHECKING

from simpy.events import Event
from simulatte.logger.logger import Logger

if TYPE_CHECKING:
    from simulatte.logger.logger import EventPayload


class LoggedEvent(Event):
    def succeed(self, value: EventPayload | None = None):
        self.timestamp = self.env.now
        payload = value or {}
        if payload:
            Logger().log(payload=payload)
        return super().succeed(value=payload.get("value"))
