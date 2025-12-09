from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import pytest

from simulatte.operations import FeedingOperation
from simulatte.products import Product
from simulatte.requests import OrderLine, PalletOrder


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


def test_order_line_tracks_lead_time(env):
    product = make_product()
    line = OrderLine(product=product, n_cases=2, env=env)
    line.started()
    env.timeout(3)
    env.run()
    line.completed()

    assert line.lead_time == pytest.approx(3.0)


def test_pallet_order_flattens_lines_and_counts_cases(env):
    p1 = make_product()
    p2 = make_product(family="G")
    lines: Sequence[OrderLine | tuple[Product, int]] = (OrderLine(product=p1, n_cases=2, env=env), (p2, 1))
    order = PalletOrder(lines, env=env)

    assert len(order.order_lines) == 2
    assert order.n_cases == 3
    assert all(line.parent is order for line in order)


def test_order_line_waits_for_feeding_operations(env):
    line = OrderLine(product=make_product(), n_cases=1, env=env)

    def attach():
        yield env.timeout(2)
        line.feeding_operations.append(cast(FeedingOperation, "op"))

    consumer = line.wait_for_feeding_operations()
    env.process(attach())
    env.run()

    assert consumer.value == ["op"]


def test_order_line_validation(env):
    with pytest.raises(ValueError):
        OrderLine(product=make_product(), n_cases=0, env=env)
