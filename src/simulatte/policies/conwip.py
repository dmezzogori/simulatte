"""ConWIP (Constant Work-In-Process) order release policy.

Maintains a shop-wide WIP cap (job count). Jobs are released from PSP
whenever the shopfloor job count is below the cap. Selection by EDD.

Reference:
    Spearman, M. L., Woodruff, D. L. & Hopp, W. J. (1990).
    CONWIP: a pull alternative to kanban.
    International Journal of Production Research, 28(5), 879-894.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulatte.job import ProductionJob
    from simulatte.psp import PreShopPool


class ConWIP:
    """ConWIP (Constant Work-In-Process) order release.

    Maintains a shop-wide WIP cap (job count). Jobs are released from PSP
    whenever the shopfloor job count is below the cap. Selection by EDD.

    Two triggers are provided:

    - ``on_completion_release``: Wired via ``on_completion_trigger``. When a job
      finishes processing, releases PSP jobs (by EDD) until the WIP cap is reached.

    - ``on_arrival_release``: Wired via ``psp.on_arrival()``. When a job enters the
      PSP, immediately releases it if shop WIP is under the cap.

    Example:
        >>> from simulatte.policies.triggers import on_completion_trigger
        >>> conwip = ConWIP(wip_cap=12)
        >>> psp = PreShopPool(env=env, shopfloor=shopfloor)
        >>> psp.on_arrival(conwip.on_arrival_release)
        >>> env.process(on_completion_trigger(shopfloor, psp, conwip.on_completion_release))
    """

    def __init__(self, wip_cap: int) -> None:
        """Initialize the ConWIP release policy.

        Args:
            wip_cap: Maximum number of jobs allowed on the shopfloor simultaneously.
                Must be >= 1.

        Raises:
            ValueError: If wip_cap < 1.
        """
        if wip_cap < 1:
            msg = f"wip_cap must be >= 1, got {wip_cap}"
            raise ValueError(msg)
        self.wip_cap = wip_cap

    def on_completion_release(self, triggering_job: ProductionJob, psp: PreShopPool) -> None:
        """On job completion, release PSP jobs (by EDD) until WIP cap is reached.

        Release is synchronous: psp.release() increments len(shopfloor.jobs)
        before the next iteration, so the while-loop correctly tracks the
        running WIP count.

        Args:
            triggering_job: The job that just completed processing (unused but
                required by the on_completion_trigger signature).
            psp: The Pre-Shop Pool containing candidate jobs.
        """
        shopfloor = psp.shopfloor
        while not psp.empty and len(shopfloor.jobs) < self.wip_cap:
            candidate = min(psp.jobs, key=lambda j: j.due_date)
            psp.release(candidate)

    def on_arrival_release(self, job: ProductionJob, psp: PreShopPool) -> None:
        """On PSP arrival, release if shop WIP is under cap.

        Pure count-cap: no first-server-idle requirement. Guards against
        idempotency issues where an earlier callback may have already
        released the job.

        Args:
            job: The job that just arrived in the PSP.
            psp: The Pre-Shop Pool containing the job.
        """
        if job not in psp:
            return
        if len(psp.shopfloor.jobs) < self.wip_cap:
            psp.release(job)
