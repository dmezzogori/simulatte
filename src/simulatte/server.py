"""Server resources for job-shop simulation with queue and utilization tracking.

This module provides the Server class, which extends SimPy's PriorityResource for
processing jobs with priority-based queueing, and ServerPriorityRequest for managing
job requests with priority information.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, cast

import simpy
from simpy.resources.resource import PriorityRequest

from simulatte.environment import Environment

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable

    from simpy.resources.resource import Release

    from simulatte.job import BaseJob
    from simulatte.shopfloor import ShopFloor
    from simulatte.typing import ProcessGenerator


class ServerPriorityRequest(PriorityRequest):
    """Priority request that carries the job reference and priority key.

    This extends SimPy's PriorityRequest to associate a job with each request,
    enabling priority-based queueing where jobs compete for server access.

    Priority semantics:

    - ``self.priority`` is set once at construction (via the parent class's
      ``__init__``) from ``job.priority(server)`` and is never refreshed.
      Treat it as the priority at queue-entry time only.
    - ``self.key`` is rewritten at every dispatch decision by
      ``Server.sort_queue``, which re-evaluates
      ``job.priority(req.server)`` for every queued request. The queue is
      sorted by ``self.key``; this is what makes dynamic priorities work.
    - To read a queued job's *current* priority value, call
      ``req.job.priority(req.server)`` directly.

    The order of attribute assignment in ``__init__`` matters: ``self.server``
    and ``self.job`` must be set before ``super().__init__()``. The superclass
    chain calls ``Put.__init__``, which appends the new request to
    ``put_queue`` and then synchronously calls ``Server._trigger_put`` →
    ``Server.sort_queue``. ``sort_queue`` reads
    ``req.job.priority(req.server)`` for every queued request including the
    one being constructed, so ``server`` and ``job`` must already be set on
    ``self`` by then.
    """

    def __init__(self, resource: Server, job: BaseJob, preempt: bool = True) -> None:  # noqa: FBT001, FBT002
        """Initialize a priority request for server access.

        Args:
            resource: The server being requested.
            job: The job requesting server access.
            preempt: If True, this request can preempt lower-priority jobs.
        """
        self.server = resource
        self.job = job
        self.preempt = preempt
        self.time = resource.env.now
        super().__init__(resource=resource, priority=job.priority(resource), preempt=preempt)  # type: ignore[arg-type]  # SimPy annotates int but works with float

    def __repr__(self) -> str:
        return f"ServerPriorityRequest(job={self.job}, server={self.server})"


class Server(simpy.PriorityResource):
    """A server/workstation for job-shop simulation with queue and utilization tracking.

    Server extends SimPy's PriorityResource to process jobs with priority-based
    queueing. It tracks queue lengths, utilization rates, and optionally records
    time-series data for visualization. When attached to a ShopFloor, the server
    is automatically registered and assigned an index for identification.

    Dynamic priorities: queued jobs' priorities are refreshed before every
    dispatch decision. ``sort_queue`` re-evaluates each queued request's
    ``job.priority_policy`` and rewrites ``req.key``; ``_trigger_put``
    (the SimPy hook invoked on both new-arrival and release paths) calls
    ``sort_queue`` before delegating to SimPy. Callers may also invoke
    ``sort_queue`` explicitly to observe the resulting order between
    events. The cost per dispatch decision is one ``priority_policy`` call
    per queued request; policies must be deterministic given ``(job, server)`` and the
    current simulation state at call time.
    """

    def __init__(
        self,
        *,
        env: Environment,
        capacity: int,
        shopfloor: ShopFloor | None = None,
        collect_time_series: bool = False,
        retain_job_history: bool = False,
    ) -> None:
        """Initialize a server resource.

        Args:
            env: The simulation environment.
            capacity: Maximum number of jobs that can be processed simultaneously.
            shopfloor: Optional ShopFloor for automatic registration. If provided,
                the server is added to the shopfloor's server list.
            collect_time_series: If True, record queue length and utilization
                over time for later visualization via plot_qt() and plot_ut().
            retain_job_history: If True, maintain a list of all processed jobs.
        """
        self.env = env
        super().__init__(env, capacity)
        self.worked_time: float = 0

        self._queue_history: dict[int, float] = defaultdict(float)
        self._qt: list[tuple[float, int]] | None = [] if collect_time_series else None
        self._ut: list[tuple[float, float]] | None = [(0, 0.0)] if collect_time_series else None

        self._last_queue_level: int = 0
        self._last_queue_level_timestamp: float = 0

        self._jobs: list[BaseJob] | None = [] if retain_job_history else None

        if shopfloor is not None:
            shopfloor.servers.append(self)
            self._idx = shopfloor.servers.index(self)
        else:
            self._idx = -1

    def __repr__(self) -> str:
        return f"Server(id={self._idx})"

    @property
    def empty(self) -> bool:
        """Whether the queue is empty."""
        return len(self.queue) == 0

    @property
    def is_idle(self) -> bool:
        """Whether the server has no active users and an empty queue."""
        return self.count == 0 and self.empty

    @property
    def current_jobs(self) -> tuple[BaseJob, ...]:
        """Jobs currently occupying active server slots (includes hook/material phases)."""
        return tuple(cast(ServerPriorityRequest, request).job for request in self.users)

    @property
    def average_queue_length(self) -> float:
        """Time-weighted average queue length over the simulation."""
        if self.env.now == 0:
            return 0.0
        return sum(queue_length * time for queue_length, time in self._queue_history.items()) / self.env.now

    @property
    def utilization_rate(self) -> float:
        """Fraction of time the server has been busy (0 to 1)."""
        if self.env.now == 0:
            return 0
        return self.worked_time / self.env.now

    @property
    def idle_time(self) -> float:
        """Total time the server has been idle."""
        return self.env.now - self.worked_time

    @property
    def queueing_jobs(self) -> Iterable[BaseJob]:
        """Iterator over jobs currently waiting in the queue."""
        return (request.job for request in self.queue)

    def _update_qt(self) -> None:
        """Record current queue length to the time-series if collection is enabled."""
        if self._qt is None:
            return
        self._qt.append((self.env.now, len(self.queue)))

    def _update_ut(self) -> None:
        """Record current utilization to the time-series if collection is enabled."""
        if self._ut is None:
            return
        status = self.count / self.capacity if self.capacity else 0.0
        if self._ut and self._ut[-1][1] == status:
            return
        self._ut.append((self.env.now, status))

    def _update_queue_history(self, _: simpy.Event | None) -> None:
        """Update queue histogram and trigger time-series updates."""
        self._queue_history[self._last_queue_level] += self.env.now - self._last_queue_level_timestamp
        self._last_queue_level_timestamp = self.env.now
        self._last_queue_level = len(self.queue)
        self._update_qt()

    def request(  # type: ignore[override]
        self,
        *,
        job: BaseJob,
        preempt: bool = True,
    ) -> ServerPriorityRequest:
        """Request server access for a job with priority-based queueing.

        Creates a priority request that enters the server queue. The request should
        be used as a context manager to ensure proper release.

        Args:
            job: The job requesting server access.
            preempt: If True, this request can preempt lower-priority jobs.

        Returns:
            A ServerPriorityRequest to be yielded and used as a context manager.
        """
        request = ServerPriorityRequest(self, job, preempt=preempt)
        job.servers_entry_at[self] = self.env.now
        job.current_server = self

        self.env.debug(
            f"Job {job.id[:8]} entered queue",
            component="Server",
            job_id=job.id,
            server_id=self._idx,
            priority=job.priority(self),
            queue_length=len(self.queue) + 1,
            sku=getattr(job, "sku", None),
        )

        self._update_queue_history(None)
        self._update_ut()
        request.callbacks.append(self._update_queue_history)
        request.callbacks.append(lambda _: self._update_ut())
        return request

    def release(self, request: ServerPriorityRequest) -> Release:  # type: ignore[override]
        """Release the server after job processing.

        Records the job's exit time and updates utilization tracking.

        Args:
            request: The ServerPriorityRequest to release.

        Returns:
            A SimPy Release event.
        """
        release = super().release(request)  # type: ignore[arg-type]
        request.job.servers_exit_at[self] = self.env.now

        job = request.job
        entry_time = job.servers_entry_at.get(self, self.env.now)
        self.env.debug(
            f"Job {job.id[:8]} released",
            component="Server",
            job_id=job.id,
            server_id=self._idx,
            time_at_server=self.env.now - cast(float | int, entry_time),
        )

        self._update_ut()
        return release

    def process_job(self, job: BaseJob, processing_time: float) -> ProcessGenerator:
        """Simulate processing a job for a given duration.

        This generator yields a timeout event for the processing duration and
        updates worked_time. Should be called within a request context.

        Args:
            job: The job being processed.
            processing_time: Duration of processing in simulation time units.

        Yields:
            A SimPy timeout event for the processing duration.
        """
        if self._jobs is not None:
            self._jobs.append(job)

        self.env.debug(
            f"Job {job.id[:8]} processing started",
            component="Server",
            job_id=job.id,
            server_id=self._idx,
            processing_time=processing_time,
        )

        yield self.env.timeout(processing_time)
        self.worked_time += processing_time

    def sort_queue(self) -> None:
        """Refresh queued requests' priority keys and resort.

        For each request in the queue, calls ``req.job.priority(req.server)``
        to obtain the current priority and rewrites ``req.key`` accordingly,
        then sorts the queue in ascending order by the refreshed keys.

        Called automatically before every dispatch decision via
        ``_trigger_put``. May also be invoked explicitly by user code
        that has mutated ``priority_policy`` and wants to observe the new
        order before the next dispatch event.

        Note: ``req.priority`` is not refreshed; it remains the snapshot
        taken at request construction. To inspect a queued job's current
        priority, call ``req.job.priority(req.server)`` directly.

        Requires that every queued request expose ``job``, ``server``,
        ``time``, and ``preempt`` attributes (which ``ServerPriorityRequest``
        does).
        """
        queue_list = cast(list, self.queue)
        for req in queue_list:
            fresh_priority = req.job.priority(req.server)
            req.key = (fresh_priority, req.time, not req.preempt)
        queue_list.sort(key=lambda req: req.key)

    def _trigger_put(self, get_event: Release | None) -> None:
        """Refresh queue priorities before SimPy iterates the put queue.

        Overrides ``simpy.resources.base.BaseResource._trigger_put`` to
        call ``sort_queue`` (which re-evaluates ``job.priority_policy``
        for every queued request and rewrites ``req.key``) before delegating
        to SimPy. SimPy invokes ``_trigger_put`` from two call sites:
        ``simpy.resources.base.Put.__init__`` (after a new arrival is
        appended to ``put_queue``) and as a callback on every Release event
        (``simpy.resources.base.Get.__init__`` registers it). Refreshing
        here therefore covers both the new-arrival and release dispatch paths.
        """
        self.sort_queue()
        super()._trigger_put(get_event)

    def plot_qt(self) -> None:  # pragma: no cover
        """Display a step plot of queue length over simulation time.

        Raises:
            RuntimeError: If time-series collection was not enabled at initialization.
        """
        import matplotlib.pyplot as plt  # lazy: keeps headless sim runs free of the matplotlib/numpy import

        if self._qt is None:
            raise RuntimeError("Queue time-series collection is disabled for this Server.")
        x, y = zip(*self._qt, strict=False)
        plt.step(x, y, where="pre")
        plt.fill_between(x, y, step="pre", alpha=1.0)
        plt.title(f"Q(t): {self} queue length over time")
        plt.xlabel("Simulation Time")
        plt.ylabel("Queue Length")
        plt.show()

    def plot_ut(self) -> None:  # pragma: no cover
        """Display a step plot of utilization rate over simulation time.

        Raises:
            RuntimeError: If time-series collection was not enabled at initialization.
        """
        import matplotlib.pyplot as plt  # lazy: keeps headless sim runs free of the matplotlib/numpy import

        if self._ut is None:
            raise RuntimeError("Utilization time-series collection is disabled for this Server.")
        ut = [*self._ut, (self.env.now, self._ut[-1][1])]
        x, y = zip(*ut, strict=False)
        plt.step(x, y, where="post")
        plt.fill_between(x, y, step="post", alpha=1.0)
        plt.title(f"U(t): {self} utilization over time")
        plt.xlabel("Simulation Time")
        plt.ylabel("Utilization rate")
        plt.show()
