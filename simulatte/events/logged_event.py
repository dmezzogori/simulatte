from __future__ import annotations

from simpy.events import Event

from simulatte.logger.logger import EventPayload, Logger


class LoggedEvent(Event):
    def succeed(self, value: EventPayload | None = None):
        payload = value or {}
        if payload:
            Logger().log(payload=payload)
        return super().succeed(value=payload.get("value"))
