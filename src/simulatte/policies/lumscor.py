"""LUMS-COR release policy and starvation trigger."""

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
    """Load-based release policy with workload norms and planned release dates.

    This policy requires CorrectedWIPStrategy on the shopfloor.
    Use shopfloor.set_wip_strategy(CorrectedWIPStrategy()) before releasing jobs.
    """

    def __init__(self, *, wl_norm: dict[Server, float], allowance_factor: int) -> None:
        self.wl_norm = wl_norm
        self.allowance_factor = allowance_factor

    def _validate_wip_strategy(self, shopfloor: ShopFloor) -> None:
        """Validate shopfloor uses CorrectedWIPStrategy."""
        if not isinstance(shopfloor.wip_strategy, CorrectedWIPStrategy):
            msg = "LumsCor requires CorrectedWIPStrategy. Use shopfloor.set_wip_strategy() first."
            raise TypeError(msg)

    def release(self, psp: PreShopPool, shopfloor: ShopFloor) -> None:
        """Release jobs from PSP if adding them keeps WIP under norms."""
        self._validate_wip_strategy(shopfloor)
        for job in sorted(psp.jobs, key=lambda j: j.planned_release_date(self.allowance_factor)):
            if all(
                shopfloor.wip.get(server, 0.0) + processing_time / (i + 1) <= self.wl_norm[server]
                for i, (server, processing_time) in enumerate(job.server_processing_times)
            ):
                psp.remove(job=job)
                shopfloor.add(job)

    def starvation_trigger(self, shopfloor: ShopFloor, psp: PreShopPool) -> ProcessGenerator:
        """Generator process that releases jobs when servers are starving."""
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
