from __future__ import annotations

from simulatte.builders import (
    MaterialSystemBuilder,
    build_immediate_release_system,
    build_lumscor_system,
    build_slar_system,
)
from simulatte.environment import Environment
from simulatte.psp import PreShopPool


class TestBuildImmediateReleaseSystem:
    """Tests for the build_immediate_release_system function."""

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


class TestMaterialSystemBuilder:
    """Tests for the MaterialSystemBuilder class."""

    def test_build_default(self) -> None:
        env = Environment()
        shopfloor, servers, warehouse, agvs, coordinator = MaterialSystemBuilder.build(env)

        assert shopfloor is not None
        assert len(servers) == 6  # Default n_servers
        assert warehouse is not None
        assert len(agvs) == 3  # Default n_agvs
        assert coordinator is not None
        assert shopfloor.material_coordinator is coordinator

    def test_build_with_custom_params(self) -> None:
        env = Environment()
        shopfloor, servers, warehouse, agvs, coordinator = MaterialSystemBuilder.build(
            env,
            n_servers=4,
            n_agvs=2,
            n_bays=3,
            products=["X", "Y"],
            initial_inventory={"X": 500, "Y": 200},
            pick_time=2.0,
            put_time=1.0,
            travel_time=3.0,
            collect_time_series=True,
            retain_job_history=True,
        )

        assert len(servers) == 4
        assert len(agvs) == 2
        assert warehouse.capacity == 3
        # Verify time series enabled
        assert servers[0]._qt is not None
        assert agvs[0]._qt is not None
