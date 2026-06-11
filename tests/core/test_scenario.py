from __future__ import annotations

import random

import pytest

from simulatte.distributions import (
    Deterministic,
    Erlang,
    Exponential,
    TruncatedErlang,
    general_flow_shop_routing,
    pure_job_shop_routing,
)
from simulatte.environment import Environment
from simulatte.psp import PreShopPool
from simulatte.scenario import Scenario, ShopType, SkuFamily
from simulatte.shopfloor import ShopFloor


def test_default_scenario_is_pure_job_shop() -> None:
    s = Scenario()
    assert s.shop_type is ShopType.PJS
    assert s.n_servers == 6
    assert s.target_utilization == 0.90
    assert len(s.families) == 1 and s.families[0].name == "F1"


def test_presets_select_shop_type() -> None:
    assert Scenario.pure_job_shop().shop_type is ShopType.PJS
    assert Scenario.general_flow_shop().shop_type is ShopType.GFS
    assert Scenario.pure_flow_shop(n_servers=12).n_servers == 12


def test_derived_rate_uses_true_truncated_mean() -> None:
    # The derivation now uses the TRUE truncated mean (≈0.989), not the nominal 1.0,
    # so the classic literature constants (0.648 PJS, 1.111 PFS) shift by ≈1%.
    e_p = TruncatedErlang(rate=2.0, shape=2, max_value=4.0).mean
    expected_pjs = 3.5 * e_p / (0.9 * 6)
    expected_pfs = 6.0 * e_p / (0.9 * 6)
    assert 1 / Scenario.pure_job_shop().resolved_arrival_rate() == pytest.approx(expected_pjs)
    assert 1 / Scenario.pure_flow_shop().resolved_arrival_rate() == pytest.approx(expected_pfs)


def test_explicit_arrival_rate_overrides_derivation() -> None:
    assert Scenario(arrival_rate=2.0).resolved_arrival_rate() == 2.0


def test_two_family_mix_weighted_derivation() -> None:
    fast = SkuFamily(name="A", weight=3.0, service_time=Erlang(rate=4.0, shape=2))  # E[p]=0.5
    slow = SkuFamily(name="B", weight=1.0, service_time=Erlang(rate=2.0, shape=2))  # E[p]=1.0
    s = Scenario.pure_flow_shop(families=(fast, slow))  # E[L]=n_servers=6 for both
    expected_work = (3 / 4) * 6 * 0.5 + (1 / 4) * 6 * 1.0
    assert s.resolved_arrival_rate() == pytest.approx(0.9 * 6 / expected_work)


def test_single_convenience_builds_one_family() -> None:
    s = Scenario.single(service_time=Erlang(rate=3.0, shape=2), shop_type=ShopType.PJS, n_servers=4)
    assert s.shop_type is ShopType.PJS and s.n_servers == 4
    assert len(s.families) == 1 and isinstance(s.families[0].service_time, Erlang)


def test_duplicate_family_names_raise() -> None:
    with pytest.raises(ValueError, match="unique"):
        Scenario(families=(SkuFamily(name="X"), SkuFamily(name="X")))


def test_invalid_scenario_configurations_raise() -> None:
    with pytest.raises(ValueError, match="at least one SkuFamily"):
        Scenario(families=())
    with pytest.raises(ValueError, match="n_servers"):
        Scenario(n_servers=0)
    with pytest.raises(ValueError, match="target_utilization"):
        Scenario(target_utilization=1.5)


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


def test_build_router_rejects_fewer_servers_than_n_servers() -> None:
    with Environment() as env:
        scenario = Scenario(n_servers=6)
        sf, _ = scenario.build_floor(env)
        servers = Scenario(n_servers=3).build_floor(env)[1]
        with pytest.raises(ValueError, match=r"!= Scenario\.n_servers"):
            scenario.build_router(env, sf, servers, psp=None)


def test_build_router_rejects_more_servers_than_n_servers() -> None:
    with Environment() as env:
        scenario = Scenario(n_servers=3)
        sf, _ = scenario.build_floor(env)
        servers = Scenario(n_servers=6).build_floor(env)[1]
        with pytest.raises(ValueError, match=r"!= Scenario\.n_servers"):
            scenario.build_router(env, sf, servers, psp=None)


def test_build_router_applies_twk_due_dates() -> None:
    random.seed(42)
    k = 8.0
    with Environment() as env:
        scenario = Scenario.single(shop_type=ShopType.PJS, twk_allowance_factor=k)
        sf, servers = scenario.build_floor(env)
        psp = PreShopPool(env=env, shopfloor=sf)
        scenario.build_router(env, sf, servers, psp=psp)
        env.run(until=50.0)
        job = next(iter(psp.jobs))
        assert job.due_date == pytest.approx(job.created_at + k * sum(job.processing_times))


def test_build_router_runs_two_family_mix() -> None:
    random.seed(42)
    families = (SkuFamily(name="A", weight=1.0), SkuFamily(name="B", weight=1.0))
    with Environment() as env:
        scenario = Scenario.pure_job_shop(families=families)
        sf, servers = scenario.build_floor(env)
        scenario.build_router(env, sf, servers, psp=None)
        env.run(until=500.0)
        skus = {job.sku for job in sf.jobs_done}
        assert skus == {"A", "B"}


def test_skufamily_defaults() -> None:
    f = SkuFamily()
    assert f.name == "F1"
    assert f.weight == 1.0
    assert isinstance(f.service_time, TruncatedErlang)
    assert f.service_time.mean == pytest.approx(0.989232, abs=1e-4)


def test_skufamily_mean_routing_length_inherits_shop_type() -> None:
    f = SkuFamily()
    assert f.mean_routing_length(ShopType.PJS, n_servers=6) == 3.5
    assert f.mean_routing_length(ShopType.GFS, n_servers=6) == 3.5
    assert f.mean_routing_length(ShopType.PFS, n_servers=6) == 6.0


def test_skufamily_routing_override_requires_expected_length() -> None:
    with pytest.raises(ValueError, match="expected_routing_length"):
        SkuFamily(routing_factory=pure_job_shop_routing)
    f = SkuFamily(routing_factory=pure_job_shop_routing, expected_routing_length=2.0)
    assert f.mean_routing_length(ShopType.PFS, n_servers=6) == 2.0  # override wins over shop type
    assert f.routing_for(ShopType.PFS) is pure_job_shop_routing


def test_skufamily_routing_for_inherits_shop_type() -> None:
    assert SkuFamily().routing_for(ShopType.GFS) is general_flow_shop_routing
    assert SkuFamily().routing_for(ShopType.PJS) is pure_job_shop_routing


def test_skufamily_weight_must_be_positive() -> None:
    with pytest.raises(ValueError, match="weight"):
        SkuFamily(weight=0.0)
    with pytest.raises(ValueError, match="weight"):
        SkuFamily(weight=-1.0)


def test_skufamily_expected_routing_length_must_be_positive() -> None:
    with pytest.raises(ValueError, match="expected_routing_length"):
        SkuFamily(expected_routing_length=0.0)
    with pytest.raises(ValueError, match="expected_routing_length"):
        SkuFamily(expected_routing_length=-1.0)


def test_family_due_date_offset_overrides_scenario_default() -> None:
    from simulatte.distributions import Uniform

    family_offset = Uniform(low=5.0, high=10.0)
    s = Scenario(families=(SkuFamily(name="F1", due_date_offset=family_offset),))
    with Environment() as env:
        sf, servers = s.build_floor(env)
        router = s.build_router(env, sf, servers, psp=None)
    assert router.due_date_offset_distribution["F1"] is family_offset


def test_custom_arrival_process_is_wired() -> None:
    seen: list[float] = []

    def record_process(rate: float) -> Exponential:
        seen.append(rate)
        return Exponential(rate)

    s = Scenario(arrival_process=record_process, arrival_rate=1.5)
    with Environment() as env:
        sf, servers = s.build_floor(env)
        s.build_router(env, sf, servers, psp=None)
    assert seen == [1.5]


def test_two_family_mix_respects_weight_proportions() -> None:
    # Gap A: build_router wires sku_distributions={f.name: f.weight} into
    # random.choices(weights=...). A 3:1 mix must produce ≈75% A-jobs. We route
    # jobs into a PSP with NO release policy so EVERY generated job accumulates
    # there (no completion bias), and count by SKU over the generated stream.
    #
    # Power: pin arrival_rate so n ≈ 2500 arrivals are generated cheaply (jobs
    # never enter a server). At n=2000 the binomial std-dev of the A-fraction is
    # sqrt(0.75 * 0.25 / 2000) ≈ 0.0097, so the ±0.03 band is ≈3σ around 0.75.
    # An inverted mix would centre on 0.25 — failing by (0.75-0.25)/0.0097 ≈ 50σ.
    random.seed(12345)
    families = (
        SkuFamily(name="A", weight=3.0, service_time=Deterministic(1.0)),
        SkuFamily(name="B", weight=1.0, service_time=Deterministic(1.0)),
    )
    with Environment() as env:
        scenario = Scenario.pure_job_shop(families=families, arrival_rate=10.0)
        sf, servers = scenario.build_floor(env)
        psp = PreShopPool(env=env, shopfloor=sf)  # no release policy: pool retains all arrivals
        scenario.build_router(env, sf, servers, psp=psp)
        env.run(until=250.0)  # mean inter-arrival 0.1 -> ~2500 arrivals

    skus = [job.sku for job in psp.jobs]
    n = len(skus)
    assert n >= 2000, f"need >=2000 generated jobs for the power claim, got {n}"
    a_fraction = skus.count("A") / n
    assert a_fraction == pytest.approx(0.75, abs=0.03)


def test_per_family_service_times_are_distinguishable() -> None:
    # Gap B: build_router wires sku_service_times={f.name: {server: f.service_time}}.
    # With Deterministic service times, every A-job's operation times must all be
    # exactly 1.0 and every B-job's exactly 3.0. This fails if the two families
    # were swapped or collapsed to families[0].service_time.
    random.seed(42)
    families = (
        SkuFamily(name="A", weight=1.0, service_time=Deterministic(1.0)),
        SkuFamily(name="B", weight=1.0, service_time=Deterministic(3.0)),
    )
    expected = {"A": 1.0, "B": 3.0}
    with Environment() as env:
        scenario = Scenario.pure_job_shop(families=families, arrival_rate=5.0)
        sf, servers = scenario.build_floor(env)
        psp = PreShopPool(env=env, shopfloor=sf)  # retain all arrivals for inspection
        scenario.build_router(env, sf, servers, psp=psp)
        env.run(until=50.0)

    jobs = list(psp.jobs)
    seen = {job.sku for job in jobs}
    assert seen == {"A", "B"}, f"expected both families generated, got {seen}"
    for job in jobs:
        assert all(p == expected[job.sku] for p in job.processing_times), (
            f"{job.sku} job had processing_times {job.processing_times}, expected all {expected[job.sku]}"
        )


def test_partial_twk_falls_back_to_flat_offset() -> None:
    # Gap C: only family A sets twk_allowance_factor, so build_router emits a
    # PARTIAL due_date_rule dict ({"A": ...}); family B has no entry and must
    # fall back to its flat due_date_offset. Router uses due_date_rule.get(sku)
    # (None -> offset path). With Deterministic service times the work content
    # and offset are exact, so:
    #   A-job: due_date - created_at == K * sum(processing_times)
    #   B-job: due_date - created_at == 7.0   (flat offset)
    # If the lookup were due_date_rule[sku] instead of .get(sku), the first
    # B-arrival would raise KeyError, which SimPy re-raises through env.run —
    # the run aborts before these assertions execute. The per-family guard
    # below additionally pins the contract (both families must appear) against
    # quieter mutations that drop B-jobs without raising.
    random.seed(42)
    k = 2.0
    families = (
        SkuFamily(name="A", weight=1.0, service_time=Deterministic(1.0), twk_allowance_factor=k),
        SkuFamily(name="B", weight=1.0, service_time=Deterministic(1.0), due_date_offset=Deterministic(7.0)),
    )
    with Environment() as env:
        scenario = Scenario.pure_job_shop(families=families, arrival_rate=5.0)
        sf, servers = scenario.build_floor(env)
        psp = PreShopPool(env=env, shopfloor=sf)  # retain all arrivals for inspection
        scenario.build_router(env, sf, servers, psp=psp)
        env.run(until=50.0)

    jobs = list(psp.jobs)
    by_sku = {"A": [j for j in jobs if j.sku == "A"], "B": [j for j in jobs if j.sku == "B"]}
    # Guard: require >=1 job of EACH family so the per-job loops below cannot
    # pass vacuously if a mutation drops one family from the generated stream.
    assert by_sku["A"], "no A-jobs generated"
    assert by_sku["B"], "no B-jobs generated"
    for job in by_sku["A"]:
        assert job.due_date - job.created_at == pytest.approx(k * sum(job.processing_times))
    for job in by_sku["B"]:
        assert job.due_date - job.created_at == pytest.approx(7.0)
