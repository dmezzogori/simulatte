from typing import TypeVar, Generator

from simpy import Event

ProcessReturn = TypeVar("ProcessReturn")
ProcessGenerator = Generator[Event, None, ProcessReturn | None]

HistoryValue = TypeVar("HistoryValue")
History = list[tuple[float, HistoryValue]]
