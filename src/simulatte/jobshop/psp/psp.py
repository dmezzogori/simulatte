"""Pre-shop pool for job release control."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from simulatte.jobshop.environment import Environment
from simulatte.jobshop.shopfloor import ShopFloor

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable

    from simulatte.jobshop.job import Job
    from simulatte.jobshop.typing import ProcessGenerator

    from .policies.base import PSPReleasePolicy


class PreShopPool:
    """Holds jobs before they enter the shopfloor; releases by policy."""

    def __init__(
        self,
        *,
        check_timeout: float = 1,
        psp_release_policy: PSPReleasePolicy | None = None,
    ) -> None:
        self.env = Environment()
        self.shopfloor = ShopFloor()
        self._check_timeout = check_timeout
        self.psp_release_policy = psp_release_policy

        self._psp: deque[Job] = deque()
        self.new_job = self.env.event()

        if check_timeout > 0 and psp_release_policy is not None:
            self.env.process(self.main())

    def __len__(self) -> int:
        return len(self._psp)

    def __contains__(self, job: Job) -> bool:
        return job in self._psp

    def __getitem__(self, index: int) -> Job:
        return self._psp[index]

    @property
    def empty(self) -> bool:
        return not self._psp

    @property
    def jobs(self) -> Iterable[Job]:
        yield from self._psp

    def main(self) -> ProcessGenerator:
        while True:
            yield self.env.timeout(self._check_timeout)
            if self._psp and self.psp_release_policy is not None:
                self.psp_release_policy.release(self, self.shopfloor)

    def add(self, job: Job) -> None:
        self._psp.append(job)
        self._signal_new_job(job)

    def remove(self, *, job: Job | None = None) -> Job:
        if job is not None:
            if job not in self._psp:
                raise ValueError(f"{job} not found in the pre-shop pool.")
            self._psp.remove(job)
        else:
            job = self._psp.popleft()
        job.psp_exit_at = self.env.now
        return job

    def _signal_new_job(self, job: Job) -> None:
        self.new_job.succeed(job)
        self.new_job = self.env.event()
