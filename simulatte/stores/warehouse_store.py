from __future__ import annotations

import random
from collections import defaultdict
from typing import TYPE_CHECKING, Generic, Iterable, Literal, TypeVar

import simulatte
from simulatte.stores import InputOperation, WarehouseLocation, WarehouseLocationSide

from ..unitload import CaseContainer, Pallet, Tray
from ..utils import Identifiable, as_process
from .inventory_position import OnHand, OnOrder
from .warehouse_location import distance

if TYPE_CHECKING:
    from simpy.resources.store import Store

    from simulatte.ant import Ant
    from simulatte.operations import FeedingOperation
    from simulatte.products import Product, ProductsGenerator
    from simulatte.service_point import ServicePoint
    from simulatte.simpy_extension import MultiStore, SequentialStore


T = TypeVar("T", bound=CaseContainer)


class WarehouseStore(Generic[T], metaclass=Identifiable):
    id: int

    input_conveyor: Store | MultiStore
    output_conveyor: SequentialStore

    input_service_point: ServicePoint

    def __init__(
        self,
        *,
        n_positions: int = 20,
        n_floors: int = 8,
        location_width: float = 1,
        location_height: float = 1,
        depth: int = 2,
        load_time: int = 20,
        conveyor_capacity: int = 5,
    ):
        self.env = simulatte.Environment()
        self.input_location = simulatte.location.Location(name=f"{self.__class__.__name__} Input")
        self.output_location = simulatte.location.Location(name=f"{self.__class__.__name__} Output")
        self.location_width = location_width
        self.location_height = location_height
        self.depth = depth
        self.n_positions = n_positions
        self.n_floors = n_floors
        self.load_time = load_time
        self.conveyor_capacity = conveyor_capacity

        self._location_origin = WarehouseLocation(
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
                        x=x, y=y, side=side, depth=self.depth, width=self.location_width, height=self.location_height
                    )
                    for x in range(self.n_positions)
                    for y in range(self.n_floors)
                    for side in [WarehouseLocationSide.LEFT, WarehouseLocationSide.RIGHT]
                ),
                key=lambda location: distance.euclidean(location, self._location_origin),
            )
        )

        self._input_operations = []
        self._replenishment_processes = defaultdict(list)
        self._product_location_map = defaultdict(set)

    @property
    def locations(self):
        return self._locations

    @property
    def n_locations(self) -> int:
        return len(self.locations)

    @property
    def name(self) -> str:
        return f"{self.__class__.__name__}_{self.id}"

    def filter_locations(self, *, product: Product) -> Iterable[WarehouseLocation]:
        yield from self._product_location_map[product.id]

    def create_input_operation(self, *, unit_load: T, location: WarehouseLocation, priority: int) -> InputOperation:
        input_operation = InputOperation(unit_load=unit_load, location=location, priority=priority)
        self._input_operations.append(input_operation)
        return input_operation

    @as_process
    def load_ant(self, *, feeding_operation: FeedingOperation):
        """
        Warehouse Output Process.

        Given a FeedingOperation, load the ant with the required unit load,
        once it is available from the Output Conveyor.
        """
        yield self.env.timeout(self.load_time)
        yield self.output_conveyor.get(
            lambda output_operation: output_operation.unit_load == feeding_operation.unit_load
        )
        yield feeding_operation.ant.load(unit_load=feeding_operation.unit_load)
        return feeding_operation.unit_load

    @as_process
    def unload_ant(self, *, ant: Ant, input_operation: InputOperation):
        """
        Warehouse Input Process.

        Given an Ant and an InputOperation, unload the unit load from the ant and put it on the input conveyor,
        once it is available.
        """
        with self.input_service_point.request(
            priority=input_operation.priority, preempt=False
        ) as input_service_point_request:
            yield input_service_point_request
            yield self.env.timeout(self.load_time)
            yield self.input_conveyor.put((input_operation,))
            yield ant.unload()

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

    def on_hand(self, *, product: Product) -> OnHand:
        n_cases = sum(location.n_cases for location in self.filter_locations(product=product))
        return OnHand(product=product, n_cases=n_cases)

    def on_order(self, *, product: Product) -> OnOrder:
        product_replenishment_processes = self._replenishment_processes[product.id]
        n_cases = (
            sum((not p.processed for p in product_replenishment_processes))
            * product.layers_per_pallet
            * product.cases_per_layer
        )
        return OnOrder(product=product, n_cases=n_cases)

    def replenishment_started(self, *, product: Product, process) -> None:
        self._replenishment_processes[product.id].append(process)

    def book_location(self, *, location: WarehouseLocation, unit_load: Tray | Pallet) -> None:
        location.freeze(unit_load=unit_load)
        self._product_location_map[unit_load.product.id].add(location)

    def unbook_location(self, *, location: WarehouseLocation) -> None:
        if location.is_half_full:
            self._product_location_map[location.product.id].remove(location)

    def warmup(
        self,
        *,
        products_generator: ProductsGenerator,
        filling: float | None = 0.5,
        locations: Literal["products", "random"],
        products: Literal["linear", "random"] | None,
    ):
        if locations == "products":
            for i, product in enumerate(products_generator.products):
                location = self.locations[i]
                unit_load = Pallet(
                    Tray(
                        product=product,
                        n_cases=product.cases_per_layer,
                    )
                )
                self.book_location(location=location, unit_load=unit_load)
                location.put(unit_load=unit_load)
        elif locations == "random":
            i = 0
            for location in self.locations:
                if random.random() < filling and i < len(products_generator.products):
                    if products == "random":
                        product = products_generator.choose_one()
                    elif products == "linear":
                        product = products_generator.products[i]
                        i += 1
                    else:
                        raise ValueError(f"Unknown products warmup policy: {products}")

                    for _ in range(location.depth):
                        unit_load = Pallet(
                            Tray(
                                product=product,
                                n_cases=product.cases_per_layer,
                            )
                        )
                        location.freeze(unit_load=unit_load)
                        location.put(unit_load=unit_load)
        else:
            raise ValueError(f"Unknown locations warmup policy {locations}")
