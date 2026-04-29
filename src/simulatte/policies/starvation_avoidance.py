"""Starvation avoidance mechanism for PSP-controlled systems.

This module provides a callback that prevents server starvation in pull-based
release systems (LumsCor, SLAR). When registered via ``psp.on_arrival()``, it
immediately releases any arriving job whose first server is idle, bypassing the
normal release policy to ensure servers don't sit idle unnecessarily.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulatte.job import ProductionJob
    from simulatte.psp import PreShopPool


def starvation_avoidance(job: ProductionJob, psp: PreShopPool) -> None:
    """Release a job immediately if its first server is idle.

    Intended for use as a ``psp.on_arrival()`` callback. When a job arrives
    in the Pre-Shop Pool and its first routing server has no active users
    and an empty queue, the job is released to the shopfloor immediately.

    Args:
        job: The job that just arrived in the PSP.
        psp: The Pre-Shop Pool containing the job.

    Example:
        >>> psp.on_arrival(starvation_avoidance)
    """
    if job not in psp:
        return
    if job.servers[0].is_idle:
        psp.release(job)
