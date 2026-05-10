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
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

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

    Example:
        >>> from simulatte.policies.triggers import on_completion_trigger
        >>> cr = ContinuousRelease(wl_norm={server: 5.0}, allowance_factor=2)
        >>> shopfloor.set_wip_strategy(CorrectedWIPStrategy())
        >>> psp = PreShopPool(env=env, shopfloor=shopfloor)
        >>> psp.on_arrival(cr.on_arrival_release)
        >>> env.process(on_completion_trigger(shopfloor, psp, cr.on_completion_release))
    """

    def __init__(self, wl_norm: dict[Server, float], allowance_factor: int = 2) -> None:
        """Initialize the ContinuousRelease policy.

        Args:
            wl_norm: Workload norm for each server. Jobs are released only if
                adding them keeps each server's corrected WIP at or below its norm.
                Must not be empty. All values must be positive and finite.
            allowance_factor: Buffer time per server for planned release date
                calculations. Used to sort PSP jobs by urgency.

        Raises:
            ValueError: If wl_norm is empty or contains non-positive/infinite values.
        """
        if not wl_norm:
            msg = "wl_norm must not be empty"
            raise ValueError(msg)
        for server, norm in wl_norm.items():
            if norm <= 0 or not math.isfinite(norm):
                msg = f"All workload norms must be positive and finite, got {norm} for {server}"
                raise ValueError(msg)
        self.wl_norm = wl_norm
        self.allowance_factor = allowance_factor

    def validate_strategy(self, shopfloor: ShopFloor) -> None:
        """Validate CorrectedWIPStrategy is active and all servers have norms.

        Args:
            shopfloor: The shopfloor to validate.

        Raises:
            TypeError: If shopfloor is not configured with CorrectedWIPStrategy.
            ValueError: If any shopfloor server is missing from wl_norm.
        """
        if not isinstance(shopfloor.wip_strategy, CorrectedWIPStrategy):
            msg = "ContinuousRelease requires CorrectedWIPStrategy. Use shopfloor.set_wip_strategy() first."
            raise TypeError(msg)
        missing = [s for s in shopfloor.servers if s not in self.wl_norm]
        if missing:
            msg = f"Shopfloor has servers with missing norms: {missing}"
            raise ValueError(msg)

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
        self._validate_wip_strategy(shopfloor)
        for job in sorted(list(psp.jobs), key=lambda j: j.planned_release_date(self.allowance_factor)):
            if self._fits_norms(job, shopfloor):
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
        self._validate_wip_strategy(shopfloor)
        if not self._fits_norms(job, shopfloor):
            return
        psp.release(job)

    def _fits_norms(self, job: ProductionJob, shopfloor: ShopFloor) -> bool:
        """Check if releasing job keeps all servers at or below norms.

        For each (server, PT) at position i in the job's routing:
            contributed_load = PT / (i + 1)
        If current_wip + contributed_load > norm for ANY server, return False.

        Args:
            job: The candidate job to check.
            shopfloor: The shopfloor providing current WIP values.

        Returns:
            True if releasing the job would keep all server WIPs within norms.
        """
        for i, (server, processing_time) in enumerate(job.server_processing_times):
            contributed_load = processing_time / (i + 1)
            current_wip = shopfloor.wip.get(server, 0.0)
            if current_wip + contributed_load > self.wl_norm[server]:
                return False
        return True

    def _validate_wip_strategy(self, shopfloor: ShopFloor) -> None:
        """Validate that the shopfloor uses CorrectedWIPStrategy.

        Args:
            shopfloor: The shopfloor to validate.

        Raises:
            TypeError: If shopfloor is not configured with CorrectedWIPStrategy.
        """
        if not isinstance(shopfloor.wip_strategy, CorrectedWIPStrategy):
            msg = "ContinuousRelease requires CorrectedWIPStrategy. Use shopfloor.set_wip_strategy() first."
            raise TypeError(msg)
