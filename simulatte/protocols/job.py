from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

from .identifiable import Identifiable
from .timed import Timed


class Job(Identifiable, Timed, Iterable, Protocol):
    """
    A protocol for a job.

    Attributes:
        id: job id
        sub_jobs: sequence of sub jobs to be completed in order to complete the job
        parent: the job that has this job as sub job
        prev: the job that should be processed before this job
        next: the job that should be processed after this job
        workload: a characterization of the amount of work required to process the job
    """

    sub_jobs: Sequence[Job] | None

    parent: Job | None
    prev: Job | None
    next: Job | None

    workload: int
    remaining_workload: int

    def __iter__(self):
        return iter(self.sub_jobs)

    def __enter__(self) -> Job:
        self.started()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.completed()
        self.remaining_workload -= self.workload
