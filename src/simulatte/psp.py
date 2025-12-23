"""Pre-shop pool for job release control."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from simulatte.environment import Environment
from simulatte.shopfloor import ShopFloor

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable

    from simulatte.job import ProductionJob
    from simulatte.typing import ProcessGenerator

    from simulatte.psp_policies.base import PSPReleasePolicy


class PreShopPool:
    """Holds jobs before they enter the shopfloor; releases by policy."""

    def __init__(
        self,
        *,
        env: Environment,
        shopfloor: ShopFloor,
        check_timeout: float = 1,
        psp_release_policy: PSPReleasePolicy | None = None,
    ) -> None:
        self.env = env
        self.shopfloor = shopfloor
        self._check_timeout = check_timeout
        self.psp_release_policy = psp_release_policy

        self._psp: deque[ProductionJob] = deque()
        self.new_job = self.env.event()

        if check_timeout > 0 and psp_release_policy is not None:
            self.env.process(self.main())

    def __len__(self) -> int:
        return len(self._psp)

    def __contains__(self, job: ProductionJob) -> bool:
        return job in self._psp

    def __getitem__(self, index: int) -> ProductionJob:
        return self._psp[index]

    @property
    def empty(self) -> bool:
        return not self._psp

    @property
    def jobs(self) -> Iterable[ProductionJob]:
        yield from self._psp

    def main(self) -> ProcessGenerator:
        while True:
            yield self.env.timeout(self._check_timeout)
            if self._psp and self.psp_release_policy is not None:
                self.psp_release_policy.release(self, self.shopfloor)

    def add(self, job: ProductionJob) -> None:
        self._psp.append(job)
        self._signal_new_job(job)

    def remove(self, *, job: ProductionJob | None = None) -> ProductionJob:
        if job is not None:
            if job not in self._psp:
                raise ValueError(f"{job} not found in the pre-shop pool.")
            self._psp.remove(job)
        else:
            job = self._psp.popleft()
        job.psp_exit_at = self.env.now
        return job

    def _signal_new_job(self, job: ProductionJob) -> None:
        self.new_job.succeed(job)
        self.new_job = self.env.event()
