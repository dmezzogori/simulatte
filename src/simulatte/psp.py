"""Pre-shop pool for job release control."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Protocol

from simulatte.environment import Environment
from simulatte.shopfloor import ShopFloor

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable

    from simulatte.job import ProductionJob
    from simulatte.typing import ProcessGenerator


class PSPReleasePolicy(Protocol):
    """Protocol defining the interface for pre-shop pool release policies.

    This protocol uses structural subtyping (duck typing). Any class implementing
    a `release` method with the required signature can be used as a release policy
    without explicitly inheriting from this protocol.

    Implementations control when and which jobs transition from the PSP to the
    shopfloor. Common strategies include workload-based release (LumsCor) and
    planned slack time ordering (SLAR).
    """

    def release(self, psp: PreShopPool, shopfloor: ShopFloor) -> None:
        """Release eligible jobs from the pre-shop pool to the shopfloor.

        Args:
            psp: The pre-shop pool containing jobs awaiting release.
            shopfloor: The target shopfloor to receive released jobs.
        """
        ...


class PreShopPool:
    """Buffer queue for controlling job release in pull-based production systems.

    The pre-shop pool (PSP) holds jobs before they enter the shopfloor, enabling
    controlled release based on configurable policies. This implements the input
    control mechanism commonly used in workload control and order release research.

    Jobs are added to the pool and released to the shopfloor either periodically
    via the configured release policy, or through event-driven triggers. The pool
    provides a `new_job` event that external processes can monitor to react
    immediately when jobs arrive (e.g., for starvation avoidance).
    """

    def __init__(
        self,
        *,
        env: Environment,
        shopfloor: ShopFloor,
        check_timeout: float = 1,
        psp_release_policy: PSPReleasePolicy | None = None,
    ) -> None:
        """Initialize the pre-shop pool.

        Args:
            env: The simulation environment.
            shopfloor: The shopfloor that will receive released jobs.
            check_timeout: Interval in simulation time units between periodic
                release policy invocations. Set to 0 to disable periodic checking
                (useful for purely event-driven release strategies).
            psp_release_policy: Optional policy controlling job release decisions.
                If provided along with check_timeout > 0, a background process
                will periodically invoke the policy's release method.
        """
        self.env = env
        self.shopfloor = shopfloor
        self._check_timeout = check_timeout
        self.psp_release_policy = psp_release_policy

        self._psp: deque[ProductionJob] = deque()
        self.new_job = self.env.event()

        if check_timeout > 0 and psp_release_policy is not None:
            self.env.process(self.main())

    def __len__(self) -> int:
        """Return the number of jobs currently in the pool."""
        return len(self._psp)

    def __contains__(self, job: ProductionJob) -> bool:
        """Check if a job is currently in the pool."""
        return job in self._psp

    def __getitem__(self, index: int) -> ProductionJob:
        """Get a job by its position in the queue (0 = oldest)."""
        return self._psp[index]

    @property
    def empty(self) -> bool:
        """Whether the pool contains no jobs."""
        return not self._psp

    @property
    def jobs(self) -> Iterable[ProductionJob]:
        """Iterate over jobs in the pool in FIFO order (oldest first)."""
        yield from self._psp

    def main(self) -> ProcessGenerator:
        """Periodic release policy invocation process.

        This SimPy process runs continuously, invoking the release policy at
        regular intervals defined by `check_timeout`. It only runs if both
        `check_timeout > 0` and a `psp_release_policy` were provided at init.

        Returns:
            A SimPy process generator that yields timeout events.
        """
        while True:
            yield self.env.timeout(self._check_timeout)
            if self._psp and self.psp_release_policy is not None:
                self.psp_release_policy.release(self, self.shopfloor)

    def add(self, job: ProductionJob) -> None:
        """Add a job to the pool and signal its arrival.

        Appends the job to the end of the queue and triggers the `new_job` event,
        allowing event-driven processes (e.g., starvation avoidance) to react
        immediately to the new arrival.

        Args:
            job: The production job to add to the pool.
        """
        self._psp.append(job)

        self.env.debug(
            f"Job {job.id[:8]} entered PSP",
            component="PreShopPool",
            job_id=job.id,
            sku=job.sku,
            psp_size=len(self._psp),
            due_date=job.due_date,
        )

        self._signal_new_job(job)

    def remove(self, *, job: ProductionJob | None = None) -> ProductionJob:
        """Remove a job from the pool and record its exit timestamp.

        Supports two modes: FIFO removal (default) or specific job removal.
        Sets `job.psp_exit_at` to the current simulation time before returning.

        Args:
            job: The specific job to remove. If None, removes the oldest job (FIFO).

        Returns:
            The removed job with its `psp_exit_at` timestamp updated.

        Raises:
            ValueError: If a specific job is requested but not found in the pool.
        """
        if job is not None:
            if job not in self._psp:
                raise ValueError(f"{job} not found in the pre-shop pool.")
            self._psp.remove(job)
        else:
            job = self._psp.popleft()

        time_in_psp = self.env.now - job.created_at
        job.psp_exit_at = self.env.now

        self.env.debug(
            f"Job {job.id[:8]} released from PSP",
            component="PreShopPool",
            job_id=job.id,
            time_in_psp=time_in_psp,
            psp_size_after=len(self._psp),
        )

        return job

    def _signal_new_job(self, job: ProductionJob) -> None:
        """Trigger the new_job event and prepare for the next signal.

        Succeeds the current `new_job` event with the job as its value, waking
        any processes yielding on it. Then creates a fresh event for the next
        signal, following the SimPy one-shot event pattern.

        Args:
            job: The job to pass as the event's value to waiting processes.
        """
        self.new_job.succeed(job)
        self.new_job = self.env.event()
