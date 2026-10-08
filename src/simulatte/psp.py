"""Pre-shop pool for job release control."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any, ClassVar

from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.environment import Environment
from simulatte.events import Deltas, DomainEvent, event_type
from simulatte.shopfloor import ShopFloor

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Iterable

    from simulatte.job import ProductionJob
    from simulatte.server import Server

__all__ = ["PreShopPool", "PspEntered", "PspExited"]


@event_type("psp.entered", touches={"psp": ("jobs",), "job": ("location", "shopfloor")})
class PspEntered(DomainEvent):
    """A job entered the pool at `position` (``insert``); the job's location becomes the pool and its owner the
    pool's shop floor."""

    job: str
    psp: str
    position: int


@event_type("psp.exited", touches={"psp": ("jobs",), "job": ("location",)})
class PspExited(DomainEvent):
    """A job left the pool (``remove``).

    `reason` is ``"released"`` (to the shop floor), ``"postponed"`` (released after a delay) or ``"removed"``. The
    job's location becomes ``"transit"`` for a postponed release and null otherwise.
    """

    job: str
    psp: str
    reason: str


class PreShopPool(Entity, kind="psp"):
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

    state_schema: ClassVar[StateSchema] = StateSchema(
        {"jobs": FieldSpec("str", collection="list"), "shopfloor": FieldSpec("str", nullable=True)}
    )

    def __init__(
        self,
        *,
        env: Environment,
        shopfloor: ShopFloor,
        name: str | None = None,
        label: str | None = None,
    ) -> None:
        """Initialize the pre-shop pool.

        Args:
            env: The simulation environment.
            shopfloor: The shopfloor that will receive released jobs.
            name: Optional id of the pool; defaults to ``psp-<n>``.
            label: Optional display label; defaults to the id.
        """
        self.env = env
        self.shopfloor = shopfloor
        self._psp: deque[ProductionJob] = deque()
        self.new_job = self.env.event()
        self._arrival_callbacks: list[Callable[[ProductionJob, PreShopPool], None]] = []
        env.entities.attach(self, name=name, label=label)

    def snapshot(self) -> dict[str, Any]:
        """Current entity state: job ids in FIFO order and the owning shop floor's id."""
        return {"jobs": [job.id for job in self._psp], "shopfloor": self.shopfloor.id, "label": self.label}

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
        job._location = self.id
        job._shopfloor_id = shopfloor_id = self.shopfloor.id
        env = self.env
        if env.wants(PspEntered):
            job_id = job.id
            position = len(self._psp) - 1
            env.emit(
                PspEntered(
                    job=job_id,
                    psp=self.id,
                    position=position,
                    deltas=Deltas.build()
                    .insert(self.id, "jobs", position, job_id)
                    .set(job_id, "location", self.id)
                    .set(job_id, "shopfloor", shopfloor_id)
                    .done(),
                )
            )

        self._signal_new_job(job)

    def remove(self, *, job: ProductionJob | None = None, reason: str = "removed") -> ProductionJob:
        """Remove a job from the pool and record its exit timestamp.

        Supports two modes: FIFO removal (default) or specific job removal.
        Sets `job.psp_exit_at` to the current simulation time before returning and emits ``psp.exited``.

        Args:
            job: The specific job to remove. If None, removes the oldest job (FIFO).
            reason: Why the job leaves: ``"released"``, ``"postponed"`` (the job's location becomes
                ``"transit"`` until it enters the shop floor) or ``"removed"`` (the default).

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

        job.psp_exit_at = self.env.now
        job._location = location = "transit" if reason == "postponed" else None
        env = self.env
        if env.wants(PspExited):
            job_id = job.id
            env.emit(
                PspExited(
                    job=job_id,
                    psp=self.id,
                    reason=reason,
                    deltas=Deltas.build().remove(self.id, "jobs", job_id).set(job_id, "location", location).done(),
                )
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
        self.remove(job=job, reason="released")
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
