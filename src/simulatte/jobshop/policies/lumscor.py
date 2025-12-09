"""LUMS-COR release policy and starvation trigger."""

from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.jobshop.psp.policies.base import PSPReleasePolicy
from simulatte.jobshop.shopfloor import ShopFloor

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.jobshop.job import Job
    from simulatte.jobshop.psp.psp import PreShopPool
    from simulatte.jobshop.server.server import Server
    from simulatte.jobshop.typing import ProcessGenerator


class LumsCor(PSPReleasePolicy):
    """Release policy based on workload norms and planned release dates."""

    allowance_factor: int

    def __init__(self, wl_norm: dict[Server, float], allowance_factor: int) -> None:
        self.wl_norm = wl_norm
        LumsCor.allowance_factor = allowance_factor
        self.enable_corrected_wip()

    def enable_corrected_wip(self) -> None:
        ShopFloor().enable_corrected_wip = True

    def release_condition(self, psp: PreShopPool, shopfloor: ShopFloor) -> bool:  # noqa: ARG002
        return True

    def release(self, psp: PreShopPool, shopfloor: ShopFloor) -> None:
        for job in sorted(psp.jobs, key=lambda j: j.planned_release_date(LumsCor.allowance_factor)):
            if all(
                shopfloor.wip[server] + processing_time / (i + 1) <= self.wl_norm[server]
                for i, (server, processing_time) in enumerate(job.server_processing_times)
            ):
                psp.remove(job=job)
                shopfloor.add(job)


def lumscor_starvation_trigger(shopfloor: ShopFloor, psp: PreShopPool) -> ProcessGenerator:
    while True:
        triggering_job: Job = yield shopfloor.job_processing_end
        server_triggered = triggering_job.previous_server
        is_empty = server_triggered.empty
        has_one = len(server_triggered.queue) == 1
        if is_empty or has_one:
            candidate_job = min(
                (job for job in psp.jobs if job.starts_at(server_triggered)),
                default=None,
                key=lambda j: j.planned_release_date(LumsCor.allowance_factor),
            )
            if candidate_job:
                psp.remove(job=candidate_job)
                shopfloor.add(candidate_job)
