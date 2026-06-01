from __future__ import annotations

import random

from simulatte.builders import (
    build_continuous_release_system,
    build_conwip_system,
    build_starvation_avoidance_system,
)
from simulatte.environment import Environment


def test_build_conwip_system_runs_and_caps_wip() -> None:
    random.seed(42)
    peak = 0
    with Environment() as env:
        psp, servers, shop_floor, router = build_conwip_system(env, wip_cap=8)

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


def test_build_continuous_release_system_runs() -> None:
    random.seed(42)
    with Environment() as env:
        psp, servers, shop_floor, router = build_continuous_release_system(env, wl_norm_level=6.0, allowance_factor=2)
        env.run(until=1000.0)

    assert psp is not None
    assert len(servers) == 6
    assert len(shop_floor.jobs_done) > 0


def test_build_starvation_avoidance_system_runs() -> None:
    random.seed(42)
    with Environment() as env:
        psp, servers, shop_floor, router = build_starvation_avoidance_system(env)
        env.run(until=1000.0)

    assert psp is not None
    assert len(servers) == 6
    # Starvation-only release keeps the first server fed, so some jobs finish.
    assert len(shop_floor.jobs_done) > 0
