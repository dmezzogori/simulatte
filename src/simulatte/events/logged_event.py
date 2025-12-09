from __future__ import annotations

from simpy.events import Event

from simulatte.events.event_payload import EventPayload
from simulatte.logger import logger
from simulatte.utils import EnvMixin


class LoggedEvent(Event, EnvMixin):
    def __init__(self):
        EnvMixin.__init__(self)
        Event.__init__(self, env=self.env)

    def succeed(self, value: EventPayload | None = None):
        if value is not None:
            if isinstance(value, dict):
                logger.debug(value["message"])
            else:
                logger.debug(value)

        return super().succeed(value=value)
