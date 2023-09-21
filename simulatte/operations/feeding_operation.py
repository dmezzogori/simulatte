from __future__ import annotations

from functools import total_ordering
from typing import TYPE_CHECKING

from simulatte.events.logged_event import LoggedEvent
from simulatte.utils.identifiable import Identifiable

if TYPE_CHECKING:
    from simulatte.agv.agv import AGV
    from simulatte.picking_cell.cell import PickingCell
    from simulatte.picking_cell.observable_areas.position import Position
    from simulatte.requests import Request
    from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation
    from simulatte.stores.warehouse_store import WarehouseStore
    from simulatte.unitload.pallet import Pallet


@total_ordering
class FeedingOperation(metaclass=Identifiable):
    """
    Represents a feeding operation assigned by the System to an agv.
    The agv is responsible for retrieving a unit load from a specific store, according to the
    picking request, and then to bring it to the assigned picking cell.
    """

    __slots__ = (
        "id",
        "cell",
        "relative_id",
        "agv",
        "store",
        "location",
        "unit_load",
        "picking_requests",
        "pre_unload_position",
        "unload_position",
        "status",
        "ready",
    )

    id: int

    def __init__(
        self,
        *,
        cell: PickingCell,
        agv: AGV,
        store: WarehouseStore,
        picking_requests: list[Request],
        location: WarehouseLocation,
        unit_load: Pallet,
    ) -> None:
        self.cell = cell
        self.relative_id = len(self.cell.feeding_operations)
        self.cell.feeding_operations.append(self)

        self.agv = agv
        self.store = store
        self.location = location
        self.unit_load = unit_load
        self.unit_load.feeding_operation = self
        self.picking_requests = picking_requests

        self.pre_unload_position: Position | None = None
        self.unload_position: Position | None = None

        self.status = {
            "arrived": False,
            "staging": False,
            "inside": False,
            "ready": False,
            "done": False,
        }
        self.ready = LoggedEvent(env=self.cell.system.env)

    def __repr__(self):
        return f"FeedingOperation{self.id}"

    def __lt__(self, other: FeedingOperation) -> bool:
        return self.id < other.id

    def __eq__(self, other: FeedingOperation) -> bool:
        return self.id == other.id

    @property
    def is_in_feeding_area(self) -> bool:
        return sum(self.status.values()) == 0

    def _check_status(self, *status_to_be_true) -> bool:
        for status in self.status:
            if status in status_to_be_true:
                if not self.status[status]:
                    return False
            else:
                if self.status[status]:
                    return False
        return True

    @property
    def is_in_front_of_staging_area(self) -> bool:
        return self._check_status("arrived")

    @property
    def is_inside_staging_area(self) -> bool:
        return self._check_status("arrived", "staging")

    @property
    def is_in_internal_area(self) -> bool:
        return self._check_status("arrived", "staging", "inside")

    @property
    def is_at_unload_position(self) -> bool:
        return self._check_status("arrived", "staging", "inside", "ready")

    @property
    def is_done(self) -> bool:
        return self._check_status("arrived", "staging", "inside", "ready", "done")

    def knock_staging_area(self) -> None:
        """
        Mark the operation as arrived in front of the picking cell, waiting to enter the staging area.
        """
        self.status["arrived"] = True

    def enter_staging_area(self) -> None:
        self.status["staging"] = True

    def enter_internal_area(self) -> None:
        self.status["inside"] = True

    def ready_for_unload(self) -> None:
        self.status["ready"] = True
        self.ready.succeed(value={"type": 0, "operation": self})

    def unloaded(self) -> None:
        self.status["done"] = True
