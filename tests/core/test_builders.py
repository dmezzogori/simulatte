from __future__ import annotations

import random

import pytest

from simulatte.builders import (
    build_continuous_release_system,
    build_conwip_system,
    build_draco_system,
    build_focus_system,
    build_immediate_release_system,
    build_lumscor_system,
    build_slar_limit_system,
    build_slar_system,
    build_starvation_avoidance_system,
)
from simulatte.dispatching_rules import shortest_processing_time
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.policies.continuous_release import ContinuousRelease
from simulatte.policies.conwip import ConWIP
from simulatte.policies.draco import Draco
from simulatte.policies.lumscor import LumsCor
from simulatte.policies.slar import Slar
from simulatte.policies.slar_limit import SlarLimit
from simulatte.psp import PreShopPool
from simulatte.scenario import Scenario


class TestBuildImmediateReleaseSystem:
    """Tests for the build_immediate_release_system function."""

    def test_shortest_processing_time_returns_processing_time(self) -> None:
        env = Environment()
        _, servers, _, _, _ = build_immediate_release_system(env=env, scenario=Scenario(n_servers=1))
        server = servers[0]
        job = ProductionJob(env=env, sku="F1", servers=[server], processing_times=[2.5], due_date=100.0)
        assert shortest_processing_time(job, server) == 2.5

    def test_build_immediate_release_system_with_shortest_processing_time(self) -> None:
        env = Environment()
        _, _, _, router, _ = build_immediate_release_system(
            env=env, scenario=Scenario(n_servers=3), priority_policies=shortest_processing_time
        )
        assert router.priority_policies is shortest_processing_time

    def test_build_immediate_release_system_basic(self) -> None:
        env = Environment()
        psp, servers, shop_floor, router, _ = build_immediate_release_system(env=env, scenario=Scenario(n_servers=3))

        assert psp is None
        assert len(servers) == 3
        assert shop_floor is not None
        assert router is not None
        assert all(server.env is env for server in servers)

    def test_build_immediate_release_system_with_options(self) -> None:
        env = Environment()
        psp, servers, shop_floor, router, _ = build_immediate_release_system(
            env=env,
            scenario=Scenario(n_servers=2, arrival_rate=0.5),
            collect_time_series=True,
            retain_job_history=True,
        )

        assert psp is None
        assert len(servers) == 2
        # Verify time series collection is enabled
        assert servers[0]._qt is not None
        assert servers[0]._ut is not None
        # Verify job history retention is enabled
        assert servers[0]._jobs is not None


class TestPullSystemBuilders:
    """Tests for the pull system builder functions."""

    def test_build_lumscor_system(self) -> None:
        env = Environment()
        psp, servers, shop_floor, router, _ = build_lumscor_system(
            env=env,
            check_timeout=10.0,
            wl_norm_level=5.0,
            allowance_factor=2,
        )

        assert isinstance(psp, PreShopPool)
        assert router.psp is psp
        assert len(servers) == 6  # Default n_servers
        assert shop_floor is not None
        assert router is not None

    def test_build_slar_system(self) -> None:
        env = Environment()
        psp, servers, shop_floor, router, _ = build_slar_system(
            env=env,
            allowance_factor=3,
        )

        assert isinstance(psp, PreShopPool)
        assert router.psp is psp
        assert len(servers) == 6  # Default n_servers
        assert shop_floor is not None
        assert router is not None

    def test_build_slar_limit_system(self) -> None:
        from simulatte.shopfloor import CorrectedWIPStrategy

        env = Environment()
        psp, servers, shop_floor, router, _ = build_slar_limit_system(
            env=env,
            allowance_factor=3.0,
            wl_norm_level=5.0,
        )

        assert isinstance(psp, PreShopPool)
        assert router.psp is psp
        assert len(servers) == 6  # Default n_servers
        assert shop_floor is not None
        assert router is not None
        # SLAR-Limit requires CorrectedWIPStrategy
        assert isinstance(shop_floor.wip_strategy, CorrectedWIPStrategy)

    def test_build_conwip_system_runs_and_caps_wip(self) -> None:
        random.seed(42)
        peak = 0
        with Environment() as env:
            psp, servers, shop_floor, router, _ = build_conwip_system(env=env, wip_cap=8)

            def _sample_wip():
                nonlocal peak
                while True:
                    peak = max(peak, len(shop_floor.jobs))
                    yield env.timeout(0.5)

            env.process(_sample_wip())
            env.run(until=1000.0)

        assert psp is not None
        assert len(servers) == 6
        assert len(shop_floor.jobs_done) > 0
        assert peak > 0, "sampler never observed jobs on the floor"
        assert peak <= 8, f"ConWIP exceeded its WIP cap: peak={peak}"

    def test_build_continuous_release_system_runs(self) -> None:
        random.seed(42)
        with Environment() as env:
            psp, servers, shop_floor, router, _ = build_continuous_release_system(
                env=env, wl_norm_level=6.0, allowance_factor=2
            )
            env.run(until=1000.0)

        assert psp is not None
        assert len(servers) == 6
        assert len(shop_floor.jobs_done) > 0

    def test_build_starvation_avoidance_system_runs(self) -> None:
        random.seed(42)
        with Environment() as env:
            psp, servers, shop_floor, router, _ = build_starvation_avoidance_system(env=env)
            env.run(until=1000.0)

        assert psp is not None
        assert len(servers) == 6
        # Starvation-only release keeps the first server fed, so some jobs finish.
        assert len(shop_floor.jobs_done) > 0


class TestScenarioShopTypes:
    """Builders compose with non-default Scenario shop-type presets."""

    def test_immediate_release_on_pure_job_shop(self) -> None:
        random.seed(42)
        with Environment() as env:
            psp, servers, shop_floor, router, _ = build_immediate_release_system(
                env=env, scenario=Scenario.pure_job_shop()
            )
            env.run(until=1000.0)

        assert psp is None  # immediate-release push baseline
        assert len(servers) == 6
        assert len(shop_floor.jobs_done) > 0
        # Pure Job Shop: routing length U[1, M], undirected.
        for job in shop_floor.jobs_done:
            assert 1 <= len(job.servers) <= len(servers)
            assert len(set(job.servers)) == len(job.servers)  # no re-entry

    def test_immediate_release_on_general_flow_shop(self) -> None:
        random.seed(42)
        with Environment() as env:
            psp, servers, shop_floor, router, _ = build_immediate_release_system(
                env=env, scenario=Scenario.general_flow_shop()
            )
            env.run(until=1000.0)

        assert psp is None
        assert len(servers) == 6
        assert len(shop_floor.jobs_done) > 0
        # General Flow Shop: variable length, but each routing is directed (ascending index).
        saw_partial_routing = False
        for job in shop_floor.jobs_done:
            indices = [servers.index(s) for s in job.servers]
            assert indices == sorted(indices)
            assert len(set(indices)) == len(indices)
            saw_partial_routing = saw_partial_routing or len(indices) < len(servers)
        assert saw_partial_routing  # at least some orders skip stations (length < M)

    def test_immediate_release_on_pure_flow_shop(self) -> None:
        random.seed(42)
        with Environment() as env:
            psp, servers, shop_floor, router, _ = build_immediate_release_system(
                env=env, scenario=Scenario.pure_flow_shop()
            )
            env.run(until=1000.0)

        assert psp is None
        assert len(servers) == 6
        # Pure Flow Shop is stable only because the arrival rate is derived for E[L]=M;
        # if it naively reused the job-shop rate (1/0.641) the queue would explode.
        assert len(shop_floor.jobs_done) > 0
        for job in shop_floor.jobs_done:
            assert list(job.servers) == list(servers)

    def test_immediate_release_pure_job_shop_with_twk_due_dates(self) -> None:
        random.seed(42)
        k = 8.74  # FOCUS pure-job-shop allowance factor (6 work centres)
        with Environment() as env:
            psp, servers, shop_floor, router, _ = build_immediate_release_system(
                env=env, scenario=Scenario.single(twk_allowance_factor=k)
            )
            env.run(until=1000.0)

        assert len(shop_floor.jobs_done) > 0
        for job in shop_floor.jobs_done:
            expected_due = job.created_at + k * sum(job.processing_times)
            assert job.due_date == pytest.approx(expected_due)

    def test_lumscor_runs_on_general_flow_shop(self) -> None:
        random.seed(42)
        with Environment() as env:
            psp, servers, sf, router, _ = build_lumscor_system(
                env=env,
                scenario=Scenario.general_flow_shop(),
                check_timeout=10.0,
                wl_norm_level=6.0,
                allowance_factor=2,
            )
            env.run(until=1000.0)
        assert len(sf.jobs_done) > 0
        for job in sf.jobs_done:
            idx = [servers.index(s) for s in job.servers]
            assert idx == sorted(idx)


class TestCollectWorkload:
    """Tests for the collect_workload parameter on all builder functions."""

    def test_build_immediate_release_collect_workload_false(self) -> None:
        from simulatte.shopfloor import CurrentWorkLoadCollector

        env = Environment()
        _, _, shop_floor, _, _ = build_immediate_release_system(env=env, scenario=Scenario(n_servers=2))
        assert not isinstance(shop_floor.time_series_collector, CurrentWorkLoadCollector)

    def test_build_immediate_release_collect_workload_true(self) -> None:
        from simulatte.shopfloor import CurrentWorkLoadCollector

        env = Environment()
        _, _, shop_floor, _, _ = build_immediate_release_system(
            env=env, scenario=Scenario(n_servers=2), collect_workload=True
        )
        assert isinstance(shop_floor.time_series_collector, CurrentWorkLoadCollector)

    def test_build_lumscor_collect_workload_true(self) -> None:
        from simulatte.shopfloor import CurrentWorkLoadCollector

        env = Environment()
        _, _, shop_floor, _, _ = build_lumscor_system(
            env=env, check_timeout=10.0, wl_norm_level=5.0, allowance_factor=2, collect_workload=True
        )
        assert isinstance(shop_floor.time_series_collector, CurrentWorkLoadCollector)

    def test_build_slar_collect_workload_true(self) -> None:
        from simulatte.shopfloor import CurrentWorkLoadCollector

        env = Environment()
        _, _, shop_floor, _, _ = build_slar_system(env=env, allowance_factor=3.0, collect_workload=True)
        assert isinstance(shop_floor.time_series_collector, CurrentWorkLoadCollector)

    def test_build_slar_limit_collect_workload_true(self) -> None:
        from simulatte.shopfloor import CurrentWorkLoadCollector

        env = Environment()
        _, _, shop_floor, _, _ = build_slar_limit_system(
            env=env,
            allowance_factor=3.0,
            wl_norm_level=5.0,
            collect_workload=True,
        )
        assert isinstance(shop_floor.time_series_collector, CurrentWorkLoadCollector)


class TestBuiltSystemPolicyField:
    """The fifth ``policy`` field exposes the wired release/dispatching policy."""

    def test_lumscor_returns_policy_instance(self) -> None:
        env = Environment()
        result = build_lumscor_system(env=env, check_timeout=10.0, wl_norm_level=5.0, allowance_factor=2)
        assert isinstance(result.policy, LumsCor)
        # Named-field access works alongside positional unpacking.
        assert result.router is result[3]
        assert result.policy is result[4]

    def test_slar_returns_policy_instance(self) -> None:
        env = Environment()
        result = build_slar_system(env=env, allowance_factor=3.0)
        assert isinstance(result.policy, Slar)

    def test_slar_limit_returns_policy_instance(self) -> None:
        env = Environment()
        result = build_slar_limit_system(env=env, allowance_factor=3.0, wl_norm_level=5.0)
        assert isinstance(result.policy, SlarLimit)

    def test_draco_returns_policy_instance(self) -> None:
        env = Environment()
        result = build_draco_system(env=env, wip_target=8, loop_target=4)
        assert isinstance(result.policy, Draco)

    def test_conwip_returns_policy_instance(self) -> None:
        env = Environment()
        result = build_conwip_system(env=env, wip_cap=8)
        assert isinstance(result.policy, ConWIP)

    def test_continuous_release_returns_policy_instance(self) -> None:
        env = Environment()
        result = build_continuous_release_system(env=env, wl_norm_level=6.0)
        assert isinstance(result.policy, ContinuousRelease)

    def test_immediate_release_has_no_policy(self) -> None:
        env = Environment()
        result = build_immediate_release_system(env=env)
        assert result.policy is None

    def test_focus_has_no_policy(self) -> None:
        env = Environment()
        result = build_focus_system(env=env)
        # FOCUS dispatching stays reachable via the router, not as a policy object.
        assert result.policy is None
        assert result.router.priority_policies is not None

    def test_starvation_avoidance_has_no_policy(self) -> None:
        env = Environment()
        result = build_starvation_avoidance_system(env=env)
        assert result.policy is None

    def test_five_target_unpacking_works(self) -> None:
        env = Environment()
        psp, servers, shop_floor, router, policy = build_conwip_system(env=env, wip_cap=8)
        assert isinstance(psp, PreShopPool)
        assert len(servers) == 6
        assert shop_floor is not None
        assert router is not None
        assert isinstance(policy, ConWIP)
