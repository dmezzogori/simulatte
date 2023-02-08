from __future__ import annotations

import pprint
from datetime import datetime
from typing import TYPE_CHECKING, Optional, TypedDict

import pandas as pd

import simulatte

if TYPE_CHECKING:
    from simulatte.picking_cell import FeedingOperation, PickingCell


class EventPayload(TypedDict, total=False):
    time: float
    cell: PickingCell
    event: str
    type: int
    operation: Optional[FeedingOperation]


class Logger(metaclass=simulatte.utils.Singleton):
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
        df = pd.DataFrame(self.logs)
        filename = filename or f"{datetime.now()}.csv"
        df.to_csv(f"./export_data/{filename}.csv")

    def __str__(self) -> str:
        return pprint.pformat(self.logs)
