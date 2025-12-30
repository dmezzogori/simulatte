"""Composable release triggers for Pre-Shop Pool policies.

This module provides generic trigger functions that invoke release callbacks
based on different simulation events. Triggers are decoupled from the release
logic itself, enabling flexible composition of periodic, event-driven, and
custom release strategies.

Example:
    >>> lumscor = LumsCor(wl_norm={server: 5.0}, allowance_factor=2)
    >>> psp = PreShopPool(env=env, shopfloor=shop_floor)
    >>> env.process(periodic_trigger(psp, 1.0, lumscor.periodic_release))
    >>> env.process(on_completion_trigger(shop_floor, psp, lumscor.starvation_release))
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from simulatte.job import ProductionJob
    from simulatte.psp import PreShopPool
    from simulatte.shopfloor import ShopFloor
    from simulatte.typing import ProcessGenerator


def periodic_trigger(
    psp: PreShopPool,
    interval: float,
    release_fn: Callable[[PreShopPool], None],
) -> ProcessGenerator:
    """Invoke a release function at regular intervals.

    This trigger runs continuously, waiting for the specified interval
    before invoking the release function. The function is only called
    if the PSP is non-empty.

    Args:
        psp: The Pre-Shop Pool to monitor.
        interval: Time between release attempts in simulation time units.
        release_fn: Function that examines the PSP and releases jobs.
            Signature: (psp: PreShopPool) -> None

    Yields:
        SimPy timeout events at the specified interval.

    Example:
        >>> def release_all(psp):
        ...     while not psp.empty:
        ...         job = psp.remove()
        ...         psp.shopfloor.add(job)
        >>> env.process(periodic_trigger(psp, 1.0, release_all))
    """
    while True:
        yield psp.env.timeout(interval)
        if not psp.empty:
            release_fn(psp)


def on_arrival_trigger(
    psp: PreShopPool,
    release_fn: Callable[[ProductionJob, PreShopPool], None],
) -> ProcessGenerator:
    """Invoke a release function when a new job arrives in the PSP.

    This trigger runs continuously, waiting for the PSP's new_job event.
    When a job arrives, the release function is called with that job,
    allowing immediate release decisions based on job properties.

    Args:
        psp: The Pre-Shop Pool to monitor.
        release_fn: Function called when a job arrives.
            Signature: (job: ProductionJob, psp: PreShopPool) -> None

    Yields:
        The PSP's new_job event repeatedly.

    Example:
        >>> def release_if_server_empty(job, psp):
        ...     if job.servers[0].empty:
        ...         psp.remove(job=job)
        ...         psp.shopfloor.add(job)
        >>> env.process(on_arrival_trigger(psp, release_if_server_empty))
    """
    while True:
        job: ProductionJob = yield psp.new_job
        release_fn(job, psp)


def on_completion_trigger(
    shopfloor: ShopFloor,
    psp: PreShopPool,
    release_fn: Callable[[ProductionJob, PreShopPool], None],
) -> ProcessGenerator:
    """Invoke a release function when any job completes processing at a server.

    This trigger runs continuously, waiting for the shopfloor's
    job_processing_end event. When a job finishes an operation, the release
    function is called, enabling starvation avoidance and load-based release.

    Args:
        shopfloor: The shopfloor to monitor for job completions.
        psp: The Pre-Shop Pool containing candidate jobs for release.
        release_fn: Function called when a job completes processing.
            Signature: (triggering_job: ProductionJob, psp: PreShopPool) -> None
            The triggering_job is the job that just finished processing.

    Yields:
        The shopfloor's job_processing_end event repeatedly.

    Example:
        >>> def release_on_starvation(triggering_job, psp):
        ...     server = triggering_job.previous_server
        ...     if server and server.empty:
        ...         candidate = next((j for j in psp.jobs if j.starts_at(server)), None)
        ...         if candidate:
        ...             psp.remove(job=candidate)
        ...             psp.shopfloor.add(candidate)
        >>> env.process(on_completion_trigger(shop_floor, psp, release_on_starvation))
    """
    while True:
        job: ProductionJob = yield shopfloor.job_processing_end
        release_fn(job, psp)
