from __future__ import annotations

from typing import Protocol


class Identifiable(Protocol):
    id: int
