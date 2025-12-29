"""Type aliases for experimental material handling components."""

from __future__ import annotations

from simulatte.experimental.agv import AGV
from simulatte.experimental.materials import MaterialCoordinator
from simulatte.experimental.warehouse import Warehouse
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor

type MaterialSystem = tuple[
    ShopFloor,
    tuple[Server, ...],
    Warehouse,
    tuple[AGV, ...],
    MaterialCoordinator,
]

__all__ = ["MaterialSystem"]
