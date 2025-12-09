from __future__ import annotations

from enum import StrEnum


class AGVStatus(StrEnum):
    """
    The status of an AGV.
    """

    IDLE = "IDLE"
    RECHARGING = "RECHARGING"

    TRAVELING_UNLOADED = "TRAVELING_UNLOADED"
    TRAVELING_LOADED = "TRAVELING_LOADED"

    WAITING_TO_BE_LOADED = "WAITING_UNLOADED"
    WAITING_TO_BE_UNLOADED = "WAITING_LOADED"
