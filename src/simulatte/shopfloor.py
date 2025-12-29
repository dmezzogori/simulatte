"""Shop floor orchestration for jobshop simulations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.environment import Environment

if TYPE_CHECKING:  # pragma: no cover
    from simpy.core import SimTime

    from simulatte.job import ProductionJob
    from simulatte.materials import MaterialCoordinator
    from simulatte.server import Server
    from simulatte.typing import ProcessGenerator

    # Type alias for jobs that can be processed
    type Job = ProductionJob


class ShopFloor:
    """Tracks jobs, WIP, and completion events across servers.

    Optionally integrates with MaterialCoordinator to handle material
    delivery before processing at each operation.
    """

    def __init__(
        self,
        *,
        env: Environment,
        ema_alpha: float = 0.01,
        material_coordinator: MaterialCoordinator | None = None,
    ) -> None:
        self.env = env
        self.ema_alpha = ema_alpha
        self.material_coordinator = material_coordinator

        self.servers: list[Server] = []  # Instance variable, not ClassVar
        self.jobs: set[Job] = set()
        self.jobs_done: list[Job] = []
        self.wip: dict[Server, float] = {}
        self.total_time_in_system: float = 0.0
        self.last_throughput_snapshot_time: SimTime = 0
        self.last_throughput_snapshot_jobs_done: int = 0
        self.current_hourly_throughput: int = 0

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
        if not self.jobs_done:
            return 0.0
        return self.total_time_in_system / len(self.jobs_done)

    def add(self, job: Job) -> None:
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

    def signal_end_processing(self, job: Job) -> None:
        self.job_processing_end.succeed(job)
        self.job_processing_end = self.env.event()

    def signal_job_finished(self, job: Job) -> None:
        self.job_finished_event.succeed(job)
        self.job_finished_event = self.env.event()

    def main(self, job: Job) -> ProcessGenerator:
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
        self._update_hourly_throughput_snapshot()

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

    def _update_hourly_throughput_snapshot(self) -> None:
        time_window = 60
        if self.env.now - self.last_throughput_snapshot_time >= time_window:
            jobs_done_now = len(self.jobs_done)
            jobs_completed_in_window = jobs_done_now - self.last_throughput_snapshot_jobs_done
            self.last_throughput_snapshot_time = self.env.now
            self.last_throughput_snapshot_jobs_done = jobs_done_now
            self.current_hourly_throughput = jobs_completed_in_window
