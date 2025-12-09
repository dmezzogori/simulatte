"""Starvation avoidance mechanism for PSP-controlled systems."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulatte.jobshop.job import Job
    from simulatte.jobshop.psp.psp import PreShopPool
    from simulatte.jobshop.shopfloor import ShopFloor
    from simulatte.jobshop.typing import ProcessGenerator


def starvation_avoidance_process(shopfloor: ShopFloor, psp: PreShopPool) -> ProcessGenerator:
    while True:
        new_job_in_psp: Job = yield psp.new_job
        if new_job_in_psp.servers[0].empty:
            psp.remove(job=new_job_in_psp)
            shopfloor.add(new_job_in_psp)
