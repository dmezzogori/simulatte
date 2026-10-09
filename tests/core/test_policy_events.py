"""Release-policy decision events and the job location they leave behind (spec §5.2, §6.1, §6.4)."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest

from simulatte.builders import (
    build_continuous_release_system,
    build_conwip_system,
    build_draco_system,
    build_lumscor_system,
    build_slar_limit_system,
    build_slar_system,
)
from simulatte.environment import Environment
from simulatte.events import DomainEvent, Event, apply_deltas
from simulatte.job import ProductionJob
from simulatte.policies import ConWIP, ContinuousRelease, Draco, LumsCor, PolicyDecision, Slar
from simulatte.psp import PreShopPool, PspExited
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


class ReplayChecker:
    """Applies the deltas of every domain event and compares the result with the live registry."""

    def __init__(self, env: Environment) -> None:
        self.env = env
        self.state: dict[str, dict[str, Any]] = {}
        self.checked: list[str] = []
        env.bus.subscribe(self, "*")

    def __call__(self, event: DomainEvent) -> None:
        apply_deltas(self.state, event.deltas)
        assert self.state == self.env.entities.snapshot(), event
        self.checked.append(event.type_name)


def _record(env: Environment, types: Any = "*") -> list[Event]:
    seen: list[Event] = []
    env.bus.subscribe(seen.append, types)
    return seen


def _decisions(seen: list[Event]) -> list[tuple[str, str, str]]:
    return [(e.policy, e.job, e.action) for e in seen if isinstance(e, PolicyDecision)]


def _location(job: ProductionJob) -> Any:
    return job.snapshot()["location"]


def _shop(env: Environment) -> tuple[ShopFloor, Server, PreShopPool]:
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    return sf, server, PreShopPool(env=env, shopfloor=sf)


def _job(env: Environment, server: Server, *, time: float, due: float) -> ProductionJob:
    return ProductionJob(env=env, sku="A", servers=[server], processing_times=[time], due_date=due)


def test_slar_postpone_events() -> None:
    env = Environment(debug=True)
    replay = ReplayChecker(env)
    seen = _record(env)
    sf, server, psp = _shop(env)
    Slar(shopfloor=sf, psp=psp, router=Mock(), allowance_factor=2)

    job1 = _job(env, server, time=2.0, due=10.0)
    job2 = _job(env, server, time=2.0, due=15.0)
    sf.add(job1)
    sf.add(job2)
    env.run(until=0.01)
    job3 = _job(env, server, time=1.0, due=20.0)
    psp.add(job3)
    assert _location(job3) == f"psp:{psp.id}"

    # job1 finishes at t=2: one job left in the queue, so job3 is released after the 0.001 delay.
    env.run(until=2.0005)
    assert _location(job3) == "transit"
    assert job3 not in psp
    assert _decisions(seen) == [("Slar", job3.id, "postpone")]
    exits = [e for e in seen if isinstance(e, PspExited)]
    assert [(e.job, e.reason, e.t) for e in exits] == [(job3.id, "postponed", 2.0)]

    env.run(until=3)
    assert _location(job3) != "transit"
    assert replay.state == env.entities.snapshot()
    assert "policy.decision" in replay.checked
    assert replay.checked.index("policy.decision") < replay.checked.index(
        "psp.exited", replay.checked.index("psp.entered")
    )


def test_slar_release_actions() -> None:
    """Idle prevention is a plain release: psp.exited(released), never null-location."""
    env = Environment(debug=True)
    replay = ReplayChecker(env)
    seen = _record(env)
    sf, server, psp = _shop(env)
    Slar(shopfloor=sf, psp=psp, router=Mock(), allowance_factor=2)

    job1 = _job(env, server, time=1.0, due=10.0)
    sf.add(job1)
    env.run(until=0.01)
    job2 = _job(env, server, time=1.0, due=20.0)
    psp.add(job2)
    env.run(until=1.0005)

    assert _decisions(seen) == [("Slar", job2.id, "release")]
    assert [(e.job, e.reason) for e in seen if isinstance(e, PspExited)] == [(job2.id, "released")]
    assert _location(job2) in {f"queue:{server.id}", f"server:{server.id}"}
    env.run()
    assert replay.state == env.entities.snapshot()


def test_slar_limit_policy_id_is_the_subclass_name() -> None:
    env = Environment(seed=3, debug=True)
    replay = ReplayChecker(env)
    seen = _record(env, (PolicyDecision,))
    build_slar_limit_system(env=env, allowance_factor=2.0, wl_norm_level=6.0)
    env.run(until=150)

    decisions = [e for e in seen if isinstance(e, PolicyDecision)]
    assert decisions
    assert {e.policy for e in decisions} == {"SlarLimit"}
    assert replay.state == env.entities.snapshot()


def test_lumscor_periodic_release_events() -> None:
    env = Environment(debug=True)
    replay = ReplayChecker(env)
    seen = _record(env)
    sf, server, psp = _shop(env)
    LumsCor(shopfloor=sf, psp=psp, router=Mock(), wl_norm=100.0, check_timeout=1.0, allowance_factor=2)

    blocker = _job(env, server, time=50.0, due=1000.0)
    sf.add(blocker)
    env.run(until=0.01)
    late = _job(env, server, time=2.0, due=30.0)
    early = _job(env, server, time=2.0, due=10.0)
    psp.add(late)
    psp.add(early)
    assert _decisions(seen) == []

    env.run(until=1.5)  # the periodic check at t=1 releases both, earliest planned release date first

    assert _decisions(seen) == [("LumsCor", early.id, "release"), ("LumsCor", late.id, "release")]
    exits = [e for e in seen if isinstance(e, PspExited)]
    assert [(e.job, e.reason) for e in exits] == [(early.id, "released"), (late.id, "released")]
    assert _location(early) != "transit" and _location(late) != "transit"
    env.run(until=100)
    assert replay.state == env.entities.snapshot()


def test_lumscor_starvation_release_and_postpone_events() -> None:
    env = Environment(debug=True)
    replay = ReplayChecker(env)
    seen = _record(env)
    sf, server, psp = _shop(env)
    LumsCor(shopfloor=sf, psp=psp, router=Mock(), wl_norm=100.0, check_timeout=10_000.0, allowance_factor=2)

    first = _job(env, server, time=2.0, due=10.0)
    second = _job(env, server, time=2.0, due=15.0)
    sf.add(first)
    sf.add(second)
    env.run(until=0.01)
    held = _job(env, server, time=1.0, due=20.0)
    psp.add(held)

    # first completes at t=2: one job remains queued, so the held job is postponed by 0.001.
    env.run(until=2.0005)
    assert _decisions(seen) == [("LumsCor", held.id, "postpone")]
    assert _location(held) == "transit"
    assert [(e.job, e.reason) for e in seen if isinstance(e, PspExited)] == [(held.id, "postponed")]

    env.run(until=100)
    assert replay.state == env.entities.snapshot()

    # second completes with the server idle: a release candidate starting there is released outright.
    env2 = Environment(debug=True)
    replay2 = ReplayChecker(env2)
    seen2 = _record(env2)
    sf2, server2, psp2 = _shop(env2)
    LumsCor(shopfloor=sf2, psp=psp2, router=Mock(), wl_norm=100.0, check_timeout=10_000.0, allowance_factor=2)
    sf2.add(_job(env2, server2, time=1.0, due=10.0))
    env2.run(until=0.01)
    waiting = _job(env2, server2, time=1.0, due=20.0)
    psp2.add(waiting)
    env2.run(until=1.0005)
    assert _decisions(seen2) == [("LumsCor", waiting.id, "release")]
    assert [(e.job, e.reason) for e in seen2 if isinstance(e, PspExited)] == [(waiting.id, "released")]
    env2.run(until=100)
    assert replay2.state == env2.entities.snapshot()


def test_draco_force_pin_event() -> None:
    env = Environment(debug=True)
    replay = ReplayChecker(env)
    seen = _record(env)
    sf, s1, psp = _shop(env)
    draco = Draco(
        shopfloor=sf, router=Mock(), psp=psp, wip_target=100, loop_target=5, total_impact_weights=(0.8, 0.1, 0.1)
    )
    blocker = _job(env, s1, time=1000.0, due=10000.0)
    queued = _job(env, s1, time=2.0, due=10000.0)
    sf.add(blocker)
    sf.add(queued)
    env.run(until=0.001)
    psp_cand = _job(env, s1, time=1.0, due=10000.0)
    psp.add(psp_cand)

    draco.decide_next_job(blocker, s1)  # the PSP candidate wins: pinned, then released

    assert _decisions(seen) == [("Draco", psp_cand.id, "force_pin"), ("Draco", psp_cand.id, "release")]
    assert [(e.job, e.reason) for e in seen if isinstance(e, PspExited)] == [(psp_cand.id, "released")]
    assert _location(psp_cand) == "transit"
    assert replay.state == env.entities.snapshot()


def test_draco_queue_winner_is_only_pinned() -> None:
    env = Environment(debug=True)
    replay = ReplayChecker(env)
    seen = _record(env)
    sf, s1, psp = _shop(env)
    # tau=1 with two floor jobs: the shop is over target, so ro^Q dominates and the queued job wins.
    draco = Draco(
        shopfloor=sf, router=Mock(), psp=psp, wip_target=1, loop_target=5, total_impact_weights=(0.8, 0.1, 0.1)
    )
    blocker = _job(env, s1, time=1000.0, due=10000.0)
    queued = _job(env, s1, time=2.0, due=10000.0)
    sf.add(blocker)
    sf.add(queued)
    env.run(until=0.001)
    psp_cand = _job(env, s1, time=1.0, due=10000.0)
    psp.add(psp_cand)

    draco.decide_next_job(blocker, s1)

    assert _decisions(seen) == [("Draco", queued.id, "force_pin")]
    assert psp_cand in psp
    assert not [e for e in seen if isinstance(e, PspExited)]
    assert replay.state == env.entities.snapshot()


def test_conwip_release_event() -> None:
    env = Environment(debug=True)
    replay = ReplayChecker(env)
    seen = _record(env)
    sf, server, psp = _shop(env)
    ConWIP(shopfloor=sf, psp=psp, wip_cap=2)

    job1 = _job(env, server, time=1.0, due=10.0)
    blocker = _job(env, server, time=50.0, due=100.0)
    sf.add(job1)
    sf.add(blocker)
    env.run(until=0.01)
    held = _job(env, server, time=1.0, due=15.0)
    psp.add(held)
    assert _decisions(seen) == []

    env.run(until=2)  # job1 finishes at t=1: the completion release frees the slot

    assert _decisions(seen) == [("ConWIP", held.id, "release")]
    assert [(e.job, e.reason) for e in seen if isinstance(e, PspExited)] == [(held.id, "released")]
    assert replay.state == env.entities.snapshot()

    # On arrival with room under the cap: the arrival callback releases at once.
    arriving = _job(env, server, time=1.0, due=15.0)
    env.run(until=60)  # blocker done, WIP below cap
    psp.add(arriving)
    assert _decisions(seen)[-1] == ("ConWIP", arriving.id, "release")
    env.run()
    assert replay.state == env.entities.snapshot()


def test_continuous_release_event() -> None:
    env = Environment(debug=True)
    replay = ReplayChecker(env)
    seen = _record(env)
    sf, server, psp = _shop(env)
    ContinuousRelease(shopfloor=sf, psp=psp, wl_norm={server: 100.0}, allowance_factor=2)

    first = _job(env, server, time=1.0, due=10.0)
    sf.add(first)
    env.run(until=0.01)
    held = _job(env, server, time=1.0, due=20.0)
    psp.add(held)  # first server busy: stays in the pool
    assert _decisions(seen) == []

    env.run(until=1.5)  # first completes: the completion release fits the norms

    assert _decisions(seen) == [("ContinuousRelease", held.id, "release")]
    assert [(e.job, e.reason) for e in seen if isinstance(e, PspExited)] == [(held.id, "released")]

    # Arrival at an idle first server: released on arrival.
    env.run(until=5)
    arriving = _job(env, server, time=1.0, due=30.0)
    psp.add(arriving)
    assert _decisions(seen)[-1] == ("ContinuousRelease", arriving.id, "release")
    env.run()
    assert replay.state == env.entities.snapshot()


@pytest.mark.parametrize(
    "build",
    [
        lambda env: build_slar_system(env=env, allowance_factor=2.0),
        lambda env: build_slar_limit_system(env=env, allowance_factor=2.0, wl_norm_level=6.0),
        lambda env: build_lumscor_system(env=env, check_timeout=5.0, wl_norm_level=6.0, allowance_factor=2),
        lambda env: build_draco_system(env=env, wip_target=8, loop_target=4),
        lambda env: build_conwip_system(env=env, wip_cap=6),
        lambda env: build_continuous_release_system(env=env, wl_norm_level=6.0),
    ],
    ids=["slar", "slar_limit", "lumscor", "draco", "conwip", "continuous"],
)
def test_no_release_leaves_a_null_location(build: Any) -> None:
    """Every pool exit of a built policy is a release or a postponement, so jobs are in transit, never null."""
    env = Environment(seed=11, debug=True)
    replay = ReplayChecker(env)
    seen = _record(env, (PspExited,))
    build(env)
    env.run(until=150)

    exits = [e for e in seen if isinstance(e, PspExited)]
    assert exits
    assert {e.reason for e in exits} <= {"released", "postponed"}
    assert replay.state == env.entities.snapshot()


def test_decision_events_not_built_without_subscribers() -> None:
    """Behind env.wants: a run with no subscriber to the type builds no PolicyDecision."""
    env = Environment(seed=11)
    assert not env.wants(PolicyDecision)
    build_slar_system(env=env, allowance_factor=2.0)
    env.run(until=50)
