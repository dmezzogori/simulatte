from __future__ import annotations

import pprint
from typing import TYPE_CHECKING

from simulatte.utils.singleton import Singleton

if TYPE_CHECKING:
    from simulatte.logger.event_payload import EventPayload


class Logger(metaclass=Singleton):
    """
    Used to register events during a simulation.
    """

    def __init__(self) -> None:
        self.logs: list[EventPayload] = []

    def log(self, *, payload: EventPayload) -> None:
        self.logs.append(payload)

    def reset(self) -> None:
        self.logs = []

    def export(self, *, filename: str | None = None) -> None:
        from datetime import datetime

        import pandas as pd

        df = pd.DataFrame(self.logs)
        filename = filename or f"{datetime.now()}.csv"
        df.to_csv(f"./export_data/{filename}.csv")

    def __str__(self) -> str:
        return pprint.pformat(self.logs)
