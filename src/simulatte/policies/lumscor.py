"""LUMS-COR release policy for workload-based job release control.

This module implements the LUMS-COR (Land's Upper limit for Make-Span with CORrected
workload) policy for controlling job releases from a Pre-Shop Pool (PSP) to the
shopfloor. The policy balances workload across servers while respecting planned
release dates to meet due date targets.

Reference:
    Land, M. J. (2006). Parameters and sensitivity in workload control.
    International Journal of Production Economics, 104(2), 625-638.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.dispatching_rules import planned_slack_time
from simulatte.policies.starvation_avoidance import starvation_avoidance
from simulatte.policies.triggers import periodic_trigger
from simulatte.shopfloor import CorrectedWIPStrategy

if TYPE_CHECKING:
    from simulatte.job import ProductionJob
    from simulatte.psp import PreShopPool
    from simulatte.router import Router
    from simulatte.server import Server
    from simulatte.shopfloor import ShopFloor
    from simulatte.typing import ProcessGenerator


class LumsCor:
    """Workload-based release policy using corrected WIP and planned release dates.

    LUMS-COR controls job releases by:
    1. Sorting PSP jobs by planned release date (earliest first)
    2. Releasing a job only if adding it keeps each server's corrected WIP
       at or below its workload norm

    The starvation release complements periodic releases by immediately releasing
    jobs when servers become idle or nearly idle.

    Construction is *active* (like ``Slar``): the instance sets
    ``CorrectedWIPStrategy`` on the shopfloor, the PST priority rule on the
    router, a periodic release trigger, a completion-triggered starvation
    release, and ``starvation_avoidance`` on PSP arrival.

    Example:
        ```python
        lumscor = LumsCor(
            shopfloor=shopfloor, psp=psp, router=router,
            wl_norm=10.0, check_timeout=1.0, allowance_factor=2,
        )
        ```
    """

    _POSTPONED_DELAY: float = 0.001

    def __init__(
        self,
        *,
        shopfloor: ShopFloor,
        psp: PreShopPool,
        router: Router,
        wl_norm: float | dict[Server, float],
        check_timeout: float,
        allowance_factor: int,
    ) -> None:
        """Initialize LUMS-COR and wire it into the system.

        Self-wiring (like ``Slar``): sets ``CorrectedWIPStrategy``, the PST
        priority rule on ``router``, a periodic release trigger, a
        completion-triggered starvation release, and ``starvation_avoidance`` on
        PSP arrival.

        Args:
            shopfloor: The shopfloor; its WIP strategy is set to corrected here.
            psp: The Pre-Shop Pool to release from.
            router: The router whose ``priority_policies`` is set to PST.
            wl_norm: Per-server workload norm. A scalar is expanded to every
                shopfloor server; a dict is used verbatim.
            check_timeout: Periodic release interval.
            allowance_factor: Buffer time per server for planned release dates.
        """
        shopfloor.set_wip_strategy(CorrectedWIPStrategy())
        self.wl_norm = wl_norm if isinstance(wl_norm, dict) else dict.fromkeys(shopfloor.servers, float(wl_norm))
        self.allowance_factor = allowance_factor
        router.priority_policies = planned_slack_time(allowance=float(allowance_factor))
        psp.env.process(periodic_trigger(psp, float(check_timeout), self.periodic_release))
        shopfloor.on_processing_end(lambda job, server: self.starvation_release(job, psp))
        psp.on_arrival(starvation_avoidance)

    def periodic_release(self, psp: PreShopPool) -> None:
        """Release jobs from PSP to shopfloor based on workload norms.

        Jobs are considered in order of their planned release date (earliest first).
        A job is released only if adding it would keep each server's corrected WIP
        at or below the configured workload norm.

        This method is designed to be used with `periodic_trigger`.

        Args:
            psp: The Pre-Shop Pool containing candidate jobs.
        """
        shopfloor = psp.shopfloor
        for job in sorted(psp.jobs, key=lambda j: j.planned_release_date(self.allowance_factor)):
            if all(
                shopfloor.wip.get(server, 0.0) + processing_time / (i + 1) <= self.wl_norm[server]
                for i, (server, processing_time) in enumerate(job.server_processing_times)
            ):
                psp.remove(job=job)
                shopfloor.add(job)

    def starvation_release(self, triggering_job: ProductionJob, psp: PreShopPool) -> None:
        """Release a job when a server risks starvation.

        When a server becomes empty or has only one job queued, releases the
        job from PSP with the earliest planned release date that starts at
        that server.

        This method is designed to be used with `on_completion_trigger`.

        Args:
            triggering_job: The job that just finished processing.
            psp: The Pre-Shop Pool to release jobs from.
        """
        server_triggered = triggering_job.previous_server

        if server_triggered is None:
            return

        is_empty = server_triggered.empty
        has_one = len(server_triggered.queue) == 1
        if is_empty:
            candidate_job = min(
                (job for job in psp.jobs if job.starts_at(server_triggered)),
                default=None,
                key=lambda j: j.planned_release_date(self.allowance_factor),
            )
            if candidate_job:
                psp.remove(job=candidate_job)
                psp.shopfloor.add(candidate_job)
        elif has_one:
            candidate_job = min(
                (job for job in psp.jobs if job.starts_at(server_triggered)),
                default=None,
                key=lambda j: j.planned_release_date(self.allowance_factor),
            )
            if candidate_job:
                psp.remove(job=candidate_job)
                psp.env.process(self._postponed_release(candidate_job, psp))

    def _postponed_release(self, job: ProductionJob, psp: PreShopPool) -> ProcessGenerator:
        """Release *job* to the shopfloor after a small delay.

        The delay ensures the job already waiting in the server queue acquires
        the server before the released job requests it, preserving queue order.
        """
        yield psp.env.timeout(self._POSTPONED_DELAY)
        psp.shopfloor.add(job)
