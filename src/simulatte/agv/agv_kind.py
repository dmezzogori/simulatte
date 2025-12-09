from __future__ import annotations

from enum import StrEnum


class AGVKind(StrEnum):
    """
    The kind of AGV.
    """

    FEEDING = "FEEDING"
    REPLENISHMENT = "REPLENISHMENT"
    INPUT = "INPUT"
    OUTPUT = "OUTPUT"
