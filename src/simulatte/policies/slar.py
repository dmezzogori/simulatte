"""SLAR release policy for PSP with planned slack priorities."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.job import BaseJob, ProductionJob
    from simulatte.psp import PreShopPool
    from simulatte.server import Server
    from simulatte.typing import ProcessGenerator


class Slar:
    """Superfluous Load Avoidance Release (SLAR) policy.

    Implements the SLAR algorithm from Land & Gaalman (1998) extended with a
    starvation-avoidance sub-trigger. On every job-completion event at a
    server, the policy decides whether to release a PSP job based on three
    branches (evaluated in order):

    1. **Empty-queue release**: if the server queue is empty, release the
       most urgent PSP candidate (lowest PST) to prevent idling.

    2. **Urgent-job insertion**: if all queued jobs are non-urgent (positive
       PST), release from PSP the urgent job (negative PST) with the shortest
       processing time to minimise disruption.

    3. **Postponed starvation avoidance**: if exactly one job remains in the
       queue, schedule a PSP candidate for delayed release so the queue is
       replenished before it drains.

    Queue ordering is handled externally via a PST-based priority policy
    wired through the Router (see ``build_slar_system``), not by this class.

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

    def pst_priority_policy(self, job: BaseJob, server: Server) -> float:
        """Get the planned slack time priority for a job at a server.

        Designed to be used as a priority_policy callback for jobs. Lower PST
        values indicate higher urgency (job is behind schedule). Returns
        infinity for servers already exited or not in the job's routing.

        Args:
            job: The production job to evaluate.
            server: The server to calculate priority for.

        Returns:
            Planned slack time for the job at the server.
        """
        pst = job.planned_slack_time_at(server, allowance=self.allowance_factor)
        if pst is None:
            return float("inf")
        return pst

    def decide_next_job(self, triggering_job: ProductionJob, psp: PreShopPool) -> None:
        """Decide whether to release a PSP job on a server's job-completion event.

        This method is the callback used with ``on_completion_trigger``. It
        selects a candidate via :meth:`_select_psp_candidate_job` and releases
        it from PSP to the shopfloor when one is returned.

        Args:
            triggering_job: The job that just finished processing.
            psp: The Pre-Shop Pool to release jobs from.
        """
        server_triggered = triggering_job.previous_server
        assert server_triggered is not None

        candidate = self._select_psp_candidate_job(server_triggered, psp)
        if candidate is not None:
            psp.remove(job=candidate)
            psp.shopfloor.add(candidate)

    def _select_psp_candidate_job(self, server: Server, psp: PreShopPool) -> ProductionJob | None:
        """Select a PSP job to release (if any) after a job completion at *server*.

        Examines the server's queue state and the Pre-Shop Pool to decide
        whether a PSP job should be released to the shopfloor. Three branches
        are evaluated in order:

        1. **Empty queue**: release the most urgent candidate (lowest PST)
           to prevent the server from idling.
        2. **Urgent insertion**: if all queued jobs are non-urgent, release
           an urgent candidate (negative PST) with the shortest processing
           time to minimise disruption to the existing queue.
        3. **Postponed starvation avoidance**: if exactly one job remains in
           the queue, schedule a delayed release so the queue is replenished
           before it drains. The candidate is removed from PSP immediately
           (to avoid double-selection) but enters the shopfloor after a small
           delay.

        Branches 1 and 2 return the candidate for the caller to release
        immediately. Branch 3 schedules a postponed sub-process and returns
        None.

        Each branch is delegated to a dedicated helper so subclasses can
        override a single branch in isolation (see :class:`SlarLimit`).

        Args:
            server: The server where the job completion triggered this check.
            psp: The Pre-Shop Pool containing candidate jobs.

        Returns:
            A PSP job to release immediately, or None.
        """
        psp_candidates = tuple(j for j in psp.jobs if j.starts_at(server))

        if not psp_candidates:
            return None

        candidate = self._branch_empty_queue(server, psp_candidates)
        if candidate is not None:
            return candidate

        candidate = self._branch_urgent_insertion(server, psp_candidates, psp)
        if candidate is not None:
            return candidate

        self._branch_postponed_starvation(server, psp_candidates, psp)
        return None

    def _branch_empty_queue(
        self,
        server: Server,
        candidates: tuple[ProductionJob, ...],
    ) -> ProductionJob | None:
        """Branch 1: release the most urgent candidate when the queue is empty."""
        if not server.empty:
            return None
        return min(candidates, key=lambda j: self.pst_priority_policy(j, server))

    def _branch_urgent_insertion(
        self,
        server: Server,
        candidates: tuple[ProductionJob, ...],
        psp: PreShopPool,  # noqa: ARG002
    ) -> ProductionJob | None:
        """Branch 2: release the min-SPT urgent candidate when all queued jobs are non-urgent."""
        if not all(self.pst_priority_policy(j, server) > 0 for j in server.queueing_jobs):
            return None
        return min(
            (j for j in candidates if self.pst_priority_policy(j, server) < 0),
            default=None,
            key=lambda j: j.processing_times[0],
        )

    def _branch_postponed_starvation(
        self,
        server: Server,
        candidates: tuple[ProductionJob, ...],
        psp: PreShopPool,
    ) -> None:
        """Branch 3: schedule a postponed release when exactly one job remains in the queue."""
        if len(server.queue) != 1:
            return
        candidate = min(candidates, key=lambda j: self.pst_priority_policy(j, server))
        psp.env.process(self._postponed_release(candidate, psp))

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
