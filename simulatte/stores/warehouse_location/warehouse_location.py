from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Generic, TypeVar

from ...unitload import CaseContainer
from ...utils import Identifiable
from .exceptions import IncompatibleUnitLoad, LocationBusy, LocationEmpty
from .physical_position import PhysicalPosition

if TYPE_CHECKING:
    from simulatte.products import Product


class WarehouseLocationSide(Enum):
    LEFT = "left"
    RIGHT = "right"
    ORIGIN = "origin"


T = TypeVar("T", bound=CaseContainer)


class WarehouseLocation(Generic[T], metaclass=Identifiable):
    """
    Warehouse physical storage location, where the unit loads are stored.
    """

    id: int

    def __init__(
        self,
        *,
        x: int,
        y: int,
        side: WarehouseLocationSide,
        depth: int = 2,
        width: float,
        height: float,
    ) -> None:
        """
        :param x: The discrete x-coordinate of the location (x-axis).
        :param y: The discrete y-coordinate of the location (y-axis).
        :param side: 'left' or 'rigth'.
        :param depth: The depth of the storage location.
        """
        self.x = x
        self.y = y

        if side not in WarehouseLocationSide:
            raise ValueError("Side must be a value of WarehouseLocationSide")
        self.side = side.value

        if depth not in (1, 2):
            raise ValueError(
                "The depth of the location must be positive and cannot be greater than 2."
            )
        self.depth = depth

        self.width = width
        self.height = height

        self.first_position = PhysicalPosition()
        self.second_position = PhysicalPosition()
        self.future_unit_loads: list[
            T
        ] = []  # Unit loads that will be stored in the future
        self.booked_pickups: list[
            T
        ] = []  # Unit loads that will be picked up in the future

    def __repr__(self) -> str:
        return (
            f"Location ({self.x}, {self.y}, {self.side}) reserved for {self.product} "
            f"[{len(self.future_unit_loads)}, {self.n_unit_loads}, "
            f"{self.is_empty}, {self.is_half_full}, {self.is_full}]"
        )

    @property
    def coordinates(self) -> tuple[float, float]:
        """
        The location coordinates in meters.
        """
        return self.x * self.width, self.y * self.height

    def check_product_compatibility(self, unit_load: T) -> bool:
        if unit_load.product != self.first_available_unit_load.product:
            raise IncompatibleUnitLoad(unit_load, self)
        return True

    def book_pickup(self, unit_load: T) -> None:
        if unit_load not in (
            self.first_position.unit_load,
            self.second_position.unit_load,
        ):
            raise RuntimeError("Unit load not available for pickup")

        if self.fully_booked:
            raise ValueError(f"Cannot book more than {self.depth} pickups")

        if unit_load in self.booked_pickups:
            raise RuntimeError("Unit load already booked")

        self.booked_pickups.append(unit_load)

    @property
    def fully_booked(self) -> bool:

        # first_position = self.first_position
        # second_position = self.second_position
        #
        # if first_position.free and second_position.free:
        #     return False
        #
        # for position in (self.first_position, self.second_position):
        #     if position.unit_load is not None:
        #         if position.unit_load not in self.booked_pickups:
        #             return False
        # return True

        return len(self.booked_pickups) == self.depth

    def freeze(self, *, unit_load: T) -> None:
        """
        Freeze the location for a certain unit load.

        If the location is not empty, first it checks that the unit load is compatible
        (same product as the one already stored).
        """
        if self.is_empty:
            if len(self.future_unit_loads) == self.depth:
                raise ValueError(
                    f"Cannot freeze a location with empty positions, but with {self.depth} future unit loads"
                )
        elif self.is_half_full:
            if len(self.future_unit_loads) >= 1:
                raise ValueError(
                    "Cannot freeze a location with one busy position, and one future unit load"
                )
        else:
            raise ValueError("Cannot freeze a location with two busy positions")

        if not self.is_empty:
            self.check_product_compatibility(unit_load)

        self.future_unit_loads.append(unit_load)

    def unfreeze(self, unit_load: T) -> None:
        if unit_load not in self.future_unit_loads:
            raise ValueError("Unit load not found in the future unit loads")

        self.future_unit_loads.remove(unit_load)

    @property
    def product(self) -> Product | None:
        """
        Returns the product associated with the location.
        A product is associated with the location in the following cases:
        - The location is not empty.
        - The location is empty, but it is frozen for a certain incoming product.
        """
        if self.is_empty:
            if len(self.future_unit_loads) == 0:
                # No unit load is stored in the location and no unit load is planned to be stored
                return None
            else:
                # No unit load is stored in the location but a unit load is planned to be stored
                return self.future_unit_loads[0].product
        else:
            return self.first_available_unit_load.product

    @property
    def n_cases(self) -> int:
        """
        Returns the number of cases stored in the location.
        """
        return (
            self.first_position.n_cases
            + self.second_position.n_cases
            + sum(unit_load.n_cases for unit_load in self.future_unit_loads)
        )

    def deals_with_products(self, product: Product) -> bool:
        return self.product == product

    @property
    def is_empty(self) -> bool:
        if self.depth == 2:
            return self.first_position.free and self.second_position.free
        return self.first_position.free

    @property
    def is_half_full(self) -> bool:
        return self.second_position.busy and self.first_position.free

    @property
    def is_full(self) -> bool:
        if self.depth == 2:
            return self.first_position.busy and self.second_position.busy
        return self.first_position.busy

    @property
    def n_unit_loads(self) -> int:
        return int(self.first_position.busy) + int(self.second_position.busy)

    @property
    def first_available_unit_load(self) -> T:
        """
        Returns the first available unit load.
        If the first position is busy, the unit load in second position is not available.
        """
        if self.is_empty:
            raise LocationEmpty(self)

        if self.is_full:
            return self.first_position.unit_load

        if self.is_half_full:
            return self.second_position.unit_load

    def put(self, unit_load: T) -> None:
        """
        Stores a unit load into the location.

        If the location is empty, the unit load will be stored into the second position.

        If the location is half full, the unit load will be stored into the first position,
        if the unit load is compatible (must contain the same product already stored).

        If the location is full, an exception will be raised.
        """
        if self.is_empty:
            # TODO: gestire singola profondità
            physical_position = self.second_position
        elif self.is_half_full:
            existing_product = self.second_position.unit_load.product
            if unit_load.product != existing_product:
                raise IncompatibleUnitLoad(unit_load, self)
            physical_position = self.first_position
        else:
            raise LocationBusy(self)

        physical_position.put(unit_load=unit_load)
        self.unfreeze(unit_load=unit_load)

    def get(self, unit_load) -> T:
        if len(self.booked_pickups) == 0:
            raise ValueError("Cannot get a unit load without booking it first")

        # if self.is_half_full:
        #     physical_position = self.second_position
        # elif self.is_full:
        #     physical_position = self.first_position
        # else:
        #     raise LocationEmpty(self)
        if self.is_empty:
            raise LocationEmpty(self)

        if unit_load is self.first_position.unit_load:
            physical_position = self.first_position
        elif unit_load is self.second_position.unit_load:
            # TODO: swap positions
            self.first_position.unit_load, self.second_position.unit_load = (
                self.second_position.unit_load,
                self.first_position.unit_load,
            )
            physical_position = self.first_position
        else:
            raise ValueError

        unit_load = physical_position.get()
        try:
            self.booked_pickups.remove(unit_load)
        except ValueError:
            raise ValueError("Cannot get a unit load without booking it first")
        return unit_load

    def affinity(self, product: Product) -> float:
        """
        Returns the affinity of the location to a certain product.
        The lower the number, the better is the location for the product.
        """

        if len(self.future_unit_loads) + self.n_unit_loads == self.depth:
            # la locazione è completamente occupata
            return float("inf")

        if self.product == product:
            # la locazione contiene/conterrà il prodotto
            return 0
        if self.product is None:
            # la locazione è vuota
            return 1

        if self.product != product:
            # la locazione contiene un prodotto diverso
            return float("inf")
