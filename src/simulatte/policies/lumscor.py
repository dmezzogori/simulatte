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

from simulatte.shopfloor import CorrectedWIPStrategy

if TYPE_CHECKING:
    from simulatte.job import ProductionJob
    from simulatte.psp import PreShopPool
    from simulatte.server import Server
    from simulatte.shopfloor import ShopFloor
    from simulatte.typing import ProcessGenerator


class LumsCor:
    """Workload-based release policy using corrected WIP and planned release dates.

    LUMS-COR controls job releases by:
    1. Sorting PSP jobs by planned release date (earliest first)
    2. Releasing a job only if adding it keeps each server's corrected WIP
       at or below its workload norm

    The starvation trigger complements periodic releases by immediately releasing
    jobs when servers become idle or nearly idle.

    Requires CorrectedWIPStrategy on the shopfloor, which accounts for downstream
    workload when computing WIP at each server.

    Example:
        >>> lumscor = LumsCor(wl_norm={server: 10.0}, allowance_factor=2)
        >>> shopfloor.set_wip_strategy(CorrectedWIPStrategy())
        >>> psp = PreShopPool(..., psp_release_policy=lumscor)
        >>> env.process(lumscor.starvation_trigger(shopfloor, psp))
    """

    def __init__(self, *, wl_norm: dict[Server, float], allowance_factor: int) -> None:
        """Initialize the LUMS-COR release policy.

        Args:
            wl_norm: Workload norm for each server. Jobs are released only if
                adding them keeps each server's WIP at or below its norm.
            allowance_factor: Buffer time per server for due date calculations.
                Used to compute planned release dates (higher = earlier release).
        """
        self.wl_norm = wl_norm
        self.allowance_factor = allowance_factor

    def _validate_wip_strategy(self, shopfloor: ShopFloor) -> None:
        """Validate that the shopfloor uses CorrectedWIPStrategy.

        Args:
            shopfloor: The shopfloor to validate.

        Raises:
            TypeError: If shopfloor is not configured with CorrectedWIPStrategy.
        """
        if not isinstance(shopfloor.wip_strategy, CorrectedWIPStrategy):
            msg = "LumsCor requires CorrectedWIPStrategy. Use shopfloor.set_wip_strategy() first."
            raise TypeError(msg)

    def release(self, psp: PreShopPool, shopfloor: ShopFloor) -> None:
        """Release jobs from PSP to shopfloor based on workload norms.

        Jobs are considered in order of their planned release date (earliest first).
        A job is released only if adding it would keep each server's corrected WIP
        at or below the configured workload norm.

        Args:
            psp: The Pre-Shop Pool containing candidate jobs.
            shopfloor: The shopfloor to release jobs into.
        """
        self._validate_wip_strategy(shopfloor)
        for job in sorted(psp.jobs, key=lambda j: j.planned_release_date(self.allowance_factor)):
            if all(
                shopfloor.wip.get(server, 0.0) + processing_time / (i + 1) <= self.wl_norm[server]
                for i, (server, processing_time) in enumerate(job.server_processing_times)
            ):
                psp.remove(job=job)
                shopfloor.add(job)

    def starvation_trigger(self, shopfloor: ShopFloor, psp: PreShopPool) -> ProcessGenerator:
        """Generator process that releases jobs when servers risk starvation.

        Listens for job processing completion events. When a server becomes empty
        or has only one job queued, releases the job from PSP with the earliest
        planned release date that starts at that server.

        This process should be registered with env.process() and runs continuously.

        Args:
            shopfloor: The shopfloor to monitor for starvation.
            psp: The Pre-Shop Pool to release jobs from.

        Yields:
            Waits for job_processing_end events from the shopfloor.
        """
        self._validate_wip_strategy(shopfloor)
        while True:
            triggering_job: ProductionJob = yield shopfloor.job_processing_end
            server_triggered = triggering_job.previous_server
            is_empty = server_triggered.empty
            has_one = len(server_triggered.queue) == 1
            if is_empty or has_one:
                candidate_job = min(
                    (job for job in psp.jobs if job.starts_at(server_triggered)),
                    default=None,
                    key=lambda j: j.planned_release_date(self.allowance_factor),
                )
                if candidate_job:
                    psp.remove(job=candidate_job)
                    shopfloor.add(candidate_job)
