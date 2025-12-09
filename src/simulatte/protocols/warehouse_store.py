from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypedDict

from simulatte.protocols import (
    HasEnv,
    Identifiable,
    ServesAGV,
    SupportsInput,
    SupportsOutput,
)

if TYPE_CHECKING:
    from simpy.resources.store import Store

    from simulatte.location import InputLocation, OutputLocation
    from simulatte.operations.feeding_operation import FeedingOperation
    from simulatte.service_point.service_point import ServicePoint
    from simulatte.simpy_extension.multi_store.multi_store import MultiStore
    from simulatte.simpy_extension.sequential_store.sequential_store import (
        SequentialStore,
    )
    from simulatte.stores.operation import InputOperation
    from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation
    from simulatte.typings.typings import ProcessGenerator
    from simulatte.unitload import CaseContainer


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


class WarehouseStoreProtocol(Identifiable, HasEnv, SupportsInput, SupportsOutput, ServesAGV, Protocol):
    """
    WarehouseStore is a generic class for a warehouse store.

    A WarehouseStore is a store that can be used to store and retrieve unit loads.
    The unit loads are stored in WarehouseLocations, which are located in the WarehouseStore.
    The WarehouseStore serves AGVs, and it is responsible for the loading and unloading of unit loads
    from the AGVs.

    Attributes:
        input_location: InputLocation of the store, where AGVs can unload unit loads.
        output_location: OutputLocation of the store, where AGVs can retrieve unit loads.
        location_width: fixed width of the locations in the store.
        location_height: fixed height of the locations in the store.
        depth: fixed depth of the locations in the store.
        n_positions: number of positions in each floor of the store.
        n_floors: number of floors of the store.
        load_time: time required to load a unit load into the store.
        conveyor_capacity: capacity of the conveyor connecting the store to the AGVs.

        input_conveyor: Store or MultiStore conveyor connecting the unloading AGVs to the store.
        output_conveyor: SequentialStore conveyor connecting the store to the retrieval AGVs.
    """

    input_location: InputLocation
    output_location: OutputLocation
    location_width: float
    location_height: float
    depth: int
    n_positions: int
    n_floors: int
    load_time: int
    conveyor_capacity: int

    _location_origin: WarehouseLocation
    _locations: tuple[WarehouseLocation, ...]

    input_conveyor: Store | MultiStore
    output_conveyor: SequentialStore

    input_service_point: ServicePoint

    @property
    def locations(self) -> tuple[WarehouseLocation, ...]:
        """
        Return the locations of the store.
        """

        return self._locations

    def get(self, *, feeding_operation: FeedingOperation):
        """
        WarehouseStore main internal retrieval process.

        Given a FeedingOperation, the specific implementation of the method must handle all the steps
        to retrieve a unit load from within the warehouse.

        Should be followed by a call to the load_agv method.
        """
        ...

    def load_agv(self, *, feeding_operation: FeedingOperation) -> ProcessGenerator:
        """
        Unit load AGV loading process.

        Given a FeedingOperation, load the agv with the required unit load,
        once it is available from the Output Conveyor.
        """
        ...

    def create_input_operation(
        self, *, unit_load: CaseContainer, location: WarehouseLocation, priority: int
    ) -> InputOperation:
        """
        Create an InputOperation for a specific unit load and location.

        An InputOperation is a dataclass that contains all the information needed
        to load a unit load into the warehouse.
        """
        ...

    def unload_agv(self, **kwargs) -> ProcessGenerator:
        """
        Unit load AGV unloading process.

        Given an AGV and an InputOperation, unload the unit load from the agv and put it on the input conveyor,
        once it is available.

        Should be followed by a call to the put method.
        """
        ...

    def put(self, **kwargs) -> ProcessGenerator:
        """
        WarehouseStore main internal storage process.

        Given a unit load and a location, the specific implementation of the method must handle all the steps
        to store a unit load within the warehouse.
        """
        ...

    def first_available_location(self) -> WarehouseLocation | None:
        """
        Return the first available location in the warehouse for storing a unit load.
        """
        ...

    def first_available_location_for_warmup(self, unit_load: CaseContainer) -> WarehouseLocation | None:
        """
        Return the first available location in the warehouse for storing a unit load during the warmup.
        """
        ...

    def book_location(self, *, location: WarehouseLocation, unit_load: CaseContainer) -> None:
        """
        Book a location for a specific unit load.
        """
        ...

    def plot(self) -> None:
        """
        Plot the store statistics.
        """
        ...
