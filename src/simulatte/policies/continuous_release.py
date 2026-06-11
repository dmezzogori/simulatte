"""Continuous workload-controlled order release policy.

Jobs may be released at any moment when a server's corrected workload
drops below its norm. On arrival, jobs are released to idle first servers
if norms permit (prevents empty-system deadlock).

Uses corrected aggregate load: contribution at position i = PT / (i + 1).
Requires CorrectedWIPStrategy on shopfloor.

Reference:
    Fernandes, N. O. & Carmo-Silva, S. (2011).
    Workload control under continuous order release.
    International Journal of Production Economics, 131(1), 257-262.
    https://doi.org/10.1016/j.ijpe.2010.09.026
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.policies.norms import expand_norms, fits_norms
from simulatte.policies.triggers import on_completion_trigger
from simulatte.shopfloor import CorrectedWIPStrategy

if TYPE_CHECKING:
    from simulatte.job import ProductionJob
    from simulatte.psp import PreShopPool
    from simulatte.server import Server
    from simulatte.shopfloor import ShopFloor


class ContinuousRelease:
    """Continuous workload-controlled order release.

    Jobs may be released at any moment when a server's corrected workload
    drops below its norm. On arrival, jobs are released to idle first servers
    if norms permit (prevents empty-system deadlock).

    Uses corrected aggregate load: contribution at position i = PT / (i + 1).
    Requires CorrectedWIPStrategy on shopfloor.

    Two triggers are provided:

    - ``on_completion_release``: Wired via ``on_completion_trigger``. When a job
      finishes processing, releases PSP jobs (sorted by planned release date)
      whose corrected WIP fits within norms.

    - ``on_arrival_release``: Wired via ``psp.on_arrival()``. When a job enters
      the PSP, immediately releases it if its first server is idle and norms permit.

    Construction is *active* (like ``Slar``): the instance sets
    ``CorrectedWIPStrategy`` on the shopfloor, a completion-triggered release,
    and ``on_arrival_release`` on PSP arrival.

    Example:
        ```python
        cr = ContinuousRelease(shopfloor=shopfloor, psp=psp, wl_norm=5.0)
        ```
    """

    def __init__(
        self,
        *,
        shopfloor: ShopFloor,
        psp: PreShopPool,
        wl_norm: float | dict[Server, float],
        allowance_factor: int = 2,
    ) -> None:
        """Initialize ContinuousRelease and wire it into the system.

        Args:
            shopfloor: The shopfloor; its WIP strategy is set to corrected here.
            psp: The Pre-Shop Pool to release from.
            wl_norm: Per-server workload norm (scalar expanded to all servers, or
                dict verbatim). Must cover every shopfloor server with a
                positive, finite value.
            allowance_factor: Buffer time per server for planned release dates.

        Raises:
            ValueError: If ``wl_norm`` is empty, contains non-positive or
                non-finite values, or misses any shopfloor server.
        """
        shopfloor.set_wip_strategy(CorrectedWIPStrategy())
        self.wl_norm = expand_norms(shopfloor=shopfloor, wl_norm=wl_norm)
        self.allowance_factor = allowance_factor
        psp.env.process(on_completion_trigger(shopfloor, psp, self.on_completion_release))
        psp.on_arrival(self.on_arrival_release)

    def on_completion_release(self, triggering_job: ProductionJob, psp: PreShopPool) -> None:
        """On job completion, release PSP jobs whose corrected WIP fits within norms.

        Sort PSP jobs by planned_release_date(allowance_factor), release those that fit.
        Each release updates shopfloor WIP, so subsequent checks see updated values.

        Args:
            triggering_job: The job that just finished processing (unused but
                required by the on_completion_trigger signature).
            psp: The Pre-Shop Pool containing candidate jobs.
        """
        del triggering_job  # Unused but required by trigger signature
        shopfloor = psp.shopfloor
        for job in sorted(list(psp.jobs), key=lambda j: j.planned_release_date(self.allowance_factor)):
            if fits_norms(job, wip=shopfloor.wip, norms=self.wl_norm):
                psp.release(job)

    def on_arrival_release(self, job: ProductionJob, psp: PreShopPool) -> None:
        """On PSP arrival, release to idle first server if norms permit.

        Guards:
        - If job not in psp: return (idempotence, another callback may have released it).
        - If first server is not idle: return.
        - If norms would be violated: return.

        Args:
            job: The job that just arrived in the PSP.
            psp: The Pre-Shop Pool containing the job.
        """
        if job not in psp:
            return
        first_server = job.servers[0]
        if not first_server.is_idle:
            return
        shopfloor = psp.shopfloor
        if not fits_norms(job, wip=shopfloor.wip, norms=self.wl_norm):
            return
        psp.release(job)
