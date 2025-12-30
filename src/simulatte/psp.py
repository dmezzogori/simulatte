"""Pre-shop pool for job release control."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from simulatte.environment import Environment
from simulatte.shopfloor import ShopFloor

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable

    from simulatte.job import ProductionJob


class PreShopPool:
    """Buffer queue for jobs awaiting shopfloor release.

    A pure container with no built-in release logic. Release policies are
    implemented as external SimPy processes using the trigger functions from
    `simulatte.policies.triggers`.

    The pool provides a `new_job` event that external processes can monitor
    to react immediately when jobs arrive (e.g., for starvation avoidance).

    Example:
        >>> from simulatte.policies.triggers import periodic_trigger, on_arrival_trigger
        >>> psp = PreShopPool(env=env, shopfloor=shopfloor)
        >>> env.process(periodic_trigger(psp, 1.0, my_release_fn))
        >>> env.process(on_arrival_trigger(psp, my_on_arrival_fn))
    """

    def __init__(self, *, env: Environment, shopfloor: ShopFloor) -> None:
        """Initialize the pre-shop pool.

        Args:
            env: The simulation environment.
            shopfloor: The shopfloor that will receive released jobs.
        """
        self.env = env
        self.shopfloor = shopfloor
        self._psp: deque[ProductionJob] = deque()
        self.new_job = self.env.event()

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
