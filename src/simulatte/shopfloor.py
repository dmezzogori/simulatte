"""Shop floor orchestration for jobshop simulations.

This module provides the ShopFloor class, which serves as the central orchestrator
for job flow through a manufacturing simulation. It manages work-in-progress (WIP)
tracking, coordinates job routing through servers, maintains exponential moving
average (EMA) metrics for performance monitoring, and provides event signaling
for job lifecycle events.

The ShopFloor integrates with:
- Server: Processing resources that handle jobs
- ProductionJob: Jobs flowing through the shop floor
- MaterialCoordinator: Optional material delivery before processing
- Environment: The SimPy-based simulation environment

Extensibility is provided through:
- OperationHook: Generator-based hooks for before/after each operation
- WIPStrategy: Pluggable WIP calculation strategies
- MetricsCollector: Pluggable metrics recording
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from simulatte.environment import Environment

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.job import ProductionJob
    from simulatte.materials import MaterialCoordinator
    from simulatte.server import Server
    from simulatte.typing import ProcessGenerator


# Sentinel to differentiate "use default" vs "explicitly None" for metrics.
_DEFAULT_METRICS_COLLECTOR = object()


# =============================================================================
# Protocols for extensibility
# =============================================================================


@runtime_checkable
class OperationHook(Protocol):
    """Hook called before or after each operation.

    Operation hooks are generator-based to support SimPy's async model.
    They can yield SimPy events (timeouts, resource requests, etc.) to
    inject delays or coordinate with other simulation components.

    Example:
        A setup time hook that adds delay before processing::

            def setup_time_hook(job, server, op_index, processing_time):
                setup = 2.0 if job.sku.startswith("COMPLEX") else 0.5
                yield server.env.timeout(setup)

            shopfloor = ShopFloor(env=env, before_operation=setup_time_hook)
    """

    def __call__(
        self,
        job: ProductionJob,
        server: Server,
        op_index: int,
        processing_time: float,
    ) -> ProcessGenerator:
        """Execute the hook.

        Args:
            job: The job being processed.
            server: The server where the operation occurs.
            op_index: Zero-based index of the current operation.
            processing_time: Duration of the operation.

        Yields:
            SimPy events (timeouts, resource requests, etc.).
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
class MetricsCollector(Protocol):
    """Collector for job completion metrics.

    Metrics collectors receive completed jobs and can compute any desired
    performance metrics. The built-in EMAMetricsCollector computes exponential
    moving averages for common metrics.

    Example:
        A simple throughput collector::

            class ThroughputCollector:
                def __init__(self):
                    self.count = 0
                    self.tardy = 0

                def record(self, job):
                    self.count += 1
                    if job.lateness > 0:
                        self.tardy += 1

            collector = ThroughputCollector()
            shopfloor = ShopFloor(env=env, metrics_collector=collector)
    """

    def record(self, job: ProductionJob) -> None:
        """Record metrics for a completed job.

        Args:
            job: The job that just completed its routing.
        """
        ...


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
        job: ProductionJob,  # noqa: ARG002
        server: Server,
        op_index: int,  # noqa: ARG002
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
        op_index: int,  # noqa: ARG002
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
# Built-in Metrics Collector
# =============================================================================


class EMAMetricsCollector:
    """Exponential moving average (EMA) metrics collector.

    Computes EMAs for common job shop performance metrics:
    - ema_makespan: Job completion time (creation to finish)
    - ema_tardy_jobs: Proportion of jobs finishing late
    - ema_early_jobs: Proportion of jobs finishing early
    - ema_in_window_jobs: Proportion of jobs finishing in due date window
    - ema_time_in_psp: Time spent in Pre-Shop Pool
    - ema_time_in_shopfloor: Time spent on shop floor
    - ema_total_queue_time: Total time waiting in queues

    Attributes:
        alpha: Smoothing factor (0 < alpha <= 1). Smaller values give
            more weight to historical data.
    """

    def __init__(self, alpha: float = 0.01) -> None:
        """Initialize the collector.

        Args:
            alpha: EMA smoothing factor. Defaults to 0.01.
        """
        self.alpha = alpha
        self.ema_makespan: float = 0.0
        self.ema_tardy_jobs: float = 0.0
        self.ema_early_jobs: float = 0.0
        self.ema_in_window_jobs: float = 0.0
        self.ema_time_in_psp: float = 0.0
        self.ema_time_in_shopfloor: float = 0.0
        self.ema_total_queue_time: float = 0.0

    def record(self, job: ProductionJob) -> None:
        """Update all EMA metrics based on the completed job."""
        lateness = job.lateness
        in_window = job.is_finished_in_due_date_window()

        self.ema_makespan += self.alpha * (job.makespan - self.ema_makespan)

        tardy_indicator = 1 if not in_window and lateness > 0 else 0
        self.ema_tardy_jobs += self.alpha * (tardy_indicator - self.ema_tardy_jobs)

        early_indicator = 1 if not in_window and lateness < 0 else 0
        self.ema_early_jobs += self.alpha * (early_indicator - self.ema_early_jobs)

        in_window_indicator = 1 if in_window else 0
        self.ema_in_window_jobs += self.alpha * (in_window_indicator - self.ema_in_window_jobs)

        self.ema_time_in_psp += self.alpha * (job.time_in_psp - self.ema_time_in_psp)
        self.ema_time_in_shopfloor += self.alpha * (job.time_in_shopfloor - self.ema_time_in_shopfloor)
        self.ema_total_queue_time += self.alpha * (job.total_queue_time - self.ema_total_queue_time)


# =============================================================================
# ShopFloor Class
# =============================================================================


class ShopFloor:
    """Central orchestrator for job flow through a manufacturing simulation.

    The ShopFloor manages the complete lifecycle of production jobs as they move
    through a sequence of servers. It tracks work-in-progress (WIP) at each server,
    maintains metrics for performance monitoring, and signals events when jobs
    complete processing steps or finish entirely.

    Extensibility is provided through composition:
    - before_operation / after_operation: Hooks for custom logic at each operation
    - wip_strategy: Pluggable WIP calculation
    - metrics_collector: Pluggable metrics recording
    - on_job_finished: Callbacks when jobs complete
    - material_coordinator: Optional material delivery coordination

    Attributes:
        env: The simulation environment providing time and process management.
        material_coordinator: Optional coordinator for material delivery.
        servers: List of servers registered with this shop floor.
        jobs: Set of jobs currently being processed on the shop floor.
        jobs_done: List of completed jobs in order of completion.
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
            shop_floor = ShopFloor(env=env, before_operation=setup_hook)
            server = Server(env=env, capacity=1, shopfloor=shop_floor)

            job = ProductionJob(
                env=env, sku="PART-A", servers=[server],
                processing_times=[10.0], due_date=100.0,
            )

            shop_floor.add(job)
            env.run()
    """

    def __init__(
        self,
        *,
        env: Environment,
        ema_alpha: float = 0.01,
        material_coordinator: MaterialCoordinator | None = None,
        wip_strategy: WIPStrategy | None = None,
        metrics_collector: MetricsCollector | None | object = _DEFAULT_METRICS_COLLECTOR,
        before_operation: OperationHook | Sequence[OperationHook] | None = None,
        after_operation: OperationHook | Sequence[OperationHook] | None = None,
        on_job_finished: Callable[[ProductionJob], None] | Sequence[Callable[[ProductionJob], None]] | None = None,
    ) -> None:
        """Initialize a new ShopFloor instance.

        Args:
            env: The simulation environment that provides time management,
                event scheduling, and process coordination.
            ema_alpha: Smoothing factor for the default EMAMetricsCollector.
                Must be in range (0, 1]. Ignored if a custom metrics_collector
                is provided. Defaults to 0.01.
            material_coordinator: Optional coordinator for handling material
                delivery to servers. When provided, the shop floor will
                ensure materials are delivered before processing begins
                at each operation, implementing FIFO blocking behavior.
            wip_strategy: Strategy for WIP calculation. Defaults to
                StandardWIPStrategy which uses full processing times.
            metrics_collector: Collector for job completion metrics. Defaults
                to EMAMetricsCollector. Pass None to disable metrics.
            before_operation: Hook(s) called after acquiring server but before
                material delivery and processing. Can be a single hook or list.
            after_operation: Hook(s) called after processing completes but
                before signaling. Can be a single hook or list.
            on_job_finished: Callback(s) called when a job completes its
                entire routing. Can be a single callable or list.
        """
        self.env = env
        self.material_coordinator = material_coordinator

        # Normalize hooks to lists
        self._before_operation: list[OperationHook] = self._normalize_hooks(before_operation)
        self._after_operation: list[OperationHook] = self._normalize_hooks(after_operation)
        self._on_job_finished: list[Callable[[ProductionJob], None]] = self._normalize_callbacks(on_job_finished)

        # Strategies with defaults
        self._wip_strategy: WIPStrategy = wip_strategy if wip_strategy is not None else StandardWIPStrategy()
        if metrics_collector is _DEFAULT_METRICS_COLLECTOR:
            self._metrics_collector: MetricsCollector | None = EMAMetricsCollector(alpha=ema_alpha)
        else:
            self._metrics_collector = cast(MetricsCollector | None, metrics_collector)

        # Core state
        self.servers: list[Server] = []
        self.jobs: set[ProductionJob] = set()
        self.jobs_done: list[ProductionJob] = []
        self.wip: dict[Server, float] = {}
        self.total_time_in_system: float = 0.0

        # Events
        self.job_processing_end = self.env.event()
        self.job_finished_event = self.env.event()

        # Peak tracking
        self.maximum_wip_value: float = 0.0
        self.maximum_shopfloor_jobs: int = 0

    @staticmethod
    def _normalize_hooks(
        hooks: OperationHook | Sequence[OperationHook] | None,
    ) -> list[OperationHook]:
        """Normalize hook parameter to a list."""
        if hooks is None:
            return []
        if isinstance(hooks, list | tuple):  # type: ignore[arg-type, misc]
            return list(hooks)  # type: ignore[arg-type]
        # Single hook
        return [hooks]  # type: ignore[list-item]

    @staticmethod
    def _normalize_callbacks(
        callbacks: Callable[[ProductionJob], None] | Sequence[Callable[[ProductionJob], None]] | None,
    ) -> list[Callable[[ProductionJob], None]]:
        """Normalize callback parameter to a list."""
        if callbacks is None:
            return []
        if isinstance(callbacks, list | tuple):  # type: ignore[arg-type, misc]
            return list(callbacks)  # type: ignore[arg-type]
        # Single callback
        return [callbacks]  # type: ignore[list-item]

    @property
    def wip_strategy(self) -> WIPStrategy:
        """The current WIP strategy used by the shopfloor."""
        return self._wip_strategy

    def set_wip_strategy(self, strategy: WIPStrategy) -> None:
        """Replace the shopfloor's WIP strategy."""
        self._wip_strategy = strategy

    @property
    def metrics_collector(self) -> MetricsCollector | None:
        """Collector called when jobs complete (or None if disabled)."""
        return self._metrics_collector

    def set_metrics_collector(self, collector: MetricsCollector | None) -> None:
        """Replace the shopfloor's metrics collector (or disable with None)."""
        self._metrics_collector = collector

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
        1. Adds the job to the active jobs set
        2. Updates WIP values via the configured WIP strategy
        3. Records the PSP exit timestamp on the job
        4. Spawns the main processing coroutine for the job

        Args:
            job: The production job to release onto the shop floor.
                The job's routing must contain valid server references.

        Note:
            This method modifies the job's psp_exit_at timestamp and spawns
            an async process. The job will begin queuing at its first server
            immediately after this call.
        """
        self.jobs.add(job)
        self._wip_strategy.add_job(job, self.wip)

        self.env.debug(
            f"Job {job.id[:8]} entered shopfloor",
            component="ShopFloor",
            job_id=job.id,
            sku=job.sku,
            wip_total=sum(self.wip.values()),
            jobs_count=len(self.jobs),
        )

        job.psp_exit_at = self.env.now
        self.env.process(self.main(job))

    def signal_end_processing(self, job: ProductionJob) -> None:
        """Signal that a job has finished processing at its current server.

        This method triggers the job_processing_end event with the completed job
        as the event value, allowing any waiting processes to react to the
        processing completion. The event is then recreated for the next signal.

        This is called after each operation completes, not just when the job
        finishes its entire routing.

        Args:
            job: The job that just completed processing at a server.

        Example:
            Waiting for any job to finish processing::

                job = yield shop_floor.job_processing_end
                print(f"Job {job.id} finished an operation")
        """
        self.job_processing_end.succeed(job)
        self.job_processing_end = self.env.event()

    def signal_job_finished(self, job: ProductionJob) -> None:
        """Signal that a job has completed its entire routing.

        This method triggers the job_finished_event with the completed job
        as the event value, notifying any waiting processes that a job has
        finished all operations. The event is then recreated for the next signal.

        Unlike signal_end_processing, this is only called once per job when
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
        2. Executes before_operation hooks
        3. If a MaterialCoordinator is configured, waits for material delivery
        4. Processes the job for the specified duration
        5. Updates WIP via the configured WIP strategy
        6. Executes after_operation hooks
        7. Signals processing completion via signal_end_processing()

        After all operations complete, it:
        - Records the finish timestamp on the job
        - Moves the job from active (jobs) to completed (jobs_done)
        - Records metrics via the configured metrics collector
        - Calls on_job_finished callbacks
        - Signals job completion via signal_job_finished()

        Args:
            job: The production job to process through its routing.

        Yields:
            SimPy events for server requests, hooks, material delivery, and processing.

        Note:
            This method is automatically spawned by add() and should not be
            called directly. It runs as a SimPy process until the job completes.
        """
        for op_index, (server, processing_time) in enumerate(job.server_processing_times):
            self.env.debug(
                f"Job {job.id[:8]} queued at server {server._idx}",
                component="ShopFloor",
                job_id=job.id,
                server_id=server._idx,
                op_index=op_index,
            )

            with server.request(job=job) as request:
                yield request

                # Before-operation hooks
                for hook in self._before_operation:
                    yield from hook(job, server, op_index, processing_time)

                # Material coordination (if configured)
                if self.material_coordinator is not None:
                    yield from self.material_coordinator.ensure(job, server, op_index)

                # Process job
                yield self.env.process(server.process_job(job, processing_time))

                # Update WIP via strategy
                self._wip_strategy.complete_operation(job, server, op_index, processing_time, self.wip)

                # After-operation hooks
                for hook in self._after_operation:
                    yield from hook(job, server, op_index, processing_time)

                self.env.debug(
                    f"Job {job.id[:8]} completed op at server {server._idx}",
                    component="ShopFloor",
                    job_id=job.id,
                    server_id=server._idx,
                    op_index=op_index,
                    processing_time=processing_time,
                )

                self.signal_end_processing(job)

        # Job completion
        job.finished_at = self.env.now
        job.current_server = None
        job.done = True
        self.jobs.remove(job)
        self.jobs_done.append(job)
        self.total_time_in_system += job.time_in_system

        self.env.debug(
            f"Job {job.id[:8]} finished",
            component="ShopFloor",
            job_id=job.id,
            sku=job.sku,
            makespan=job.makespan,
            lateness=job.lateness,
            total_queue_time=job.total_queue_time,
        )

        # Record metrics via collector
        if self._metrics_collector is not None:
            self._metrics_collector.record(job)

        # Job finished callbacks
        for callback in self._on_job_finished:
            callback(job)

        self.signal_job_finished(job)
