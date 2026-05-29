# DRACO + FOCUS Implementation — Faithfulness Review

**Subjects:**
- `src/simulatte/policies/draco.py` — the DRACO non-hierarchical WIP-control policy
- `src/simulatte/dispatching_rules/focus.py` — the FOCUS dispatching rule (DRACO's `D` term, and a standalone rule)

**Reference papers:**
1. **DRACO** — T.A. Arno Kasper, Martin J. Land, Ruud H. Teunter (2023), *Non-hierarchical work-in-progress control in manufacturing*, **International Journal of Production Economics 257, 108768**. https://doi.org/10.1016/j.ijpe.2022.108768 — methodology §3.1, Eqs (1)–(10); experimental design §4 / Table 2.
2. **FOCUS** — T.A. Arno Kasper, Martin J. Land, Ruud H. Teunter (2023), *Towards system state dispatching in high-variety manufacturing*, **Omega 114, 102726**. https://doi.org/10.1016/j.omega.2022.102726 — methodology §3, Eqs (1)–(13); experimental setup §4.2.

**Date:** 2026-05-29
**Verification:** `uv run pytest tests/core/test_draco.py tests/core/test_focus.py` → **95 passed**; `draco.py` at 100% line + branch coverage.

> **Revision note.** This report originally reviewed DRACO only and treated FOCUS (and its order-book definition) as out of scope. It has since been extended with a full review of FOCUS against its Omega-2023 source paper. The most consequential update: the FOCUS paper **adjudicates** the in-process-order question that DRACO's source left ambiguous — Deviation A is now **confirmed against the primary source**, not an open question.

---

## 1. Executive verdict

Both implementations are **faithful and unusually careful** renderings of their respective papers. Every scoring formula is reproduced exactly:

- **DRACO** — release `R` (Eqs 1–3), authorization `A` (Eq 4), dispatching `D` = FOCUS (Eqs 5–9), selection (Eq 10), completion trigger. ✅
- **FOCUS** — SPT `π` (Eq 1), WIP-balancing `β` (Eqs 2–6), starvation `ξ` (Eq 7), slack timing `τ` (Eqs 8–9), pacing `δ` (Eqs 10–11), aggregate `I` (Eq 12), selection from the queue (Eq 13). ✅

The deviations identified (§6) are **not formula errors**. They are *state-population* and *weight-labeling* choices. Ranked by materiality:

- **A (state population)** — in-process orders are excluded from the FOCUS order book `O`. The Omega paper defines `H_j ⊆ O` explicitly, so this is now a **confirmed deviation from the primary source** (previously flagged as an unverified `TODO`). It affects all four FOCUS normalizers and can shift decisions; magnitude low but nonzero. **The single substantive faithfulness gap.**
- **G (weight ordering)** — the `weights` tuple follows the *DRACO* paper's Eq-9 order (`π, ξ, τ, δ`, with `β` appended 5th), not the *FOCUS* paper's Eq-12 order (`π, β, ξ, τ, δ`). The default and the all-equal baseline are order-invariant, but reproducing the Omega paper's per-mechanism ablations requires translating the index. Doc-only fix.
- **B–F** — DRACO decision-instant and liveness choices, all but one documented in code.
- **H** — minor: the `spec §3.3.x` citations in `focus.py` don't match the Omega paper's numbering.

A positive correctness note that the second paper *confirms*: `focus.py`'s claim that "Kasper et al. report beta as counter-productive" is **verified** by the Omega paper (§5.2, Fig 3 — `FOCUS - β` is consistently the best ablation), and the implementation's β-off default is therefore a well-chosen default, not a deviation.

---

## 2. DRACO — methodology (IJPE 2023)

### 2.1 Notation (§3.1)

- `O` — order book: all orders arrived but not yet completed.
- `J` — work centres; index `j`, decision centre `k`.
- `Q_j` — orders queuing at `j`. `P_j ⊆ O` — pool: unreleased orders whose first op is at `j`. `H_j` — order in process at `j`, `|H_j| ≤ 1`.
- `w = Σ_{j∈J}(|Q_j| + |H_j|)` — WIP, a **job count**. `τ > 0` — WIP target.

### 2.2 Release impact `R` (§3.1.1)

```
ρ^P(i,k) = 1 − w/(2τ)  if w < 2τ  else 0      (Eq 1, unreleased i ∈ P_k)
ρ^Q(i,k) = w/(2τ)      if w < 2τ  else 1      (Eq 2, released  i ∈ Q_k)
R(i,k)   = ρ^P·𝟙_{P_k}(i) + ρ^Q·𝟙_{Q_k}(i)                          (Eq 3)
```

### 2.3 Authorization impact `A` (§3.1.2)

```
a_{k,u} = |H_k| + |Q_u| + |H_u|                  (overlapping loop, u = n_i)
A(i,k)  = 1 − a_{k,n_i}/ζ_{k,n_i}  if a < ζ  else 0                  (Eq 4)
A(i,k)  = 1                        if k is the last operation of i
```

### 2.4 Dispatching impact `D` = FOCUS (§3.1.3, Eqs 5–9)

The DRACO paper restates the **best-performing four-mechanism** FOCUS: `D(i,k) = π·w₁ + ξ·w₂ + ψ·w₃ + δ·w₄`, `Σwₘ = 1` (Eq 9), with `π` (SPT, Eq 5), `ξ` (starvation, Eq 6, `S = {j:Q_j=∅}`), `ψ` (slack, Eq 7), `δ` (pacing, Eq 8). There is **no β term** in the DRACO restatement. (The full FOCUS definition, including β, is reviewed in §4.)

### 2.5 Order selection (§3.1.4, Eq 10)

```
z = argmax_{i ∈ Q_k ∪ P_k}  W^R·R(i,k) + W^A·A(i,k) + W^D·D(i,k)
```

### 2.6 Experimental configuration (§4 / Table 2)

Full DRACO: `W^R = W^A = 1/4, W^D = 1/2`; `τ ∈ {3,6,7,9,12,18,42}`; `ζ_{ju} = max(1, ⌊2τ/|J|⌋)`. FOCUS sub-weights `w₁..w₄ = 1/4`. System: pure job shop, **6 centres, capacity 1**, ≈ 90% utilization.

---

## 3. DRACO — formula-by-formula correspondence

| Paper construct | Implementation site | Verdict |
|---|---|---|
| `w = Σ_j(|Q_j|+|H_j|)`, job count | `Draco._count_wip` = `Σ_s(len(s.queue) + s.count)` — `draco.py:266–273` | ✅ Deliberately a *count*, independent of the shopfloor's workload `WIPStrategy`. |
| `ρ^P` (Eq 1) | `Draco._ro_P` = `max(0, 1 − wip/(2τ))` — `draco.py:275–277` | ✅ |
| `ρ^Q` (Eq 2) | `Draco._ro_Q` = `min(1, wip/(2τ))` — `draco.py:279–281` | ✅ |
| `R` (Eq 3) | `_full_score(..., in_psp=…)` selects `ro_P`/`ro_Q` — `draco.py:329` | ✅ |
| `a_{k,u} = |H_k|+|Q_u|+|H_u|` | `_overlapping_loop_count` = `server_k.count + len(server_u.queue) + server_u.count` — `draco.py:283–291` | ✅ At decision time `|H_k| = 0` (k just freed). |
| `A` (Eq 4), last-op = 1 | `_authorization_impact` — `draco.py:299–313` | ✅ `u=None⇒1`; `a≥ε⇒0`; else `1−a/ε`. Scalar or per-pair `ζ` dict. |
| `D` = FOCUS (Eq 9) | `Focus.score` w/ default `(.25,.25,.25,.25,0)` — `focus.py:468–477` | ✅ See §5. |
| selection (Eq 10) | `Draco.decide_next_job`: `max` over `Q_k` ∪ `P_k = [j for j in psp.jobs if j.starts_at(k)]` — `draco.py:232–246` | ✅ |
| decision moment = centre frees | `shopfloor.on_processing_end(draco.decide_next_job)` — `builders.py:511`; `capacity=1` — `builders.py:480` | ✅ |

### 3.1 Force-pin / dispatch mechanism (no paper analogue)

`Draco._forced_at_server` (`draco.py:163`) pins the Eq-10 winner: `priority_policy` returns `−inf` for it (`draco.py:195–198`), guaranteeing `queue[0] = winner`; a PSP winner is additionally released (`draco.py:255–260`). The SimPy timing argument (URGENT `Initialize` before NORMAL `Release`, `draco.py:63–95`) is **implementation scaffolding** with no paper counterpart; it does not change the math and its net effect is validated behaviorally by `test_draco_psp_winner_processes_immediately` and `test_draco_winner_via_R_boost_still_processes_first`. Correctness assumes `capacity == 1`.

---

## 4. FOCUS — methodology (Omega 2023)

FOCUS is a **standalone dispatching rule** that, unlike DRACO, makes **no release decision** — it operates in an immediate-release setting (§4.2) and selects only from the queue.

### 4.1 Notation (§3, p.4)

- `i ∈ I` orders, `j ∈ J` work centres.
- `O ⊆ I` — orders arrived but not yet completed.
- `Q_j ⊆ O` — queue at `j`. **`H_j ⊆ O`** — orders in process at `j`. `W_j = Q_j ∪ H_j ⊆ O` — orders *located* at `j`.
- `P = {(i,j), …}` — pairs of order `i` (with remaining ops, so `i ∈ O`) and centres `j` executing those remaining ops.

> **Key:** the paper states `H_j ⊆ O` explicitly. **In-process orders belong to the order book**, hence to `P` and to every `max_{i∈O}` normalizer. This is the fact that adjudicates Deviation A.

### 4.2 Projected impact functions (§3.1)

```
SPT          π(i',j') = 1 − p_{i'j'} / max_{(i,j)∈P} p_ij                       (Eq 1)

WIP balance  l(j)      = Σ_{i∈W_j} p_ij                                          (Eq 2)
             l_{ij}^+  = l(j) − p_ij  if j = k_i^-   (centre i leaves)
                       = l(j) + p_ij  if j = k_i^+   (first downstream centre)
                       = l(j)          else                                      (Eq 3)
             e(i)      = − Σ_{j∈J} (l_{ij}^+/Σl_{ij}^+) · ln(l_{ij}^+/Σl_{ij}^+) (Eq 4)
                          (e_max = ln|J| perfect balance; e_min = 0)
             c(i')     = e(i') − e^-                                             (Eq 5)
             β(i')     = c(i')/max_{i∈O} c(i)  if c(i') > 0  else 0              (Eq 6)

Starvation   ξ(i',j')  = π(i',j')  if k_i^+ ∈ S  else 0,  S = {j∈J : Q_j = ∅}    (Eq 7)

Slack        s(i)      = d_i − t − Σ_{j∈R_i} p_ij                                (Eq 8)
             τ(i')     = 1 − s(i')/max_{i∈O} s(i)  if s(i') > 0  else 1          (Eq 9)

Pacing       v(i)      = s(i)/|R_i|                                              (Eq 10)
             δ(i')     = 1 − v(i')/max_{i∈O} v(i)  if v(i') > 0  else 1          (Eq 11)
```

### 4.3 Aggregate and selection (§3.2)

```
I(i,j) = π(i,j)·w₁ + β(i)·w₂ + ξ(i,j)·w₃ + τ(i)·w₄ + δ(i)·w₅                    (Eq 12)
z      = argmax_{i' ∈ Q_{j'}} I(i', j')   (instantly dispatched)                (Eq 13)
```

Note the Eq-12 weight order: **(π, β, ξ, τ, δ)**.

### 4.4 Experimental setup (§4.2)

The full FOCUS uses **all five weights = 1/5** (β active). Ablations remove one mechanism (`FOCUS - π`: `w₁ = 0`, others `1/4`; similarly `-β, -ξ, -τ, -δ`). Results (§5.2, Fig 3): **`FOCUS - β` is consistently best** — removing WIP balancing improves performance, i.e. β is counter-productive.

---

## 5. FOCUS — formula-by-formula correspondence

| Paper construct | Implementation site | Verdict |
|---|---|---|
| `O ⊇ H_j` (in-process in order book) | `build_context` scans `jobs = queueing_jobs (+ psp.jobs)`, **excludes `current_jobs`** — `focus.py:317–319` | ⚠ **Deviation A** (confirmed): `H` dropped from the order book. |
| `P` = remaining-op pairs over `O`; `π` (Eq 1) | `Focus.pi` over `ctx.max_pij`; `max_pij` = max over **all remaining ops** of scanned jobs — `focus.py:369–377`, `328–331` | ✅ formula; ⚠ population (Dev. A). `max_pij==0 ⇒ π=1` guard. |
| `l(j) = Σ_{i∈W_j} p_ij` (Eq 2) | `workloads = Σ queueing_jobs p + Σ current_jobs p` — `focus.py:306–308` | ✅ **Correctly includes in-process** (`W_j = Q_j ∪ H_j`). |
| `l_{ij}^+` perturbation (Eq 3) | `_delta_entropy`: `w'[k]=max(0, l(k)−p_ik)`, `w'[u]=l(u)+p_iu` — `focus.py:65–99` | ✅ matches Eq 3; the `max(0,·)` clamp is a documented **DRACO-only** extension (PSP candidate not yet in `W_k`), a no-op for pure-FOCUS queue candidates. |
| `e(i)` Shannon entropy (Eq 4) | `_entropy` = `−Σ(w/Σw)·ln(w/Σw)`, `0` for idle shop — `focus.py:43–62` | ✅ |
| `c(i') = e(i') − e^-` (Eq 5) | `_delta_entropy` returns `_entropy(w') − pre_entropy` — `focus.py:99` | ✅ |
| `β` (Eq 6) | `Focus.beta` = `c_i/ctx.max_positive_c if c_i>0 else 0` — `focus.py:427–466` | ✅ formula; ⚠ normalizer `max_{i∈O} c(i)` excludes in-process (Dev. A). |
| `ξ`, `S = {j:Q_j=∅}` (Eq 7) | `Focus.omega`: next server ∈ `empty_queue_servers` — `focus.py:379–391`; `S` = `len(s.queue)==0` — `focus.py:302` | ✅ keys on queue-emptiness, not idleness; last-op ⇒ 0. |
| `s`, `τ` (Eqs 8–9) | `Focus.psi`, `_slack = due − now − Σ_{unfinished} p` — `focus.py:393–406, 479–482` | ✅ formula; ⚠ `max_positive_slack` excludes in-process (Dev. A). |
| `v`, `δ` (Eqs 10–11) | `Focus.gamma`, `v = s/len(remaining)` — `focus.py:408–425` | ✅ formula; ⚠ `max_positive_pacing` excludes in-process (Dev. A). |
| `I = π·w₁ + β·w₂ + ξ·w₃ + τ·w₄ + δ·w₅` (Eq 12) | `score = w₁·pi + w₂·omega + w₃·psi + w₄·gamma + w₅·beta` — `focus.py:468–477` | ⚠ **Deviation G**: weight indices reordered vs Eq 12 (see §6.G). |
| `z = argmax_{i'∈Q_{j'}} I` (Eq 13) | `FocusPriorityRule` (negated score) applied via `Server.sort_queue` over queued requests — `focus.py:485–526`, `builders.py:104+` (`build_focus_system`, push/immediate-release, no PSP, `capacity=1`) | ✅ pure-FOCUS selects from the queue. |
| full FOCUS = all `1/5`; best = `FOCUS - β` | default `(.25,.25,.25,.25,0)` = β-off — `focus.py:250` | ✅ The default reproduces **`FOCUS - β`** (the paper's best ablation and the DRACO paper's FOCUS), **not** the nominal all-`1/5` baseline. A good default; see §6.G for the index caveat. |

---

## 6. Deviations from the papers (ranked by materiality)

### A. In-process orders excluded from the order book `O` — **confirmed against the FOCUS paper**

The Omega paper defines `H_j ⊆ O` and `W_j = Q_j ∪ H_j ⊆ O` (§3, p.4), and ranges all four FOCUS normalizers over `O` / `P` (Eqs 1, 6, 9, 11). As a set identity:

- **Paper:** `O = Q ∪ H ∪ P` (pure FOCUS: `O = Q ∪ H`, no pool)
- **Code:** `jobs = Q (∪ P)` — the in-process set `H` is dropped (`focus.py:317–319`).

The paper also **resolves the internal inconsistency** flagged in the original report: `l(j)` is defined over `W_j = Q_j ∪ H_j` (Eq 2), so the *workload* vector should include in-process orders — and the code's `workloads` correctly does (`focus.py:306–308`). But the *normalizer* scan (`max_pij`, `max_positive_slack`, `max_positive_pacing`, `max_positive_c`) is built over a different population that excludes them (`focus.py:317–339`). The paper says both must range over the same `O ⊇ H_j`. The `TODO(draco-focus)` at `focus.py:311–316` can now be **closed in favour of including in-process orders.**

**Magnitude (analytic).** Within one mechanism, the *relative* ranking of candidates is invariant to its shared normalizer (e.g. π ranks by `−p_ik` for fixed `max_pij`). But each mechanism has its *own* normalizer; changing one (e.g. `max_slack`) rescales that term alone, shifting the cross-mechanism trade-off in the weighted sum `D`/`I`, so the Eq-10/Eq-13 argmax can change. In-process orders, being partway done, tend to *raise* the slack/pacing maxima, lowering ψ/δ for everyone. Probability of a flipped decision is low but nonzero. This is the **only** deviation that changes which orders are scored.

**Caveat on the fix (not a clean one-liner).** Adding `current_jobs` to the scan carries a sub-decision the paper does not fully pin down: whether an in-process order's **current, in-progress** operation counts as a "remaining operation" in `P` and in `Σ_{j∈R_i} p_ij`. Its *downstream* ops unambiguously belong in `O`/`P`; the in-progress op is arguable. The fix should make this choice explicitly.

### G. `weights` tuple ordering differs from FOCUS Eq (12) — reproducibility hazard for ablations

Verified:

- **Paper Eq 12:** `I = π·w₁ + β·w₂ + ξ·w₃ + τ·w₄ + δ·w₅` → order `(π, β, ξ, τ, δ)`.
- **Implementation `score`:** `w₁·pi + w₂·omega + w₃·psi + w₄·gamma + w₅·beta` = `w₁·π + w₂·ξ + w₃·τ + w₄·δ + w₅·β` → order `(π, ξ, τ, δ, β)`.

The implementation follows the **DRACO paper's Eq-9** four-mechanism order (`π, ξ, τ, δ`) and appends `β` as the 5th slot. The index→mechanism→symbol map:

| Mechanism | FOCUS Eq-12 weight | Implementation weight |
|---|---|---|
| SPT `π` (`pi`) | w₁ | w₁ |
| WIP-balancing `β` (`beta`) | **w₂** | **w₅** |
| Starvation `ξ` (`omega`) | w₃ | **w₂** |
| Slack `τ` (`psi`) | w₄ | **w₃** |
| Pacing `δ` (`gamma`) | w₅ | **w₄** |

**Scope and severity.** DRACO is **unaffected**: its default is order-invariant (four equal, β = 0) and its Eq-9 order matches the implementation's `w₁..w₄`. The FOCUS default `(.25,.25,.25,.25,0)` and the paper's all-`1/5` baseline are also order-invariant. The hazard is narrow but **silent**: a user reproducing the Omega paper's per-mechanism ablations who copies the paper's "`wᵢ = 0`" index will zero the *wrong* mechanism (e.g. paper `FOCUS - ξ` is `w₃ = 0`, but `weights=(.25,.25,0,.25,.25)` zeroes `psi`/slack in the implementation, producing `FOCUS - τ`). It still sums to 1 and passes validation, so nothing errors. Medium severity — above doc nits, below Deviation A. **Resolution is documentation only** (do *not* reorder the tuple: DRACO, the default, and every caller depend on the current order).

### B. The just-completed multi-op job is absent at the DRACO decision instant *(documented; off-by-one, multi-op only)*

`decide_next_job` fires after `server.release` but before the finished job re-enters its next queue (`draco.py:85–95`). For a multi-op trigger the in-transit job is in no `Q_j`/`H_j`, whereas the paper treats completion and queue-join as simultaneous. Consequences: WIP undercounted by 1 (biases `R` toward release; pinned by `test_..._uses_uncorrected_count_wip`); `|Q_u|` one lower in `A`; the target `u` can spuriously enter the starvation set `S`, giving a bogus `ξ` bonus. Single-op (last-op) triggers are unaffected. A defensible completion-instant interpretation, off by one job.

### C. DRACO default `total_impact_weights = (1/3, 1/3, 1/3)` reproduces no Table-2 variant *(reproducibility)*

The paper's full DRACO is `(W^R, W^A, W^D) = (1/4, 1/4, 1/2)` (Table 2). Constructor (`draco.py:136`) and `build_draco_system` (`builders.py:425`) default to equal thirds — matching none of the five Table-2 configurations. Reproducing the paper requires passing `(0.25, 0.25, 0.5)`. *(Contrast §6.G: the FOCUS default is the opposite case — it does reproduce a named, best-performing paper config.)*

### D. DRACO `loop_target ζ` is not auto-derived from τ *(reproducibility)*

Full DRACO sets `ζ_{ju} = max(1, ⌊2τ/|J|⌋)`. The implementation takes `loop_target` as a free scalar/dict — correct and general, but reproducing the paper means computing `max(1, ⌊2τ/6⌋)` by hand.

### E. `starvation_avoidance` bypasses `R`/`A`/`D` on arrival *(documented liveness provision)*

`build_draco_system` wires `psp.on_arrival(starvation_avoidance)` (`builders.py:512`), releasing an arrival immediately iff its first server is idle (`starvation_avoidance.py:32–35`), bypassing Eq-10 scoring. Documented at `draco.py:97–106` as a liveness provision whose paper-faithfulness is unverified; can diverge when multiple `P_k` pool candidates exist at the idle server.

### F. A released job routing into an idle *downstream* server is auto-granted with no DRACO decision *(the only deviation with no acknowledging comment)*

`decide_next_job` fires only on completion. When a released job routes into an idle downstream `B` while a `P_B` pool candidate waits, SimPy grants `B` to the arriving job and DRACO never weighs pulling the pool candidate. A genuine "decision moment" in the paper's sense passes without scoring. Rare at ~90% utilization; unlike A/B/E it has no docstring/TODO/test acknowledging it.

### H. `spec §3.3.x` citations in `focus.py` don't match the Omega paper *(minor doc note)*

The mechanism docstrings cite `spec §3.3.1`–`§3.3.5` (`focus.py:370, 380, 394, 409, 428`), but the Omega paper's methodology is **§3.1** (Eqs 1–11) with bold paragraph labels, not numbered `§3.3.x` subsections. These likely reference an **internal design spec** (note `draco.py`'s `§3.1/§3.2` citations *did* match the DRACO paper, so "spec" ≠ "paper" here). The equation-level correspondence is correct; only the section labels don't resolve to this paper.

---

## 7. Test-suite assessment

Strong and well-targeted (95 tests pass; `draco.py` 100% line + branch from this subset).

**Strengths.** Hand-computed constants pin each term (`test_draco_full_score_matches_hand_computed_total_impact`; FOCUS `test_focus_score_equals_hand_computed_constant` and per-mechanism `pi/omega/psi/gamma/beta` tests); R-term boundaries; A-term (last-op, `a=ε`, partial, per-pair dict); the force-pin lifecycle; both PSP- and queue-winner dispatch incl. R-boost override; context memoization; count-vs-workload WIP; cold-start `starvation_avoidance`; and FOCUS context aggregates incl. β entropy/WIP-balance cases and the `compute_beta` gating.

**Gaps (none severe).**
- **Deviations A and B are encoded as expectations, not questioned** (`test_..._uses_uncorrected_count_wip`; FOCUS context tests assert in-process exclusion, e.g. `test_focus_context_max_pij_basic` treats the in-service job as excluded). No test would flag divergence from the paper's `O = Q ∪ H ∪ P` — a test asserting an *in-process* order can set `max_pij`/`max_slack` would catch the Deviation-A fix or a regression.
- **Deviation G is untested** — no test reproduces the Omega ablations or asserts the weight→mechanism mapping.
- **Deviation F is untested**; `starvation_avoidance` with multiple competing `P_k` candidates is untested.
- Per-pair `ζ` dict tested with a single pair only.
- No long-horizon integration test driving DRACO through many consecutive decisions.

---

## 8. Suggested follow-ups (ranked)

1. **Fix Deviation A (now for faithfulness, not just "consider").** Include `current_jobs`' remaining operations in the `build_context` scan (`focus.py:317–319`) so all four normalizers and the workload/entropy baseline range over one population `O ⊇ H_j`, matching the paper. Decide explicitly whether the in-progress op counts as "remaining" (downstream ops unambiguously do). Guard with a test asserting an in-process order can move `max_pij`/`max_slack`. This is the only change with a plausible effect on decisions; the `TODO(draco-focus)` can be closed accordingly.
2. **Document Deviation G.** Add the index→mechanism→paper-symbol table (§6.G) to `Focus.__init__`/the module docstring, noting the tuple follows DRACO Eq-9 order (`π, ξ, τ, δ, β`), not FOCUS Eq-12 order — and how to reproduce each Omega ablation. **Do not reorder the tuple.**
3. **Default to the paper's DRACO weights** `(0.25, 0.25, 0.5)`, or document prominently that the current default reproduces no Table-2 variant (Deviation C).
4. **Acknowledge Deviation F** with a one-line comment at the downstream auto-grant path; decide whether a routed job landing on an idle server with non-empty `P_k` should trigger a DRACO decision.
5. **Reproducibility / coverage.** Optionally derive `ζ = max(1, ⌊2τ/n_servers⌋)` in `build_draco_system` (Deviation D); add tests for multi-pair `ζ` dicts, the Omega ablations (§6.G), and `starvation_avoidance` with multiple `P_k` candidates.
6. **Fix the `spec §3.3.x` citations** (Deviation H) — point them at the actual sections (FOCUS §3.1, Eqs 1–11) or clarify they reference an internal spec.

---

## 9. Scope and limitations of this review

- **Both source papers were read.** DRACO was reviewed against IJPE 257, 108768 (§3.1, Eqs 1–10; §4/Table 2). FOCUS was reviewed against its **primary source**, Omega 114, 102726 (methodology §3, Eqs 1–13; experimental setup §4.2). The earlier scope caveat — "FOCUS source not read" — **no longer applies**; in particular, the in-process-order question (Deviation A) is now adjudicated by the Omega paper's explicit `H_j ⊆ O`.
- For the FOCUS paper, **pages 11–21 were not read** (results §5, discussion §6, conclusions §7, appendices). Those sections contain no additional impact-function or selection formulas — the complete method is §3, Eqs 1–13, plus the weight settings in §4.2 — so the formula review is complete; the unread pages cover performance evidence and sensitivity analysis only.
- Faithfulness was assessed analytically against the published equations, confirmed by reading the code and running the targeted tests. No new simulation experiments were run to quantify the numerical impact of Deviations A/B/G; the "low but nonzero" magnitude claims are analytic.
- The β mechanism is an Omega-paper construct that DRACO's restatement omits; it is **off by default** (`w₅ = 0`). Its inclusion in the implementation is for completeness, and its documented characterization as counter-productive is verified by the Omega paper (§5.2, Fig 3).

---

## 10. Bottom line

DRACO and FOCUS are both implemented correctly and with care: all governing equations of both papers match (DRACO Eqs 1–10; FOCUS Eqs 1–13), the non-hierarchical single-decision structure is preserved, and the trickiest mechanics (force-pinning the Eq-10 winner under SimPy) are handled and tested. Reviewing FOCUS against its primary source resolved the one previously-open question: **the order book `O` includes in-process orders (`H_j ⊆ O`), so excluding them from the FOCUS normalizers (Deviation A) is a confirmed — if low-impact — deviation, and the fix is now well-defined.** The second finding is purely a labeling one: the **`weights` tuple is indexed in DRACO Eq-9 order, not FOCUS Eq-12 order** (Deviation G), which silently misroutes anyone reproducing the Omega ablations and should be documented. Addressing Deviation A, aligning the DRACO default weights with Table 2, and documenting the weight indexing would make both implementations exact, paper-reproducible renderings.
