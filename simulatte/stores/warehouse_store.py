from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.location import InputLocation, OutputLocation
from simulatte.protocols import warehouse_store
from simulatte.stores.operation import InputOperation
from simulatte.stores.warehouse_location import distance
from simulatte.stores.warehouse_location.warehouse_location import (
    WarehouseLocation,
    WarehouseLocationSide,
)
from simulatte.utils import EnvMixin, IdentifiableMixin, as_process

if TYPE_CHECKING:
    from simulatte.agv.agv import AGV
    from simulatte.operations.feeding_operation import FeedingOperation
    from simulatte.typings import History, ProcessGenerator
    from simulatte.unitload import CaseContainer


class WarehouseStore(IdentifiableMixin, EnvMixin, warehouse_store.WarehouseStoreProtocol):
    def __init__(self, *, config: warehouse_store.WarehouseStoreConfig):
        IdentifiableMixin.__init__(self)
        EnvMixin.__init__(self)

        self.input_location = InputLocation(self)
        self.output_location = OutputLocation(self)
        self.location_width = config["location_width"]
        self.location_height = config["location_height"]
        self.depth = config["depth"]
        self.n_positions = config["n_positions"]
        self.n_floors = config["n_floors"]
        self.load_time = config["load_time"]
        self.conveyor_capacity = config["conveyor_capacity"]

        self._location_origin = WarehouseLocation(
            store=self,
            x=0,
            y=0,
            width=self.location_width,
            height=self.location_height,
            depth=self.depth,
            side=WarehouseLocationSide.ORIGIN,
        )
        self._locations = tuple(
            sorted(
                (
                    WarehouseLocation(
                        store=self,
                        x=x,
                        y=y,
                        side=side,
                        depth=self.depth,
                        width=self.location_width,
                        height=self.location_height,
                    )
                    for x in range(self.n_positions)
                    for y in range(self.n_floors)
                    for side in (
                        WarehouseLocationSide.LEFT,
                        WarehouseLocationSide.RIGHT,
                    )
                ),
                key=lambda location: distance.euclidean(location, self._location_origin),
            )
        )

        self.retrieval_jobs_counter = 0
        self.retrieval_jobs_history = []
        self.storage_jobs_counter = 0
        self.storage_jobs_history = []

        self._saturation_history = []
        self._saturation = 0

        self.full_unit_loads = 0
        self.partial_unit_loads = 0
        self._unit_load_history = []

        self.input_agvs_queue = 0
        self.input_agvs_queue_history = []
        self.output_agvs_queue = 0
        self.output_agvs_queue_history = []

    def get(self, *, feeding_operation: FeedingOperation) -> ProcessGenerator:
        """
        To be implemented by the specific store.
        """

        raise NotImplementedError

    @as_process
    def load_agv(self, *, feeding_operation: FeedingOperation):
        """
        Load an AGV with a unit load from the output conveyor, as requested by a feeding operation.
        """

        # Get the unit load from the output conveyor
        yield self.output_conveyor.get(lambda x: x.unit_load == feeding_operation.unit_load)
        self._saturation -= 1
        self._saturation_history.append((self.env.now, self._saturation))

        # Unload the unit load from the output conveyor
        yield self.env.timeout(self.load_time)
        yield feeding_operation.agv.load(unit_load=feeding_operation.unit_load)

        if (
            feeding_operation.unit_load.n_cases
            == feeding_operation.unit_load.product.cases_per_layer
            * feeding_operation.unit_load.product.layers_per_pallet
        ):
            self.full_unit_loads -= 1
        else:
            self.partial_unit_loads -= 1
        n_locations = len(self.locations) * 2
        self._unit_load_history.append(
            (self.env.now, self.full_unit_loads / n_locations, self.partial_unit_loads / n_locations)
        )

    def create_input_operation(
        self, *, unit_load: CaseContainer, location: WarehouseLocation, priority: int
    ) -> InputOperation:
        return InputOperation(unit_load=unit_load, location=location, priority=priority)

    @as_process
    def unload_agv(self, *, agv: AGV, input_operation: InputOperation):
        """
        Unload the unit load carried by an AGV in the input conveyor, as requested by an input operation.
        """

        # Wait for the input service point to be available
        with self.input_service_point.request(
            priority=input_operation.priority, preempt=False
        ) as input_service_point_request:
            yield input_service_point_request

            # Put the input operation in the input conveyor
            yield self.input_conveyor.put((input_operation,))

            # Wait for the loading
            yield self.env.timeout(self.load_time)

            # Wait for the AGV to be unloaded
            yield agv.unload()

            # Release the AGV
            agv.release_current()

        self._saturation += 1
        self._saturation_history.append((self.env.now, self._saturation))

        if (
            input_operation.unit_load.n_cases
            == input_operation.unit_load.product.cases_per_layer * input_operation.unit_load.product.layers_per_pallet
        ):
            self.full_unit_loads += 1
        else:
            self.partial_unit_loads += 1
        n_locations = len(self.locations) * 2
        self._unit_load_history.append(
            (self.env.now, self.full_unit_loads / n_locations, self.partial_unit_loads / n_locations)
        )

    def put(
        self, *, unit_load: CaseContainer, location: WarehouseLocation, agv: AGV, priority: int
    ) -> ProcessGenerator:
        """
        To be implemented by the specific store.
        """

        raise NotImplementedError

    def first_available_location(self) -> WarehouseLocation | None:
        """
        Iterate over the locations and return the first location that is empty and has no future unit loads.
        """

        for location in self.locations:
            if location.is_empty and len(location.future_unit_loads) == 0:
                return location

    def first_available_location_for_warmup(self, unit_load: CaseContainer) -> WarehouseLocation | None:
        """
        Iterate over the locations and return the first location that is empty,
        or partially occupied by a unit load of the same product.
        """

        for location in self.locations:
            if location.is_empty:
                return location
            if location.is_half_full and location.product == unit_load.product:
                return location

    def book_location(self, *, location: WarehouseLocation, unit_load: CaseContainer) -> None:
        location.freeze(unit_load=unit_load)

    def plot(self, *, window, queue_stats: History, title: str) -> None:
        import statistics

        import matplotlib.pyplot as plt

        def iter_timestamps(_x: list[float], start: int, step: int):
            ret = []
            i = 0
            while i <= len(_x):
                idxs = []
                e = _x[i]
                while start <= e <= (start + step):
                    idxs.append(i)
                    i += 1
                    try:
                        e = _x[i]
                    except IndexError:
                        ret.append(idxs)
                        return ret
                ret.append(idxs)
                start += step

        timestamps = [t for t, _ in queue_stats]
        get_queues = [s for _, s in queue_stats]

        y = [
            statistics.mean([get_queues[i] for i in idxs]) if idxs else 0
            for idxs in iter_timestamps(timestamps, 0, window)
        ]
        x = [i / 3600 for i in range(0, self.env.now, window)][: len(y)]

        plt.plot(x, y, label=f"{self}")
        plt.xlabel("Time [h]")
        plt.ylabel("Queue [# agv]")
        plt.title(title)
        plt.show()

    def plot_output_queue(self, window=300) -> None:
        """
        Plot the output queue history.
        """
        self.plot(window=window, queue_stats=self.output_agvs_queue_history, title=f"{self} Output Queue")

    def plot_input_queue(self, window=300) -> None:
        """
        Plot the input queue history.
        """
        self.plot(window=window, queue_stats=self.input_agvs_queue_history, title=f"{self} Input Queue")
