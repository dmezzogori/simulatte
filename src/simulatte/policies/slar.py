"""SLAR release policy for PSP with planned slack priorities."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.job import ProductionJob
    from simulatte.psp import PreShopPool
    from simulatte.server import Server
    from simulatte.shopfloor import ShopFloor
    from simulatte.typing import ProcessGenerator


class Slar:
    """Superfluous Load Avoidance Release (SLAR) policy.

    Implements the SLAR algorithm from Land & Gaalman (1998) with an extension
    for more aggressive starvation avoidance.

    The policy releases jobs from the Pre-Shop Pool based on two triggers:

    1. **Starvation avoidance**: When a station's queue is empty or has only
       one job (extension: original paper only triggers on empty), release
       the job with earliest planned start time (lowest PST).

    2. **Urgent job insertion**: When all queued jobs at a station are
       non-urgent (positive PST), release an urgent job (negative PST)
       with the shortest processing time to minimize disruption.

    Reference:
        Land, M.J. & Gaalman, G.J.C. (1998). The performance of workload
        control concepts in job shops: Improving the release method.
        International Journal of Production Economics, 56-57, 347-364.
        https://doi.org/10.1016/S0925-5273(98)00052-8

    Args:
        allowance_factor: Slack allowance per operation (parameter 'k' in paper).
            Higher values result in more conservative (later) release timing.
    """

    def __init__(self, allowance_factor: float = 2.0) -> None:
        self.allowance_factor = allowance_factor

    def pst_priority_policy(self, job: ProductionJob, server: Server) -> float | None:
        return job.planned_slack_times(allowance=self.allowance_factor)[server]

    def _pst_value(self, job: ProductionJob, server: Server) -> float:
        """Return planned slack time as float (None -> 0)."""
        pst = self.pst_priority_policy(job, server)
        return float(pst) if pst is not None else 0.0

    def slar_release_triggers(self, shopfloor: ShopFloor, psp: PreShopPool) -> ProcessGenerator:
        """Monitor job completions and trigger releases from the Pre-Shop Pool.

        This generator process waits for job processing completions and evaluates
        whether to release a new job based on the SLAR algorithm triggers.

        Args:
            shopfloor: The shopfloor to monitor for job completions.
            psp: The Pre-Shop Pool to release jobs from.

        Yields:
            Waits for job_processing_end events from the shopfloor.
        """
        while True:
            triggering_job: ProductionJob = yield shopfloor.job_processing_end
            server_triggered = triggering_job.previous_server

            if server_triggered is None:  # pragma: no cover
                continue

            candidate_job: ProductionJob | None = None

            is_empty = server_triggered.empty
            has_one = len(server_triggered.queue) == 1

            # Extension: Also trigger when queue has exactly 1 job to prevent
            # imminent starvation (more aggressive than original paper algorithm)
            if is_empty or has_one:
                candidate_job = min(
                    (job for job in psp.jobs if job.starts_at(server_triggered)),
                    default=None,
                    key=lambda j: self._pst_value(j, server_triggered),
                )
            elif all(self._pst_value(job, server_triggered) > 0 for job in server_triggered.queueing_jobs):
                # Per paper: select shortest processing time to minimize disruption
                # when inserting urgent job into non-urgent queue
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
