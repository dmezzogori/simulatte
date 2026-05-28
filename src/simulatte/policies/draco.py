"""DRACO non-hierarchical work-in-progress control policy.

Implements DRACO (*Dispatching, Release, and Authorization for Controlled
Order flow*) from Kasper, Land, Teunter (2023), *Non-hierarchical
work-in-progress control in manufacturing*, IJPE 257, 108768. DRACO
merges release, authorization, and dispatching into a single per-server
decision triggered on every job completion: at each completion at server
``k``, the policy scores every candidate in ``Q_k ∪ P_k`` (queued jobs
at ``k`` plus PSP jobs whose first server is ``k``) by a weighted total
impact ``w^R·R + w^A·A + w^D·D`` and selects the maximum.

DRACO uses FOCUS (Kasper et al. 2023, Omega 114, 102726) as its
dispatching component — see ``simulatte.dispatching_rules.Focus``.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, cast

from simulatte.dispatching_rules.focus import Focus, FocusContext, _next_server_after

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.job import BaseJob, ProductionJob
    from simulatte.psp import PreShopPool
    from simulatte.server import Server
    from simulatte.shopfloor import ShopFloor


class Draco:
    """Non-hierarchical DRACO release/dispatch policy.

    Design note: DRACO is a class (not a dispatching-rule factory) because
    it holds shop-coupled state (the WIP target, per-pair loop targets, the
    embedded ``Focus``, and the one-shot force flags) and exposes both a
    ``priority_policy`` and an ``on_completion`` callback.

    Trigger: ``on_completion_trigger`` — same trigger as SLAR. On every
    job exit from any server ``k``, ``decide_next_job`` runs and
    selects the next job to be processed at ``k`` from
    ``Q_k ∪ P_k``.

    Strict paper semantics — "the winner is the next processed":
        DRACO's R term differs between PSP candidates (``ro^P``) and
        queue members (``ro^Q``). A PSP candidate ``i`` can win
        DRACO's decision via the R boost ``w^R · (ro^P − ro^Q)`` even
        when some queued ``j`` has a higher A+D contribution. Once ``i``
        is released and enters the queue, its queue-side priority (which
        uses ``ro^Q`` like everyone else) might be worse than ``j``'s,
        and SimPy's ``_trigger_put`` would dispatch ``j`` first —
        violating DRACO's decision. To preserve strict paper semantics,
        DRACO sets a ``_forced_at_server`` flag for the PSP
        winner; ``priority_policy`` returns ``-inf`` for the forced
        job at that server for as long as the flag is set, guaranteeing
        ``queue[0] = winner`` across every ``sort_queue`` re-evaluation.
        The flag is cleared at the START of the next
        ``decide_next_job`` call for the same server (not on first
        read), so repeated ``_trigger_put`` sorts cannot wipe it out.

    Timing — why this is correct without any ``shopfloor.py`` changes:
        At a job completion, ``shopfloor.main`` (around
        ``shopfloor.py:1091``) succeeds ``job_processing_end`` (NORMAL)
        while the server is still held; the ``with``-block exit then
        calls ``server.release(req)``, which synchronously removes the
        request from ``users`` (so ``count`` drops to 0) and schedules a
        Release event (NORMAL). The on-completion trigger callback
        (DRACO) runs first because NORMAL events at the same instant are
        processed in id order and ``job_processing_end`` has the smaller
        id. If DRACO releases a PSP winner via ``psp.shopfloor.add``,
        the new process's ``Initialize`` event is **URGENT**
        (``simpy/events.py:270``) and therefore runs *before* the
        pending Release event; inside the new process, ``server.request``
        calls ``_trigger_put`` synchronously, finds ``users`` empty, and
        grants the slot immediately. The Release event then fires with
        nothing to dispatch. Net result: the PSP winner has the server.

    For the queue-winner case, the imminent Release event's
    ``_trigger_put`` calls ``sort_queue``,
    which re-evaluates ``priority_policy`` (live) for every queued
    request and yields the correct queue order.

    Cold start / bootstrapping:
        DRACO's decision is triggered *only* on job completions. In an idle
        or lightly loaded shop, no completion fires, so an arriving job would
        sit in the PSP indefinitely. ``build_draco_system`` therefore also
        wires ``psp.on_arrival(starvation_avoidance)``: when a new arrival's
        first server is completely idle, the job is released immediately,
        bypassing the ``R/A/D`` scoring. This is a liveness provision, not a
        DRACO decision — in steady state, completion-triggered decisions
        dominate. (Faithfulness of this provision to Kasper et al. 2023 has
        not been verified against the primary source.)

    Args:
        shopfloor: The shopfloor against which DRACO's contexts and
            count-WIP are computed. Required (unlike SLAR which is
            stateless against shop state).
        focus_weights: ``(w1, w2, w3, w4, w5)`` for FOCUS's five pieces.
        total_impact_weights: ``(w^R, w^A, w^D)``, must sum to 1.
        wip_target: Target shop WIP ``τ`` (count of jobs). Spec §3.1.
        loop_target: Target overlapping loop ``ε_{k,u}``. Spec §3.2.
            Accepts a scalar (applied to every pair) or a
            ``dict[(k, u), int]`` mapping for per-pair targets.
        psp: Optional PreShopPool. When provided, its jobs are included
            in the ``O`` aggregate that FOCUS uses (so PSP candidates
            are reflected in shop-wide aggregates like ``max p_ij`` and
            ``max S_i``). Optional because some test setups don't have
            a PSP wired up.

    References:
        Kasper, A., Land, M., Teunter, R. (2023). Non-hierarchical
        work-in-progress control in manufacturing. *International
        Journal of Production Economics*, 257, 108768.
        https://doi.org/10.1016/j.ijpe.2022.108768
    """

    def __init__(
        self,
        *,
        shopfloor: ShopFloor,
        focus_weights: tuple[float, float, float, float, float] = (0.25, 0.25, 0.25, 0.25, 0.0),
        total_impact_weights: tuple[float, float, float] = (1.0 / 3, 1.0 / 3, 1.0 / 3),
        wip_target: int,
        loop_target: int | dict[tuple[Server, Server], int],
        psp: PreShopPool | None = None,
    ) -> None:
        if len(total_impact_weights) != 3:
            raise ValueError(f"total_impact_weights must have exactly 3 elements, got {len(total_impact_weights)}")
        if not all(0.0 <= w <= 1.0 for w in total_impact_weights):
            raise ValueError(f"total_impact_weights must each be in [0, 1], got {total_impact_weights}")
        if not math.isclose(sum(total_impact_weights), 1.0, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(f"total_impact_weights must sum to 1, got {sum(total_impact_weights)}")
        if wip_target <= 0:
            raise ValueError(f"wip_target must be > 0, got {wip_target}")
        if isinstance(loop_target, dict):
            if not loop_target:
                raise ValueError("loop_target dict must not be empty")
            if not all(v > 0 for v in loop_target.values()):
                raise ValueError(f"loop_target values must all be > 0, got {loop_target}")
        elif loop_target <= 0:
            raise ValueError(f"loop_target must be > 0, got {loop_target}")

        self.focus = Focus(weights=focus_weights)
        self.wR, self.wA, self.wD = total_impact_weights
        self.tau = wip_target
        self.loop_target = loop_target
        self._shopfloor = shopfloor
        self._psp = psp
        self._forced_at_server: dict[Server, ProductionJob] = {}

    # ----- Public API -----

    def priority_policy(self, job: BaseJob, server: Server) -> float:
        """Queue-side priority for *job* at *server*.

        Returns ``-inf`` when DRACO has elected *job* as the PSP winner
        at *server* and the force flag is still set.  The flag is set in
        ``decide_next_job`` and cleared at the START of the next
        ``decide_next_job`` call for the same server — not on first
        read — so every ``sort_queue`` re-evaluation triggered by SimPy's
        ``_trigger_put`` consistently keeps the winner at ``queue[0]``
        until it is dispatched.

        Other jobs at the same server, and the same job at any other
        server, are unaffected because the flag is per ``(server, job)``
        identity.

        Otherwise returns the negated queue-side DRACO total impact,
        using ``R = ro^Q``. (R is constant across queue members at the
        same instant, but it is included for sign-consistency.)
        """
        forced = self._forced_at_server.get(server)
        if forced is job:
            return float("-inf")
        return -self._queue_side_score(job, server)

    def decide_next_job(self, triggering_job: ProductionJob, psp: PreShopPool) -> None:
        """``on_completion_trigger`` callback — the non-hierarchical decision.

        Scores every candidate in ``Q_k ∪ P_k`` using the full DRACO
        formula (``ro^P`` for PSP candidates, ``ro^Q`` for queued ones),
        then either releases the PSP winner (with the force-flag staged)
        or relies on the imminent Release event's ``_trigger_put`` call to
        ``sort_queue`` (which re-evaluates
        ``priority_policy`` live) to make the queue winner ``queue[0]``.
        """
        server_k = triggering_job.previous_server
        if server_k is None:
            return

        # Clear any force flag from the previous decide_next_job for this
        # server.  The previous winner has either been dispatched (and is
        # no longer in the queue) or moved on; the flag has done its job.
        self._forced_at_server.pop(server_k, None)

        now = self._shopfloor.env.now
        wip = self._count_wip()
        ctx = self.focus.build_context(self._shopfloor, now, psp=psp, compute_beta=self.focus.w5 != 0.0)

        queue_jobs = [req.job for req in server_k.queue]
        queue_scores: dict[BaseJob, float] = {
            j: self._full_score(j, server_k, ctx, now, wip, in_psp=False) for j in queue_jobs
        }

        psp_candidates = [j for j in psp.jobs if j.starts_at(server_k)]
        psp_scores: dict[BaseJob, float] = {
            j: self._full_score(j, server_k, ctx, now, wip, in_psp=True) for j in psp_candidates
        }

        all_scores: dict[BaseJob, float] = {**queue_scores, **psp_scores}
        if not all_scores:
            return

        winner = max(all_scores, key=lambda j: all_scores[j])

        if winner in psp_scores:
            # winner came from psp.jobs (Iterable[ProductionJob]), so the cast is safe.
            psp_winner: ProductionJob = cast("ProductionJob", winner)
            # Force absolute first-dispatch via the one-shot flag.
            self._forced_at_server[server_k] = psp_winner
            psp.remove(job=psp_winner)
            psp.shopfloor.add(psp_winner)
        # else: queue winner — the imminent Release event's _trigger_put will
        # call sort_queue, which re-evaluates priority_policy (live) for every
        # queued request and yields the correct order.

    # ----- Internal helpers (R, A, scoring) -----

    def _count_wip(self) -> int:
        """``W = Σ(|Q_j| + |H_j|)`` over all servers (spec §3.1).

        Count-based (jobs), independent of the shopfloor's ``WIPStrategy``
        (Standard/Corrected), which measures *workload*. The two metrics
        will not match numerically — DRACO's ``τ`` is a job count.
        """
        return sum(len(s.queue) + s.count for s in self._shopfloor.servers)

    def _ro_P(self, wip: int) -> float:
        """``ro^P = max(0, 1 - W/(2τ))`` (spec §3.1, PSP-side R term)."""
        return max(0.0, 1.0 - wip / (2.0 * self.tau))

    def _ro_Q(self, wip: int) -> float:
        """``ro^Q = min(1, W/(2τ))`` (spec §3.1, queue-side R term)."""
        return min(1.0, wip / (2.0 * self.tau))

    def _overlapping_loop_count(self, server_k: Server, server_u: Server) -> int:
        """``a_{k,u} = |H_k| + |Q_u| + |H_u|`` (spec §3.2).

        Note: at DRACO decision time the triggered server has just freed
        its slot, so ``|H_k| = 0`` (see spec §6.4). This formula is
        general and also correct in other call sites (e.g. priority
        evaluation at queue entry on a still-busy server).
        """
        return server_k.count + len(server_u.queue) + server_u.count

    def _overlapping_loop_target(self, server_k: Server, server_u: Server) -> int:
        """Resolve ``ε_{k,u}`` from the scalar or per-pair dict parameter."""
        if isinstance(self.loop_target, dict):
            return self.loop_target[(server_k, server_u)]
        return self.loop_target

    def _authorization_impact(self, job: BaseJob, server_k: Server) -> float:
        """``A(i, k)`` per spec §3.2.

        Returns 1 when *server_k* is the last operation in *job*'s
        routing (hard-coded per spec). Otherwise:
        ``A = max(0, 1 - a_{k,u} / ε_{k,u})``.
        """
        u = _next_server_after(job, server_k)
        if u is None:
            return 1.0
        a = self._overlapping_loop_count(server_k, u)
        eps = self._overlapping_loop_target(server_k, u)
        if a >= eps:
            return 0.0
        return 1.0 - a / eps

    def _full_score(
        self,
        job: BaseJob,
        server: Server,
        ctx: FocusContext,
        now: float,
        wip: int,
        *,
        in_psp: bool,
    ) -> float:
        """Full DRACO total impact for *job* at *server*.

        Uses ``ro^P`` when ``in_psp`` else ``ro^Q`` for the R term.
        """
        ro_r = self._ro_P(wip) if in_psp else self._ro_Q(wip)
        a_impact = self._authorization_impact(job, server)
        d_impact = self.focus.score(job, server, ctx, now)
        return self.wR * ro_r + self.wA * a_impact + self.wD * d_impact

    def _queue_side_score(self, job: BaseJob, server: Server) -> float:
        """Queue-side DRACO total impact (``R = ro^Q``).

        Rebuilds ``ctx`` and ``wip`` against current shopfloor state at
        O(|O|) cost per call. Called from ``priority_policy`` for
        single-job priority computation at queue entry. Inside
        ``decide_next_job``, the cached ``ctx``/``wip`` are forwarded
        to ``_full_score`` directly to avoid rebuilding.
        """
        now = self._shopfloor.env.now
        ctx = self.focus.build_context(self._shopfloor, now, psp=self._psp, compute_beta=self.focus.w5 != 0.0)
        wip = self._count_wip()
        return self._full_score(job, server, ctx, now, wip, in_psp=False)
