"""Result extraction functions for post-simulation analysis."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simpy.core import SimTime
    from simulatte.typing import System


def _average_wip(wip_ts: list[tuple[float, float]], warmup: float) -> float:
    """Arithmetic mean of WIP snapshots recorded after the warmup period."""
    values = [w for t, w in wip_ts if t > warmup]
    if not values:
        return float("nan")
    return sum(values) / len(values)


def extract_results(warmup: SimTime, system: System) -> dict[str, float]:
    """Extract performance metrics from a completed simulation.

    Filters out jobs that finished during the warmup period to report
    steady-state statistics only.

    Args:
        warmup: Discard jobs with finished_at <= this time.
        system: Tuple of (psp_or_none, servers, shopfloor, router).

    Returns:
        Dict with keys: completed_jobs, avg_time_in_shopfloor, avg_time_in_psp,
        avg_queue_time, pct_tardy, avg_lateness, avg_utilization, avg_wip.
    """
    from simulatte.shopfloor import CurrentWorkLoadCollector

    _, servers, shopfloor, _ = system
    jobs_done = [j for j in shopfloor.jobs_done if j.finished_at is not None and j.finished_at > warmup]
    n = len(jobs_done)

    collector = shopfloor.time_series_collector
    avg_wip = (
        _average_wip(collector.wip_ts, float(warmup))
        if isinstance(collector, CurrentWorkLoadCollector)
        else float("nan")
    )

    if n == 0:
        return {
            "completed_jobs": 0,
            "avg_time_in_shopfloor": float("nan"),
            "avg_time_in_psp": float("nan"),
            "avg_queue_time": float("nan"),
            "pct_tardy": float("nan"),
            "avg_lateness": float("nan"),
            "avg_utilization": float("nan"),
            "avg_wip": avg_wip,
        }

    avg_time_in_shopfloor = sum(j.time_in_shopfloor for j in jobs_done) / n
    avg_time_in_psp = sum(j.time_in_psp if j.psp_exit_at is not None else 0.0 for j in jobs_done) / n
    avg_queue_time = sum(j.total_queue_time for j in jobs_done) / n
    pct_tardy = sum(1 for j in jobs_done if j.lateness > 0) / n * 100
    avg_lateness = sum(j.lateness for j in jobs_done) / n
    avg_utilization = sum(s.utilization_rate for s in servers) / len(servers)

    return {
        "completed_jobs": n,
        "avg_time_in_shopfloor": avg_time_in_shopfloor,
        "avg_time_in_psp": avg_time_in_psp,
        "avg_queue_time": avg_queue_time,
        "pct_tardy": pct_tardy,
        "avg_lateness": avg_lateness,
        "avg_utilization": avg_utilization,
        "avg_wip": avg_wip,
    }
