from __future__ import annotations

from simulatte.builders import (
    build_immediate_release_system,
    build_lumscor_system,
    build_slar_limit_system,
    build_slar_system,
    spt_priority_policy,
)
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.psp import PreShopPool


class TestBuildImmediateReleaseSystem:
    """Tests for the build_immediate_release_system function."""

    def test_spt_priority_policy_returns_processing_time(self) -> None:
        env = Environment()
        _, servers, _, _ = build_immediate_release_system(env, n_servers=1)
        server = servers[0]
        job = ProductionJob(env=env, sku="F1", servers=[server], processing_times=[2.5], due_date=100.0)
        assert spt_priority_policy(job, server) == 2.5

    def test_build_immediate_release_system_with_spt(self) -> None:
        env = Environment()
        _, _, _, router = build_immediate_release_system(env, n_servers=3, priority_policies=spt_priority_policy)
        assert router.priority_policies is spt_priority_policy

    def test_build_immediate_release_system_basic(self) -> None:
        env = Environment()
        psp, servers, shop_floor, router = build_immediate_release_system(env, n_servers=3)

        assert psp is None
        assert len(servers) == 3
        assert shop_floor is not None
        assert router is not None
        assert all(server.env is env for server in servers)

    def test_build_immediate_release_system_with_options(self) -> None:
        env = Environment()
        psp, servers, shop_floor, router = build_immediate_release_system(
            env,
            n_servers=2,
            arrival_rate=0.5,
            service_rate=2.0,
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
        psp, servers, shop_floor, router = build_lumscor_system(
            env,
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
        psp, servers, shop_floor, router = build_slar_system(
            env,
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
        psp, servers, shop_floor, router = build_slar_limit_system(
            env,
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


class TestCollectWorkload:
    """Tests for the collect_workload parameter on all builder functions."""

    def test_build_immediate_release_collect_workload_false(self) -> None:
        from simulatte.shopfloor import CurrentWorkLoadCollector

        env = Environment()
        _, _, shop_floor, _ = build_immediate_release_system(env, n_servers=2)
        assert not isinstance(shop_floor.time_series_collector, CurrentWorkLoadCollector)

    def test_build_immediate_release_collect_workload_true(self) -> None:
        from simulatte.shopfloor import CurrentWorkLoadCollector

        env = Environment()
        _, _, shop_floor, _ = build_immediate_release_system(env, n_servers=2, collect_workload=True)
        assert isinstance(shop_floor.time_series_collector, CurrentWorkLoadCollector)

    def test_build_lumscor_collect_workload_true(self) -> None:
        from simulatte.shopfloor import CurrentWorkLoadCollector

        env = Environment()
        _, _, shop_floor, _ = build_lumscor_system(
            env, check_timeout=10.0, wl_norm_level=5.0, allowance_factor=2, collect_workload=True
        )
        assert isinstance(shop_floor.time_series_collector, CurrentWorkLoadCollector)

    def test_build_slar_collect_workload_true(self) -> None:
        from simulatte.shopfloor import CurrentWorkLoadCollector

        env = Environment()
        _, _, shop_floor, _ = build_slar_system(env, allowance_factor=3.0, collect_workload=True)
        assert isinstance(shop_floor.time_series_collector, CurrentWorkLoadCollector)

    def test_build_slar_limit_collect_workload_true(self) -> None:
        from simulatte.shopfloor import CurrentWorkLoadCollector

        env = Environment()
        _, _, shop_floor, _ = build_slar_limit_system(
            env,
            allowance_factor=3.0,
            wl_norm_level=5.0,
            collect_workload=True,
        )
        assert isinstance(shop_floor.time_series_collector, CurrentWorkLoadCollector)
