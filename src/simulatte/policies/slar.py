"""SLAR release policy for PSP with planned slack priorities."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.job import ProductionJob
    from simulatte.psp import PreShopPool
    from simulatte.server import Server


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

    Example:
        >>> from simulatte.policies.triggers import on_completion_trigger
        >>> slar = Slar(allowance_factor=3.0)
        >>> psp = PreShopPool(env=env, shopfloor=shopfloor)
        >>> env.process(on_completion_trigger(shopfloor, psp, slar.starvation_release))

    Reference:
        Land, M.J. & Gaalman, G.J.C. (1998). The performance of workload
        control concepts in job shops: Improving the release method.
        International Journal of Production Economics, 56-57, 347-364.
        https://doi.org/10.1016/S0925-5273(98)00052-8
    """

    def __init__(self, allowance_factor: float = 2.0) -> None:
        """Initialize the SLAR release policy.

        Args:
            allowance_factor: Slack allowance per operation (parameter 'k' in paper).
                Higher values result in more conservative (later) release timing.
        """
        self.allowance_factor = allowance_factor

    def pst_priority_policy(self, job: ProductionJob, server: Server) -> float | None:
        """Get the planned slack time priority for a job at a server.

        This method is designed to be used as a priority_policy callback for jobs.
        Lower PST values indicate higher urgency (job is behind schedule).

        Args:
            job: The production job to evaluate.
            server: The server to calculate priority for.

        Returns:
            Planned slack time for the job at the server, or None if the server
            is not in the job's routing.
        """
        return job.planned_slack_times(allowance=self.allowance_factor)[server]

    def _pst_value(self, job: ProductionJob, server: Server) -> float:
        """Get planned slack time as a numeric value for comparisons.

        Converts None values to 0.0 to enable consistent sorting and comparisons.

        Args:
            job: The production job to evaluate.
            server: The server to calculate PST for.

        Returns:
            Planned slack time as float, or 0.0 if PST is None.
        """
        pst = self.pst_priority_policy(job, server)
        return float(pst) if pst is not None else 0.0

    def starvation_release(self, triggering_job: ProductionJob, psp: PreShopPool) -> None:
        """Release a job based on SLAR algorithm triggers.

        Evaluates whether to release a new job when a job finishes processing.
        Implements two release triggers: starvation avoidance and urgent job
        insertion.

        This method is designed to be used with `on_completion_trigger`.

        Note:
            This implementation extends the original paper algorithm by also
            triggering starvation avoidance when a queue has exactly one job,
            not just when empty.

        Args:
            triggering_job: The job that just finished processing.
            psp: The Pre-Shop Pool to release jobs from.
        """
        server_triggered = triggering_job.previous_server

        if server_triggered is None:
            return

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
        elif all(self._pst_value(job, server_triggered) > 0 for job in server_triggered.queueing_jobs):  # type: ignore[arg-type]
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
            psp.shopfloor.add(candidate_job)
