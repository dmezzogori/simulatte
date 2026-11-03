"""Feedback boundaries, live-handle cleanup and future-controller commitments."""

import gzip
import json

import pytest

from simulatte import Environment
from simulatte.experimental.native import NativeProduction
from simulatte.stopping import Horizon, execute_until
from prototypes.native_feedback_retention import plant
from prototypes.native_feedback_retention.analysis import validate_frames


def run(tmp_path, engine, *, seed=100, horizon=80, warmup=2, **kwargs):
    path = tmp_path / engine
    path.mkdir()
    params = plant.Params(engine=engine, **kwargs)
    with Environment(seed=seed, artifacts_dir=path) as env:
        policy = Horizon(horizon, warmup=warmup)
        env._execution_policy = policy
        env.configure_kpis(warmup=warmup)
        env.enable_digest()
        plant.build(env, None, params)
        execute_until(env, policy)
        fingerprint = env.fingerprint()
    physical = json.loads((path / "physical.json").read_text())
    with gzip.open(path / "frames.jsonl.gz", "rt") as file:
        frames = [json.loads(line) for line in file]
    validate_frames(frames, physical, vars(params), seed)
    return physical, (path / "frames.jsonl.gz").read_bytes(), fingerprint


@pytest.mark.parametrize("policy", ["fifo", "spt"])
@pytest.mark.parametrize("hard_limit,wip_cap", [(8, 2), (12, 4)])
@pytest.mark.parametrize("seed", [100, 101])
@pytest.mark.parametrize("horizon,warmup", [(0.25, 0), (1, 0), (17.25, 2), (80, 10)])
def test_every_boundary_matches_ordinary_production(tmp_path, policy, hard_limit, wip_cap, seed, horizon, warmup):
    values = [
        run(
            tmp_path,
            engine,
            policy=policy,
            hard_limit=hard_limit,
            wip_cap=wip_cap,
            seed=seed,
            horizon=horizon,
            warmup=warmup,
        )
        for engine in ("python", "full", "bounded")
    ]
    assert values[0][:2] == values[1][:2] == values[2][:2]
    assert values[0][2].kpis == values[1][2].kpis == values[2][2].kpis
    if horizon == 80 and seed == 100:
        state = values[0][0]["controller"]
        assert all(state[name] > 0 for name in ("short", "normal", "blocked", "tight", "relaxed", "pool_polls"))


def test_normal_priority_is_detected_at_a_constructed_completion_tie():
    import simpy

    def observe(priority):
        env, completed, seen = simpy.Environment(), [], []

        def work():
            yield env.timeout(1)
            completed.append(True)

        def poll():
            yield plant.wake(env, 0.5, priority=priority)
            yield plant.wake(env, 1, priority=priority)
            seen.append(bool(completed))

        env.process(work())
        env.process(poll())
        env.run(until=1.25)
        return seen

    assert observe(0) == [False]
    assert observe(1) == [True]


def test_disabling_feedback_changes_real_admissions(tmp_path):
    first = tmp_path / "on"
    first.mkdir()
    second = tmp_path / "off"
    second.mkdir()
    on = run(first, "bounded")[0]["controller"]
    off = run(second, "bounded", feedback=False)[0]["controller"]
    assert on["jobs_sha256"] != off["jobs_sha256"]
    assert on["decisions_sha256"] != off["decisions_sha256"]
    assert off["short"] == off["tight"] == 0


def test_pure_callback_commits_future_seed_and_wake(tmp_path):
    with Environment(seed=100, artifacts_dir=tmp_path) as env:
        env._execution_policy = Horizon(20)
        controller = plant.Controller(env, plant.Params(record=False))
        controller.adapter = NativeProduction(3, retention="bounded", input_state=controller.state)
        env.attach_execution_backend(controller.adapter)
        env.activate()
        before = controller.state()
        digest = controller.adapter.state_digest()
        assert controller.state() == before
        for field in ("seed", "next_at"):
            value = getattr(controller, field)
            setattr(controller, field, value + 1)
            assert controller.adapter.state_digest() != digest
            setattr(controller, field, value)
            assert controller.adapter.state_digest() == digest


def test_extra_queries_preserve_physical_and_semantic_output(tmp_path, monkeypatch):
    first = tmp_path / "plain"
    first.mkdir()
    second = tmp_path / "queried"
    second.mkdir()
    a = run(first, "bounded")
    monkeypatch.setenv("SIMULATTE_FEEDBACK_EXTRA_QUERIES", "1")
    b = run(second, "bounded")
    assert a == b
