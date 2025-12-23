from __future__ import annotations

from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.psp import PreShopPool, PSPReleasePolicy
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


class DummyPolicy(PSPReleasePolicy):
    def __init__(self) -> None:
        self.called = 0

    def release_condition(self, psp: PreShopPool, shopfloor: ShopFloor) -> bool:  # noqa: ARG002
        self.called += 1
        return True


def test_psp_add_remove_sets_exit_time() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, family="A", servers=[server], processing_times=[1], due_date=5)
    psp = PreShopPool(env=env, shopfloor=sf)

    assert len(psp) == 0
    psp.add(job)
    assert len(psp) == 1

    removed = psp.remove()
    assert removed is job
    assert job.psp_exit_at == env.now


def test_psp_main_invokes_policy() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, family="A", servers=[server], processing_times=[1], due_date=5)
    policy = DummyPolicy()
    psp = PreShopPool(env=env, shopfloor=sf, check_timeout=0.1, psp_release_policy=policy)

    psp.add(job)
    env.run(until=0.3)

    assert policy.called == 1  # policy invoked while job present
    assert len(psp) == 0
    assert job in sf.jobs or job in sf.jobs_done


def test_psp_signal_new_job_triggers_event() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, family="A", servers=[server], processing_times=[1], due_date=5)
    psp = PreShopPool(env=env, shopfloor=sf)

    events = []

    def listener():
        while True:
            j = yield psp.new_job
            events.append(j)

    env.process(listener())
    # Prime the listener so it is waiting on the current new_job event
    env.run(until=0.0001)
    psp.add(job)
    env.run(until=0.1)

    assert events == [job]
