from __future__ import annotations

from simulatte.exceptions.base import SimulationError


class OutOfStockError(SimulationError):
    """Raised when a product is out of stock."""

    pass
