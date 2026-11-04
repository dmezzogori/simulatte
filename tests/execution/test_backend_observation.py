"""Backend observation is framework-owned, bounded and optional."""

import pytest

from simulatte.events import KpiSample
from simulatte.execution.backend import BackendCapabilities
from simulatte.execution.observation import BackendObservation
from simulatte.kpi import KPI
from simulatte.stopping import execute_until
from tests.execution.test_backend import FixedBackend, environment


def test_framework_observations_use_one_snapshot_per_boundary_and_keep_scalars(tmp_path):
    backend = FixedBackend()
    backend.observation = BackendObservation(interval=0.5, max_samples=3)
    env, recorder = environment(backend, path=tmp_path / "observed.simtrace")
    rows = []
    env.bus.subscribe(lambda event: rows.append((event.t, event.value)), (KpiSample,))
    binding = env._execution_backend
    assert binding.description()["observation"]["scheduled_samples"] == 3
    try:
        execute_until(env, env._execution_policy)
        assert rows == [(0, 1), (1, 1), (2, 1)]
        assert binding.kpi_values() == {"constant": 1}
        with pytest.raises(RuntimeError, match="finalized"):
            binding.observe()
    finally:
        env.close()


def test_observation_query_failure_cannot_publish_or_mask_primary_exception():
    class Failed(FixedBackend):
        def kpi_values(self):
            if self.context.env.now > 0:
                raise MemoryError("primary observation failure")
            return super().kpi_values()

    backend = Failed()
    backend.observation = BackendObservation(interval=1)
    env, _ = environment(backend)
    env.bus.subscribe(lambda event: None, (KpiSample,))
    try:
        with pytest.raises(MemoryError, match="primary observation failure"):
            execute_until(env, env._execution_policy)
    finally:
        env.close()
    assert backend.closed == 1 and backend.finished == 0


def test_invalid_observation_and_experimental_flag_are_rejected():
    backend = FixedBackend()
    backend.observation = object()
    with pytest.raises(TypeError, match="observation"):
        environment(backend)
    assert backend.opened == 0
    with pytest.raises(ValueError, match="experimental"):
        BackendCapabilities("test", "v1", experimental=1)


def test_existing_series_declarations_are_not_duplicated():
    backend = FixedBackend()
    backend.kpis = (KPI("constant", unit="jobs", kind=("series", "scalar")),)
    backend.observation = BackendObservation(interval=1)
    env, _ = environment(backend)
    try:
        assert env.collectors[0].kpis == backend.kpis
    finally:
        env.close()
