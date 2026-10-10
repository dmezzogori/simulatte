"""Intralogistics time parameters as managed bindings (spec §8.2)."""

from __future__ import annotations

import random
from collections.abc import Callable

import pytest
from simpy.events import ProcessGenerator

from simulatte.distributions import Exponential, Uniform
from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVType
from simulatte.intralogistics.charging import ChargingStation
from simulatte.intralogistics.graph import Node
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.warehouse import Warehouse
from simulatte.rng import derive_seed

SKU_A = SKU(id="SKU-A", weight=1.0, volume=0.1)
SEED = 17


def _agv_type(**kwargs: object) -> AGVType:
    return AGVType(
        name="test",
        speed_profile=TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0),
        battery_capacity=100.0,
        weight_capacity=100.0,
        volume_capacity=10.0,
        **kwargs,  # ty: ignore[invalid-argument-type]
    )


def _warehouse(env: Environment, name: str = "WH", **kwargs: object) -> Warehouse:
    bay = Node(id=f"{name}-BAY", x=0.0, y=0.0)
    return Warehouse(
        env=env,
        name=name,
        input_bays=[bay],
        output_bays=[bay],
        n_slots=1,
        products=[SKU_A],
        initial_inventory={SKU_A: 10},
        **kwargs,  # ty: ignore[invalid-argument-type]
    )


def _station(env: Environment, **kwargs: object) -> ChargingStation:
    return ChargingStation(env=env, name="CS", node=Node(id="CS-NODE", x=0.0, y=0.0), n_slots=1, **kwargs)  # ty: ignore[invalid-argument-type]


def _stream(name: str) -> random.Random:
    return random.Random(derive_seed(SEED, name))


def test_numbers_are_managed() -> None:
    env = Environment(seed=SEED)
    agv = AGV(env=env, agv_type=_agv_type(load_time=2, unload_time=1.5))
    warehouse = _warehouse(env, pick_time=3, put_time=4.5)
    station = _station(env, recharge_time=7)

    assert env.opaque_sampler_owners == []
    assert (agv.sample_load_time(), agv.sample_unload_time()) == (2.0, 1.5)
    assert isinstance(agv.sample_load_time(), float)

    def process() -> ProcessGenerator:
        yield from warehouse.pick(SKU_A, 1)
        assert env.now == 3.0
        yield from warehouse.put(SKU_A, 1)
        assert env.now == 7.5
        agv.battery.level = 10.0
        yield from station.recharge(agv, target_pct=1.0)  # the context (levels) is ignored
        assert env.now == 14.5

    env.process(process())
    env.run()
    assert env.now == 14.5
    assert env.opaque_sampler_owners == []


def test_defaults_are_managed_zero_and_rate_based_recharge() -> None:
    env = Environment(seed=SEED)
    agv = AGV(env=env, agv_type=_agv_type())
    assert (agv.sample_load_time(), agv.sample_unload_time()) == (0.0, 0.0)
    station = _station(env)  # no recharge_time: the battery's own rate-based computation
    agv.battery.level = 50.0

    env.process(station.recharge(agv, target_pct=1.0))
    env.run()
    assert env.now == pytest.approx(50.0)  # (target - current) * 1.0, the battery's default rate
    assert env.opaque_sampler_owners == []


def test_contextual_callable_keeps_signature_and_is_opaque() -> None:
    env = Environment(seed=SEED)
    calls: list[tuple[object, ...]] = []

    def pick(sku: SKU, qty: int) -> float:
        calls.append(("pick", sku, qty))
        return 2.0

    def put(sku: SKU, qty: int) -> float:
        calls.append(("put", sku, qty))
        return 1.0

    def recharge(current_level: float, target_level: float) -> float:
        calls.append(("recharge", current_level, target_level))
        return 5.0

    def load() -> float:
        calls.append(("load",))
        return 0.5

    agv = AGV(env=env, agv_type=_agv_type(load_time=load, unload_time=0.25))
    warehouse = _warehouse(env, pick_time=pick, put_time=put)
    station = _station(env, recharge_time=recharge)
    assert env.opaque_sampler_owners == [agv.id, warehouse.id, station.id]

    def process() -> ProcessGenerator:
        yield from warehouse.pick(SKU_A, 3)
        yield from warehouse.put(SKU_A, 2)
        agv.battery.level = 40.0
        yield from station.recharge(agv, target_pct=1.0)

    env.process(process())
    env.run()
    assert agv.sample_load_time() == 0.5 and agv.sample_unload_time() == 0.25
    assert calls == [("pick", SKU_A, 3), ("put", SKU_A, 2), ("recharge", 40.0, 100.0), ("load",)]
    assert env.now == 8.0


def test_distribution_streams_per_entity() -> None:
    env = Environment(seed=SEED)
    agv_type = _agv_type(load_time=Uniform(1.0, 9.0), unload_time=Exponential(0.5))
    first = AGV(env=env, agv_type=agv_type)
    second = AGV(env=env, agv_type=agv_type)
    warehouse = _warehouse(env, "WH-X", pick_time=Uniform(1.0, 2.0), put_time=Uniform(3.0, 4.0))
    station = _station(env, recharge_time=Uniform(10.0, 20.0))
    assert env.opaque_sampler_owners == []

    def draws(sample: Callable[[], float], n: int = 5) -> list[float]:
        return [sample() for _ in range(n)]

    expected_load = {agv.id: Uniform(1.0, 9.0).sampler(_stream(f"{agv.id}/load")) for agv in (first, second)}
    expected_unload = {agv.id: Exponential(0.5).sampler(_stream(f"{agv.id}/unload")) for agv in (first, second)}
    for agv in (first, second):
        assert draws(agv.sample_load_time) == draws(expected_load[agv.id])
        assert draws(agv.sample_unload_time) == draws(expected_unload[agv.id])
    assert first.id != second.id

    # Same description, different AGVs: independent streams.
    env_b = Environment(seed=SEED)
    a, b = AGV(env=env_b, agv_type=agv_type), AGV(env=env_b, agv_type=agv_type)
    assert draws(a.sample_load_time) != draws(b.sample_load_time)

    pick = Uniform(1.0, 2.0).sampler(_stream("WH-X/pick"))
    put = Uniform(3.0, 4.0).sampler(_stream("WH-X/put"))
    recharge = Uniform(10.0, 20.0).sampler(_stream("CS/recharge"))
    expected = [pick(), put()]

    def process() -> ProcessGenerator:
        yield from warehouse.pick(SKU_A, 1)
        yield from warehouse.put(SKU_A, 1)
        first.battery.level = 0.0
        yield from station.recharge(first, target_pct=1.0)

    env.process(process())
    env.run()
    assert env.now == pytest.approx(sum(expected) + recharge())
