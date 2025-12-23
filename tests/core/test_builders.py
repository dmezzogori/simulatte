from __future__ import annotations

import pytest

from simulatte.builders import LiteratureSystemBuilder, MaterialSystemBuilder, build_push_system
from simulatte.environment import Environment
from simulatte.psp import PreShopPool


class TestBuildPushSystem:
    """Tests for the build_push_system function."""

    def test_build_push_system_basic(self) -> None:
        env = Environment()
        psp, servers, shop_floor, router = build_push_system(env, n_servers=3)

        assert psp is None
        assert len(servers) == 3
        assert shop_floor is not None
        assert router is not None
        assert all(server.env is env for server in servers)

    def test_build_push_system_with_options(self) -> None:
        env = Environment()
        psp, servers, shop_floor, router = build_push_system(
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


class TestLiteratureSystemBuilder:
    """Tests for the LiteratureSystemBuilder class."""

    def test_call_push_system(self) -> None:
        env = Environment()
        builder = LiteratureSystemBuilder()
        psp, servers, shop_floor, router = builder(env, "push")

        assert psp is None
        assert len(servers) == 6  # Default n_servers
        assert shop_floor is not None
        assert router is not None

    def test_call_lumscor_system(self) -> None:
        env = Environment()
        builder = LiteratureSystemBuilder()
        psp, servers, shop_floor, router = builder(
            env,
            "lumscor",
            check_timeout=10.0,
            wl_norm_level=5.0,
            allowance_factor=2,
        )

        assert isinstance(psp, PreShopPool)
        assert router.psp is psp
        assert len(servers) == 6
        assert shop_floor is not None
        assert router is not None

    def test_call_slar_system(self) -> None:
        env = Environment()
        builder = LiteratureSystemBuilder()
        psp, servers, shop_floor, router = builder(
            env,
            "slar",
            allowance_factor=3,
        )

        assert isinstance(psp, PreShopPool)
        assert router.psp is psp
        assert len(servers) == 6
        assert shop_floor is not None
        assert router is not None

    def test_call_invalid_system_raises(self) -> None:
        env = Environment()
        builder = LiteratureSystemBuilder()

        with pytest.raises(ValueError, match="Unknown system type"):
            builder(env, "invalid_system")  # type: ignore[arg-type]

    def test_build_system_push(self) -> None:
        env = Environment()
        psp, servers, shop_floor, router = LiteratureSystemBuilder.build_system_push(env)

        assert psp is None
        assert len(servers) == 6
        assert shop_floor is not None
        assert router is not None

    def test_build_system_lumscor(self) -> None:
        env = Environment()
        psp, servers, shop_floor, router = LiteratureSystemBuilder.build_system_lumscor(
            env,
            check_timeout=5.0,
            wl_norm_level=10.0,
            allowance_factor=2,
        )

        assert isinstance(psp, PreShopPool)
        assert router.psp is psp
        assert len(servers) == 6
        assert shop_floor is not None
        assert router is not None

    def test_build_system_slar(self) -> None:
        env = Environment()
        psp, servers, shop_floor, router = LiteratureSystemBuilder.build_system_slar(
            env,
            allowance_factor=2,
        )

        assert isinstance(psp, PreShopPool)
        assert router.psp is psp
        assert len(servers) == 6
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
