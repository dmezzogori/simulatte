from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Generic, TypedDict, TypeVar

import simulatte
from simulatte.stores import InputOperation, WarehouseLocation, WarehouseLocationSide

from ..location import InputLocation, OutputLocation
from ..unitload import CaseContainer, Pallet, Tray
from ..utils import Identifiable, as_process
from .warehouse_location import distance

if TYPE_CHECKING:
    from simpy.resources.store import Store
    from simulatte.operations import FeedingOperation
    from simulatte.products import Product
    from simulatte.service_point import ServicePoint
    from simulatte.simpy_extension import MultiStore, SequentialStore

    from eagle_trays.agv.ant import Ant


T = TypeVar("T", bound=CaseContainer)


class WarehouseStoreConfig(TypedDict):
    """
    Configuration of a WarehouseStore.
    """

    n_positions: int | None
    n_floors: int
    location_width: float
    location_height: float
    depth: int
    load_time: int
    conveyor_capacity: int


class WarehouseStore(Generic[T], metaclass=Identifiable):
    id: int

    input_conveyor: Store | MultiStore
    output_conveyor: SequentialStore

    input_service_point: ServicePoint

    total_get_queue_stats = {}
    total_put_queue_stats = {}
    total_get_queue = 0
    total_put_queue = 0

    def __init__(
        self,
        *,
        config: WarehouseStoreConfig,
    ):
        self.env = simulatte.Environment()
        self.input_location = InputLocation(self)
        self.output_location = OutputLocation(self)
        self.location_width = config["location_width"]
        self.location_height = config["location_height"]
        self.depth = config["depth"]
        self.n_positions = config["n_positions"]
        self.n_floors = config["n_floors"]
        self.load_time = config["load_time"]
        self.conveyor_capacity = config["conveyor_capacity"]

        self.queue_stats = []
        self._get_queue = 0
        self._put_queue = 0

        self.get_queue_stats = []
        self.put_queue_stats = []

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
                    for side in [
                        WarehouseLocationSide.LEFT,
                        WarehouseLocationSide.RIGHT,
                    ]
                ),
                key=lambda location: distance.euclidean(location, self._location_origin),
            )
        )

        self._input_operations = []
        self._replenishment_processes = defaultdict(list)
        self._product_location_map = defaultdict(set)
        self._saturation_history = []

    @property
    def get_queue(self) -> int:
        return self._get_queue

    @get_queue.setter
    def get_queue(self, value: int):
        self._get_queue = value
        WarehouseStore.total_get_queue = value
        self.get_queue_stats.append((self.env.now, self._get_queue))
        WarehouseStore.total_get_queue_stats.setdefault(self.__class__.__name__, []).append(
            (self.env.now, WarehouseStore.total_get_queue)
        )

    @property
    def put_queue(self) -> int:
        return self._put_queue

    @put_queue.setter
    def put_queue(self, value: int):
        self._put_queue = value
        WarehouseStore.total_put_queue = value
        self.put_queue_stats.append((self.env.now, self._put_queue))
        WarehouseStore.total_put_queue_stats.setdefault(self.__class__.__name__, []).append(
            (self.env.now, WarehouseStore.total_put_queue)
        )

    @property
    def locations(self):
        return self._locations

    @property
    def n_locations(self) -> int:
        return len(self.locations)

    @property
    def name(self) -> str:
        return f"{self.__class__.__name__}_{self.id}"

    def first_available_location(self) -> WarehouseLocation:
        """
        Cerchiamo la locazione in cui mettere la unit lod.
        Consideriamo solo le locazioni vuote e senza future unit load.
        """
        for location in self.locations:
            if location.is_empty and len(location.future_unit_loads) == 0:
                return location

    def first_available_location_for_warmup(self, unit_load):
        for location in self.locations:
            if location.is_empty:
                return location
            if location.is_half_full and location.product == unit_load.product:
                return location

    def create_input_operation(self, *, unit_load: T, location: WarehouseLocation, priority: int) -> InputOperation:
        input_operation = InputOperation(unit_load=unit_load, location=location, priority=priority)
        self._input_operations.append(input_operation)
        return input_operation

    @as_process
    def load_ant(self, *, feeding_operation: FeedingOperation):
        """
        Warehouse Output Process.

        Given a FeedingOperation, load the agv with the required unit load,
        once it is available from the Output Conveyor.
        """
        n_non_empty_locations = sum(not location.is_empty for location in self.locations)
        # n_non_empty_locations = sum(location.n_cases for location in self.locations)
        self._saturation_history.append((self.env.now, n_non_empty_locations / self.n_locations))

        output_operation = yield self.output_conveyor.get(
            lambda output_operation: output_operation.unit_load == feeding_operation.unit_load
        )
        if output_operation.unit_load != feeding_operation.unit_load:
            raise ValueError("Unit load mismatch")

        yield self.env.timeout(self.load_time)

        yield feeding_operation.agv.load(unit_load=feeding_operation.unit_load)

        self.get_queue -= 1

    @as_process
    def unload_ant(self, *, ant: Ant, input_operation: InputOperation):
        """
        Warehouse Input Process.

        Given an Ant and an InputOperation, unload the unit load from the agv and put it on the input conveyor,
        once it is available.
        """

        n_non_empty_locations = sum(not location.is_empty for location in self.locations)
        # n_non_empty_locations = sum(location.n_cases for location in self.locations)
        self._saturation_history.append((self.env.now, n_non_empty_locations / self.n_locations))

        with self.input_service_point.request(
            priority=input_operation.priority, preempt=False
        ) as input_service_point_request:
            self.queue_stats.append((self.env.now, len(self.input_service_point.queue)))
            yield input_service_point_request
            yield self.input_conveyor.put((input_operation,))
            yield self.env.timeout(self.load_time)
            yield ant.unload()
            ant.release_current()

        self.put_queue -= 1

    def get(self, *, feeding_operation: FeedingOperation) -> simulatte.typings.ProcessGenerator:
        """
        Warehouse Main internal retrieval process.

        Given a FeedingOperation, the specific implementation of the method must handle all the steps
        to retrieve the unit load from within the warehouse from a specific location, until the loading of the
        unit load on the Output Conveyor.
        """
        raise NotImplementedError

    def load(
        self, *, unit_load: T, location: WarehouseLocation, ant: Ant, priority: int
    ) -> simulatte.typings.ProcessGenerator:
        """
        Warehouse Main internal loading process.

        Given a UnitLoad, the specific implementation of the method must handle all the steps
        needed to load the unit load on the Input Conveyor, and then delegate the loading of the unit laod inside
        the warehouse to the ._put method.
        """
        raise NotImplementedError

    def _put(self, *args, **kwargs):
        raise NotImplementedError

    def replenishment_started(self, *, product: Product, process) -> None:
        self._replenishment_processes[product.id].append(process)

    def book_location(self, *, location: WarehouseLocation, unit_load: Tray | Pallet) -> None:
        location.freeze(unit_load=unit_load)

    def plot(self, *, window=300, queue_stats, title: str):
        import statistics

        import matplotlib.pyplot as plt

        def iter_timestamps(x: list[int], start: int, step: int):
            ret = []
            i = 0
            while i <= len(x):
                idxs = []
                e = x[i]
                while start <= e <= (start + step):
                    idxs.append(i)
                    i += 1
                    try:
                        e = x[i]
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
        x = [i / 3600 for i in range(0, self.env.now, window)]

        plt.plot(x, y, label=f"{self.name}")
        plt.xlabel("Time [h]")
        plt.ylabel("Queue [# agv]")
        plt.title(title)
        # plt.show()

    def plot_output_queue(self, window=300):
        self.plot(window=window, queue_stats=self.get_queue_stats, title=f"{self.__class__.__name__} Output Queue")

    def plot_input_queue(self, window=300):
        self.plot(window=window, queue_stats=self.put_queue_stats, title=f"{self.__class__.__name__} Input Queue")
