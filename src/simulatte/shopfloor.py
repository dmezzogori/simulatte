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
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.environment import Environment

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.job import ProductionJob
    from simulatte.materials import MaterialCoordinator
    from simulatte.server import Server
    from simulatte.typing import ProcessGenerator


class ShopFloor:
    """Central orchestrator for job flow through a manufacturing simulation.

    The ShopFloor manages the complete lifecycle of production jobs as they move
    through a sequence of servers. It tracks work-in-progress (WIP) at each server,
    maintains exponential moving average (EMA) metrics for performance monitoring,
    and signals events when jobs complete processing steps or finish entirely.

    When a MaterialCoordinator is configured, the ShopFloor coordinates material
    delivery before processing begins at each operation, implementing FIFO blocking
    to ensure materials arrive before processing starts.

    Attributes:
        env: The simulation environment providing time and process management.
        ema_alpha: Smoothing factor for EMA calculations (0 < alpha <= 1).
            Smaller values give more weight to historical data.
        material_coordinator: Optional coordinator for material delivery.
            When set, ensures materials are delivered before each operation.
        servers: List of servers registered with this shop floor.
        jobs: Set of jobs currently being processed on the shop floor.
        jobs_done: List of completed jobs in order of completion.
        wip: Dictionary mapping each server to its current WIP value
            (sum of remaining processing times for jobs routing through it).
        total_time_in_system: Cumulative time spent by all completed jobs.
        ema_makespan: EMA of job makespan (creation to completion time).
        ema_tardy_jobs: EMA of tardy job indicator (1 if late, 0 otherwise).
        ema_early_jobs: EMA of early job indicator (1 if early, 0 otherwise).
        ema_in_window_jobs: EMA of in-window job indicator.
        ema_time_in_psp: EMA of time jobs spend in the Pre-Shop Pool.
        ema_time_in_shopfloor: EMA of time jobs spend on the shop floor.
        ema_total_queue_time: EMA of total queue time across all servers.
        enable_corrected_wip: When True, applies position-based WIP correction
            that discounts processing times by operation position.
        job_processing_end: SimPy event triggered when any job finishes
            processing at a server. Recreated after each trigger.
        job_finished_event: SimPy event triggered when any job completes
            its entire routing. Recreated after each trigger.
        maximum_wip_value: Peak total WIP observed during simulation.
        maximum_shopfloor_jobs: Peak number of concurrent jobs observed.

    Example:
        Basic usage with a single server::

            from simulatte import Environment, Server, ProductionJob, ShopFloor

            env = Environment()
            server = Server(env=env, capacity=1)
            shop_floor = ShopFloor(env=env)
            shop_floor.servers.append(server)

            job = ProductionJob(
                env=env,
                sku="PART-A",
                servers=[server],
                processing_times=[10.0],
                due_date=100.0,
            )

            shop_floor.add(job)
            env.run()

            print(f"Job completed at: {job.finished_at}")
    """

    def __init__(
        self,
        *,
        env: Environment,
        ema_alpha: float = 0.01,
        material_coordinator: MaterialCoordinator | None = None,
    ) -> None:
        """Initialize a new ShopFloor instance.

        Args:
            env: The simulation environment that provides time management,
                event scheduling, and process coordination.
            ema_alpha: Smoothing factor for exponential moving average
                calculations. Must be in range (0, 1]. Smaller values
                (e.g., 0.01) give more weight to historical observations,
                while larger values respond more quickly to recent data.
                Defaults to 0.01.
            material_coordinator: Optional coordinator for handling material
                delivery to servers. When provided, the shop floor will
                ensure materials are delivered before processing begins
                at each operation, implementing FIFO blocking behavior.
        """
        self.env = env
        self.ema_alpha = ema_alpha
        self.material_coordinator = material_coordinator

        self.servers: list[Server] = []  # Instance variable, not ClassVar
        self.jobs: set[ProductionJob] = set()
        self.jobs_done: list[ProductionJob] = []
        self.wip: dict[Server, float] = {}
        self.total_time_in_system: float = 0.0

        self.ema_makespan: float = 0.0
        self.ema_tardy_jobs: float = 0.0
        self.ema_early_jobs: float = 0.0
        self.ema_in_window_jobs: float = 0.0

        self.ema_time_in_psp: float = 0.0
        self.ema_time_in_shopfloor: float = 0.0
        self.ema_total_queue_time: float = 0.0

        self.enable_corrected_wip: bool = False

        self.job_processing_end = self.env.event()
        self.job_finished_event = self.env.event()

        self.maximum_wip_value: float = 0.0
        self.maximum_shopfloor_jobs: int = 0

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
        2. Updates WIP values for all servers in the job's routing
        3. Records the PSP exit timestamp on the job
        4. Spawns the main processing coroutine for the job

        The WIP calculation depends on enable_corrected_wip:
        - When False: Full processing time is added to each server's WIP
        - When True: Processing time is discounted by operation position
          (first op gets full time, second gets time/2, etc.)

        Args:
            job: The production job to release onto the shop floor.
                The job's routing must contain valid server references.

        Note:
            This method modifies the job's psp_exit_at timestamp and spawns
            an async process. The job will begin queuing at its first server
            immediately after this call.
        """
        self.jobs.add(job)

        if self.enable_corrected_wip:
            for i, (server, processing_time) in enumerate(job.server_processing_times):
                self.wip.setdefault(server, 0.0)
                self.wip[server] += processing_time / (i + 1)
        else:
            for server, processing_time in job.server_processing_times:
                self.wip.setdefault(server, 0.0)
                self.wip[server] += processing_time

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
        2. If a MaterialCoordinator is configured, waits for material delivery
           (FIFO blocking - holds server while waiting for materials)
        3. Processes the job for the specified duration
        4. Updates WIP values (decrements current server, adjusts remaining if
           corrected WIP is enabled)
        5. Signals processing completion via signal_end_processing()

        After all operations complete, it:
        - Records the finish timestamp on the job
        - Moves the job from active (jobs) to completed (jobs_done)
        - Updates all EMA metrics (makespan, tardiness, queue times, etc.)
        - Signals job completion via signal_job_finished()

        Args:
            job: The production job to process through its routing.

        Yields:
            SimPy events for server requests, material delivery, and processing.

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

                # If materials are required, block while holding server (FIFO blocking)
                if self.material_coordinator is not None:
                    yield from self.material_coordinator.ensure(job, server, op_index)

                yield self.env.process(server.process_job(job, processing_time))
                self.wip[server] -= processing_time

                if self.enable_corrected_wip:
                    for i, remaining_server in enumerate(job.remaining_routing):
                        remaining_processing_time = job.routing[remaining_server]
                        self.wip[remaining_server] -= remaining_processing_time / (i + 2)
                        self.wip[remaining_server] += remaining_processing_time / (i + 1)

                self.env.debug(
                    f"Job {job.id[:8]} completed op at server {server._idx}",
                    component="ShopFloor",
                    job_id=job.id,
                    server_id=server._idx,
                    op_index=op_index,
                    processing_time=processing_time,
                )

                self.signal_end_processing(job)

        job.finished_at = self.env.now
        job.current_server = None
        job.done = True
        self.jobs.remove(job)
        self.jobs_done.append(job)
        self.total_time_in_system += job.time_in_system

        lateness = job.lateness
        in_window = job.is_finished_in_due_date_window()

        self.env.debug(
            f"Job {job.id[:8]} finished",
            component="ShopFloor",
            job_id=job.id,
            sku=job.sku,
            makespan=job.makespan,
            lateness=lateness,
            total_queue_time=job.total_queue_time,
        )
        self.ema_makespan += self.ema_alpha * (job.makespan - self.ema_makespan)

        self.ema_tardy_jobs += self.ema_alpha * ((1 if not in_window and lateness > 0 else 0) - self.ema_tardy_jobs)
        self.ema_early_jobs += self.ema_alpha * ((1 if not in_window and lateness < 0 else 0) - self.ema_early_jobs)
        self.ema_in_window_jobs += self.ema_alpha * ((1 if in_window else 0) - self.ema_in_window_jobs)

        self.ema_time_in_psp += self.ema_alpha * (job.time_in_psp - self.ema_time_in_psp)
        self.ema_time_in_shopfloor += self.ema_alpha * (job.time_in_shopfloor - self.ema_time_in_shopfloor)
        self.ema_total_queue_time += self.ema_alpha * (job.total_queue_time - self.ema_total_queue_time)

        self.signal_job_finished(job)
