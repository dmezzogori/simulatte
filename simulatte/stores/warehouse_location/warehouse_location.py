from __future__ import annotations

from simulatte.products import Product
from simulatte.unitload import Pallet

from .exceptions import IncompatibleUnitLoad, LocationBusy, LocationEmpty
from .physical_position import PhysicalPosition


class WarehouseLocation:
    """
    Warehouse physical storage location, where the unit loads are stored.
    """

    def __init__(self, *, x: int, y: int, side: str, depth: int = 2) -> None:
        """
        :param x: The discrete x-coordinate of the location (x-axis).
        :param y: The discrete y-coordinate of the location (y-axis).
        :param side: 'left' or 'rigth'.
        :param depth: The depth of the storage location.
        """
        self.x = x
        self.y = y

        if side not in ("left", "right"):
            raise ValueError("Side must be 'left' or 'right'")
        self.side = side

        if depth not in (1, 2):
            raise ValueError("The depth of the location must be positive and cannot be greater than 2.")
        self.depth = depth

        self.first_position = PhysicalPosition()
        self.second_position = PhysicalPosition()
        self.frozen = False
        self.future_unit_loads: list[Pallet] = []

    def __repr__(self) -> str:
        return f"Location ({self.x}, {self.y}, {self.side}) containing {self.product})"

    def check_product_compatibility(self, unit_load: Pallet) -> bool:
        if unit_load.product != self.first_available_unit_load.product:
            raise IncompatibleUnitLoad(unit_load, self)
        return True

    def freeze(self, *, unit_load: Pallet) -> None:
        """
        Freeze the location for a certain unit load.

        If the location is not empty, first it checks that the unit load is compatible
        (same product as the one already stored).
        """
        if not self.is_empty:
            self.check_product_compatibility(unit_load)

        self.frozen = True
        self.future_unit_loads.append(unit_load)

    def unfreeze(self, unit_load: Pallet) -> None:
        self.frozen = False
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

    def deals_with_products(self, product: Product) -> bool:
        return self.product == product

    @property
    def is_empty(self) -> bool:
        return self.first_position.free and self.second_position.free if self.depth == 2 else self.first_position.free

    @property
    def is_half_full(self) -> bool:
        return self.second_position.busy and self.first_position.free

    @property
    def is_full(self) -> bool:
        return self.first_position.busy and self.second_position.busy if self.depth == 2 else self.first_position.busy

    @property
    def n_unit_loads(self) -> int:
        return int(self.first_position.busy) + int(self.second_position.busy)

    @property
    def first_available_unit_load(self) -> Pallet:
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

    def put(self, unit_load: Pallet) -> None:
        """
        Stores a unit load into the location.

        If the location is empty, the unit load will be stored into the second position.

        If the location is half full, the unit load will be stored into the first position,
        if the unit load is compatible (must contain the same product already stored).

        If the location is full, an exception will be raised.
        """
        if self.is_empty:
            physical_position = self.second_position
        elif self.is_half_full:
            existing_product = self.first_available_unit_load.product
            if unit_load.product != existing_product:
                raise IncompatibleUnitLoad(unit_load, self)
            physical_position = self.first_position
        else:
            raise LocationBusy(self)

        physical_position.put(unit_load=unit_load)

    def get(self) -> Pallet:
        if self.is_half_full:
            physical_position = self.second_position
        elif self.is_full:
            physical_position = self.first_position
        else:
            raise LocationEmpty(self)
        return physical_position.get()