from __future__ import annotations

from simulatte.operations.feeding_operation import FeedingOperation
from simulatte.products import Product
from simulatte.requests import OrderLine
from simulatte.unitload.layer import LayerSingleProduct
from simulatte.unitload.pallet import PalletSingleProduct


class DummyStore:
    output_location = object()

    def load_agv(self, feeding_operation):
        return feeding_operation.env.timeout(1)

    def get(self, feeding_operation):
        return feeding_operation.env.timeout(1)


class DummyArea:
    def __init__(self):
        self.items = []

    def trigger_signal_event(self, payload):
        return payload

    def append(self, item):
        self.items.append(item)

    def append_exceed(self, item):
        self.items.append(item)

    def remove(self, item):
        if item in self.items:
            self.items.remove(item)


class DummyCell:
    def __init__(self):
        self.feeding_operations = []
        self.staging_area = DummyArea()
        self.internal_area = DummyArea()
        self.input_location = object()
        self.staging_location = object()
        self.internal_location = object()
        self.system = type("System", (), {"idle_feeding_agvs": []})()

    def __repr__(self):
        return "DummyCell"


class DummyAGV:
    def __init__(self):
        self.current_location = None
        self.picking_cell = DummyCell
        self.users = []
        self.queue = []

    def move_to(self, location, skip_idle_signal=False):
        return DummyStore().load_agv  # placeholder

    def release_current(self):
        return None

    def load(self, unit_load):
        return DummyStore().load_agv

    def unload(self):
        return DummyStore().load_agv


def make_partial_pallet(product: Product) -> PalletSingleProduct:
    partial_layer = LayerSingleProduct(product=product, n_cases=product.cases_per_layer - 1)
    return PalletSingleProduct(partial_layer)


def test_feeding_operation_initial_state_and_status_flags(env):
    product = Product(
        probability=1,
        family="F",
        cases_per_layer=4,
        layers_per_pallet=2,
        max_case_per_pallet=8,
        min_case_per_pallet=4,
        lp_enabled=True,
    )
    request = OrderLine(product=product, n_cases=1, env=env)
    pallet = make_partial_pallet(product)

    cell = DummyCell()
    agv = DummyAGV()
    store = DummyStore()
    feeding = FeedingOperation(
        cell=cell,
        agv=agv,
        store=store,
        order_lines=[request],
        location=object(),
        unit_load=pallet,
        env=env,
    )

    assert feeding in cell.feeding_operations
    assert feeding.has_partial_unit_load
    assert feeding.is_in_front_of_staging_area is False

    feeding.status["arrived"] = True
    feeding.enter_staging_area()
    assert feeding.is_inside_staging_area

    feeding.enter_internal_area()
    feeding.ready_for_unload()
    feeding.unloaded()
    assert feeding.is_done
