from __future__ import annotations

import random

from simulatte.builders import build_conwip_system, build_continuous_release_system
from simulatte.environment import Environment


def test_build_conwip_system_runs_and_caps_wip() -> None:
    random.seed(42)
    with Environment() as env:
        psp, servers, shop_floor, router = build_conwip_system(env, wip_cap=8)
        env.run(until=1000.0)

    assert psp is not None
    assert len(servers) == 6
    assert len(shop_floor.jobs_done) > 0
    # ConWIP caps concurrent shop jobs at wip_cap.
    assert shop_floor.maximum_shopfloor_jobs <= 8


def test_build_continuous_release_system_runs() -> None:
    random.seed(42)
    with Environment() as env:
        psp, servers, shop_floor, router = build_continuous_release_system(env, wl_norm_level=6.0, allowance_factor=2)
        env.run(until=1000.0)

    assert psp is not None
    assert len(servers) == 6
    assert len(shop_floor.jobs_done) > 0
