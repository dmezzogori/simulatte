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

from simulatte.policies.triggers import on_completion_trigger

if TYPE_CHECKING:
    from simulatte.job import ProductionJob
    from simulatte.psp import PreShopPool
    from simulatte.shopfloor import ShopFloor


class ConWIP:
    """ConWIP (Constant Work-In-Process) order release.

    Maintains a shop-wide WIP cap (job count). Jobs are released from PSP
    whenever the shopfloor job count is below the cap. Selection by EDD.

    Two triggers are provided:

    - ``on_completion_release``: Wired via ``on_completion_trigger``. When a job
      finishes processing, releases PSP jobs (by EDD) until the WIP cap is reached.

    - ``on_arrival_release``: Wired via ``psp.on_arrival()``. When a job enters the
      PSP, immediately releases it if shop WIP is under the cap.

    Construction is *active* (like ``Slar``): the instance wires a
    completion-triggered release and ``on_arrival_release`` on PSP arrival.

    Example:
        ```python
        conwip = ConWIP(shopfloor=shopfloor, psp=psp, wip_cap=12)
        ```
    """

    def __init__(self, *, shopfloor: ShopFloor, psp: PreShopPool, wip_cap: int) -> None:
        """Initialize ConWIP and wire it into the system.

        Args:
            shopfloor: The shopfloor whose completions drive release.
            psp: The Pre-Shop Pool to release from.
            wip_cap: Maximum jobs on the floor at once. Must be >= 1.

        Raises:
            ValueError: If wip_cap < 1.
        """
        if wip_cap < 1:
            msg = f"wip_cap must be >= 1, got {wip_cap}"
            raise ValueError(msg)
        self.wip_cap = wip_cap
        psp.env.process(on_completion_trigger(shopfloor, psp, self.on_completion_release))
        psp.on_arrival(self.on_arrival_release)

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
