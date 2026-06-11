"""Shared workload-norm validation and corrected-load fit check.

Workload-control release policies (LumsCor, SLAR-Limit, Continuous Release)
share two pieces of logic: turning a user-supplied workload norm into a
validated per-server dict, and deciding whether releasing a job would keep
every server in its routing at or below its norm.

The fit check uses the *corrected aggregate load* (Oosterman et al.; Land,
2006): the contribution a job makes to the workload of the server at position
``i`` in its routing (0-indexed) is ``PT / (i + 1)``, not the full processing
time. Downstream operations are discounted because their load is spread over
time as the job advances through the shop, so a job deep in a routing
contributes less to any single server's instantaneous workload.

Reference:
    Land, M. J. (2006). Parameters and sensitivity in workload control.
    International Journal of Production Economics, 104(2), 625-638.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulatte.job import ProductionJob
    from simulatte.server import Server
    from simulatte.shopfloor import ShopFloor


def expand_norms(*, shopfloor: ShopFloor, wl_norm: float | dict[Server, float]) -> dict[Server, float]:
    """Expand and validate a workload norm into a per-server dict.

    A scalar ``wl_norm`` is expanded to ``{server: float(wl_norm)}`` for every
    server registered with ``shopfloor``. A dict is taken verbatim. Both forms
    are then validated through the same checks: the resulting norms must be
    non-empty, every value must be positive and finite, and (for dicts) the
    norms must cover every server in ``shopfloor.servers``.

    Args:
        shopfloor: The shopfloor whose servers a scalar norm expands over and
            against which a dict's coverage is checked.
        wl_norm: A scalar norm applied to every shopfloor server, or a dict
            mapping each shopfloor server to its own norm.

    Returns:
        A dict mapping every shopfloor server to a positive, finite norm.

    Raises:
        ValueError: If the norms are empty, contain a non-positive or
            non-finite value (0, negative, inf, nan), or — for a dict — omit
            any server registered with ``shopfloor``.
    """
    if isinstance(wl_norm, dict):
        norms: dict[Server, float] = {server: float(norm) for server, norm in wl_norm.items()}
    else:
        scalar = float(wl_norm)
        norms = {server: scalar for server in shopfloor.servers}
    if not norms:
        msg = "wl_norm must not be empty"
        raise ValueError(msg)
    for server, norm in norms.items():
        if norm <= 0 or not math.isfinite(norm):
            msg = f"All workload norms must be positive and finite, got {norm} for {server}"
            raise ValueError(msg)
    missing = [s for s in shopfloor.servers if s not in norms]
    if missing:
        msg = f"Shopfloor has servers with missing norms: {missing}"
        raise ValueError(msg)
    return norms


def fits_norms(job: ProductionJob, *, wip: dict[Server, float], norms: dict[Server, float]) -> bool:
    """Return whether releasing *job* keeps every server in its routing within norms.

    For each ``(server, processing_time)`` at position ``i`` (0-indexed) in the
    job's routing, the job's position-corrected contribution to that server's
    workload is ``processing_time / (i + 1)`` (Oosterman/Land corrected
    aggregate load). The job fits iff, for every such server,

        ``wip.get(server, 0.0) + processing_time / (i + 1) <= norms[server]``.

    Args:
        job: The candidate job whose ``server_processing_times`` define its
            routing and per-operation processing times.
        wip: Current corrected work-in-progress per server; a server absent
            from the dict is treated as having zero WIP.
        norms: Per-server workload norms, as produced by `expand_norms`. Must
            contain every server in the job's routing.

    Returns:
        True if releasing the job keeps every server in its routing at or below
        its norm; False otherwise.
    """
    return all(
        wip.get(server, 0.0) + processing_time / (i + 1) <= norms[server]
        for i, (server, processing_time) in enumerate(job.server_processing_times)
    )
