# DRACO Implementation — Faithfulness Review

**Subject:** `src/simulatte/policies/draco.py` (and its dependency `src/simulatte/dispatching_rules/focus.py`)
**Reference paper:** T.A. Arno Kasper, Martin J. Land, Ruud H. Teunter (2023), *Non-hierarchical work-in-progress control in manufacturing*, **International Journal of Production Economics 257, 108768**. https://doi.org/10.1016/j.ijpe.2022.108768
**Methodology reviewed:** §3.1, Equations (1)–(10); experimental design §4 / Table 2.
**Date:** 2026-05-29
**Verification:** `uv run pytest tests/core/test_draco.py tests/core/test_focus.py` → **95 passed**; `draco.py` at 100% line + branch coverage.

---

## 1. Executive verdict

The implementation is a **faithful and unusually careful** rendering of DRACO. Every scoring formula in the paper is reproduced exactly:

- Release impact `R` — Eqs (1)–(3) ✅
- Authorization impact `A` — Eq (4) ✅
- Dispatching impact `D` (= FOCUS) — Eqs (5)–(9) ✅
- Non-hierarchical order selection — Eq (10) ✅
- Decision trigger = "a work centre becomes available" ✅

The deviations identified are **not formula errors**. They are *modeling-instant* and *state-population* choices. All but one are explicitly documented in the source (docstrings, a `TODO`, or a regression test that pins the behavior). They are listed in §4, ranked by materiality.

A scope note (see §7): the `D` term **is** FOCUS, whose primitives — especially the optional β mechanism — originate in a *different* paper (Kasper, Land & Teunter, *Omega* 114, 102726, 2023). This review verifies `D` against the **restatement** of FOCUS given in the IJPE/DRACO paper (Eqs 5–9), which is the correct standard for "DRACO as introduced here." It does not trace FOCUS back to its Omega source.

---

## 2. Paper methodology (as implemented against)

### 2.1 Notation (§3.1)

- `O` — the **order book**: all orders that have *arrived but are not yet completed*.
- `J` — set of work centres; generic index `j`, decision centre `k`.
- `Q_j` — orders queuing at work centre `j`.
- `P_j ⊆ O` — the **pool**: unreleased orders whose *first* operation is at `j`.
- `H_j` — the order *in process* at `j`; each centre handles ≤ 1 order, so `|H_j| ≤ 1`.
- `w = Σ_{j∈J} (|Q_j| + |H_j|)` — WIP, a **job count** (similar to CONWIP).
- `τ > 0` — WIP target.

### 2.2 Release impact `R` (§3.1.1)

For an **unreleased** order `i ∈ P_k` (Eq 1):

```
ρ^P(i,k) = 1 − w/(2τ)   if w < 2τ
         = 0            if w ≥ 2τ
```

For an **already-released** order `i ∈ Q_k` (Eq 2):

```
ρ^Q(i,k) = w/(2τ)       if w < 2τ
         = 1            if w ≥ 2τ
```

Combined (Eq 3): `R(i,k) = ρ^P(i,k)·𝟙_{P_k}(i) + ρ^Q(i,k)·𝟙_{Q_k}(i)`.

### 2.3 Authorization impact `A` (§3.1.2)

Overlapping loop between centre `k` and the next centre `u = n_i`:

```
a_{k,u} = |H_k| + |Q_u| + |H_u|
```

Projected authorization impact (Eq 4):

```
A(i,k) = 1 − a_{k,n_i}/ζ_{k,n_i}   if a_{k,n_i} < ζ_{k,n_i}
       = 0                          if a_{k,n_i} ≥ ζ_{k,n_i}
A(i,k) = 1                          if k is the last operation of order i
```

### 2.4 Dispatching impact `D` = FOCUS (§3.1.3)

`D = {(i,j), …}` — pairs of order `i` with a **remaining operation** at centre `j`, over all orders in `O`.

- **SPT (Eq 5):** `π(i,k) = 1 − p_{ik} / max_{(i,j)∈D} p_{ij}`
- **Starvation response (Eq 6):** `ξ(i,k) = π(i,k)` if `n_i ∈ S`, else `0`, where `S = {j∈J : Q_j = ∅}`.
- **Slack timing (Eq 7):** `s(i) = d_i − t − Σ_{j∈R_i} p_{ij}`; `ψ(i) = 1 − s(i)/max_{i∈O} s(i)` if `s(i) > 0`, else `1`.
- **Pacing (Eq 8):** `υ(i) = s(i)/|R_i|`; `δ(i) = 1 − υ(i)/max_{i∈O} υ(i)` if `υ(i) > 0`, else `1`.
- **Aggregate (Eq 9):** `D(i,k) = π·w₁ + ξ·w₂ + ψ·w₃ + δ·w₄`, with `Σ wₘ = 1`.

(The paper's FOCUS has exactly these **four** mechanisms. There is no β/WIP-balancing term in the IJPE paper — that belongs to the Omega FOCUS paper.)

### 2.5 Order selection (§3.1.4, Eq 10)

```
z = argmax_{i ∈ Q_k ∪ P_k}  W^R·R(i,k) + W^A·A(i,k) + W^D·D(i,k)
```

### 2.6 Experimental configuration (§4 / Table 2)

| Variant | W^R | W^A | W^D | Notes |
|---|---|---|---|---|
| **DRACO (full)** | 1/4 | 1/4 | 1/2 | `τ ∈ {3,6,7,9,12,18,42}`, `ζ_{ju} = max(1, ⌊2τ/|J|⌋)` |
| DRACO (D) | 0 | 0 | 1 | dispatching only |
| DRACO (R+D) | 1/2 | 0 | 1/2 | |
| DRACO (A+D) | 0 | 1/2 | 1/2 | `ζ_{ju} ∈ {1,2,3,4,8,10}` |
| FOCUS | 0 | 0 | 1 | immediate release + authorization |

- FOCUS sub-weights set to `w₁..w₄ = 1/4` for all variants.
- System: pure job shop, **6 work centres, each capacity 1**; arrivals `1/λ = 0.648`; 2-Erlang process times (mean 1, truncated at 4); ≈ 90% utilization.

---

## 3. Formula-by-formula correspondence

| Paper construct | Implementation site | Verdict |
|---|---|---|
| `w = Σ_j(|Q_j|+|H_j|)`, job count | `Draco._count_wip` = `Σ_s(len(s.queue) + s.count)` — `draco.py:266–273` | ✅ Exact. Deliberately a *count*, independent of the shopfloor's workload-based `WIPStrategy` (docstring notes the two metrics won't match numerically; DRACO's τ is a job count). |
| `ρ^P` (Eq 1) | `Draco._ro_P` = `max(0, 1 − wip/(2τ))` — `draco.py:275–277` | ✅ |
| `ρ^Q` (Eq 2) | `Draco._ro_Q` = `min(1, wip/(2τ))` — `draco.py:279–281` | ✅ |
| `R` (Eq 3) | `_full_score(..., in_psp=…)` selects `ro_P` vs `ro_Q` — `draco.py:329` | ✅ PSP candidates scored with `ρ^P`, queue candidates with `ρ^Q`. |
| `a_{k,u} = |H_k|+|Q_u|+|H_u|` | `Draco._overlapping_loop_count` = `server_k.count + len(server_u.queue) + server_u.count` — `draco.py:283–291` | ✅ At decision time `|H_k| = 0` (k just freed); the formula is general and correct elsewhere too. |
| `A` (Eq 4), last-op = 1 | `Draco._authorization_impact` — `draco.py:299–313` | ✅ `u = None ⇒ 1.0`; `a ≥ ε ⇒ 0.0`; else `1 − a/ε`. |
| `ζ_{k,u}` resolution | `Draco._overlapping_loop_target` — `draco.py:293–297` | ✅ Scalar **or** per-pair `dict[(Server,Server), int]`. |
| `π` (Eq 5) | `Focus.pi` over `ctx.max_pij` — `focus.py:369–377`; `max_pij` over **all remaining ops** of all scored jobs — `focus.py:328–331` | ✅ formula; ⚠ population (Deviation A). `max_pij == 0 ⇒ π = 1` defensive guard. |
| `ξ`, `S = {j:Q_j=∅}` (Eq 6) | `Focus.omega`: next server ∈ `ctx.empty_queue_servers` — `focus.py:379–391`; `S` built as `len(s.queue)==0` — `focus.py:302` | ✅ Correctly keys on **queue-emptiness**, not idleness: a busy server with an empty queue still counts as starving, matching the paper. Last-op ⇒ 0. |
| `ψ`, slack (Eq 7) | `Focus.psi`; `s_i = due − now − Σ_{remaining} p` — `focus.py:393–406` | ✅ `s_i ≤ 0 ⇒ 1`; `max_positive_slack ≤ 0 ⇒ 1` defensive. |
| `δ`, pacing (Eq 8) | `Focus.gamma`; `υ = s_i/|R_i|` — `focus.py:408–425` | ✅ |
| `D` (Eq 9) | `Focus.score`, default weights `(.25,.25,.25,.25,0)` — `focus.py:468–477` | ✅ With default weights the 5th term (β) is zero and `D` reduces exactly to Eq 9. |
| selection (Eq 10) | `Draco.decide_next_job`: `max` over `Q_k = [req.job for req in server_k.queue]` ∪ `P_k = [j for j in psp.jobs if j.starts_at(server_k)]` — `draco.py:232–246` | ✅ |
| decision moment = centre becomes available | `shopfloor.on_processing_end(draco.decide_next_job)` — `builders.py:511`; servers `capacity=1` — `builders.py:480` | ✅ Completion trigger + single-machine `|H_j| ≤ 1`. |

### 3.1 Force-pin / dispatch mechanism (no paper analogue)

`Draco` maintains `_forced_at_server: dict[Server, ProductionJob]` (`draco.py:163`). On each decision it pins the Eq-10 winner: `priority_policy` returns `−inf` for the forced job (`draco.py:195–198`), guaranteeing `queue[0] = winner` across every `sort_queue` re-evaluation; a PSP winner is additionally released onto the floor (`draco.py:255–260`). The accompanying SimPy timing argument (URGENT `Initialize` before NORMAL `Release`; `draco.py:63–95`) is **implementation scaffolding** with no counterpart in the paper — it exists only to make SimPy's dispatch obey DRACO's decision and does not change the DRACO math. Its net effect ("the Eq-10 winner is processed next") is validated behaviorally by `test_draco_psp_winner_processes_immediately` and `test_draco_winner_via_R_boost_still_processes_first`. Correctness assumes `capacity == 1` (one freed slot per completion), which `build_draco_system` enforces.

---

## 4. Deviations from the paper (ranked by materiality)

### A. In-process orders are excluded from the order book `O` *(documented `TODO`; the substantive gap)*

The paper: `O = {orders arrived and not yet completed}` (p.4), and the FOCUS normalizers range over `O` / `D`. As a set identity:

- **Paper:** `O = Q ∪ H ∪ P` (queued ∪ **in-process** ∪ pool)
- **Code:** `jobs = Q ∪ P` — the in-process set `H` is dropped (`focus.py:317–319`):

```python
jobs: list[BaseJob] = [j for s in shopfloor.servers for j in s.queueing_jobs]
if psp is not None:
    jobs.extend(psp.jobs)
```

In-process orders have arrived and are not completed, so by the paper's own wording they unambiguously belong to `O`. This is therefore a **deviation**, not a genuine textual ambiguity; the `TODO(draco-focus)` hedging at `focus.py:311–316` is justified by *change-risk*, not by the paper being unclear.

**Corroborating internal inconsistency.** Within the *same* `FocusContext`, two different populations are used:

- `workloads` and `pre_entropy` (`focus.py:306–309`) **include** `current_jobs` (in-process);
- the normalizer scan that produces `max_pij`, `max_positive_slack`, `max_positive_pacing`, `max_positive_c` (`focus.py:317–339`) **excludes** them.

So β's entropy baseline counts in-process orders, but the SPT/slack/pacing/β denominators do not — whereas the paper's `O` is a single set. This split is the signature of an oversight rather than a deliberate, consistent modeling choice.

**Magnitude (analytic — measuring empirically is the wrong altitude).** The *relative* ranking of candidates at the same centre `k` by `π` is invariant to `max_pij` (π is monotone in `p_ik` for fixed denominator). But the *absolute* values of `π`, `ψ`, `δ` feed the weighted sum `D`, and `D` feeds the Eq-10 argmax across `R`/`A`/`D`. Excluding `H` can therefore shift the selected order. Probability low, but nonzero — and this is the **only** deviation that changes *which orders are scored*.

### B. The just-completed multi-op job is absent at the decision instant *(documented; off-by-one, multi-op only)*

`decide_next_job` fires after `server.release` but **before** the finished job re-enters its next server's queue (`draco.py:85–95`, "Decision instant"). For a multi-operation triggering job this means, at the decision moment, the job is in **no** `Q_j` and **no** `H_j` — it is "in transit." The paper treats completion and queue-join as simultaneous, so it would already count that job in `Q_{next}`. Consequences:

1. **WIP / R bias.** `w` is undercounted by 1 ⇒ `ρ^P` higher, `ρ^Q` lower ⇒ the `R` term is nudged toward *releasing* a pool job. The test `test_draco_decide_next_job_uses_uncorrected_count_wip` pins this as intentional.
2. **A term.** Any candidate routing to the same downstream `u` sees `|Q_u|` one lower than the paper would.
3. **Starvation set `S`.** The in-transit job's target `u` can spuriously appear in `S` (empty queue), granting a bogus `ξ` bonus to candidates that feed `u`.

This is best framed as a **completion-instant ordering** interpretation (does the finished job join `Q_{next}` *before* or *after* the decision?), resolved here as "pre-transit." It affects exactly one job and only multi-operation triggers; single-op (last-operation) triggers genuinely leave `O`, so they are unaffected and faithful.

### C. Default `total_impact_weights = (1/3, 1/3, 1/3)` reproduces no Table-2 variant *(reproducibility, not a bug)*

The paper's canonical full DRACO uses **`W^R = W^A = 1/4, W^D = 1/2`** (Table 2). Both the `Draco` constructor (`draco.py:136`) and `build_draco_system` (`builders.py:425`) default to equal thirds, which matches **none** of the five Table-2 configurations. The parameter is fully configurable, but reproducing the paper requires passing `total_impact_weights=(0.25, 0.25, 0.5)` explicitly. (The FOCUS sub-weight default `(0.25, 0.25, 0.25, 0.25, 0.0)` *does* match the paper's `w₁..w₄ = 1/4`.)

### D. `loop_target ζ` is not auto-derived from τ *(reproducibility)*

For full DRACO the paper ties the overlapping-loop target to the WIP target: `ζ_{ju} = max(1, ⌊2τ/|J|⌋)`. The implementation takes `loop_target` as a free scalar/dict — correct and more general, but reproducing the paper's full-DRACO curve means computing `max(1, ⌊2τ/6⌋)` by hand for each τ.

### E. `starvation_avoidance` bypasses `R`/`A`/`D` scoring on arrival *(documented liveness provision)*

DRACO decisions fire only on completion, so in an idle/lightly loaded shop an arriving job could sit in the PSP forever. `build_draco_system` wires `psp.on_arrival(starvation_avoidance)` (`builders.py:512`), which releases an arrival immediately **iff its first server is idle** (`starvation_avoidance.py:32–35`), bypassing the Eq-10 scoring. The docstring (`draco.py:97–106`) explicitly frames this as a liveness provision whose faithfulness to the paper is "not verified against the primary source." In the common case (single arrival, fully idle shop) the outcome coincides with what DRACO would pick; it can diverge when multiple `P_k` pool candidates exist at the idle server, where the paper would score and choose among them.

### F. A released job routing into an idle *downstream* server is auto-granted with no DRACO decision *(the only deviation with no acknowledging comment)*

Because `decide_next_job` is wired solely to `on_processing_end`, consider: a released job finishes at `A` and routes into a *downstream* server `B` that is idle, while a pre-existing pool candidate in `P_B` waits. SimPy grants `B` to the arriving job (via `_trigger_put` → `sort_queue` → immediate grant); **no `decide_next_job` runs for `B`**, so DRACO never weighs pulling the `P_B` candidate instead. This is a genuine "decision moment" in the paper's sense (a candidate is available at an available centre) that passes without DRACO scoring. It is rare at the paper's ~90% utilization, but unlike Deviations A/B/E it has **no docstring/TODO/test acknowledging it**.

---

## 5. Test-suite assessment

`tests/core/test_draco.py` (≈758 lines) and `tests/core/test_focus.py` (≈1098 lines); **95 tests pass**; `draco.py` 100% line + branch coverage from this subset.

**Strengths**

- **Hand-computed constants** pin each term: `test_draco_full_score_matches_hand_computed_total_impact` checks both `in_psp` paths against `(ρ + A + D)/3`; `test_focus_score_equals_hand_computed_constant` and the per-mechanism tests (`pi`/`omega`/`psi`/`gamma`/`beta`) check FOCUS pieces individually.
- **R term** boundaries (`_ro_P`/`_ro_Q` at `w=0`, `w=τ`, `w≥2τ`).
- **A term**: last-operation = 1, `a = ε ⇒ 0`, partial `1 − a/ε`, and the per-pair `dict` form.
- **Force-pin lifecycle**: set on win, persists across repeated `priority_policy` calls, per-server, cleared at the *start* of the next `decide_next_job`.
- **Dispatch paths**: PSP-winner-processes-immediately, queue-winner-dispatched, and R-boost overriding A+D — all validating "winner is processed next" behaviorally.
- **Context memoization** fingerprint (rebuild on time advance or job-set change).
- **WIP counting**: count vs workload, across multiple servers, and the deliberate "uncorrected" (in-transit-excluded) count.
- **Cold start**: `test_build_draco_system_wires_starvation_avoidance`.

**Gaps (none severe)**

- **Deviations A and B are locked in as expectations, not questioned.** `test_draco_decide_next_job_uses_uncorrected_count_wip` enshrines the in-transit exclusion; nothing tests against the paper's `O = Q ∪ H ∪ P`. A test asserting that an in-process order moves `max_pij`/`max_slack` would catch a future fix or regression in either direction.
- **Deviation F is untested** (downstream idle auto-grant), as is `starvation_avoidance` with multiple competing `P_k` candidates at the idle server.
- **Per-pair `ζ` dict** is tested with a single pair only; multi-pair routings and key/routing mismatches (sparse dict, key not on the job's route) are untested.
- No long-horizon integration test driving DRACO through many consecutive decisions (the behavioral dispatch tests are short, targeted runs).

---

## 6. Suggested follow-ups (optional, ranked)

1. **Resolve Deviation A (substantive).** Include `current_jobs`' remaining operations in the `jobs` scan at `focus.py:317–319` so the SPT/slack/pacing/β normalizers and the workload/entropy baseline use one population — matching the paper's `O = Q ∪ H ∪ P`. Guard with a test asserting an in-process order can set `max_pij`. This is the only change with a plausible effect on decisions.
2. **Default to the paper's weights.** Make `total_impact_weights` default to `(0.25, 0.25, 0.5)` (full DRACO), or document prominently that the current default reproduces no Table-2 variant.
3. **Acknowledge Deviation F.** Add a one-line comment at the downstream auto-grant path so it is documented alongside A/B/E, and decide whether to trigger a DRACO decision when a routed job lands on an idle server with non-empty `P_k`.
4. **Reproducibility helper.** Optionally let `build_draco_system` derive `ζ = max(1, ⌊2τ/n_servers⌋)` for paper-faithful full-DRACO setups.
5. **Coverage** for the per-pair `ζ` dict (multi-pair routings) and `starvation_avoidance` with multiple `P_k` candidates.

---

## 7. Scope and limitations of this review

- The `D` term **is** FOCUS. Its primitives — particularly the optional β (WIP-balancing) mechanism, `focus.py:427–466`, which is **off by default** (`w₅ = 0`) and documented as counter-productive per its source — originate in **Kasper, Land & Teunter (2023), *Towards system state dispatching in high-variety manufacturing*, Omega 114, 102726**, which was *not* read for this review. `D` was verified against the **restatement** in the IJPE/DRACO paper (Eqs 5–9), which is the correct standard for "DRACO as introduced." Deeper FOCUS faithfulness (β's exact entropy formulation, the precise definition of FOCUS's own `O`) traces to that Omega source.
- Faithfulness was assessed analytically against the published equations and confirmed by reading the code and running the targeted tests. No new long-horizon simulation experiments were run to quantify the *numerical* impact of Deviations A/B; the argument that their impact is "low but nonzero" is analytic.
- The SimPy event-ordering argument underpinning the force-pin (Deviation-free, but implementation-specific) was confirmed at the wiring level (`on_processing_end` fires after `server.release` and before process-based listeners resume) and validated behaviorally by the dispatch tests, not by instrumenting the SimPy event queue directly.

---

## 8. Bottom line

DRACO is implemented correctly and with care: all ten governing equations match, the non-hierarchical single-decision structure is preserved, and the trickiest part (making SimPy dispatch obey the Eq-10 winner) is handled and tested. The faithfulness questions that remain are about **which orders populate the order book at the decision instant** (Deviation A — in-process exclusion — and Deviation B — the in-transit triggering job), both of which shift state by at most a handful of orders and only Deviation A can change which orders are scored. Addressing Deviation A and aligning the default weights with Table 2 would make the implementation an exact, paper-reproducible DRACO.
