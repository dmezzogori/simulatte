from __future__ import annotations


from simulatte.job import Job
from simulatte.psp_policies.base import PSPReleasePolicy
from simulatte.psp import PreShopPool
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


class DummyPolicy(PSPReleasePolicy):
    def __init__(self) -> None:
        self.called = 0

    def release_condition(self, psp: PreShopPool, shopfloor: ShopFloor) -> bool:  # noqa: ARG002
        self.called += 1
        return True


def test_psp_add_remove_sets_exit_time() -> None:
    server = Server(capacity=1)
    job = Job(family="A", servers=[server], processing_times=[1], due_date=5)
    psp = PreShopPool()

    assert len(psp) == 0
    psp.add(job)
    assert len(psp) == 1

    removed = psp.remove()
    assert removed is job
    assert job.psp_exit_at == psp.env.now


def test_psp_main_invokes_policy() -> None:
    server = Server(capacity=1)
    job = Job(family="A", servers=[server], processing_times=[1], due_date=5)
    policy = DummyPolicy()
    psp = PreShopPool(check_timeout=0.1, psp_release_policy=policy)

    psp.add(job)
    psp.env.run(until=0.3)

    assert policy.called == 1  # policy invoked while job present
    assert len(psp) == 0
    assert job in psp.shopfloor.jobs or job in psp.shopfloor.jobs_done


def test_psp_signal_new_job_triggers_event() -> None:
    server = Server(capacity=1)
    job = Job(family="A", servers=[server], processing_times=[1], due_date=5)
    psp = PreShopPool()

    events = []

    def listener():
        while True:
            j = yield psp.new_job
            events.append(j)

    psp.env.process(listener())
    # Prime the listener so it is waiting on the current new_job event
    psp.env.run(until=0.0001)
    psp.add(job)
    psp.env.run(until=0.1)

    assert events == [job]
