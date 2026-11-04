"""Rates and public result materialization preserve the frozen model contracts."""

from itertools import permutations

import pytest

from simulatte import Environment
from simulatte.stopping import Horizon, execute_until
from prototypes.native_identity_usage import plant, projection
from prototypes.native_identity_usage.common import assert_copies
from prototypes.native_identity_usage.run import schedule
from prototypes.native_identity_usage.measurement import prepare_operations
from simulatte.experimental.native import NativeProduction


def test_sources_stay_frozen():
    assert_copies()


def test_primary_schedule_is_paired_and_order_balanced():
    cases = [r for r in schedule() if not r["disabled"]]
    assert len(cases) == 144
    for fixture in ("fixed", "coupled"):
        for every in (0, 64, 16, 1):
            blocks = [
                [r for r in cases if (r["fixture"], r["every"], r["seed"]) == (fixture, every, seed)]
                for seed in range(100, 106)
            ]
            assert {tuple(r["mode"] for r in block) for block in blocks} == set(
                permutations(("helper", "prepared", "unprepared"))
            )


def simulate(path, fixture, retention):
    path.mkdir()
    with Environment(seed=100, artifacts_dir=path) as env:
        policy = Horizon(96 if fixture == "fixed" else 80, warmup=10)
        env._execution_policy = policy
        env.configure_kpis(warmup=10)
        env.enable_digest()
        plant.build(env, None, plant.Params(fixture=fixture, engine=retention))
        execute_until(env, policy)
        fingerprint = env.fingerprint()
    return fingerprint, {p.name: p.read_bytes() for p in path.iterdir() if p.is_file()}


@pytest.mark.parametrize("fixture", ["fixed", "coupled"])
@pytest.mark.parametrize("retention", ["full", "bounded"])
def test_all_three_consumers_and_rates_have_exact_artifacts(tmp_path, monkeypatch, fixture, retention):
    pytest.importorskip("simulatte_native")
    results = []
    for mode in ("helper", "prepared", "unprepared"):
        for every in (0, 64, 16, 1):
            monkeypatch.setenv("SIMULATTE_PROJECTION_MODE", mode)
            monkeypatch.setenv("SIMULATTE_IDENTITIES_QUERY_EVERY", str(every))
            results.append(simulate(tmp_path / f"{mode}-{every}", fixture, retention))
    assert all(r == results[0] for r in results)


@pytest.mark.parametrize("retention", ["full", "bounded"])
def test_measured_query_operations_are_read_only(retention):
    pytest.importorskip("simulatte_native")
    state = dict(seed=1)
    with Environment() as env:
        env._execution_policy = Horizon(10)
        env.configure_kpis()
        backend = NativeProduction(1, retention=retention, input_state=lambda: state)
        env.attach_execution_backend(backend, scope="shopfloor")
        env.activate()
        before = backend.state_digest()
        actions = projection.Actions().state()
        operations, context = prepare_operations(backend, lambda: state, actions)
        outputs = {name: operation() for name, operation in operations.items()}
        assert outputs["helper_pipeline"] == outputs["prepared_pipeline"] == outputs["unprepared_pipeline"]
        assert context["native_bytes"] > 0 and backend.state_digest() == before
        assert state == dict(seed=1)
