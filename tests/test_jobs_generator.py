from __future__ import annotations

from collections.abc import Iterator
from typing import cast

import pytest

from simulatte.demand.jobs_generator import JobsGenerator
from simulatte.demand.shift import Shift
from simulatte.products import ProductsGenerator
from simulatte.environment import Environment
from simulatte.protocols.distribution_callable import DistributionCallable
from simulatte.protocols.job import Job
from simulatte.utils import EnvMixin


class FakeJob(EnvMixin):
    def __init__(self, *, job_id: int, workload: int = 1, env: Environment) -> None:
        super().__init__(env=env)
        self.id = job_id
        self.workload = workload
        self.remaining_workload = workload
        self.sub_jobs: list[Job] | None = None
        self.parent = None
        self.prev = None
        self.next = None
        self._start_time = None
        self._end_time = None

    def __iter__(self):
        return iter(self.sub_jobs or [])

    def started(self) -> None:
        self._start_time = self.env.now

    def completed(self) -> None:
        self._end_time = self.env.now


class CounterDist(DistributionCallable[int]):
    def __init__(self, value: int):
        self.value = value
        self.calls = 0

    def __call__(self) -> int:  # type: ignore[override]
        self.calls += 1
        return self.value


class ConcreteJobsGenerator(JobsGenerator):
    def __init__(
        self,
        *,
        n_days: int,
        n_shift_per_day: int,
        orders_dist: DistributionCallable[int],
        jobs_dist: DistributionCallable[int],
    ) -> None:
        self.n_days = n_days
        self.n_shift_per_day = n_shift_per_day
        self.orders_per_shift_distribution = orders_dist
        self.jobs_per_order_distribution = jobs_dist
        self.products_generator = cast(ProductsGenerator, None)  # not used by this test
        self._shifts = None
        self._job_id = 0
        self.env = Environment()

    def _generate_jobs(self) -> Iterator[Job]:  # type: ignore[override]
        for _ in range(self.jobs_per_order_distribution()):
            job = FakeJob(job_id=self._job_id, env=self.env)
            self._job_id += 1
            yield job


@pytest.fixture()
def generator() -> ConcreteJobsGenerator:
    return ConcreteJobsGenerator(
        n_days=2,
        n_shift_per_day=2,
        orders_dist=CounterDist(2),
        jobs_dist=CounterDist(3),
    )


def test_jobs_generator_builds_shifts_and_caches(generator: ConcreteJobsGenerator):
    first_pass = tuple(generator)

    assert len(first_pass) == generator.n_days * generator.n_shift_per_day
    assert all(isinstance(shift, Shift) for shift in first_pass)
    orders_dist = cast(CounterDist, generator.orders_per_shift_distribution)
    jobs_dist = cast(CounterDist, generator.jobs_per_order_distribution)
    assert orders_dist.calls == generator.n_days * generator.n_shift_per_day
    assert jobs_dist.calls == orders_dist.calls * 2

    # Second pass should reuse cached shifts (no extra distribution calls)
    cache_id = id(generator._shifts)
    second_pass = tuple(generator)
    assert cache_id == id(generator._shifts)
    assert orders_dist.calls == generator.n_days * generator.n_shift_per_day
    assert jobs_dist.calls == orders_dist.calls * 2
    assert second_pass == first_pass


def test_shift_contents(generator: ConcreteJobsGenerator):
    shifts = tuple(generator)
    sample_shift = shifts[0]

    assert sample_shift.day == 0
    assert sample_shift.shift == 0
    orders_dist = cast(CounterDist, generator.orders_per_shift_distribution)
    jobs_dist = cast(CounterDist, generator.jobs_per_order_distribution)
    assert len(sample_shift.customer_orders) == orders_dist.value

    sample_orders = tuple(sample_shift.customer_orders)
    sample_order = sample_orders[0]
    assert len(sample_order.jobs) == jobs_dist.value
    assert all(isinstance(job, FakeJob) for job in sample_order.jobs)
