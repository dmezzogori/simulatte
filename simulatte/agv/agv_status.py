from __future__ import annotations

from enum import StrEnum


class AGVStatus(StrEnum):
    """
    The status of an AGV.
    """

    IDLE = "IDLE"

    TRAVELING_UNLOADED = "TRAVELING_UNLOADED"
    TRAVELING_LOADED = "TRAVELING_LOADED"

    WAITING_UNLOADED = "WAITING_UNLOADED"
    WAITING_LOADED = "WAITING_LOADED"
