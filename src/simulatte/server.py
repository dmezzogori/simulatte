"""Server resources for job-shop simulation with queue and utilization tracking.

This module provides the Server class, which extends SimPy's PriorityResource for
processing jobs with priority-based queueing, and ServerPriorityRequest for managing
job requests with priority information.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections import defaultdict
from typing import TYPE_CHECKING, Any, ClassVar, cast

import simpy
from simpy.resources.resource import PriorityRequest

from simulatte._wire import Wire, freeze, wire_float, wire_float_or_none
from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.environment import Environment
from simulatte.events import Deltas, DomainEvent, event_type

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable

    from simpy.resources.resource import Release

    from simulatte.job import BaseJob
    from simulatte.shopfloor import ShopFloor
    from simulatte.typing import ProcessGenerator


__all__ = [
    "JobGranted",
    "JobQueueLeft",
    "JobQueued",
    "JobReleased",
    "Server",
    "ServerPriorityRequest",
    "ServerQueueReordered",
    "ServerWorkCredited",
]


# ---------------------------------------------------------------------------------------------------------
# Resource events
# ---------------------------------------------------------------------------------------------------------


@event_type("job.queued", touches={"server": ("queue",), "job": ("location",)})
class JobQueued(DomainEvent):
    """A request entered the server queue (``insert`` at its index); the job's location becomes ``queue:<server>``.

    `queue_length` counts the waiting requests when the job joins, the job itself included: a job that finds
    a free slot has ``queue_length == 1`` and is granted in the next event. `priority` is the priority
    stored on the request at construction, as a wire value: a number becomes a float, another wire value (for
    example a tuple of numbers or a string) is frozen, and a priority that is not a wire value (for example a
    ``Decimal`` or a user object) is recorded as None. Building the event therefore never fails for a priority
    SimPy can sort, and never calls user code such as ``__repr__`` (whose output could also hold memory
    addresses and make the digest irreproducible).
    """

    job: str
    server: str
    priority: Wire
    queue_length: int


@event_type("job.granted", touches={"server": ("queue", "users"), "job": ("location",)})
class JobGranted(DomainEvent):
    """A queued request was granted a slot: queue ``remove``, users ``insert``; the job's location becomes
    ``server:<server>``."""

    job: str
    server: str


@event_type("job.queue_left", touches={"server": ("queue",), "job": ("location",)})
class JobQueueLeft(DomainEvent):
    """A waiting request left the queue without being granted; `reason` is ``"cancelled"``. The job's location
    becomes ``transit``."""

    job: str
    server: str
    reason: str


@event_type("job.released", touches={"server": ("users",), "job": ("location",)})
class JobReleased(DomainEvent):
    """A granted request released its slot (users ``remove``); the job's location becomes ``transit``."""

    job: str
    server: str


@event_type("server.queue_reordered", touches={"server": ("queue",)})
class ServerQueueReordered(DomainEvent):
    """Refreshed priorities changed the queue order; the deltas are the minimal set of ``move`` operations."""

    server: str


@event_type("server.work_credited", touches={"server": ("worked_time",)})
class ServerWorkCredited(DomainEvent):
    """:meth:`Server.process_job` credited `processing_time` to the server's ``worked_time`` (``set``).

    Emitted by the server itself, so the credit replays for a ``ShopFloor`` and for direct ``Server`` users alike
    (ruling R29); a ``ShopFloor`` operation emits ``operation.completed`` right after it.
    """

    server: str
    job: str
    processing_time: float


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
    ``self`` by then. For the same reason the request registers itself as the
    server's pending arrival just before ``super().__init__()``, so that
    ``_trigger_put`` emits its ``job.queued`` event.
    """

    def __init__(self, resource: Server, job: BaseJob, preempt: bool = True) -> None:
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
        priority = job.priority(resource)
        resource._arrival = self
        super().__init__(resource=resource, priority=priority, preempt=preempt)  # ty: ignore[invalid-argument-type]  # SimPy annotates int but works with float

    def __repr__(self) -> str:
        return f"ServerPriorityRequest(job={self.job}, server={self.server})"

    def cancel(self) -> None:
        """Withdraw a waiting request from the queue (SimPy calls this when a ``with`` block exits).

        Emits ``job.queue_left`` only when the request was actually removed, i.e. it had not been granted.
        """
        if self.triggered:
            return
        super().cancel()
        server = self.server
        env = server.env
        job = self.job
        job._location = "transit"
        if env.wants(JobQueueLeft):
            job_id = job.id
            env.emit(
                JobQueueLeft(
                    job=job_id,
                    server=server.id,
                    reason="cancelled",
                    deltas=Deltas.build().remove(server.id, "queue", job_id).set(job_id, "location", "transit").done(),
                )
            )


class Server(simpy.PriorityResource, Entity, kind="server"):
    """A server/workstation for job-shop simulation with queue and utilization tracking.

    Server extends SimPy's PriorityResource to process jobs with priority-based
    queueing. It tracks queue lengths and utilization rates; time series of both
    come from :class:`~simulatte.collectors.ServerTimeSeries`. Its id is the ``name`` given to the
    constructor, or ``server-<n>`` in attachment order. When attached to a ShopFloor,
    the server is automatically registered on it.

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

    state_schema: ClassVar[StateSchema] = StateSchema(
        {
            "capacity": FieldSpec("int"),
            "users": FieldSpec("str", collection="list"),
            "queue": FieldSpec("str", collection="list"),
            "worked_time": FieldSpec("float"),
        }
    )

    def __init__(
        self,
        *,
        env: Environment,
        capacity: int,
        shopfloor: ShopFloor | None = None,
        retain_job_history: bool = False,
        name: str | None = None,
        label: str | None = None,
    ) -> None:
        """Initialize a server resource.

        Args:
            env: The simulation environment.
            capacity: Maximum number of jobs that can be processed simultaneously.
            shopfloor: Optional ShopFloor for automatic registration. If provided,
                the server is added to the shopfloor's server list.
            retain_job_history: If True, maintain a list of all processed jobs.
            name: Optional id of the server; defaults to ``server-<n>``.
            label: Optional display label; defaults to the id.
        """
        self.env = env
        self._arrival: ServerPriorityRequest | None = None  # request being constructed, set by its __init__
        super().__init__(env, capacity)
        self.worked_time: float = 0

        self._queue_history: dict[int, float] = defaultdict(float)

        self._last_queue_level: int = 0
        self._last_queue_level_timestamp: float = 0

        self._jobs: list[BaseJob] | None = [] if retain_job_history else None

        env.entities.attach(self, name=name, label=label)
        # Job locations written by the queue and grant hooks (spec §5.2), built once.
        self._queue_location = f"queue:{self.id}"
        self._server_location = f"server:{self.id}"

        if shopfloor is not None:
            shopfloor.servers.append(self)

    def __repr__(self) -> str:
        return f"Server(id={self.id!r})"

    def snapshot(self) -> dict[str, Any]:
        """Current entity state: capacity, job ids of users and queue (in order) and worked time."""
        return {
            "capacity": self.capacity,
            "users": [cast(ServerPriorityRequest, request).job.id for request in self.users],
            "queue": [cast(ServerPriorityRequest, request).job.id for request in self.queue],
            "worked_time": wire_float(self.worked_time),
            "label": self.label,
        }

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
        return math.fsum(queue_length * time for queue_length, time in self._queue_history.items()) / self.env.now

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

    def _update_queue_history(self, _: simpy.Event | None) -> None:
        """Update the queue-length histogram behind :attr:`average_queue_length`."""
        self._queue_history[self._last_queue_level] += self.env.now - self._last_queue_level_timestamp
        self._last_queue_level_timestamp = self.env.now
        self._last_queue_level = len(self.queue)

    def request(  # ty: ignore[invalid-method-override]
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

        self._update_queue_history(None)
        request.callbacks.append(self._update_queue_history)
        return request

    def release(self, request: ServerPriorityRequest) -> Release:  # ty: ignore[invalid-method-override]
        """Release the server after job processing.

        Records the job's exit time. SimPy removes the request from ``users``
        while creating the Release event; if it was there, the job's location becomes ``transit`` and
        ``job.released`` is emitted. Releasing a request that was never granted or was already released changes
        neither and emits nothing.

        Args:
            request: The ServerPriorityRequest to release.

        Returns:
            A SimPy Release event.
        """
        users = self.users
        before = len(users)
        release = super().release(request)
        env = self.env
        job = request.job
        if len(users) != before:
            job._location = "transit"
            if env.wants(JobReleased):
                job_id = job.id
                env.emit(
                    JobReleased(
                        job=job_id,
                        server=self.id,
                        deltas=Deltas.build()
                        .remove(self.id, "users", job_id)
                        .set(job_id, "location", "transit")
                        .done(),
                    )
                )
        job.servers_exit_at[self] = env.now
        return release

    def process_job(self, job: BaseJob, processing_time: float) -> ProcessGenerator:
        """Simulate processing a job for a given duration.

        This generator yields a timeout event for the processing duration and
        updates worked_time, then emits ``server.work_credited``. Should be called
        within a request context.

        Args:
            job: The job being processed.
            processing_time: Duration of processing in simulation time units.

        Yields:
            A SimPy timeout event for the processing duration.
        """
        if self._jobs is not None:
            self._jobs.append(job)

        env = self.env
        yield env.timeout(processing_time)
        self.worked_time += processing_time
        if env.wants(ServerWorkCredited):
            env.emit(
                ServerWorkCredited(
                    server=self.id,
                    job=job.id,
                    processing_time=wire_float(processing_time),
                    deltas=Deltas.build().set(self.id, "worked_time", wire_float(self.worked_time)).done(),
                )
            )

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

        When the relative order changes, emits ``server.queue_reordered`` with the minimal set of ``move``
        operations.
        """
        queue_list: list[Any] = self.queue  # ty: ignore[invalid-assignment]  # a SortedQueue; no cast() call here
        for req in queue_list:
            fresh_priority = req.job.priority(req.server)
            req.key = (fresh_priority, req.time, not req.preempt)
        if len(queue_list) < 2:
            return  # nothing to order (sorting would still call the key function)
        if not self.env.wants(ServerQueueReordered):
            queue_list.sort(key=_request_key)
            return
        before = queue_list[:]
        queue_list.sort(key=_request_key)
        if any(old is not new for old, new in zip(before, queue_list, strict=True)):
            self.env.emit(ServerQueueReordered(server=self.id, deltas=self._reorder_deltas(before, queue_list)))

    def _reorder_deltas(self, before: list[Any], after: list[Any]) -> Deltas:
        """Minimal ``move`` operations that turn the queue order `before` into `after`.

        The requests on a longest increasing subsequence of old positions keep their place; every other
        request is moved, in final order, to just after its final predecessor. Each move is applied to a
        working copy so that its index is valid when the moves are replayed in sequence.
        """
        position = {id(req): i for i, req in enumerate(before)}
        keep = _longest_increasing_run([position[id(req)] for req in after])
        current = before[:]
        build = Deltas.build()
        for i, req in enumerate(after):
            if i in keep:
                continue
            current.remove(req)
            index = 0 if i == 0 else current.index(after[i - 1]) + 1
            current.insert(index, req)
            build.move(self.id, "queue", req.job.id, index)
        return build.done()

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

        Events: on entry, the request being constructed (if any) is announced with ``job.queued`` at its
        current queue index; after SimPy returns, a request appended to ``users`` is announced with
        ``job.granted``. SimPy pops a granted request from the queue only after ``_do_put`` returns, so
        this is the first point where both changes are complete. ``users`` only grows here (a
        ``PriorityResource`` never preempts) and ``Resource._do_put`` stops SimPy's loop after the first
        request it processes, so comparing lengths finds the single grant. Both events also set the job's
        location (``queue:<server>``, then ``server:<server>``), which changes whether or not they are observed.
        """
        env = self.env
        arrival = self._arrival
        if arrival is not None:
            self._arrival = None
            arrival.job._location = location = self._queue_location
            if env.wants(JobQueued):
                queue = self.queue
                job_id = arrival.job.id
                env.emit(
                    JobQueued(
                        job=job_id,
                        server=self.id,
                        priority=_wire_priority(arrival.priority),
                        queue_length=len(queue),
                        deltas=Deltas.build()
                        .insert(self.id, "queue", queue.index(arrival), job_id)
                        .set(job_id, "location", location)
                        .done(),
                    )
                )
        self.sort_queue()
        users = self.users
        before = len(users)
        super()._trigger_put(get_event)
        if len(users) != before:
            location = self._server_location
            wants = env.wants(JobGranted)
            for index in range(before, len(users)):
                job = users[index].job  # ty: ignore[unresolved-attribute]  # a ServerPriorityRequest; no cast() call
                job._location = location
                if wants:
                    job_id = job.id
                    env.emit(
                        JobGranted(
                            job=job_id,
                            server=self.id,
                            deltas=Deltas.build()
                            .remove(self.id, "queue", job_id)
                            .insert(self.id, "users", index, job_id)
                            .set(job_id, "location", location)
                            .done(),
                        )
                    )


def _request_key(request: Any) -> Any:
    return request.key


def _wire_priority(priority: object) -> Wire:
    """`priority` as recorded by ``job.queued`` (see :class:`JobQueued`); never raises and calls no user code.

    Numbers (``bool`` and subclasses of ``int`` and ``float`` included) become floats through the built-in
    conversions, None beyond the float range; other values are frozen, None when they are not wire values (R14).
    """
    if issubclass(type(priority), (int, float)):
        return wire_float_or_none(priority)
    try:
        return freeze(priority)
    except (TypeError, OverflowError):
        return None


def _longest_increasing_run(values: list[int]) -> set[int]:
    """Indices of one longest strictly increasing subsequence of non-empty `values` (patience sorting, O(n log n))."""
    tails: list[int] = []  # smallest tail value of an increasing subsequence of each length
    tail_index: list[int] = []  # index in `values` of that tail
    previous = [-1] * len(values)
    for i, value in enumerate(values):
        length = bisect_left(tails, value)
        if length == len(tails):
            tails.append(value)
            tail_index.append(i)
        else:
            tails[length] = value
            tail_index[length] = i
        previous[i] = tail_index[length - 1] if length else -1
    result: set[int] = set()
    i = tail_index[-1]
    while i != -1:
        result.add(i)
        i = previous[i]
    return result
