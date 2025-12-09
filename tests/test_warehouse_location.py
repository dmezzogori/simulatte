from __future__ import annotations

import math

import pytest

from simulatte.exceptions.unitload import IncompatibleUnitLoad
from simulatte.products import Product
from simulatte.stores.warehouse_location.physical_position import PhysicalPosition
from simulatte.stores.warehouse_location.warehouse_location import (
    WarehouseLocation,
    WarehouseLocationSide,
)


class DummyStore:
    pass


class DummyUnitLoad:
    def __init__(self, product: Product, n_cases: int):
        self.product = product
        self.n_cases = n_cases
        self.location = None


def make_location(side=WarehouseLocationSide.LEFT, depth=2):
    return WarehouseLocation(
        store=DummyStore(),
        x=0,
        y=0,
        side=side,
        depth=depth,
        width=1.0,
        height=1.0,
    )


def make_product(family="A", cases_per_layer=4):
    return Product(
        probability=0.5,
        family=family,
        cases_per_layer=cases_per_layer,
        layers_per_pallet=2,
        max_case_per_pallet=cases_per_layer * 2,
        min_case_per_pallet=cases_per_layer,
        lp_enabled=True,
    )


def test_physical_position_put_get_and_errors():
    position = PhysicalPosition()
    unit_load = DummyUnitLoad(make_product(), 3)

    with pytest.raises(Exception):
        position.get()

    position.put(unit_load=unit_load)
    assert position.busy and not position.free
    assert position.n_cases == 3

    retrieved = position.get()
    assert retrieved is unit_load
    assert position.free and not position.busy


def test_freeze_put_book_get_and_affinity_flow():
    product = make_product()
    unit_load = DummyUnitLoad(product, 3)
    location = make_location()

    location.freeze(unit_load=unit_load)
    assert location.future_unit_loads == [unit_load]
    assert location.affinity(product) == 0  # frozen for product

    with pytest.raises(ValueError):
        location.put(unit_load=DummyUnitLoad(product, 2))  # not frozen

    location.put(unit_load=unit_load)
    assert not location.is_empty
    assert location.is_half_full
    assert location.second_position.unit_load is unit_load
    assert unit_load.location is location

    location.book_pickup(unit_load)
    retrieved = location.get(unit_load)
    assert retrieved is unit_load
    assert location.is_empty

    assert location.affinity(product) == 1  # empty again


def test_incompatible_product_and_invalid_sides():
    product_a = make_product("A")
    product_b = make_product("B")
    location = make_location()
    unit_load = DummyUnitLoad(product_a, 1)

    location.freeze(unit_load=unit_load)
    with pytest.raises(IncompatibleUnitLoad):
        location.freeze(unit_load=DummyUnitLoad(product_b, 1))

    with pytest.raises(ValueError):
        make_location(side="wrong")  # type: ignore[arg-type]


def test_affinity_when_full_returns_inf():
    product = make_product()
    location = make_location()
    unit_load1 = DummyUnitLoad(product, 1)
    unit_load2 = DummyUnitLoad(product, 1)

    location.freeze(unit_load=unit_load1)
    location.put(unit_load=unit_load1)
    location.freeze(unit_load=unit_load2)
    location.put(unit_load=unit_load2)

    assert location.is_full
    assert math.isinf(location.affinity(product))
