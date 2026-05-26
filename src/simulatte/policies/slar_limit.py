"""SLAR-Limit release policy: SLAR with a workload-norm limit on urgent insertion."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from simulatte.shopfloor import CorrectedWIPStrategy

from .slar import Slar

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.job import ProductionJob
    from simulatte.psp import PreShopPool
    from simulatte.router import Router
    from simulatte.server import Server
    from simulatte.shopfloor import ShopFloor


class SlarLimit(Slar):
    """SLAR augmented with a workload-norm limit on urgent insertion.

    Identical to `Slar` except for the urgent-insertion branch:

    - Classic SLAR releases the urgent PSP candidate (negative PST) with
      the shortest processing time, unconditionally.
    - SLAR-Limit iterates urgent PSP candidates in ascending SPT order and
      releases the **first** whose corrected workload contribution
      ``PT / (i + 1)`` keeps every server in its routing at or below its
      configured norm. If no urgent candidate fits, the drain safety-net
      branch may still fire — same as in `Slar`.

    The idle-prevention and drain-safety-net branches, the PST priority
    rule, and the postponed-release mechanism are inherited unchanged.

    Requires `CorrectedWIPStrategy` on the
    shopfloor — the corrected contribution formula ``PT / (i + 1)`` only
    makes sense under that strategy. The strategy and the norm coverage
    are checked eagerly at construction.

    Example:
        >>> from simulatte.policies.slar_limit import SlarLimit
        >>> from simulatte.shopfloor import CorrectedWIPStrategy
        >>> shop_floor.set_wip_strategy(CorrectedWIPStrategy())
        >>> slar_limit = SlarLimit(
        ...     shopfloor=shop_floor, psp=psp, router=router,
        ...     wl_norm={s: 5.0 for s in servers},
        ...     allowance_factor=3.0,
        ... )

    Reference:
        Thürer, M. & Stevenson, M. (2021). Improving superfluous load
        avoidance release (SLAR): A new load-based SLAR mechanism.
        International Journal of Production Economics, 231, 107881.
        https://doi.org/10.1016/j.ijpe.2020.107881
    """

    def __init__(
        self,
        *,
        shopfloor: ShopFloor,
        psp: PreShopPool,
        router: Router,
        wl_norm: dict[Server, float],
        allowance_factor: float = 2.0,
    ) -> None:
        """Initialize the SLAR-Limit release policy.

        Args:
            shopfloor: The shopfloor whose completion events drive release
                decisions. Must already be configured with
                `CorrectedWIPStrategy`.
            psp: The Pre-Shop Pool to release jobs from.
            router: The router whose ``priority_policies`` should be set
                to PST. See `Slar` for details.
            wl_norm: Workload norm for each server. In the urgent-insertion
                branch, an urgent PSP candidate is released only if its
                corrected workload contribution keeps each server in its
                routing at or below its norm. Must cover every shopfloor
                server with a positive, finite value.
            allowance_factor: Slack allowance per operation (parameter
                ``k`` in the SLAR paper). Forwarded to `Slar`.

        Raises:
            ValueError: If ``wl_norm`` is empty, contains non-positive or
                non-finite values, or misses any shopfloor server.
            TypeError: If ``shopfloor`` is not configured with
                `CorrectedWIPStrategy`.
        """
        if not wl_norm:
            msg = "wl_norm must not be empty"
            raise ValueError(msg)
        for server, norm in wl_norm.items():
            if norm <= 0 or not math.isfinite(norm):
                msg = f"All workload norms must be positive and finite, got {norm} for {server}"
                raise ValueError(msg)
        if not isinstance(shopfloor.wip_strategy, CorrectedWIPStrategy):
            msg = "SlarLimit requires CorrectedWIPStrategy. Use shopfloor.set_wip_strategy() first."
            raise TypeError(msg)
        missing = [s for s in shopfloor.servers if s not in wl_norm]
        if missing:
            msg = f"Shopfloor has servers with missing norms: {missing}"
            raise ValueError(msg)

        self.wl_norm = wl_norm
        super().__init__(
            shopfloor=shopfloor,
            psp=psp,
            router=router,
            allowance_factor=allowance_factor,
        )

    def _release_urgent_insertion(
        self,
        server: Server,
        candidates: tuple[ProductionJob, ...],
    ) -> bool:
        """Insert the first urgent candidate that fits all workload norms.

        If at least one queued job is already urgent (PST < 0), no
        insertion is needed — the priority rule dispatches it next.
        Otherwise iterates urgent PSP candidates in ascending SPT order
        and releases the first whose corrected workload contribution
        keeps every server in its routing at or below its norm. If no
        urgent candidate fits, the drain safety-net branch may still fire.

        Returns ``True`` iff a job was released.
        """
        if any(self._pst(j, server) < 0 for j in server.queueing_jobs):
            return False
        urgent_by_spt = sorted(
            (j for j in candidates if self._pst(j, server) < 0),
            key=lambda j: j.processing_times[0],
        )
        for job in urgent_by_spt:
            if self._fits_norms(job):
                self.psp.release(job=job)
                return True
        return False

    def _fits_norms(self, job: ProductionJob) -> bool:
        """Return True iff releasing *job* keeps every server in its routing at or below its norm.

        Uses corrected aggregate load: contribution at position ``i`` in
        the routing is ``PT / (i + 1)``. Same formula as
        `ContinuousRelease._fits_norms` and the inline check in
        `LumsCor.periodic_release`.
        """
        for i, (server, processing_time) in enumerate(job.server_processing_times):
            contributed_load = processing_time / (i + 1)
            current_wip = self.shopfloor.wip.get(server, 0.0)
            if current_wip + contributed_load > self.wl_norm[server]:
                return False
        return True
