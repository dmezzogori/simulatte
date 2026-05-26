"""SLAR release policy for PSP with planned slack priorities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.dispatching_rules import planned_slack_time
from simulatte.policies.starvation_avoidance import starvation_avoidance

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

    from simulatte.job import BaseJob, ProductionJob
    from simulatte.psp import PreShopPool
    from simulatte.router import Router
    from simulatte.server import Server
    from simulatte.shopfloor import ShopFloor
    from simulatte.typing import ProcessGenerator


class Slar:
    """Superfluous Load Avoidance Release (SLAR) policy.

    Implements the SLAR algorithm from Land & Gaalman (1998) extended with
    a starvation-avoidance sub-trigger. On every job-completion event at a
    server, `_consider_release` evaluates three branches (in order):

    1. **Idle prevention** (paper rule): if the server queue is empty,
       release the most urgent PSP candidate (lowest PST) to prevent the
       server from idling.
    2. **Urgent insertion** (paper rule): if no queued job is urgent
       (negative PST) but the PSP holds an urgent candidate, release the
       urgent candidate with the shortest processing time to minimise
       disruption to the queue. Skipped if an urgent job is already in
       the queue — the priority rule will dispatch it next.
    3. **Drain safety net** (extension): if exactly one job remains in
       the queue, schedule a postponed release of the most urgent PSP
       candidate so the queue is replenished before it drains. Mutually
       exclusive with (2): if (2) fires, (3) does not, to avoid
       superfluous load.

    Construction is *active*: the instance self-registers with
    ``shopfloor.on_processing_end`` and ``psp.on_arrival(starvation_avoidance)``,
    and (if a ``router`` is provided) sets ``router.priority_policies`` to
    the PST dispatching rule. This makes it impossible to forget the
    priority wiring that the algorithm depends on.

    Example:
        >>> slar = Slar(
        ...     shopfloor=shop_floor, psp=psp, router=router,
        ...     allowance_factor=3.0,
        ... )

    Reference:
        Land, M.J. & Gaalman, G.J.C. (1998). The performance of workload
        control concepts in job shops: Improving the release method.
        International Journal of Production Economics, 56-57, 347-364.
        https://doi.org/10.1016/S0925-5273(98)00052-8
    """

    _POSTPONED_DELAY: float = 0.001

    def __init__(
        self,
        *,
        shopfloor: ShopFloor,
        psp: PreShopPool,
        router: Router,
        allowance_factor: float = 2.0,
    ) -> None:
        """Initialize the SLAR release policy and wire it into the system.

        Args:
            shopfloor: The shopfloor whose completion events drive release
                decisions. Registered via ``on_processing_end``.
            psp: The Pre-Shop Pool to release jobs from. Also registered
                for ``on_arrival(starvation_avoidance)`` so jobs whose
                first server is idle are released immediately.
            router: The router whose ``priority_policies`` is set to the
                PST dispatching rule. Required: SLAR's queue-dispatch
                semantics depend on PST-based priority on every job.
            allowance_factor: Per-operation queue-time allowance ``k``
                in PST (parameter 'k' in the paper). Higher values yield
                more conservative (later) release timing.
        """
        self.shopfloor = shopfloor
        self.psp = psp
        self.router = router
        self.allowance_factor = allowance_factor

        self._pst: Callable[[BaseJob, Server], float] = planned_slack_time(allowance=allowance_factor)
        router.priority_policies = self._pst
        shopfloor.on_processing_end(self._consider_release)
        psp.on_arrival(starvation_avoidance)

    def _consider_release(self, _triggering_job: ProductionJob, server: Server) -> None:
        """Decide whether to release a PSP job after a completion at *server*.

        Dispatches to the three release branches in priority order.

        Args:
            _triggering_job: The job that just finished processing
                (signature required by ``on_processing_end``; only the
                server identity is consulted).
            server: The server where the job completed.
        """
        candidates = tuple(j for j in self.psp.jobs if j.starts_at(server))
        if not candidates:
            return

        if self._release_for_idle_prevention(server, candidates):
            return

        if self._release_urgent_insertion(server, candidates):
            return

        self._schedule_drain_safety_net(server, candidates)

    def _release_for_idle_prevention(
        self,
        server: Server,
        candidates: tuple[ProductionJob, ...],
    ) -> bool:
        """Release the most urgent candidate when the queue is empty.

        Returns ``True`` iff a job was released.
        """
        if not server.empty:
            return False
        candidate = min(candidates, key=lambda j: self._pst(j, server))
        self.psp.release(job=candidate)
        return True

    def _release_urgent_insertion(
        self,
        server: Server,
        candidates: tuple[ProductionJob, ...],
    ) -> bool:
        """Insert an urgent PSP candidate when no queued job is urgent.

        If at least one queued job is already urgent (PST < 0), the
        priority rule will dispatch it next, so no insertion is needed.
        Otherwise releases the urgent PSP candidate with the shortest
        processing time (min-SPT tie-break to minimise disruption).

        Returns ``True`` iff a job was released.
        """
        if any(self._pst(j, server) < 0 for j in server.queueing_jobs):
            return False
        urgent = [j for j in candidates if self._pst(j, server) < 0]
        if not urgent:
            return False
        candidate = min(urgent, key=lambda j: j.processing_times[0])
        self.psp.release(job=candidate)
        return True

    def _schedule_drain_safety_net(
        self,
        server: Server,
        candidates: tuple[ProductionJob, ...],
    ) -> None:
        """Schedule a postponed release when exactly one job remains in the queue.

        Mutually exclusive with `_release_urgent_insertion` — if
        that branch already released a job, this one is skipped to avoid
        superfluous load (the just-released job will refill the queue).
        """
        if len(server.queue) != 1:
            return
        candidate = min(candidates, key=lambda j: self._pst(j, server))
        self.psp.env.process(self._postponed_release(candidate))

    def _postponed_release(self, job: ProductionJob) -> ProcessGenerator:
        """Release *job* from PSP after a tiny delay.

        Removes *job* from the PSP immediately so other triggers cannot
        select the same candidate, then yields a small timeout so the
        single queued job at the triggering server starts processing first,
        then adds *job* to the shopfloor.
        """
        self.psp.remove(job=job)
        yield self.psp.env.timeout(self._POSTPONED_DELAY)
        self.psp.shopfloor.add(job)
