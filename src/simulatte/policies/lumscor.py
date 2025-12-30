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


class LumsCor:
    """Workload-based release policy using corrected WIP and planned release dates.

    LUMS-COR controls job releases by:
    1. Sorting PSP jobs by planned release date (earliest first)
    2. Releasing a job only if adding it keeps each server's corrected WIP
       at or below its workload norm

    The starvation release complements periodic releases by immediately releasing
    jobs when servers become idle or nearly idle.

    Requires CorrectedWIPStrategy on the shopfloor, which accounts for downstream
    workload when computing WIP at each server.

    Example:
        >>> from simulatte.policies.triggers import periodic_trigger, on_completion_trigger
        >>> lumscor = LumsCor(wl_norm={server: 10.0}, allowance_factor=2)
        >>> shopfloor.set_wip_strategy(CorrectedWIPStrategy())
        >>> psp = PreShopPool(env=env, shopfloor=shopfloor)
        >>> env.process(periodic_trigger(psp, 1.0, lumscor.periodic_release))
        >>> env.process(on_completion_trigger(shopfloor, psp, lumscor.starvation_release))
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
        self._validate_wip_strategy(shopfloor)
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
        self._validate_wip_strategy(psp.shopfloor)
        server_triggered = triggering_job.previous_server

        if server_triggered is None:
            return

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
                psp.shopfloor.add(candidate_job)
