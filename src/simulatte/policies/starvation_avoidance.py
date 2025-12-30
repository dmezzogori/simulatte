"""Starvation avoidance mechanism for PSP-controlled systems.

This module provides a SimPy process that prevents server starvation in pull-based
release systems (LumsCor, SLAR). It monitors the Pre-Shop Pool for newly arrived
jobs and immediately releases any job whose first server is idle, bypassing the
normal release policy to ensure servers don't sit idle unnecessarily.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulatte.job import ProductionJob
    from simulatte.psp import PreShopPool
    from simulatte.shopfloor import ShopFloor
    from simulatte.typing import ProcessGenerator


def starvation_avoidance_process(shopfloor: ShopFloor, psp: PreShopPool) -> ProcessGenerator:
    """SimPy process that prevents server starvation by releasing jobs immediately.

    This process monitors the Pre-Shop Pool for incoming jobs. When a job arrives
    and its first server has an empty queue, the job is immediately released to
    the shopfloor, bypassing the normal PSP release policy.

    This mechanism ensures that servers do not remain idle when there are jobs
    in the PSP that could be processed, improving overall system utilization.

    Args:
        shopfloor: The shopfloor instance where released jobs will be added.
        psp: The Pre-Shop Pool to monitor for incoming jobs.

    Yields:
        Waits on psp.new_job event to receive newly arrived ProductionJob instances.

    Note:
        This process runs in an infinite loop and should be registered with the
        simulation environment using ``env.process(starvation_avoidance_process(...))``.
        It is typically used alongside pull-based release policies like LumsCor or SLAR.

    Example:
        >>> env.process(starvation_avoidance_process(shop_floor, psp))
    """
    while True:
        new_job_in_psp: ProductionJob = yield psp.new_job
        if new_job_in_psp.servers[0].empty:
            psp.remove(job=new_job_in_psp)
            shopfloor.add(new_job_in_psp)
