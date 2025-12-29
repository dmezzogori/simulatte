"""Tests for the experimental MaterialSystemBuilder class."""

from __future__ import annotations

from simulatte.environment import Environment
from simulatte.experimental import MaterialSystemBuilder


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
