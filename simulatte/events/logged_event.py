from __future__ import annotations

from simpy.events import Event
from simulatte.environment import Environment
from simulatte.events.event_payload import EventPayload
from simulatte.logger import logger


class LoggedEvent(Event):
    def __init__(self):
        super().__init__(env=Environment())

    def succeed(self, value: EventPayload | None = None):
        if value is not None:
            if isinstance(value, dict):
                logger.debug(value["message"])
            else:
                logger.debug(value)

        return super().succeed(value=value)
