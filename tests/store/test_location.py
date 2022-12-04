import pytest

from simulatte.products import Product
from simulatte.stores.warehouse_location import (
    IncompatibleUnitLoad,
    LocationBusy,
    LocationEmpty,
)
from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation
from simulatte.unitload import Pallet, Tray


@pytest.fixture(scope="function")
def location() -> WarehouseLocation:
    return WarehouseLocation(x=0, y=0, side="left", depth=2)


@pytest.fixture(scope="function")
def product_a() -> Product:
    return Product(probability=1, cases_per_layer=1, max_case_per_pallet=10, min_case_per_pallet=10, lp_enabled=True)


@pytest.fixture(scope="function")
def product_b() -> Product:
    return Product(probability=1, cases_per_layer=1, max_case_per_pallet=10, min_case_per_pallet=10, lp_enabled=True)


def test_location_01(location: WarehouseLocation, product_a: Product, product_b: Product) -> None:
    """
    Test the location class.

    The location is initialized empty.
    We immediately try to retrieve a unit load from it, which should fail.
    """
    assert location.is_empty
    assert location.second_position.free
    assert location.first_position.free
    assert location.n_unit_loads == 0

    with pytest.raises(LocationEmpty):
        location.get()


def test_location_02(location: WarehouseLocation, product_a: Product, product_b: Product) -> None:
    """
    Test the location class.

    The location is initialized empty.
    We put a unit load with product A in it. The location should be half full.
    """

    unit_load = Pallet(Tray(product=product_a, n_cases=1))
    location.freeze(unit_load=unit_load)
    location.put(unit_load)

    assert location.is_half_full
    assert location.second_position.busy
    assert location.first_position.free
    assert location.n_unit_loads == 1
    assert location.first_available_unit_load == unit_load
    assert location.product is product_a


def test_location_03(location: WarehouseLocation, product_a: Product, product_b: Product) -> None:
    """
    Test the location class.

    The location is initialized empty.
    We then put two unit loads with product A in it. The location should be full.
    We then try to put a third unit load with product B in it. This should fail.
    """

    unit_load = Pallet(Tray(product=product_a, n_cases=1))
    location.freeze(unit_load=unit_load)
    location.put(unit_load)

    unit_load_b = Pallet(Tray(product=product_a, n_cases=1))
    location.freeze(unit_load=unit_load_b)
    location.put(unit_load_b)

    assert location.is_full
    assert location.second_position.busy
    assert location.first_position.busy
    assert location.n_unit_loads == 2
    assert location.first_available_unit_load == unit_load_b
    assert location.product is product_a

    with pytest.raises(LocationBusy):
        unit_load_c = Pallet()
        location.put(unit_load_c)


def test_location_04(location: WarehouseLocation, product_a: Product, product_b: Product) -> None:
    """
    Test the location class.

    The location is initialized empty.
    We then put two unit loads with product A in it.
    We retrieve each unit load, and test that the location returns the correct unit loads in the correct order.
    """

    unit_load_a = Pallet(Tray(product=product_a, n_cases=1))
    location.freeze(unit_load=unit_load_a)
    location.put(unit_load_a)

    unit_load_b = Pallet(Tray(product=product_a, n_cases=1))
    location.freeze(unit_load=unit_load_b)
    location.put(unit_load_b)

    unit_load = location.get()
    assert unit_load == unit_load_b
    assert location.is_half_full
    assert location.second_position.busy
    assert location.first_position.free

    unit_load = location.get()
    assert unit_load == unit_load_a
    assert location.is_empty
    assert location.second_position.free
    assert location.first_position.free


def test_location_05(location: WarehouseLocation, product_a: Product, product_b: Product) -> None:
    """
    Test the location class.

    The location is initialized empty.
    We try to store two unit loads with different products, which should fail.
    """

    unit_load_a = Pallet(Tray(product=product_a, n_cases=1))
    location.freeze(unit_load=unit_load_a)
    location.put(unit_load_a)

    with pytest.raises(IncompatibleUnitLoad):
        unit_load_b = Pallet(Tray(product=product_b, n_cases=1))
        location.put(unit_load_b)
