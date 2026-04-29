"""Pre-shop pool for job release control."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from simulatte.environment import Environment
from simulatte.shopfloor import ShopFloor

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Iterable

    from simulatte.job import ProductionJob
    from simulatte.server import Server


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
        self._arrival_callbacks: list[Callable[[ProductionJob, PreShopPool], None]] = []

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

    def release(self, job: ProductionJob) -> None:
        """Remove a job from the pool and release it to the shopfloor.

        Convenience method combining remove() and shopfloor.add().
        Use remove() instead if you want to discard a job without releasing it.

        Args:
            job: The job to release from the pool to the shopfloor.

        Raises:
            ValueError: If the job is not found in the pool.
        """
        self.remove(job=job)
        self.shopfloor.add(job)

    def jobs_starting_at(self, server: Server) -> list[ProductionJob]:
        """Return jobs in the pool whose routing begins at the given server.

        Args:
            server: The server to filter by.

        Returns:
            List of jobs whose first routing server matches, in FIFO order.
        """
        return [job for job in self._psp if job.starts_at(server)]

    def _signal_new_job(self, job: ProductionJob) -> None:
        """Invoke arrival callbacks and trigger the new_job event.

        First invokes all registered on_arrival callbacks synchronously,
        then succeeds the SimPy new_job event (waking process-based listeners).

        Args:
            job: The job to pass to callbacks and as the event's value.
        """
        for callback in self._arrival_callbacks:
            callback(job, self)

        self.new_job.succeed(job)
        self.new_job = self.env.event()

    def on_arrival(self, callback: Callable[[ProductionJob, PreShopPool], None]) -> None:
        """Subscribe a callback to be invoked each time a job arrives in the pool.

        Callbacks are invoked synchronously during add(), before the SimPy
        new_job event fires. No env.run() priming is needed.

        Args:
            callback: Function called with (job, psp) when a job arrives.
        """
        self._arrival_callbacks.append(callback)
