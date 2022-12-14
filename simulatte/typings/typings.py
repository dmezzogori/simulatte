from typing import TypeVar, Generator

from simpy import Event

T = TypeVar("T")

ProcessGenerator = Generator[Event, None, T | None]
