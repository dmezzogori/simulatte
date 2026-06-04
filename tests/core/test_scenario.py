from __future__ import annotations

import random

import pytest

from simulatte.distributions import general_flow_shop_routing, pure_flow_shop_routing, pure_job_shop_routing
from simulatte.environment import Environment
from simulatte.psp import PreShopPool
from simulatte.scenario import Scenario, ShopType
from simulatte.shopfloor import ShopFloor


def test_default_scenario_is_pure_job_shop() -> None:
    s = Scenario()
    assert s.shop_type is ShopType.PJS
    assert s.n_servers == 6
    assert s.target_utilization == 0.90


def test_presets_select_shop_type_and_routing() -> None:
    assert Scenario.pure_job_shop().shop_type is ShopType.PJS
    assert Scenario.general_flow_shop().shop_type is ShopType.GFS
    assert Scenario.pure_flow_shop(n_servers=12).shop_type is ShopType.PFS
    assert Scenario.pure_flow_shop(n_servers=12).n_servers == 12
    assert Scenario().routing_for() is not None


def test_mean_routing_length_per_shop_type() -> None:
    assert Scenario.pure_job_shop(n_servers=6).mean_routing_length == 3.5
    assert Scenario.general_flow_shop(n_servers=6).mean_routing_length == 3.5
    assert Scenario.pure_flow_shop(n_servers=6).mean_routing_length == 6.0


def test_derived_arrival_rate_matches_literature_constants() -> None:
    assert 1 / Scenario.pure_job_shop().resolved_arrival_rate() == pytest.approx(0.648, abs=1e-3)
    assert 1 / Scenario.pure_flow_shop().resolved_arrival_rate() == pytest.approx(1.111, abs=1e-3)


def test_explicit_arrival_rate_overrides_derivation() -> None:
    assert Scenario(arrival_rate=2.0).resolved_arrival_rate() == 2.0


def test_custom_routing_factory_requires_length_or_rate() -> None:
    custom = Scenario(routing_factory=pure_job_shop_routing, target_utilization=0.9)
    with pytest.raises(ValueError, match="expected_routing_length"):
        custom.resolved_arrival_rate()
    ok = Scenario(routing_factory=pure_job_shop_routing, expected_routing_length=3.5)
    assert ok.resolved_arrival_rate() > 0
    assert Scenario(routing_factory=pure_flow_shop_routing, arrival_rate=1.0).resolved_arrival_rate() == 1.0


def test_routing_for_maps_shop_type_to_factory() -> None:
    assert Scenario.pure_job_shop().routing_for() is pure_job_shop_routing
    assert Scenario.general_flow_shop().routing_for() is general_flow_shop_routing
    assert Scenario.pure_flow_shop().routing_for() is pure_flow_shop_routing


def test_build_floor_creates_servers() -> None:
    with Environment() as env:
        sf, servers = Scenario(n_servers=4).build_floor(env)
        assert isinstance(sf, ShopFloor)
        assert len(servers) == 4


def test_build_router_runs_pure_flow_shop_routing() -> None:
    random.seed(42)
    with Environment() as env:
        scenario = Scenario.pure_flow_shop(n_servers=6)
        sf, servers = scenario.build_floor(env)
        scenario.build_router(env, sf, servers, psp=None)
        env.run(until=200.0)
        assert len(sf.jobs_done) > 0
        for job in sf.jobs_done:
            assert list(job.servers) == list(servers)


def test_build_router_applies_twk_due_dates() -> None:
    random.seed(42)
    k = 8.0
    with Environment() as env:
        scenario = Scenario.pure_job_shop(twk_allowance_factor=k)
        sf, servers = scenario.build_floor(env)
        psp = PreShopPool(env=env, shopfloor=sf)
        scenario.build_router(env, sf, servers, psp=psp)
        env.run(until=50.0)
        job = next(iter(psp.jobs))
        assert job.due_date == pytest.approx(job.created_at + k * sum(job.processing_times))
