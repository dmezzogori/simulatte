from __future__ import annotations

import pytest

from simulatte.products import Product
from simulatte.requests import (
    CaseRequest,
    LayerRequestSingleProduct,
    PalletRequest,
    ProductRequest,
)


def make_product(family="F", cases_per_layer=3):
    return Product(
        probability=0.5,
        family=family,
        cases_per_layer=cases_per_layer,
        layers_per_pallet=2,
        max_case_per_pallet=cases_per_layer * 2,
        min_case_per_pallet=cases_per_layer,
        lp_enabled=True,
    )


def test_product_request_structure_and_lead_time_context():
    product = make_product()
    request = ProductRequest(product=product, n_cases=2)
    assert isinstance(request.sub_jobs[0], CaseRequest)
    assert request.n_cases == 2
    assert request.remaining_workload == 2

    with request:
        pass
    assert request.lead_time == request.env.now - request._start_time
    assert request.remaining_workload == 0


def test_layer_and_pallet_requests_flags():
    product = make_product()
    product_request = ProductRequest(product=product, n_cases=1)
    layer = LayerRequestSingleProduct(product_request)
    pallet = PalletRequest(layer)

    assert pallet.all_layers_single_product
    assert not pallet.all_layers_multi_product
    assert not pallet.is_top_off
    assert pallet.n_cases == 1
    assert pallet.unit_load is not None


def test_iter_feeding_operations_waits_until_available():
    product = make_product()
    request = ProductRequest(product=product, n_cases=1)

    def add_ops():
        yield request.env.timeout(2)
        request.feeding_operations.append("op")

    consumer = request.iter_feeding_operations()
    request.env.process(add_ops())
    request.env.run()

    assert consumer.value == ["op"]


def test_product_request_validation_against_cases_per_layer():
    product = make_product(cases_per_layer=1)
    with pytest.raises(ValueError):
        ProductRequest(product=product, n_cases=2)
