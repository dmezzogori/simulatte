"""SLAR-Limit release policy: SLAR with a workload-norm limit on urgent insertion."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from simulatte.shopfloor import CorrectedWIPStrategy

from .slar import Slar

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.job import ProductionJob
    from simulatte.psp import PreShopPool
    from simulatte.server import Server
    from simulatte.shopfloor import ShopFloor


class SlarLimit(Slar):
    """SLAR augmented with a workload-norm limit on urgent insertion.

    Identical to :class:`Slar` except for Branch 2 (urgent insertion when all
    queued jobs are non-urgent):

    - Classic SLAR releases the urgent PSP candidate (negative PST) with the
      shortest processing time, unconditionally.
    - SLAR-Limit iterates urgent PSP candidates in ascending SPT order and
      releases the **first** whose corrected workload contribution PT / (i + 1)
      keeps every server in its routing at or below its configured norm.
      If no urgent candidate fits, control falls through to Branch 3
      (postponed starvation) — same as in SLAR.

    Branches 1 (empty queue), 3 (postponed starvation), the PST priority
    policy, and the postponed-release mechanism are inherited unchanged.

    Requires :class:`~simulatte.shopfloor.CorrectedWIPStrategy` on the
    shopfloor — the corrected contribution formula PT / (i + 1) only makes
    sense under that strategy. The check is performed lazily on the first
    completion trigger, matching the pattern used by
    :class:`~simulatte.policies.lumscor.LumsCor` and
    :class:`~simulatte.policies.continuous_release.ContinuousRelease`.

    Example:
        >>> from simulatte.policies.slar_limit import SlarLimit
        >>> from simulatte.policies.triggers import on_completion_trigger
        >>> from simulatte.shopfloor import CorrectedWIPStrategy
        >>> shopfloor.set_wip_strategy(CorrectedWIPStrategy())
        >>> slar_limit = SlarLimit(allowance_factor=3.0, wl_norm={s: 5.0 for s in servers})
        >>> psp = PreShopPool(env=env, shopfloor=shopfloor)
        >>> env.process(on_completion_trigger(shopfloor, psp, slar_limit.decide_next_job))

    Reference:
        Thürer, M. & Stevenson, M. (2021). Improving superfluous load
        avoidance release (SLAR): A new load-based SLAR mechanism.
        International Journal of Production Economics, 231, 107881.
        https://doi.org/10.1016/j.ijpe.2020.107881
    """

    def __init__(
        self,
        allowance_factor: float = 2.0,
        *,
        wl_norm: dict[Server, float],
    ) -> None:
        """Initialize the SLAR-Limit release policy.

        Args:
            allowance_factor: Slack allowance per operation (parameter 'k' in
                the SLAR paper). Forwarded to :class:`Slar`.
            wl_norm: Workload norm for each server. In the urgent-insertion
                branch, an urgent PSP candidate is released only if its
                corrected workload contribution keeps each server in its
                routing at or below its norm. Must not be empty; all values
                must be positive and finite.

        Raises:
            ValueError: If ``wl_norm`` is empty or contains non-positive or
                non-finite values.
        """
        super().__init__(allowance_factor=allowance_factor)
        if not wl_norm:
            msg = "wl_norm must not be empty"
            raise ValueError(msg)
        for server, norm in wl_norm.items():
            if norm <= 0 or not math.isfinite(norm):
                msg = f"All workload norms must be positive and finite, got {norm} for {server}"
                raise ValueError(msg)
        self.wl_norm = wl_norm

    def validate_strategy(self, shopfloor: ShopFloor) -> None:
        """Validate CorrectedWIPStrategy is active and all servers have norms.

        Args:
            shopfloor: The shopfloor to validate.

        Raises:
            TypeError: If shopfloor is not configured with CorrectedWIPStrategy.
            ValueError: If any shopfloor server is missing from wl_norm.
        """
        if not isinstance(shopfloor.wip_strategy, CorrectedWIPStrategy):
            msg = "SlarLimit requires CorrectedWIPStrategy. Use shopfloor.set_wip_strategy() first."
            raise TypeError(msg)
        missing = [s for s in shopfloor.servers if s not in self.wl_norm]
        if missing:
            msg = f"Shopfloor has servers with missing norms: {missing}"
            raise ValueError(msg)

    def _validate_wip_strategy(self, shopfloor: ShopFloor) -> None:
        """Validate that the shopfloor uses CorrectedWIPStrategy.

        Args:
            shopfloor: The shopfloor to validate.

        Raises:
            TypeError: If shopfloor is not configured with CorrectedWIPStrategy.
        """
        if not isinstance(shopfloor.wip_strategy, CorrectedWIPStrategy):
            msg = "SlarLimit requires CorrectedWIPStrategy. Use shopfloor.set_wip_strategy() first."
            raise TypeError(msg)

    def decide_next_job(self, triggering_job: ProductionJob, psp: PreShopPool) -> None:
        """Decide whether to release a PSP job on a server's job-completion event.

        Validates that the shopfloor uses :class:`CorrectedWIPStrategy`
        (required for the workload-norm check in Branch 2) and then
        delegates to :meth:`Slar.decide_next_job`.

        Args:
            triggering_job: The job that just finished processing.
            psp: The Pre-Shop Pool to release jobs from.

        Raises:
            TypeError: If the shopfloor is not configured with
                :class:`CorrectedWIPStrategy`.
        """
        self._validate_wip_strategy(psp.shopfloor)
        super().decide_next_job(triggering_job, psp)

    def _branch_urgent_insertion(
        self,
        server: Server,
        candidates: tuple[ProductionJob, ...],
        psp: PreShopPool,
    ) -> ProductionJob | None:
        """Branch 2 (SLAR-Limit): release the first urgent candidate that fits all norms.

        Iterates urgent PSP candidates (negative PST) in ascending SPT order
        and returns the first whose corrected workload contribution keeps
        every server in its routing at or below its norm. If no candidate
        fits, returns ``None`` so Branch 3 may fire.
        """
        if not all(self.pst_priority_policy(j, server) > 0 for j in server.queueing_jobs):
            return None
        urgent_by_spt = sorted(
            (j for j in candidates if self.pst_priority_policy(j, server) < 0),
            key=lambda j: j.processing_times[0],
        )
        for job in urgent_by_spt:
            if self._fits_norms(job, psp.shopfloor):
                return job
        return None

    def _fits_norms(
        self,
        job: ProductionJob,
        shopfloor: ShopFloor,
    ) -> bool:
        """Return True iff releasing *job* keeps every server in its routing at or below its norm.

        Uses corrected aggregate load: contribution at position i in the
        routing is ``PT / (i + 1)``. Same formula as
        :meth:`ContinuousRelease._fits_norms` and the inline check in
        :meth:`LumsCor.periodic_release`.
        """
        for i, (server, processing_time) in enumerate(job.server_processing_times):
            contributed_load = processing_time / (i + 1)
            current_wip = shopfloor.wip.get(server, 0.0)
            if current_wip + contributed_load > self.wl_norm[server]:
                return False
        return True
