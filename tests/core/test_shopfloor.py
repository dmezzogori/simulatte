from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.job import Job
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


def test_single_job_processing() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = Job(env=env, family="A", servers=[server], processing_times=[5], due_date=10)
    sf.add(job)

    assert job in sf.jobs
    assert not sf.jobs_done
    assert sf.wip[server] == pytest.approx(5)

    env.run()

    assert job.done
    assert job.psp_exit_at == pytest.approx(0)
    assert job.finished_at == pytest.approx(5)
    assert job in sf.jobs_done
    assert sf.wip[server] == pytest.approx(0)

    assert server.worked_time == pytest.approx(5)
    assert server.utilization_rate == pytest.approx(1.0)
    assert server.idle_time == pytest.approx(0.0)


def test_multiple_jobs_sequential_processing_and_queue() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job1 = Job(env=env, family="A", servers=[server], processing_times=[3], due_date=10)
    job2 = Job(env=env, family="B", servers=[server], processing_times=[4], due_date=10)
    sf.add(job1)
    sf.add(job2)

    assert sf.wip[server] == pytest.approx(7)
    assert server.count == 0

    env.run()

    assert job1.done
    assert job2.done
    assert job1.finished_at == pytest.approx(3)
    assert job2.finished_at == pytest.approx(7)
    assert sf.jobs_done == [job1, job2]
    assert sf.wip[server] == pytest.approx(0)

    assert server.worked_time == pytest.approx(7)
    assert server.average_queue_length == (1 * 3 + 0 * 4) / 7
    assert server.utilization_rate == 1


def test_parallel_processing_with_capacity() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=2, shopfloor=sf)
    job1 = Job(env=env, family="A", servers=[server], processing_times=[5], due_date=10)
    job2 = Job(env=env, family="B", servers=[server], processing_times=[5], due_date=10)
    sf.add(job1)
    sf.add(job2)

    assert sf.wip[server] == pytest.approx(10)

    env.run()

    assert job1.finished_at == pytest.approx(5)
    assert job2.finished_at == pytest.approx(5)
    assert env.now == pytest.approx(5)

    assert server.worked_time == pytest.approx(10)
    assert server.utilization_rate == pytest.approx(2.0)


def test_enable_corrected_wip() -> None:
    env = Environment()
    shopfloor = ShopFloor(env=env)
    server1 = Server(env=env, capacity=1, shopfloor=shopfloor)
    server2 = Server(env=env, capacity=1, shopfloor=shopfloor)
    server3 = Server(env=env, capacity=1, shopfloor=shopfloor)
    shopfloor.enable_corrected_wip = True
    job1 = Job(env=env, family="A", servers=[server1, server2], processing_times=[2, 3], due_date=10)
    job2 = Job(env=env, family="B", servers=[server2, server3], processing_times=[4, 5], due_date=10)
    shopfloor.add(job1)
    shopfloor.add(job2)

    assert shopfloor.wip[server1] == 2
    assert shopfloor.wip[server2] == 5.5
    assert shopfloor.wip[server3] == 2.5

    env.run(until=shopfloor.job_processing_end)
    assert job1.current_server == server2
    assert job1.remaining_routing == ()

    assert shopfloor.wip[server1] == 0
    assert shopfloor.wip[server2] == 7
    assert shopfloor.wip[server3] == 2.5

    env.run(until=shopfloor.job_processing_end)
    assert job2.current_server == server3
    assert job2.remaining_routing == ()

    assert shopfloor.wip[server1] == 0
    assert shopfloor.wip[server2] == 3
    assert shopfloor.wip[server3] == 5

    env.run(until=shopfloor.job_processing_end)
    assert job1.done

    assert shopfloor.wip[server1] == 0
    assert shopfloor.wip[server2] == 0
    assert shopfloor.wip[server3] == 5

    env.run()
    assert job2.done

    assert shopfloor.wip[server1] == 0
    assert shopfloor.wip[server2] == 0
    assert shopfloor.wip[server3] == 0
