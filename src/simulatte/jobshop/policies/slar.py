"""SLAR release policy for PSP with planned slack priorities."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.jobshop.job import Job
    from simulatte.jobshop.psp.psp import PreShopPool
    from simulatte.jobshop.server.server import Server
    from simulatte.jobshop.shopfloor import ShopFloor
    from simulatte.jobshop.typing import ProcessGenerator


class Slar:
    """Superfluous Load Avoidance Release policy."""

    def __init__(self, allowance_factor: int = 2) -> None:
        self.allowance_factor = allowance_factor

    def pst_priority_policy(self, job: Job, server: Server) -> float | None:
        return job.planned_slack_times(allowance=self.allowance_factor)[server]

    def _pst_value(self, job: Job, server: Server) -> float:
        """Return planned slack time as float (None -> 0)."""
        pst = self.pst_priority_policy(job, server)
        return float(pst) if pst is not None else 0.0

    def slar_release_triggers(self, shopfloor: ShopFloor, psp: PreShopPool) -> ProcessGenerator:
        while True:
            triggering_job: Job = yield shopfloor.job_processing_end
            server_triggered = triggering_job.previous_server

            candidate_job: Job | None = None

            is_empty = server_triggered.empty
            has_one = len(server_triggered.queue) == 1
            if is_empty or has_one:
                candidate_job = min(
                    (job for job in psp.jobs if job.starts_at(server_triggered)),
                    default=None,
                    key=lambda j: self._pst_value(j, server_triggered),
                )
            elif all(self._pst_value(job, server_triggered) > 0 for job in server_triggered.queueing_jobs):
                candidate_job = min(
                    (
                        job
                        for job in psp.jobs
                        if (job.starts_at(server_triggered) and self._pst_value(job, server_triggered) < 0)
                    ),
                    default=None,
                    key=lambda j: j.processing_times[0],
                )

            if candidate_job is not None:
                psp.remove(job=candidate_job)
                shopfloor.add(candidate_job)
