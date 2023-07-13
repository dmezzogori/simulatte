from __future__ import annotations

import pytest
import simulatte
from simpy.resources.store import Store
from simulatte import SystemController
from simulatte.demand.generators import fixed_sequence
from simulatte.picking_cell import PickingCell
from simulatte.products import Product


@pytest.fixture(scope="function")
def products() -> list[Product]:
    return [
        Product(
            probability=1,
            cases_per_layer=10,
            max_case_per_pallet=60,
            min_case_per_pallet=60,
            lp_enabled=True,
        )
        for _ in range(4)
    ]


@pytest.fixture(scope="function")
def system(env) -> SystemController:
    return simulatte.SystemController(env=env)


@pytest.fixture(scope="function")
def picking_cell(system) -> PickingCell:
    return PickingCell(
        system=system,
        input_queue=Store(env=system.env, capacity=4),
        output_queue=simulatte.simpy_extension.SequentialStore(system.env, capacity=300),
        building_point=simulatte.resources.MonitoredResource(system.env, capacity=1),
        feeding_area_capacity=8,
        staging_area_capacity=1,
        internal_area_capacity=2,
        register_main_process=False,
    )


# TODO: fixme
def _test_case_picking_cell_assign_pallet_request(
    system: SystemController, products: list[Product], picking_cell: PickingCell
) -> None:
    """
    Test the correct assignment of a PalletRequest to a PickingCell.
    The PickingCell input queue is supposed to register one item (the PalletRequest).
    The picking requests queue should be empty, as all the product requests are being immediately
    assigned to the FeedingArea.
    """

    def test():
        pallet_requests = list(fixed_sequence(products, n_pallet_requests=1, n_layers=4))
        pallet_request = pallet_requests[0]
        yield picking_cell.put(pallet_request=pallet_request)

        # There should be one PalletRequest in the PickingCell input queue
        assert len(picking_cell.input_queue.items) == 1
        assert picking_cell.input_queue.items[0] == pallet_request

        # There should be no PickingRequests in the PickingCell picking requests queue
        # All PickingRequests should be immediately handled and assigned to the FeedingArea, in the correct order.
        assert len(picking_cell.picking_requests_queue) == 0
        assert len(picking_cell.feeding_area) == 4
        for feeding_operation, product_request in zip(
            picking_cell.feeding_area, pallet_request.sub_requests[0].sub_requests
        ):
            assert feeding_operation.picking_request == product_request

    system.env.process(test())
    system.env.run(until=0.1)
