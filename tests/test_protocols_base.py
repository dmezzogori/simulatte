from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.policies.agv_selection_policy.base import AGVSelectionPolicy, MultiAGVSelectionPolicy
from simulatte.protocols.timed import Timed
from simulatte.protocols.job import Job
from simulatte.utils import EnvMixin


class DummyTimed(EnvMixin, Timed):
    def __init__(self, env: Environment):
        EnvMixin.__init__(self, env=env)
        self._start_time = None
        self._end_time = None


class ConcreteJob(Job, DummyTimed):
    def __init__(self, env: Environment, workload: int = 2):
        DummyTimed.__init__(self, env=env)
        self.id = 1
        self.workload = workload
        self.remaining_workload = workload
        self.sub_jobs: list[Job] | None = []
        self.parent = None
        self.prev = None
        self.next = None


def test_agv_selection_policy_not_implemented():
    policy = AGVSelectionPolicy()
    with pytest.raises(NotImplementedError):
        policy(agvs=[])

    multi_policy = MultiAGVSelectionPolicy()
    with pytest.raises(NotImplementedError):
        multi_policy(agvs=[])


def test_timed_lead_time_and_job_context_manager(env):
    job = ConcreteJob(env=env, workload=3)

    assert job.lead_time is None

    assert list(iter(job)) == []

    job.started()
    job.env.run(until=5)
    job.completed()

    assert job._start_time is not None and job._end_time is not None
    assert job.lead_time == job._end_time - job._start_time

    with job:
        pass
    assert job.remaining_workload == 0
