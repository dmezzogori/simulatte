"""Shop floor orchestration for jobshop simulations.

This module provides the ShopFloor class, which serves as the central orchestrator
for job flow through a manufacturing simulation. It manages work-in-progress (WIP)
tracking, coordinates job routing through servers, maintains exponential moving
average (EMA) metrics for performance monitoring, and provides event signaling
for job lifecycle events.

The ShopFloor integrates with:
- Server: Processing resources that handle jobs
- ProductionJob: Jobs flowing through the shop floor
- MaterialCoordinator: Optional material delivery coordination (protocol)
- Environment: The SimPy-based simulation environment

Extensibility is provided through:
- OperationHook: Sync or generator-based hooks for before/after each operation
- WIPStrategy: Pluggable WIP calculation strategies

Metrics come from collectors on the event bus (:mod:`simulatte.collectors`); every ShopFloor attaches an
:class:`~simulatte.collectors.EMACollector` as ``metrics`` unless built with ``default_metrics=False``.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, cast, runtime_checkable

from simulatte._wire import FrozenMap, freeze, wire_float
from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.environment import Environment
from simulatte.events import Deltas, DomainEvent, event_type

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.collectors import EMACollector
    from simulatte.events import DeltaBuilder
    from simulatte.job import ProductionJob
    from simulatte.psp import PreShopPool
    from simulatte.server import Server
    from simulatte.typing import ProcessGenerator


# =============================================================================
# Protocols for extensibility
# =============================================================================


@runtime_checkable
class OperationHook(Protocol):
    """Hook called before or after each operation.

    Hooks may be plain synchronous functions (returning None) or
    generator-based (yielding SimPy events). Both styles can coexist
    in the same hook list and execute in registration order.

    Examples:
        A synchronous logging hook::

            def log_hook(job, server, op_index, processing_time):
                print(f"t={server.env.now}: {job.sku} op {op_index} on {server}")

        A generator hook that adds setup time::

            def setup_time_hook(job, server, op_index, processing_time):
                setup = 2.0 if job.sku.startswith("COMPLEX") else 0.5
                yield server.env.timeout(setup)

            shopfloor = ShopFloor(env=env, on_before_operation=setup_time_hook)
    """

    def __call__(
        self,
        job: ProductionJob,
        server: Server,
        op_index: int,
        processing_time: float,
    ) -> ProcessGenerator | None:
        """Execute the hook.

        Args:
            job: The job being processed.
            server: The server where the operation occurs.
            op_index: Zero-based index of the current operation.
            processing_time: Duration of the operation.

        Returns:
            None for synchronous hooks, or a generator yielding SimPy events.
        """
        ...


@runtime_checkable
class WIPStrategy(Protocol):
    """Strategy for calculating work-in-progress (WIP).

    WIP strategies define how processing times are accumulated when jobs
    enter the shop floor and how they are decremented as operations complete.

    Two built-in strategies are provided:
    - StandardWIPStrategy: Full processing time per server
    - CorrectedWIPStrategy: Position-discounted WIP (1/1, 1/2, 1/3, ...)
    """

    def add_job(self, job: ProductionJob, wip: dict[Server, float]) -> None:
        """Update WIP when a job enters the shop floor.

        Args:
            job: The job entering the shop floor.
            wip: Dictionary mapping servers to their current WIP values.
        """
        ...

    def complete_operation(
        self,
        job: ProductionJob,
        server: Server,
        op_index: int,
        processing_time: float,
        wip: dict[Server, float],
    ) -> None:
        """Update WIP when an operation completes.

        Args:
            job: The job that completed the operation.
            server: The server where the operation completed.
            op_index: Zero-based index of the completed operation.
            processing_time: Duration of the completed operation.
            wip: Dictionary mapping servers to their current WIP values.
        """
        ...


@runtime_checkable
class MaterialCoordinator(Protocol):
    """Protocol for material delivery coordination.

    A material coordinator ensures that required materials are delivered
    to a server before processing begins. Implementations must provide
    an ``ensure`` method that yields SimPy events to block until delivery
    is complete.
    """

    def ensure(
        self,
        job: ProductionJob,
        server: Server,
        op_index: int,
    ) -> ProcessGenerator:
        """Ensure materials are delivered before processing can begin.

        Args:
            job: The production job requiring materials.
            server: The server where processing will occur.
            op_index: The operation index (0-based).

        Yields:
            SimPy events for the delivery process.
        """
        ...


class Dispatcher(Protocol):  # pragma: no cover
    """Reference protocol showing the full dispatcher interface.

    All methods are optional at runtime — ``attach_dispatcher`` wires
    only those that are present and callable on the dispatcher object.

    This protocol is NOT runtime-checkable. It exists for documentation
    and IDE support. Partial implementations are explicitly supported.
    """

    def on_before_operation(
        self,
        job: ProductionJob,
        server: Server,
        op_index: int,
        processing_time: float,
    ) -> ProcessGenerator | None: ...

    def on_after_operation(
        self,
        job: ProductionJob,
        server: Server,
        op_index: int,
        processing_time: float,
    ) -> ProcessGenerator | None: ...

    def on_job_finished(self, job: ProductionJob) -> None: ...

    def on_processing_end(self, job: ProductionJob, server: Server) -> None: ...

    def on_psp_arrival(self, job: ProductionJob, psp: PreShopPool) -> None: ...


# =============================================================================
# Built-in WIP Strategies
# =============================================================================


class StandardWIPStrategy:
    """Default WIP strategy: full processing time added per server.

    When a job enters the shop floor, the full processing time for each
    operation is added to the corresponding server's WIP. When an operation
    completes, only that operation's processing time is decremented.
    """

    def add_job(self, job: ProductionJob, wip: dict[Server, float]) -> None:
        """Add full processing times to WIP for all servers in routing."""
        for server, processing_time in job.server_processing_times:
            wip.setdefault(server, 0.0)
            wip[server] += processing_time

    def complete_operation(
        self,
        job: ProductionJob,
        server: Server,
        op_index: int,
        processing_time: float,
        wip: dict[Server, float],
    ) -> None:
        """Decrement WIP by the completed operation's processing time."""
        del job, op_index  # Unused but required by protocol
        wip[server] -= processing_time


class CorrectedWIPStrategy:
    """Position-discounted WIP strategy.

    Processing times are discounted by operation position:
    - 1st operation: full time (1/1)
    - 2nd operation: half time (1/2)
    - 3rd operation: third time (1/3)
    - etc.

    As operations complete, remaining operations' WIP values are adjusted
    upward to reflect their new position in the routing.

    This strategy provides a more balanced view of workload when jobs
    have long routings, preventing downstream servers from appearing
    overloaded due to jobs that haven't reached them yet.
    """

    def add_job(self, job: ProductionJob, wip: dict[Server, float]) -> None:
        """Add position-discounted processing times to WIP."""
        for i, (server, processing_time) in enumerate(job.server_processing_times):
            wip.setdefault(server, 0.0)
            wip[server] += processing_time / (i + 1)

    def complete_operation(
        self,
        job: ProductionJob,
        server: Server,
        op_index: int,
        processing_time: float,
        wip: dict[Server, float],
    ) -> None:
        """Decrement WIP and adjust remaining operations' discounts."""
        del op_index  # Unused but required by protocol
        wip[server] -= processing_time
        # Adjust remaining operations: they move up one position
        for i, remaining_server in enumerate(job.remaining_routing):
            remaining_processing_time = job.routing[remaining_server]
            # Remove old discounted value, add new discounted value
            wip[remaining_server] -= remaining_processing_time / (i + 2)
            wip[remaining_server] += remaining_processing_time / (i + 1)


# =============================================================================
# Flow events
# =============================================================================


@event_type("shopfloor.entered", touches={"shopfloor": ("jobs_in_system", "wip"), "job": ("shopfloor", "location")})
class ShopFloorEntered(DomainEvent):
    """A job entered the shop floor: ``jobs_in_system``, the WIP entries its strategy changed (``put``), the
    job's owner (``shopfloor``) and its location (``transit`` until it joins its first queue)."""

    job: str
    shopfloor: str


@event_type("operation.started", touches={"job": ("op_index",)})
class OperationStarted(DomainEvent):
    """A granted operation starts processing, after the before-operation hooks and material delivery.

    The job's ``op_index`` is set (its location is already ``server:<server>`` from ``job.granted``);
    `planned_end` is the start time plus `processing_time`.
    """

    job: str
    server: str
    op_index: int
    processing_time: float
    planned_end: float


@event_type("operation.completed")
class OperationCompleted(DomainEvent):
    """An operation finished processing; the server's ``worked_time`` includes it (credited by the
    ``server.work_credited`` event that :meth:`Server.process_job <simulatte.server.Server.process_job>` emitted just
    before)."""

    job: str
    server: str
    op_index: int
    processing_time: float


@event_type("shopfloor.wip_updated", touches={"shopfloor": ("wip",)})
class ShopFloorWipUpdated(DomainEvent):
    """The WIP strategy updated the WIP after an operation; `changes` maps each changed server id to its new load.

    `changes` lists the updated entries only (the ``put`` operations of the deltas); an entry the strategy
    removed appears only as a ``delete`` operation in the deltas.
    """

    shopfloor: str
    changes: FrozenMap


@event_type("job.finished", touches={"job": ("location", "finished_at"), "shopfloor": ("jobs_in_system",)})
class JobFinished(DomainEvent):
    """A job completed its routing: its location becomes ``"done"`` and it leaves ``jobs_in_system``."""

    job: str
    shopfloor: str
    makespan: float
    lateness: float
    total_queue_time: float


# =============================================================================
# ShopFloor Class
# =============================================================================


class ShopFloor(Entity, kind="shopfloor"):
    """Central orchestrator for job flow through a manufacturing simulation.

    The ShopFloor manages the complete lifecycle of production jobs as they move
    through a sequence of servers. It tracks work-in-progress (WIP) at each server,
    maintains metrics for performance monitoring, and signals events when jobs
    complete processing steps or finish entirely.

    Extensibility is provided through composition:
    - on_before_operation / on_after_operation: Hooks for custom logic at each operation
    - wip_strategy: Pluggable WIP calculation
    - on_job_finished: Callbacks when jobs complete
    - material_coordinator: Optional material delivery coordination

    Attributes:
        env: The simulation environment providing time and process management.
        material_coordinator: Optional coordinator for material delivery.
        servers: List of servers registered with this shop floor.
        jobs: Jobs currently on the shop floor, as an insertion-ordered dict keyed by job (values are
            None): membership, ``len`` and iteration in entry order.
        jobs_done: List of completed jobs in order of completion.
        metrics: The default :class:`~simulatte.collectors.EMACollector` (``ema_*`` averages of completed jobs),
            or None when built with ``default_metrics=False``.
        wip: Dictionary mapping each server to its current WIP value.
        total_time_in_system: Cumulative time spent by all completed jobs.
        job_processing_end: SimPy event triggered when any job finishes
            processing at a server. Recreated after each trigger.
        job_finished_event: SimPy event triggered when any job completes
            its entire routing. Recreated after each trigger.
        maximum_wip_value: Peak total WIP observed during simulation.
        maximum_shopfloor_jobs: Peak number of concurrent jobs observed.

    Example:
        Basic usage with hooks::

            from simulatte import Environment, Server, ProductionJob, ShopFloor

            def setup_hook(job, server, op_index, pt):
                yield server.env.timeout(1.0)  # 1s setup time

            env = Environment()
            shop_floor = ShopFloor(env=env, on_before_operation=setup_hook)
            server = Server(env=env, capacity=1, shopfloor=shop_floor)

            job = ProductionJob(
                env=env, sku="PART-A", servers=[server],
                processing_times=[10.0], due_date=100.0,
            )

            shop_floor.add(job)
            env.run()
    """

    state_schema: ClassVar[StateSchema] = StateSchema(
        {"wip": FieldSpec("float", collection="map"), "jobs_in_system": FieldSpec("int")}
    )

    def __init__(
        self,
        *,
        env: Environment,
        ema_alpha: float = 0.01,
        material_coordinator: MaterialCoordinator | None = None,
        wip_strategy: WIPStrategy | None = None,
        default_metrics: bool = True,
        on_before_operation: OperationHook | Sequence[OperationHook] | None = None,
        on_after_operation: OperationHook | Sequence[OperationHook] | None = None,
        on_job_finished: Callable[[ProductionJob], None] | Sequence[Callable[[ProductionJob], None]] | None = None,
        name: str | None = None,
        label: str | None = None,
    ) -> None:
        """Initialize a new ShopFloor instance.

        Args:
            env: The simulation environment that provides time management,
                event scheduling, and process coordination.
            ema_alpha: Smoothing factor of the default EMACollector, in
                range (0, 1]. Defaults to 0.01.
            material_coordinator: Optional coordinator for handling material
                delivery to servers. When provided, the shop floor will
                ensure materials are delivered before processing begins
                at each operation, implementing FIFO blocking behavior.
            wip_strategy: Strategy for WIP calculation. Defaults to
                StandardWIPStrategy which uses full processing times.
            default_metrics: If True (the default), attach an
                :class:`~simulatte.collectors.EMACollector` as ``metrics``.
                Pass False to run without it; other collectors from
                :mod:`simulatte.collectors` are attached with ``attach(env)``.
            on_before_operation: Hook(s) called after acquiring server but before
                material delivery and processing. Can be a single hook or list.
            on_after_operation: Hook(s) called after processing completes but
                before signaling. Can be a single hook or list.
            on_job_finished: Callback(s) called when a job completes its
                entire routing. Can be a single callable or list.
            name: Optional id of the shop floor; defaults to ``shopfloor-<n>``.
            label: Optional display label; defaults to the id.
        """
        self.env = env
        self.material_coordinator = material_coordinator

        # Normalize hooks to lists
        self._before_operation: list[OperationHook] = self._normalize_hooks(on_before_operation)
        self._after_operation: list[OperationHook] = self._normalize_hooks(on_after_operation)
        self._on_job_finished: list[Callable[[ProductionJob], None]] = self._normalize_callbacks(on_job_finished)
        self._processing_end_callbacks: list[Callable[[ProductionJob, Server], None]] = []

        # Strategies with defaults
        self._wip_strategy: WIPStrategy = wip_strategy if wip_strategy is not None else StandardWIPStrategy()

        # Core state
        self.servers: list[Server] = []
        self.jobs: dict[ProductionJob, None] = {}
        self.jobs_done: list[ProductionJob] = []
        self.wip: dict[Server, float] = {}
        self.total_time_in_system: float = 0.0

        # Events
        self.job_processing_end = self.env.event()
        self.job_finished_event = self.env.event()

        # Peak tracking
        self.maximum_wip_value: float = 0.0
        self.maximum_shopfloor_jobs: int = 0

        env.entities.attach(self, name=name, label=label)

        self.metrics: EMACollector | None = None
        if default_metrics:
            from simulatte.collectors import EMACollector  # the collectors import this module

            self.metrics = EMACollector(self, alpha=ema_alpha).attach(env)

    def snapshot(self) -> dict[str, Any]:
        """Current entity state: WIP per server id and the number of jobs in the system."""
        return {
            "wip": {server.id: wire_float(load) for server, load in self.wip.items()},
            "jobs_in_system": len(self.jobs),
            "label": self.label,
        }

    @staticmethod
    def _normalize_hooks(
        hooks: OperationHook | Sequence[OperationHook] | None,
    ) -> list[OperationHook]:
        """Normalize hook parameter to a list."""
        if hooks is None:
            return []
        if isinstance(hooks, Sequence):
            return list(hooks)
        # Single hook
        return [hooks]

    @staticmethod
    def _normalize_callbacks(
        callbacks: Callable[[ProductionJob], None] | Sequence[Callable[[ProductionJob], None]] | None,
    ) -> list[Callable[[ProductionJob], None]]:
        """Normalize callback parameter to a list."""
        if callbacks is None:
            return []
        if isinstance(callbacks, Sequence):
            return list(callbacks)
        # Single callback
        return [callbacks]

    def on_before_operation(self, hook: OperationHook) -> None:
        """Register a hook to run before each operation.

        Hooks registered post-construction execute after any hooks
        passed via __init__, in registration order.
        """
        self._before_operation.append(hook)

    def on_after_operation(self, hook: OperationHook) -> None:
        """Register a hook to run after each operation.

        Hooks registered post-construction execute after any hooks
        passed via __init__, in registration order.
        """
        self._after_operation.append(hook)

    def on_job_finished(self, callback: Callable[[ProductionJob], None]) -> None:
        """Register a callback for when a job completes its entire routing.

        Callbacks registered post-construction execute after any callbacks
        passed via __init__, in registration order.
        """
        self._on_job_finished.append(callback)

    def on_processing_end(self, callback: Callable[[ProductionJob, Server], None]) -> None:
        """Register a callback for when a job completes processing at any server.

        Callbacks are invoked synchronously after the server is released
        (``servers_exit_at`` is stamped and ``job.previous_server`` is
        available). This fires after each operation, not just when the
        job finishes its entire routing.

        Note:
            The SimPy ``job_processing_end`` event is succeeded earlier
            (while the server is still held). These callbacks fire after
            server release and always run before SimPy process-based
            listeners resume.

        Args:
            callback: Function called with (job, server) after processing completes.
        """
        self._processing_end_callbacks.append(callback)

    def attach_dispatcher(self, dispatcher: object, *, psp: PreShopPool | None = None) -> None:
        """Wire a dispatcher object's hook methods to this shopfloor.

        Detects which hook methods exist on the dispatcher and registers
        only those that are callable. This allows partial implementations
        where a dispatcher only handles a subset of events.

        Args:
            dispatcher: Object with any combination of on_before_operation,
                on_after_operation, on_job_finished, on_processing_end,
                and on_psp_arrival methods.
            psp: If provided and dispatcher has on_psp_arrival, registers
                an arrival subscription on the PSP.
        """
        hook = getattr(dispatcher, "on_before_operation", None)
        if callable(hook):
            self.on_before_operation(hook)

        hook = getattr(dispatcher, "on_after_operation", None)
        if callable(hook):
            self.on_after_operation(hook)

        hook = getattr(dispatcher, "on_job_finished", None)
        if callable(hook):
            self.on_job_finished(hook)

        hook = getattr(dispatcher, "on_processing_end", None)
        if callable(hook):
            self.on_processing_end(hook)

        if psp is not None:
            hook = getattr(dispatcher, "on_psp_arrival", None)
            if callable(hook):
                psp.on_arrival(hook)

    @property
    def wip_strategy(self) -> WIPStrategy:
        """The current WIP strategy used by the shopfloor."""
        return self._wip_strategy

    def set_wip_strategy(self, strategy: WIPStrategy) -> None:
        """Replace the shopfloor's WIP strategy."""
        self._wip_strategy = strategy

    @property
    def average_time_in_system(self) -> float:
        """Average time jobs spend in the system from first server entry to completion.

        Calculated as total_time_in_system divided by the number of completed jobs.
        Returns 0.0 if no jobs have completed yet.

        Returns:
            Average time in system for all completed jobs, or 0.0 if none completed.
        """
        if not self.jobs_done:
            return 0.0
        return self.total_time_in_system / len(self.jobs_done)

    def add(self, job: ProductionJob) -> None:
        """Release a job from the Pre-Shop Pool onto the shop floor.

        This method performs the following actions:
        1. Adds the job to the active jobs
        2. Updates WIP values via the configured WIP strategy
        3. Emits ``shopfloor.entered``
        4. Records the PSP exit timestamp on the job
        5. Spawns the main processing coroutine for the job

        Args:
            job: The production job to release onto the shop floor.
                The job's routing must contain valid server references.

        Note:
            This method modifies the job's psp_exit_at timestamp and spawns
            an async process. The job will begin queuing at its first server
            immediately after this call.
        """
        env = self.env
        jobs = self.jobs
        jobs[job] = None
        before = dict(self.wip) if env.wants(ShopFloorEntered) else None
        self._wip_strategy.add_job(job, self.wip)
        job._shopfloor_id = self.id
        job._location = "transit"
        if before is not None:
            build = Deltas.build().set(self.id, "jobs_in_system", len(jobs))
            self._wip_deltas(build, before)
            build.set(job.id, "shopfloor", self.id).set(job.id, "location", "transit")
            env.emit(ShopFloorEntered(job=job.id, shopfloor=self.id, deltas=build.done()))

        job.psp_exit_at = self.env.now
        self.env.process(self.main(job))

    def _wip_deltas(self, build: DeltaBuilder, before: dict[Server, float]) -> dict[str, float]:
        """Add a ``put`` (``delete``) to `build` for each WIP entry changed (removed) since `before`.

        Returns the changed entries as server id to load.
        """
        shopfloor_id = self.id
        wip = self.wip
        changes: dict[str, float] = {}
        for server, load in wip.items():
            if server not in before or before[server] != load:
                changes[server.id] = value = wire_float(load)
                build.put(shopfloor_id, "wip", server.id, value)
        for server in before:
            if server not in wip:
                build.delete(shopfloor_id, "wip", server.id)
        return changes

    def _operate(self, job: ProductionJob, server: Server, op_index: int, processing_time: float) -> ProcessGenerator:
        """Process the operation on `server` (which credits its ``worked_time`` with ``server.work_credited``), then
        emit ``operation.completed`` in the same step."""
        yield from server.process_job(job, processing_time)
        env = self.env
        if env.wants(OperationCompleted):
            env.emit(
                OperationCompleted(
                    job=job.id,
                    server=server.id,
                    op_index=op_index,
                    processing_time=wire_float(processing_time),
                )
            )

    def _fire_processing_end_callbacks(self, job: ProductionJob, server: Server) -> None:
        """Invoke on_processing_end callbacks after server release.

        Called after the ``with server.request()`` context exits, meaning
        servers_exit_at is stamped and the server resource is freed. This
        is the correct point for release-policy decisions that inspect
        server state or job.previous_server.

        Args:
            job: The job that just completed processing.
            server: The server where processing completed.
        """
        for callback in self._processing_end_callbacks:
            callback(job, server)

    def signal_job_finished(self, job: ProductionJob) -> None:
        """Signal that a job has completed its entire routing.

        This method triggers the job_finished_event with the completed job
        as the event value, notifying any waiting processes that a job has
        finished all operations. The event is then recreated for the next signal.

        Unlike job_processing_end, this is only called once per job when
        it completes its final operation.

        Args:
            job: The job that just completed its entire routing.

        Example:
            Counting completed jobs::

                completed = 0
                while completed < target:
                    job = yield shop_floor.job_finished_event
                    completed += 1
                    print(f"Job {job.id} completed. Total: {completed}")
        """
        self.job_finished_event.succeed(job)
        self.job_finished_event = self.env.event()

    def main(self, job: ProductionJob) -> ProcessGenerator:
        """Execute the main processing loop for a job through all its servers.

        This generator manages the complete lifecycle of a job as it moves through
        its routing. For each server in the job's routing, it:

        1. Requests and acquires the server resource (queuing if busy)
        2. Executes on_before_operation hooks
        3. If a MaterialCoordinator is configured, waits for material delivery
        4. Processes the job for the specified duration
        5. Updates WIP via the configured WIP strategy
        6. Executes on_after_operation hooks
        7. Signals processing completion (job_processing_end event + on_processing_end callbacks)

        After all operations complete, it:
        - Records the finish timestamp on the job
        - Moves the job from active (jobs) to completed (jobs_done)
        - Emits ``job.finished`` (the default EMACollector and other collectors update on it)
        - Calls on_job_finished callbacks
        - Signals job completion via signal_job_finished()
        - Retires the job from the environment's entity registry (``entity.retired``); ``jobs_done`` and
          other Python references keep it

        Args:
            job: The production job to process through its routing.

        Yields:
            SimPy events for server requests, hooks, material delivery, and processing.

        Note:
            This method is automatically spawned by add() and should not be
            called directly. It runs as a SimPy process until the job completes.
        """
        env = self.env
        for op_index, (server, processing_time) in enumerate(job.server_processing_times):
            with server.request(job=job) as request:
                yield request

                # Before-operation hooks
                for hook in self._before_operation:
                    result = hook(job, server, op_index, processing_time)
                    if result is None:
                        continue
                    if inspect.isgenerator(result):
                        yield from result
                    else:
                        raise TypeError(f"OperationHook must return None or a generator, got {type(result).__name__}")

                # Material coordination (if configured)
                if self.material_coordinator is not None:
                    yield from self.material_coordinator.ensure(job, server, op_index)

                # Process job
                job._op_index = op_index
                if env.wants(OperationStarted):
                    now, duration = wire_float(env.now), wire_float(processing_time)
                    env.emit(
                        OperationStarted(
                            job=job.id,
                            server=server.id,
                            op_index=op_index,
                            processing_time=duration,
                            planned_end=now + duration,
                            deltas=Deltas.build().set(job.id, "op_index", op_index).done(),
                        )
                    )
                yield env.process(self._operate(job, server, op_index, processing_time))

                # Update WIP via strategy
                before = dict(self.wip) if env.wants(ShopFloorWipUpdated) else None
                self._wip_strategy.complete_operation(job, server, op_index, processing_time, self.wip)
                if before is not None:
                    build = Deltas.build()
                    changes = cast(FrozenMap, freeze(self._wip_deltas(build, before)))  # canonical: packs as is
                    env.emit(ShopFloorWipUpdated(shopfloor=self.id, changes=changes, deltas=build.done()))

                # After-operation hooks (server still held)
                for hook in self._after_operation:
                    result = hook(job, server, op_index, processing_time)
                    if result is None:
                        continue
                    if inspect.isgenerator(result):
                        yield from result
                    else:
                        raise TypeError(f"OperationHook must return None or a generator, got {type(result).__name__}")

                # SimPy event (server still held — preserves existing semantics)
                self.job_processing_end.succeed(job)
                self.job_processing_end = self.env.event()

            # Server released — servers_exit_at is now stamped
            self._fire_processing_end_callbacks(job, server)

        # Job completion
        job.finished_at = finished_at = env.now
        job.current_server = None
        job.done = True
        job._location = "done"
        del self.jobs[job]
        self.jobs_done.append(job)
        self.total_time_in_system += job.time_in_system
        if env.wants(JobFinished):
            job_id = job.id
            # Built directly, without DeltaBuilder: the default EMACollector makes this event part of every run with
            # a shop floor. The values are already wire values (ids, a finite time, a count), so freeze() is a no-op.
            deltas = Deltas(
                (
                    ("set", job_id, "location", "done"),
                    ("set", job_id, "finished_at", wire_float(finished_at)),
                    ("set", self.id, "jobs_in_system", len(self.jobs)),
                )
            )
            env.emit(
                JobFinished(
                    job=job_id,
                    shopfloor=self.id,
                    makespan=wire_float(job.makespan),
                    lateness=wire_float(job.lateness),
                    total_queue_time=wire_float(job.total_queue_time),
                    deltas=deltas,
                )
            )

        # Job finished callbacks
        for callback in self._on_job_finished:
            callback(job)

        self.signal_job_finished(job)
        env.entities.retire(job)
