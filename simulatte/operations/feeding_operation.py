from __future__ import annotations

from functools import total_ordering
from itertools import count
from typing import TYPE_CHECKING

from simulatte.events import LoggedEvent
from simulatte.unitload import Pallet


if TYPE_CHECKING:
    from simulatte.ant import Ant
    from simulatte.stores import WarehouseStore
    from simulatte.picking_cell import PickingCell
    from simulatte.picking_cell.areas.position import Position
    from simulatte.requests import PickingRequest


@total_ordering
class FeedingOperation:
    """
    Represents a feeding operation assigned by the WMS to an ant.
    The ant is responsible for retrieving a unit load from a specific store, according to the
    picking request, and then to bring it to the assigned picking cell.
    """

    _id_iter = count()

    def __init__(
        self,
        *,
        cell: PickingCell,
        ant: Ant,
        store: WarehouseStore,
        picking_request: PickingRequest,
        location,
        unit_load: Pallet,
    ) -> None:
        """
        Initialize.
        :param wms: the WMS instance
        :param cell: The LayerCell for which the FeedingOperation has been generated.
        :param ant: The ant that takes care of the feeding operation
        :param store: The storage area where the unit load of interest is taken
        :param picking_request: The picking request that triggered the feeding operation

        :attr states: The steps each FeedingOperation should pass through
        :attr side: The side where the ant is going once inside the layer picking cell.
        :attr ready: The event triggered when the operation is ready for an unload by the robot.
        """
        self.id = next(self._id_iter)

        self.cell = cell
        self.cell.feeding_operations.append(self)

        self.ant = ant
        self.store = store
        self.location = location
        self.unit_load = unit_load
        self.picking_request = picking_request
        self.picking_request.feeding_operations.append(self)
        self.unload_position: Position | None = None

        self.status = {"arrived": False, "staging": False, "inside": False, "ready": False, "done": False}
        self.ready = LoggedEvent(env=self.cell.system.env)

    def __lt__(self, other: FeedingOperation):
        return self.id < other.id

    def __eq__(self, other: FeedingOperation):
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
