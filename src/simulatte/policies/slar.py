"""SLAR release policy for PSP with planned slack priorities."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.job import ProductionJob
    from simulatte.psp import PreShopPool
    from simulatte.server import Server
    from simulatte.typing import ProcessGenerator


class Slar:
    """Superfluous Load Avoidance Release (SLAR) policy.

    Implements the SLAR algorithm from Land & Gaalman (1998) extended with a
    more aggressive starvation-avoidance sub-trigger. On every job-completion
    event at a server, the policy decides whether to release a PSP job based
    on three branches:

    1. **Urgent-job insertion** (non-empty queue, all queued jobs non-urgent):
       release from PSP the urgent job (negative PST) starting at the server
       with the shortest first-operation processing time, to minimise disruption.

    2. **Starvation avoidance with postponed release** (non-empty queue of
       exactly one job): release from PSP the earliest-PST job starting at the
       server, with a tiny delay so the queued job starts processing first.

    3. **Empty-queue release**: release from PSP the earliest-PST job starting
       at the server.

    Example:
        >>> from simulatte.policies.triggers import on_completion_trigger
        >>> slar = Slar(allowance_factor=3.0)
        >>> psp = PreShopPool(env=env, shopfloor=shopfloor)
        >>> env.process(on_completion_trigger(shopfloor, psp, slar.decide_next_job))

    Reference:
        Land, M.J. & Gaalman, G.J.C. (1998). The performance of workload
        control concepts in job shops: Improving the release method.
        International Journal of Production Economics, 56-57, 347-364.
        https://doi.org/10.1016/S0925-5273(98)00052-8
    """

    _POSTPONED_DELAY: float = 0.001

    def __init__(self, allowance_factor: float = 2.0) -> None:
        """Initialize the SLAR release policy.

        Args:
            allowance_factor: Slack allowance per operation (parameter 'k' in paper).
                Higher values result in more conservative (later) release timing.
        """
        self.allowance_factor = allowance_factor

    def pst_priority_policy(self, job: ProductionJob, server: Server) -> float | None:
        """Get the planned slack time priority for a job at a server.

        Designed to be used as a priority_policy callback for jobs. Lower PST
        values indicate higher urgency (job is behind schedule).

        Args:
            job: The production job to evaluate.
            server: The server to calculate priority for.

        Returns:
            Planned slack time for the job at the server, or None if the server
            is not in the job's remaining routing.
        """
        return job.planned_slack_times(allowance=self.allowance_factor)[server]

    def decide_next_job(self, triggering_job: ProductionJob, psp: PreShopPool) -> None:
        """Decide whether to release a PSP job on a server's job-completion event.

        This method is the callback used with ``on_completion_trigger``. It
        selects a candidate via :meth:`_select_candidate_job` and releases it
        from PSP to the shopfloor when one is returned.

        Args:
            triggering_job: The job that just finished processing.
            psp: The Pre-Shop Pool to release jobs from.
        """
        server_triggered = triggering_job.previous_server

        candidate = self._select_candidate_job(server_triggered, psp)
        if candidate is not None:
            psp.remove(job=candidate)
            psp.shopfloor.add(candidate)

    def _select_candidate_job(self, server: Server, psp: PreShopPool) -> ProductionJob | None:
        """Select the PSP job to release (if any) after a completion at *server*.

        Returns a job when the caller should execute the release immediately.
        Returns ``None`` when no release is needed or when the release has been
        scheduled as a postponed SimPy sub-process (branch B).

        Args:
            server: The server that triggered the release check.
            psp: Pre-shop pool containing candidate jobs.

        Returns:
            The PSP job to release immediately, or None.
        """
        if server.empty:
            # Branch C: empty queue -> release earliest-PST PSP job starting here.
            psp_here = [j for j in psp.jobs if j.starts_at(server)]
            return min(
                psp_here,
                default=None,
                key=lambda j: self.pst_priority_policy(j, server),  # type: ignore[arg-type,return-value]
            )

        # Branch A: urgent PSP job release when all queued jobs are non-urgent.
        if all(self.pst_priority_policy(j, server) > 0 for j in server.queueing_jobs):  # type: ignore[operator]
            urgent_psp = [
                j
                for j in psp.jobs
                if j.starts_at(server) and self.pst_priority_policy(j, server) < 0
            ]
            candidate = min(urgent_psp, default=None, key=lambda j: j.processing_times[0])
            if candidate is not None:
                return candidate

        # Branch B: starvation avoidance via postponed release when queue has exactly 1.
        if len(server.queue) == 1:
            psp_here = [j for j in psp.jobs if j.starts_at(server)]
            candidate_two = min(
                psp_here,
                default=None,
                key=lambda j: self.pst_priority_policy(j, server),  # type: ignore[arg-type,return-value]
            )
            if candidate_two is not None:
                psp.env.process(self._postponed_release(candidate_two, psp))

        return None

    def _postponed_release(self, job: ProductionJob, psp: PreShopPool) -> ProcessGenerator:
        """Release *job* from PSP after a tiny delay.

        Removes *job* from the PSP immediately to avoid races with other
        triggers selecting the same candidate, then yields a small timeout so
        the single queued job at the triggering server starts processing first,
        then adds *job* to the shopfloor.

        Args:
            job: The PSP job selected for postponed release.
            psp: The Pre-Shop Pool the job is currently in.

        Yields:
            A SimPy timeout event.
        """
        psp.remove(job=job)
        yield psp.env.timeout(self._POSTPONED_DELAY)
        psp.shopfloor.add(job)
