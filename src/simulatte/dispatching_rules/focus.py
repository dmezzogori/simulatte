"""FOCUS dispatching rule (Kasper, Land, Teunter 2023, Omega 114, 102726).

FOCUS is a self-established dispatching rule that combines five weighted
impact mechanisms — SPT (``pi``), starvation response (``omega``), slack
timing (``psi``), pacing (``gamma``), and WIP balancing (``beta``) —
each valued in ``[0, 1]``. The total score is their weighted average,
also in ``[0, 1]``.

The rule is also a building block for higher-level non-hierarchical
policies (see ``simulatte.policies.draco.Draco``).

Mechanism activation:
    Any subset of mechanisms can be deactivated by setting the
    corresponding weight to zero. The remaining (non-zero) weights must
    still sum to 1. The default weights are ``(0.25, 0.25, 0.25, 0.25,
    0.0)`` — beta dormant — which preserves the original four-mechanism
    behaviour. Kasper et al. (2023) report beta as counter-productive in
    their experiments, but it is included here for reproducibility and
    as a building block for future WIP-balance-aware policies.

References:
    Kasper, A., Land, M., Teunter, R. (2023). Towards system state
    dispatching in high-variety manufacturing. *Omega*, 114, 102726.
    https://doi.org/10.1016/j.omega.2022.102726
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from simulatte.job import BaseJob
    from simulatte.psp import PreShopPool
    from simulatte.server import Server
    from simulatte.shopfloor import ShopFloor


def _entropy(workloads: Iterable[float]) -> float:
    """Shannon entropy of the normalized workload vector.

    Uses the ``0 · ln 0 = 0`` convention for empty bins. When the total
    workload is zero (idle shop — e.g. at ``t=0`` or briefly between
    jobs), entropy is mathematically undefined; this helper returns
    ``0`` by convention. Downstream, that makes ``Focus.beta``
    return ``0`` for every candidate in the empty-shop regime,
    deferring the decision to ``pi``/``omega``/``psi``/``gamma`` — which
    is consistent with the intuition that "WIP balance" is meaningless
    when there is no WIP. An alternative convention (treating the
    all-zero vector as uniform with ``e = ln |J|``) yields the same
    dispatch outcome (all candidates clip to 0 in ``Focus.beta``),
    so the choice is purely cosmetic — convention (a) keeps the code
    uniform with the bin-level ``0 · ln 0 = 0`` rule.
    """
    total = sum(workloads)
    if total <= 0:
        return 0.0
    return -sum((w / total) * math.log(w / total) for w in workloads if w > 0)


def _delta_entropy(
    *,
    job: BaseJob,
    server: Server,
    workloads: Sequence[float],
    server_index: Mapping[Server, int],
    pre_entropy: float,
) -> float:
    """Change in workload-entropy from hypothetically dispatching *job* at *server*.

    Computes ``c(i) = e(i) - pre_entropy`` where ``e(i)`` is the entropy
    of the perturbed workload vector after the dispatch. The perturbation
    is two-point: ``W'[k] = max(0, W[k] - p_ik)`` and (if a next server
    ``u`` exists in *job*'s routing) ``W'[u] = W[u] + p_iu``.

    The ``max(0, ...)`` clamp at ``k`` handles the PSP-candidate edge
    case: a PSP candidate is not yet in any server's queue or users, so
    ``W[k]`` does not include ``p_ik``. The clamp keeps ``W'[k] ≥ 0``;
    physically this represents "release into k creates load there",
    and the resulting entropy delta still gives the correct dispatch
    signal (releasing into an idle ``k`` increases imbalance, so
    ``c(i) ≤ 0`` and beta = 0 — i.e. beta refuses to pull from PSP for
    balance reasons in that situation).
    """
    k_idx = server_index[server]
    p_ik = job.routing[server]
    u = _next_server_after(job, server)

    w_prime = list(workloads)
    w_prime[k_idx] = max(0.0, w_prime[k_idx] - p_ik)
    if u is not None:
        u_idx = server_index[u]
        w_prime[u_idx] = w_prime[u_idx] + job.routing[u]

    return _entropy(w_prime) - pre_entropy


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

    Built once per decision via ``Focus.build_context`` and reused
    across all candidates being scored at that instant. Computing this
    object is ``O(|O| · |J|)`` (the ``|J|`` factor comes from the beta
    entropy pass; without beta the cost is ``O(|O|)``).

    Attributes:
        max_pij: Max processing time over all pending ``(i, j)`` pairs in
            the shop (the set ``D`` in the spec). ``0`` if no pending ops.
        empty_queue_servers: Servers whose ``queue`` is empty at the
            snapshot instant.
        max_positive_slack: Max of ``S_i`` across all jobs in ``O`` with
            positive slack; ``0`` if no positive-slack jobs.
        max_positive_pacing: Max of ``V_i = S_i / |R_i|`` across all jobs
            in ``O`` with positive ``V_i``; ``0`` if none.
        workloads: Per-server workload ``W_j = sum p_xj`` over jobs in
            ``server.queue ∪ server.users`` (full processing time). Indexed
            by ``server_index[server]``.
        server_index: Read-only mapping ``Server -> index into workloads``.
            Wrapped in ``types.MappingProxyType`` so shallow
            mutation of the index is detected at runtime. Tightly coupled
            with ``workloads``; same lifetime.
        pre_entropy: Shop-wide workload entropy at the snapshot instant
            (``e_minus`` in the beta spec). See ``_entropy`` for the
            empty-shop convention.
        max_positive_c: Max of ``c(i) = e(i) - pre_entropy`` across all
            jobs ``i`` in ``O`` with ``c(i) > 0``; ``0`` if no improving
            dispatch exists. Beta's normalizer.
        c_values: Read-only mapping ``job -> c(i)`` for every job in ``O`` with
            remaining ops, computed in the beta pass at the job's first
            uncompleted server. Empty when ``compute_beta=False``. Lets
            ``beta`` reuse the per-job entropy delta instead of recomputing it.
    """

    max_pij: float
    empty_queue_servers: frozenset[Server]
    max_positive_slack: float
    max_positive_pacing: float
    workloads: tuple[float, ...]
    server_index: Mapping[Server, int]
    pre_entropy: float
    max_positive_c: float
    c_values: Mapping[BaseJob, float] = field(default_factory=lambda: MappingProxyType({}))


class _StateMemo:
    """Single-entry memo of a FocusContext derived from frozen shop state.

    ``Server.sort_queue`` invokes the priority policy once per queued request
    within one synchronous pass, during which ``env.now`` and the scanned shop
    state are frozen; ``Focus.build_context`` would otherwise recompute an
    identical context once per request. This caches the last context keyed on
    an identity fingerprint of the scanned state — ``now`` plus the identities
    of all queued, in-service, and PSP jobs — rebuilding only when the
    fingerprint changes. The fingerprint provably determines the context: every
    ``FocusContext`` field derives from those jobs and ``now``, and a job's
    ``unfinished_routing`` changes only when it leaves the scanned set.

    The saving is a constant factor when beta is disabled (``build`` is
    ``O(|O|)``) and asymptotically significant (``O(|O|·|J|)`` → ``O(|O|)``)
    when beta is active. The fingerprint is order-sensitive, so a queue
    reorder at the same instant forces a rebuild that is redundant (never
    stale) — an acceptable cost given the win within a sort pass.
    """

    def __init__(
        self,
        shopfloor: ShopFloor,
        *,
        psp: PreShopPool | None,
        build: Callable[[float], FocusContext],
    ) -> None:
        self._shopfloor = shopfloor
        self._psp = psp
        self._build = build
        self._key: tuple[object, ...] | None = None
        self._ctx: FocusContext | None = None

    def _fingerprint(self, now: float) -> tuple[object, ...]:
        servers = self._shopfloor.servers
        return (
            now,
            tuple(j for s in servers for j in s.queueing_jobs),
            tuple(j for s in servers for j in s.current_jobs),
            tuple(self._psp.jobs) if self._psp is not None else (),
        )

    def get(self, now: float) -> FocusContext:
        key = self._fingerprint(now)
        if self._ctx is None or key != self._key:
            self._ctx = self._build(now)
            self._key = key
        return self._ctx


class Focus:
    """FOCUS dispatching rule — weighted combination of five impact mechanisms.

    The class is stateless beyond its weights. Computations are organised
    so each mechanism (``pi``, ``omega``, ``psi``, ``gamma``, ``beta``)
    is exposed independently for testability and for use as a building
    block by higher-level policies (DRACO).

    All five mechanisms return values in ``[0, 1]`` with ``1`` indicating
    a "relevant" impact and ``0`` an "irrelevant" one. The aggregated
    ``score`` is the weighted average of the five pieces and also
    lies in ``[0, 1]``.

    Design note: unlike the stateless dispatching-rule factories (e.g.
    ``planned_slack_time``), FOCUS is a class because it exposes each
    mechanism as an independently testable method and a shared per-decision
    ``build_context`` consumed by higher-level policies (DRACO). A bare
    ``(job, server) -> float`` closure cannot expose these. Use
    ``FocusPriorityRule`` to adapt a ``Focus`` to the ``priority_policy``
    contract.

    Args:
        weights: ``(w1, w2, w3, w4, w5)`` for the five mechanisms; must
            each be in ``[0, 1]`` and sum to ``1`` (within floating-point
            tolerance). A zero weight disables the corresponding
            mechanism. Defaults to ``(0.25, 0.25, 0.25, 0.25, 0.0)`` —
            beta dormant, preserving the original four-mechanism
            behaviour.

    Example (inside DRACO — one ctx, many candidates):
        >>> focus = Focus(weights=(0.2, 0.2, 0.2, 0.2, 0.2))
        >>> ctx = focus.build_context(shopfloor, env.now, psp=psp)
        >>> for candidate in candidates:
        ...     d_score = focus.score(candidate, server_k, ctx, env.now)
    """

    def __init__(
        self,
        weights: tuple[float, float, float, float, float] = (0.25, 0.25, 0.25, 0.25, 0.0),
    ) -> None:
        if len(weights) != 5:
            raise ValueError(f"focus weights must have exactly 5 elements, got {len(weights)}")
        if not all(0.0 <= w <= 1.0 for w in weights):
            raise ValueError(f"focus weights must each be in [0, 1], got {weights}")
        if not math.isclose(sum(weights), 1.0, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(f"focus weights must sum to 1, got {sum(weights)}")
        self.w1, self.w2, self.w3, self.w4, self.w5 = weights

    @staticmethod
    def build_context(
        shopfloor: ShopFloor,
        now: float,
        *,
        psp: PreShopPool | None = None,
        compute_beta: bool = True,
    ) -> FocusContext:
        """Compute the shop-wide aggregates needed by FOCUS at *now*.

        The order book ``O`` scanned here is every arrived, not-yet-completed
        order: jobs queued at any server (``server.queue``), jobs in process
        (``server.users``), and — if provided — ``psp.jobs``. This matches
        the FOCUS paper, which defines ``H_j ⊆ O`` and ``W_j = Q_j ∪ H_j``
        (Kasper et al. 2023, Omega §3): in-process orders belong to ``O`` and
        therefore to every normalizer (``max_pij``, ``max_positive_slack``,
        ``max_positive_pacing``, beta's ``max_positive_c``) and to the
        ``workloads``/``pre_entropy`` baseline, so all aggregates range over
        one consistent population. An in-process order's currently-executing
        operation counts as remaining (it is in ``unfinished_routing``),
        consistent with ``workloads`` already counting its full ``p_ij``.
        Pass *psp* when scoring decisions that include PSP candidates
        (e.g. DRACO); omit it for standalone-FOCUS dispatching where there
        is no pool.

        Cost: ``O(|O| · |J|)`` (the ``|J|`` factor comes from the beta
        entropy pass — one ``_entropy`` evaluation per job in ``O``);
        ``O(|O|)`` when ``compute_beta=False``.

        Pass ``compute_beta=False`` to skip the per-job workload-entropy
        pass when the beta mechanism is inactive (weight 0). ``workloads``,
        ``server_index`` and ``pre_entropy`` are still populated (cheap), so
        a direct ``beta`` call is still safe and returns ``0`` via its
        ``max_positive_c <= 0`` guard.

        Note on the empty-shop case: when every server is idle, the
        workload vector is all zero. By convention ``_entropy``
        returns ``0`` in that case, so ``pre_entropy = 0`` and every
        candidate's ``c(i) = 0``, producing ``beta = 0`` shop-wide. See
        ``_entropy`` for the rationale.
        """
        max_pij = 0.0
        max_positive_slack = 0.0
        max_positive_pacing = 0.0
        empty_queue_servers = frozenset(s for s in shopfloor.servers if len(s.queue) == 0)

        servers: Sequence[Server] = shopfloor.servers
        server_index: dict[Server, int] = {s: i for i, s in enumerate(servers)}
        workloads: list[float] = [
            sum(j.routing[s] for j in s.queueing_jobs) + sum(j.routing[s] for j in s.current_jobs) for s in servers
        ]
        pre_entropy = _entropy(workloads)

        # O is the order book: every arrived, not-yet-completed order. Per
        # Kasper et al. (2023, Omega §3) H_j ⊆ O, so in-process orders
        # (server.users) belong to it alongside queued orders (server.queue)
        # and — when scoring release decisions — PSP candidates. All four
        # normalizers (max_pij, slack, pacing, beta's max_positive_c) and the
        # workload/entropy baseline therefore range over one population. An
        # in-process order's currently-executing operation counts as remaining
        # (via unfinished_routing), consistent with the workload vector above
        # which already includes its full p_ij (W_j = Q_j ∪ H_j, Eq 2).
        jobs: list[BaseJob] = [j for s in shopfloor.servers for j in (*s.queueing_jobs, *s.current_jobs)]
        if psp is not None:
            jobs.extend(psp.jobs)

        max_positive_c = 0.0
        c_values: dict[BaseJob, float] = {}
        for job in jobs:
            remaining = job.unfinished_routing
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

            # Beta: c(i) at the job's first uncompleted server. Skipped when
            # beta is disabled (compute_beta=False) — the dominant per-rebuild
            # cost, pure waste when the beta weight is 0.
            if compute_beta:
                k = remaining[0]
                c_i = _delta_entropy(
                    job=job,
                    server=k,
                    workloads=workloads,
                    server_index=server_index,
                    pre_entropy=pre_entropy,
                )
                if c_i > max_positive_c:
                    max_positive_c = c_i
                c_values[job] = c_i

        return FocusContext(
            max_pij=max_pij,
            empty_queue_servers=empty_queue_servers,
            max_positive_slack=max_positive_slack,
            max_positive_pacing=max_positive_pacing,
            workloads=tuple(workloads),
            server_index=MappingProxyType(server_index),
            pre_entropy=pre_entropy,
            max_positive_c=max_positive_c,
            c_values=MappingProxyType(c_values),
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
        remaining = job.unfinished_routing
        if not remaining:
            return 1.0
        s_i = job.due_date - now - sum(job.routing[srv] for srv in remaining)
        v_i = s_i / len(remaining)
        if v_i <= 0:
            return 1.0
        if ctx.max_positive_pacing <= 0:
            return 1.0
        return 1.0 - v_i / ctx.max_positive_pacing

    def beta(self, job: BaseJob, server: Server, ctx: FocusContext) -> float:
        """WIP balancing (Omega paper §3.3.5): favour dispatches that improve workload balance.

        ``beta = c(i) / ctx.max_positive_c`` if ``c(i) > 0``, else ``0``.

        ``c(i) = e(i) - ctx.pre_entropy`` is the change in shop-wide
        workload entropy from hypothetically dispatching *job* at
        *server*. See ``_entropy`` for the empty-shop convention and
        ``_delta_entropy`` for the perturbation formula.

        Guard: if ``ctx.max_positive_c <= 0`` (no candidate in the
        snapshot improved balance), beta returns ``0`` immediately,
        preventing a ``ZeroDivisionError`` for future callers that build
        a context with a candidate set disjoint from the one used here.

        Invariant: callers must pass *server* equal to *job*'s first
        uncompleted server (``job.unfinished_routing[0]``) — the server at
        which ``ctx.max_positive_c`` was normalized in ``build_context``. All
        built-in call paths satisfy this (queue ordering scores a job at the
        server whose queue it sits in; PSP candidates start at *server* via
        ``starts_at``), which keeps ``beta`` in ``[0, 1]``. Passing any other
        server can yield ``beta > 1``. This same invariant governs cache
        validity: a valid caller's cached ``c_i`` from ``ctx.c_values`` equals
        exactly what a fresh ``_delta_entropy`` call would return, because both
        evaluate at ``job.unfinished_routing[0]``.
        """
        c_i = ctx.c_values.get(job)  # server implicit: cache built at job's unfinished_routing[0]
        if c_i is None:
            c_i = _delta_entropy(
                job=job,
                server=server,
                workloads=ctx.workloads,
                server_index=ctx.server_index,
                pre_entropy=ctx.pre_entropy,
            )
        if c_i <= 0.0:
            return 0.0
        if ctx.max_positive_c <= 0:
            return 0.0
        return c_i / ctx.max_positive_c

    def score(self, job: BaseJob, server: Server, ctx: FocusContext, now: float) -> float:
        """Aggregate weighted score of the five mechanisms; value in ``[0, 1]``."""
        beta_term = self.w5 * self.beta(job, server, ctx) if self.w5 != 0.0 else 0.0
        return (
            self.w1 * self.pi(job, server, ctx)
            + self.w2 * self.omega(job, server, ctx)
            + self.w3 * self.psi(job, ctx, now)
            + self.w4 * self.gamma(job, ctx, now)
            + beta_term
        )

    @staticmethod
    def _slack(job: BaseJob, now: float) -> float:
        """``S_i = d_i - now - sum(p_ij for j in R_i)`` over ``job.unfinished_routing``."""
        return job.due_date - now - sum(job.routing[srv] for srv in job.unfinished_routing)


class FocusPriorityRule:
    """Adapter exposing ``Focus`` as a simulatte ``priority_policy``.

    Wraps a ``Focus`` and a ``ShopFloor``
    into a ``(job, server) -> float`` callable suitable for
    ``simulatte.router.Router.priority_policies``. The returned value
    is the *negated* FOCUS score because simulatte's
    ``simpy.resources.resource.PriorityResource`` sorts ascending
    (lower key = served first).

    Liveness guarantee:
        ``simulatte.server.Server.sort_queue`` re-evaluates
        ``priority_policy`` for every queued request before every
        dispatch event (auto-called by ``_trigger_put``). The context
        is memoized within a single ``sort_queue`` pass (same ``now``
        and frozen shop state → one build reused across all requests in
        that pass) and rebuilt whenever the scanned shop state changes,
        so the key returned at dispatch time always reflects current
        shop aggregates — no external refresh helper is needed.

    Args:
        focus: A ``Focus`` instance.
        shopfloor: The shopfloor against which ``ctx`` is built per call.
        psp: Optional PreShopPool; when provided its jobs are included
            in the ``O`` aggregate (so PSP candidates show up in FOCUS
            aggregates even before release).
    """

    def __init__(self, focus: Focus, shopfloor: ShopFloor, *, psp: PreShopPool | None = None) -> None:
        self.focus = focus
        self.shopfloor = shopfloor
        self.psp = psp
        self._memo = _StateMemo(
            shopfloor,
            psp=psp,
            build=lambda now: focus.build_context(shopfloor, now, psp=psp, compute_beta=focus.w5 != 0.0),
        )

    def __call__(self, job: BaseJob, server: Server) -> float:
        now = self.shopfloor.env.now
        ctx = self._memo.get(now)
        return -self.focus.score(job, server, ctx, now)
