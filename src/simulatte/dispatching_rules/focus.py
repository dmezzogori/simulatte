"""FOCUS dispatching rule (Kasper, Land, Teunter 2023, Omega 114, 102726).

FOCUS is a self-established dispatching rule that combines four weighted
impact mechanisms — SPT (``pi``), starvation response (``omega``), slack
timing (``psi``), and pacing (``gamma``) — each valued in ``[0, 1]``. The
total score is their weighted average, also in ``[0, 1]``.

The rule is also a building block for higher-level non-hierarchical
policies (see :class:`simulatte.policies.draco.Draco`).

Note on the omitted 5th mechanism (`beta`, WIP balancing):
    The original Omega paper describes a fifth mechanism called *beta*
    that aims to balance WIP across servers. Kasper et al. (2023) report
    it as counter-productive in their experiments, so it is omitted here.
    The class is structured so a future ``beta`` method and a fifth
    weight slot can be added cleanly if subsequent research re-evaluates
    that finding.

References:
    Kasper, A., Land, M., Teunter, R. (2023). Towards system state
    dispatching in high-variety manufacturing. *Omega*, 114, 102726.
    https://doi.org/10.1016/j.omega.2022.102726
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.job import BaseJob
    from simulatte.psp import PreShopPool
    from simulatte.server import Server
    from simulatte.shopfloor import ShopFloor


def _remaining_after_completion(job: BaseJob) -> list[Server]:
    """Servers in *job*'s routing that have not been completed (exited) yet.

    Spec-compliant definition of FOCUS's ``R_i``: every server at which the
    job has yet to be *processed*. Differs from
    :pyattr:`simulatte.job.BaseJob.remaining_routing`, which excludes
    servers already entered into the queue but not yet exited — FOCUS's
    ``R_i`` keeps them included as long as the operation has not finished.
    """
    return [srv for srv in job.servers if job.servers_exit_at[srv] is None]


def _next_server_after(job: BaseJob, server: Server) -> Server | None:
    """The server after *server* in *job*'s routing, or ``None`` if last."""
    servers = job.servers
    try:
        idx = servers.index(server)
    except ValueError:
        return None
    if idx + 1 >= len(servers):
        return None
    return servers[idx + 1]


@dataclass(frozen=True)
class FocusContext:
    """Snapshot of shop-wide aggregates at a single decision instant.

    Built once per decision via :meth:`Focus.build_context` and reused
    across all candidates being scored at that instant. Computing this
    object is ``O(|O|)``.

    Attributes:
        max_pij: Max processing time over all pending ``(i, j)`` pairs in
            the shop (the set ``D`` in the spec). ``0`` if no pending ops.
        empty_queue_servers: Servers whose ``queue`` is empty at the
            snapshot instant.
        max_positive_slack: Max of ``S_i`` across all jobs in ``O`` with
            positive slack; ``0`` if no positive-slack jobs.
        max_positive_pacing: Max of ``V_i = S_i / |R_i|`` across all jobs
            in ``O`` with positive ``V_i``; ``0`` if none.
    """

    max_pij: float
    empty_queue_servers: frozenset[Server]
    max_positive_slack: float
    max_positive_pacing: float


class Focus:
    """FOCUS dispatching rule — weighted combination of four impact mechanisms.

    The class is stateless beyond its weights. Computations are organised
    so each mechanism (``pi``, ``omega``, ``psi``, ``gamma``) is exposed
    independently for testability and for use as a building block by
    higher-level policies (DRACO).

    All four mechanisms return values in ``[0, 1]`` with ``1`` indicating
    a "relevant" impact and ``0`` an "irrelevant" one. The aggregated
    :meth:`score` is the weighted average of the four pieces and also
    lies in ``[0, 1]``.

    Args:
        weights: ``(w1, w2, w3, w4)`` for the four mechanisms; must each
            be in ``[0, 1]`` and sum to ``1`` (within floating-point
            tolerance). Defaults to equal weights.

    Example (inside DRACO — one ctx, many candidates):
        >>> focus = Focus(weights=(0.25, 0.25, 0.25, 0.25))
        >>> ctx = focus.build_context(shopfloor, env.now, psp=psp)
        >>> for candidate in candidates:
        ...     d_score = focus.score(candidate, server_k, ctx, env.now)
    """

    def __init__(self, weights: tuple[float, float, float, float] = (0.25, 0.25, 0.25, 0.25)) -> None:
        if len(weights) != 4:
            raise ValueError(f"focus weights must have exactly 4 elements, got {len(weights)}")
        if not all(0.0 <= w <= 1.0 for w in weights):
            raise ValueError(f"focus weights must each be in [0, 1], got {weights}")
        if not math.isclose(sum(weights), 1.0, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(f"focus weights must sum to 1, got {sum(weights)}")
        self.w1, self.w2, self.w3, self.w4 = weights

    @staticmethod
    def build_context(
        shopfloor: ShopFloor,
        now: float,
        *,
        psp: PreShopPool | None = None,
    ) -> FocusContext:
        """Compute the shop-wide aggregates needed by FOCUS at *now*.

        The set ``O`` (arrived orders not yet completed) is taken to be
        the union of ``shopfloor.jobs`` and (if provided) ``psp.jobs``.
        Pass *psp* when scoring decisions that include PSP candidates
        (e.g. DRACO); omit it for standalone-FOCUS dispatching where the
        scored set is queue-only.

        Cost: ``O(|O|)``.
        """
        max_pij = 0.0
        max_positive_slack = 0.0
        max_positive_pacing = 0.0
        empty_queue_servers = frozenset(s for s in shopfloor.servers if len(s.queue) == 0)

        jobs: list[BaseJob] = list(shopfloor.jobs)
        if psp is not None:
            jobs.extend(psp.jobs)

        for job in jobs:
            remaining = _remaining_after_completion(job)
            if not remaining:
                continue
            sum_remaining_pt = 0.0
            for srv in remaining:
                pt = job.routing[srv]
                if pt > max_pij:
                    max_pij = pt
                sum_remaining_pt += pt
            s_i = job.due_date - now - sum_remaining_pt
            if s_i > 0:
                if s_i > max_positive_slack:
                    max_positive_slack = s_i
                v_i = s_i / len(remaining)
                if v_i > max_positive_pacing:
                    max_positive_pacing = v_i

        return FocusContext(
            max_pij=max_pij,
            empty_queue_servers=empty_queue_servers,
            max_positive_slack=max_positive_slack,
            max_positive_pacing=max_positive_pacing,
        )

    def pi(self, job: BaseJob, server: Server, ctx: FocusContext) -> float:
        """SPT mechanism (spec §3.3.1): favour short operations at *server*.

        ``pi = 1 - p_{ik} / ctx.max_pij``, or ``1`` when ``max_pij == 0``.
        """
        if ctx.max_pij <= 0:
            return 1.0
        p_ik = job.routing[server]
        return 1.0 - p_ik / ctx.max_pij

    def omega(self, job: BaseJob, server: Server, ctx: FocusContext) -> float:
        """Starvation response (spec §3.3.2): relieve idle downstream servers.

        ``omega = pi(job, server, ctx)`` when the next server in *job*'s
        routing has an empty queue, else ``0``. ``0`` if *server* is the
        last operation in *job*'s routing (no downstream to relieve).
        """
        next_srv = _next_server_after(job, server)
        if next_srv is None:
            return 0.0
        if next_srv in ctx.empty_queue_servers:
            return self.pi(job, server, ctx)
        return 0.0

    def psi(self, job: BaseJob, ctx: FocusContext, now: float) -> float:
        """Slack timing (spec §3.3.3): favour due-date urgency.

        ``S_i = d_i - now - sum(p_ij for j in R_i)``. If ``S_i <= 0`` the
        job is tardy or just-in-time → ``psi = 1`` (saturated). Otherwise
        ``psi = 1 - S_i / ctx.max_positive_slack``. Returns ``1`` if there
        are no positive-slack jobs (defensive).
        """
        s_i = self._slack(job, now)
        if s_i <= 0:
            return 1.0
        if ctx.max_positive_slack <= 0:
            return 1.0
        return 1.0 - s_i / ctx.max_positive_slack

    def gamma(self, job: BaseJob, ctx: FocusContext, now: float) -> float:
        """Pacing (spec §3.3.4): favour orders behind per-operation pace.

        ``V_i = S_i / |R_i|``. If ``V_i <= 0`` → ``gamma = 1`` (saturated).
        Otherwise ``gamma = 1 - V_i / ctx.max_positive_pacing``. Returns
        ``1`` for jobs with no remaining operations or when there are no
        positive-pacing jobs (defensive).
        """
        remaining = _remaining_after_completion(job)
        if not remaining:
            return 1.0
        s_i = job.due_date - now - sum(job.routing[srv] for srv in remaining)
        v_i = s_i / len(remaining)
        if v_i <= 0:
            return 1.0
        if ctx.max_positive_pacing <= 0:
            return 1.0
        return 1.0 - v_i / ctx.max_positive_pacing

    def score(self, job: BaseJob, server: Server, ctx: FocusContext, now: float) -> float:
        """Aggregate weighted score of the four mechanisms; value in ``[0, 1]``."""
        return (
            self.w1 * self.pi(job, server, ctx)
            + self.w2 * self.omega(job, server, ctx)
            + self.w3 * self.psi(job, ctx, now)
            + self.w4 * self.gamma(job, ctx, now)
        )

    @staticmethod
    def _slack(job: BaseJob, now: float) -> float:
        """``S_i = d_i - now - sum(p_ij for j in R_i)``."""
        remaining = _remaining_after_completion(job)
        return job.due_date - now - sum(job.routing[srv] for srv in remaining)


class FocusPriorityRule:
    """Adapter exposing :class:`Focus` as a simulatte ``priority_policy``.

    Wraps a :class:`Focus` and a :class:`~simulatte.shopfloor.ShopFloor`
    into a ``(job, server) -> float`` callable suitable for
    :pyattr:`simulatte.router.Router.priority_policies`. The returned value
    is the *negated* FOCUS score because simulatte's
    :class:`simpy.resources.resource.PriorityResource` sorts ascending
    (lower key = served first).

    Staleness caveat:
        simulatte's protocol computes ``req.key`` once at queue-entry
        (``server.py:49``). FOCUS aggregates evolve discretely on every
        shop event, so a key keyed at queue entry can be arbitrarily
        stale by the time the request is dispatched. Pair this adapter
        with :func:`refresh_focus_queue` (e.g. via
        ``shopfloor.on_processing_end``) when FOCUS is used as a
        standalone dispatching rule.

        Inside :class:`simulatte.policies.draco.Draco` this is *not* an
        issue: DRACO uses :meth:`Focus.score` directly with a fresh
        ``ctx`` at every decision.

    Args:
        focus: A :class:`Focus` instance.
        shopfloor: The shopfloor against which ``ctx`` is built per call.
        psp: Optional PreShopPool; when provided its jobs are included
            in the ``O`` aggregate (so PSP candidates show up in FOCUS
            aggregates even before release).
    """

    def __init__(self, focus: Focus, shopfloor: ShopFloor, *, psp: PreShopPool | None = None) -> None:
        self.focus = focus
        self.shopfloor = shopfloor
        self.psp = psp

    def __call__(self, job: BaseJob, server: Server) -> float:
        now = self.shopfloor.env.now
        ctx = self.focus.build_context(self.shopfloor, now, psp=self.psp)
        return -self.focus.score(job, server, ctx, now)


def refresh_focus_queue(
    server: Server,
    focus: Focus,
    shopfloor: ShopFloor,
    *,
    psp: PreShopPool | None = None,
) -> None:
    """Re-key every queued request at *server* with a fresh FOCUS score.

    Builds a fresh :class:`FocusContext`, assigns a fresh
    ``priority_policy`` closure to every queued job, then calls
    :meth:`simulatte.server.Server.sort_queue` to restore the sorted
    invariant. Used as the companion refresh hook for standalone-FOCUS
    dispatching (see staleness caveat on :class:`FocusPriorityRule`).

    Args:
        server: The server whose queue is to be refreshed.
        focus: The Focus instance.
        shopfloor: Shopfloor used to build the FocusContext.
        psp: Optional PreShopPool to include in the context's aggregates.
    """
    now = shopfloor.env.now
    ctx = focus.build_context(shopfloor, now, psp=psp)
    fresh_rule = lambda j, s: -focus.score(j, s, ctx, now)  # noqa: E731
    for req in server.queue:
        req.job.priority_policy = fresh_rule
    server.sort_queue()
