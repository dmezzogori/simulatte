"""Experimental features for material handling, warehouse, and AGV operations.

This module contains less mature implementations that are complete and tested
but not yet considered stable for the core API. These features may change
in future releases.

Exports:
    AGV: Automated guided vehicle server for transport operations.
    MaterialCoordinator: Orchestrates material delivery with FIFO blocking.
    MaterialSystem: Type alias for material handling system tuple.
    MaterialSystemBuilder: Factory for building material handling systems.
    TransportJob: Job type for AGV transport operations.
    Warehouse: Warehouse server with inventory containers.
    WarehouseJob: Job type for warehouse pick/put operations.
"""

from __future__ import annotations

from simulatte.experimental.agv import AGV
from simulatte.experimental.builders import MaterialSystemBuilder
from simulatte.experimental.job import TransportJob, WarehouseJob
from simulatte.experimental.materials import MaterialCoordinator
from simulatte.experimental.typing import MaterialSystem
from simulatte.experimental.warehouse import Warehouse

__all__ = [
    "AGV",
    "MaterialCoordinator",
    "MaterialSystem",
    "MaterialSystemBuilder",
    "TransportJob",
    "Warehouse",
    "WarehouseJob",
]
