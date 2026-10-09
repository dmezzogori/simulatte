"""Fleet activation: deferred submissions and cancellations, PENDING_ACTIVATION, initial placement (spec §10)."""

from __future__ import annotations

import pytest

from simulatte.entities import EntityCreated
from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVState
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.order import OrderStatus, TransferOrder
from simulatte.intralogistics.traffic import ResourceBasedTrafficManager

from tests.intralogistics.test_entities import SKU_A, _agv_type, _line_graph, _system


def test_pending_activation_status() -> None:
    env = Environment()
    created: list[EntityCreated] = []
    env.bus.subscribe(created.append, (EntityCreated,))
    coordinator, (agv,), wh_a, wh_b, _ = _system(env)
    first = coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b)
    second = TransferOrder(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b, created_at=0.0)

    assert first.id == "order-0" and first.status is OrderStatus.PENDING  # attached at once (R18)
    assert created[-1].entity == first.id and created[-1].ordinal is None  # a prelude event
    assert coordinator.submit(first) is None
    coordinator.submit(second)
    assert second.id == "order-1"  # attached at submit
    assert first.status is second.status is OrderStatus.PENDING_ACTIVATION
    assert coordinator.pending_count == 0 and agv.state is AGVState.IDLE  # nothing ran yet

    env.activate()
    assert env.initial_state[first.id]["status"] == "PENDING_ACTIVATION"
    assert env.initial_state[second.id]["status"] == "PENDING_ACTIVATION"
    assert first.status is OrderStatus.DISPATCHED and first.dispatched_at == 0.0
    assert second.status is OrderStatus.PENDING and coordinator.snapshot()["pending"] == [second.id]

    third = coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b)
    coordinator.submit(third)  # after activation: runs at once
    assert third.status is OrderStatus.PENDING

    env.run()
    assert all(order.status is OrderStatus.COMPLETED for order in (first, second, third))


def test_submit_then_cancel_before_run_cancels() -> None:
    env = Environment()
    coordinator, (agv,), wh_a, wh_b, _ = _system(env)
    order = coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b)
    queued = coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b)
    coordinator.submit(order)
    coordinator.submit(queued)
    assert coordinator.cancel(order) is None
    coordinator.cancel(queued)
    assert order.status is queued.status is OrderStatus.PENDING_ACTIVATION

    env.run()  # submissions, then cancellations, in call order at time 0
    assert order.status is queued.status is OrderStatus.CANCELLED
    assert order.picked_at is None and wh_a.get_inventory_level(SKU_A) == 100
    assert not env.entities.is_live(order) and not env.entities.is_live(queued)
    assert agv.state is AGVState.IDLE and agv.order is None


def test_placement_conflict_raises_at_activation() -> None:
    env = Environment()
    nodes, graph = _line_graph()
    agvs = [AGV(env=env, agv_type=_agv_type(), initial_node=nodes[1]) for _ in range(2)]
    traffic = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=1)
    FleetCoordinator(env=env, graph=graph, fleet=agvs, warehouses=[], charging_stations=[], traffic_manager=traffic)

    with pytest.raises(RuntimeError, match="cannot place agv-1 at node C1"):
        env.run()
    assert traffic._node_resources[nodes[1]].count == 1


def test_placement_after_activation_runs_at_construction() -> None:
    env = Environment()
    env.activate()
    nodes, graph = _line_graph()
    agv = AGV(env=env, agv_type=_agv_type(), initial_node=nodes[2])
    traffic = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=1)
    FleetCoordinator(env=env, graph=graph, fleet=[agv], warehouses=[], charging_stations=[], traffic_manager=traffic)
    assert traffic._node_resources[nodes[2]].count == 1
    assert env.entities.bind_node(nodes[2]).reserved_by == [agv.id]
