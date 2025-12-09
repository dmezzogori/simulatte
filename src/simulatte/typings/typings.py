from __future__ import annotations

from collections.abc import Generator
from typing import TypeVar

from simpy import Event

_ProcessReturn = TypeVar("_ProcessReturn")
ProcessGenerator = Generator[Event, None, _ProcessReturn]


HistoryValue = TypeVar("HistoryValue")
History = list[tuple[float, HistoryValue]]
