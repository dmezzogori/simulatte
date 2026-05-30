# Design: ATC, RR, WINQ, COVERT dispatching rules

**Date:** 2026-05-30
**Status:** Approved (pending spec review)
**Scope:** Add four literature dispatching rules to `simulatte.dispatching_rules`.

## 1. Goal

Add four common dispatching rules from the production-planning literature to the
`simulatte.dispatching_rules` package, following the existing conventions for
that package (pure `(job, server) -> float` callables, lower numeric value =
served first, dynamic re-evaluation through `Server.sort_queue`):

- **WINQ** — Work In Next Queue
- **ATC** — Apparent Tardiness Cost
- **COVERT** — Cost Over Time
- **RR** — Raghu & Rajendran's rule

## 2. Background: existing patterns

`simulatte.dispatching_rules` exposes rules as `(job, server) -> float`
callables. `Server.sort_queue` re-evaluates `job.priority(server)` for every
queued request before each dispatch decision and serves the **lowest** key
first, so rules may read live state: `server.env.now`, the server's own queue
(`server.queueing_jobs`), and — through `job.servers` — any other `Server`
object (each of which exposes its own `queueing_jobs`, `routing`-indexed
processing times via the queued jobs, and `utilization_rate`).

Three API shapes already exist and are reused here:

1. **Plain functions** `(job, server) -> float` — e.g. `shortest_processing_time`,
   `critical_ratio`.
2. **Scalar factories** returning a rule — e.g. `planned_slack_time(allowance)`,
   `slack_per_remaining_operation(allowance)`.
3. **Class + adapter** for shop-wide aggregates — `Focus` / `FocusPriorityRule`.

Relevant `BaseJob` API:

- `job.routing: dict[Server, float]` — processing time at each server.
- `job.servers: tuple[Server, ...]` — routing order.
- `job.due_date`, `server.env.now` — due date and current time.
- `job.unfinished_routing: tuple[Server, ...]` — servers not yet exited
  (includes the operation currently in progress). Used for remaining
  processing time (RPT), consistent with `critical_ratio`.

Relevant `Server` API:

- `server.queueing_jobs: Iterable[BaseJob]` — jobs waiting in the queue.
- `server.utilization_rate: float` — cumulative `worked_time / env.now` in
  `[0, 1]` (`0` when `env.now == 0`).

**Jobs carry no weight attribute.** Weighted ATC/COVERT therefore default to
`w = 1`, with an optional caller-supplied weight function. Adding a job-level
weight field is out of scope.

**Key finding:** none of the four rules requires a `ShopFloor` binding. Every
quantity — including RR's machine utilization and WINQ's next-queue workload —
is reachable from `(job, server)`.

## 3. Parameterization philosophy (decided)

Shop-derived quantities (ATC's average processing time `p̄`, RR's utilization
`u`) are **computed live by default, with an optional fixed override**:

- `None` (default) → compute live from shop state at decision time.
- a `float` → use the supplied constant (reproducible; matches the
  experimental "known load level" convention).

Look-ahead tuning knobs (`κ` for ATC, `k` for COVERT) are **always explicit**
required factory parameters — they have no sensible automatic default.

## 4. Module organization

Mirror the existing by-family taxonomy. Three new modules under
`src/simulatte/dispatching_rules/`:

| Module | Public callable(s) | Family |
|--------|--------------------|--------|
| `work_content.py` | `work_in_next_queue` | work-content / look-ahead |
| `tardiness_cost.py` | `apparent_tardiness_cost`, `cost_over_time` | tardiness-cost |
| `composite.py` | `raghu_rajendran` | composite |

Names use full snake_case to match `shortest_processing_time` /
`critical_ratio`. `_work_in_next_queue(job, server)` is a shared private helper
in `work_content.py`, imported by `composite.py` for RR's WINQ term.

`__init__.py` is updated to import and re-export the four public callables in
`__all__`, and its module docstring's family list is extended with the three
new families.

## 5. Formulas and conventions

Common symbols, evaluated at decision time:

- `now = server.env.now`
- `p = job.routing[server]` — imminent-operation processing time
- `d = job.due_date`
- `RPT = sum(job.routing[s] for s in job.unfinished_routing)` — remaining
  processing time (includes the current operation)

### 5.1 WINQ — `work_in_next_queue(job, server) -> float`

Plain function, no parameters. Lower = served first (**not** negated).

```
next_server = the server immediately after `server` in job.servers
              (None if `server` is the last operation or not in the routing)

work_in_next_queue =
    0.0                                              if next_server is None
    sum(q.routing[next_server] for q in next_server.queueing_jobs)  otherwise
```

Conventions (documented in the docstring):

- **Queue-only**: the work of the job currently *in service* at the next
  machine is excluded (canonical WINQ).
- **Terminal-operation convention**: a job on its last operation has no
  downstream queue → WINQ = `0.0`.

### 5.2 ATC — `apparent_tardiness_cost(lookahead, *, avg_processing=None, weight=None)`

Factory. The returned rule yields the **negated** priority index (higher
apparent tardiness cost → lower key → served first).

```
w  = weight(job) if weight is not None else 1.0
p̄ = avg_processing if avg_processing is not None
     else mean(q.routing[server] for q in server.queueing_jobs)
     with fallback to p if the queue is empty or the mean is <= 0
slack = max(0.0, d - p - now)              # imminent-operation slack
I = (w / p) * exp(-slack / (lookahead * p̄))
return -I
```

Conventions:

- **Imminent-operation slack** `d - p - now` (canonical Vepsäläinen–Morton
  single-machine form), documented as the chosen variant (not a remaining-work
  or operational-due-date variant).
- `p̄` live = average imminent processing time over the jobs currently queued
  at `server` (the candidate is itself in that queue).

### 5.3 COVERT — `cost_over_time(lookahead, *, weight=None)`

Factory. The returned rule yields the **negated** priority index.

```
w = weight(job) if weight is not None else 1.0
slack = max(0.0, d - now - RPT)
C = w * max(0.0, 1.0 - slack / (lookahead * RPT)) / p
return -C
```

Conventions:

- Denominator `lookahead * RPT` = remaining-work waiting allowance
  (documented job-shop convention; the single-machine variant uses
  `lookahead * p` and is noted in the docstring).
- When `slack <= 0` (tardy / just-in-time) the rule reduces to a WSPT-like
  `w / p`; when `slack >= lookahead * RPT` the cost is `0`.

### 5.4 RR — `raghu_rajendran(*, utilization=None)`

Factory. The returned rule yields the priority index directly — RR is a
minimum-Z rule, so it is **not** negated.

```
u = utilization if utilization is not None else server.utilization_rate
s = d - RPT - now                          # raw slack, MAY be negative
winq = _work_in_next_queue(job, server)
Z = exp(u) * p + (s / RPT) * exp(-u) * p + winq
return Z
```

Conventions:

- `u` is the **current machine's** utilization (RR's defining feature is
  machine-varying weights), taken live from `server.utilization_rate`.
  Cold-start `u ≈ 0` early in a run ⇒ `exp(0) = 1`, degrading gracefully to
  `p + (s / RPT) * p + winq`.
- `s = d - RPT - now` is the **raw slack** and may be negative; tardy jobs
  produce a smaller (more negative) middle term and are strongly prioritized.

### 5.5 Validation and defensive guards

Factory-construction validation (raises `ValueError`, mirroring
`planned_slack_time`):

- `apparent_tardiness_cost`: `lookahead > 0`; if `avg_processing` given,
  `avg_processing > 0`.
- `cost_over_time`: `lookahead > 0`.
- `raghu_rajendran`: if `utilization` given, `0 <= utilization <= 1`.

Runtime guards (documented):

- `p <= 0` (degenerate zero-processing operation): the cost rules return the
  most-urgent sentinel (`-inf`) and RR is unaffected (no division by `p`).
- `RPT <= 0`: cannot occur for a job that is queued (it always has at least
  its current operation pending), but COVERT and RR guard against it
  defensively rather than dividing by zero.
- `p̄ <= 0` in ATC: falls back to `p`.

## 6. Complexity note

ATC's live `p̄` and WINQ both scan a queue once per `priority_policy` call.
Because `Server.sort_queue` calls the policy once per queued request, a single
dispatch decision is `O(n²)` in the contended queue length `n` for these rules
(FOCUS has the same shape and mitigates it with a per-pass memo). Queue lengths
in these simulations are small, so this is acceptable; a memo can be added later
if profiling warrants it. This is recorded, not optimized preemptively.

## 7. Testing

Mirror the existing per-family unit tests plus the shared integration suite.

### 7.1 Unit tests

- `tests/core/test_work_content_rules.py` — WINQ: next-queue sum, terminal-op
  `0.0`, multi-server discrimination.
- `tests/core/test_tardiness_cost_rules.py` — ATC and COVERT: hand-computed
  negated-index values; ATC `p̄` live vs override and empty-queue fallback;
  COVERT tardy → WSPT reduction and `slack >= k·RPT` → `0`; weight function;
  `ValueError` on bad parameters; `p <= 0` sentinel.
- `tests/core/test_composite_rules.py` — RR: hand-computed `Z`; live vs fixed
  `u`; negative raw slack prioritizes tardy jobs; `ValueError` on out-of-range
  `utilization`.

Each rule's returned float is asserted against a value computed by hand in the
test, as in `test_slack_rules.py`.

### 7.2 Integration tests

Extend `tests/core/test_dispatching_rules_integration.py`: wire each rule in as
a job's `priority_policy`, seize a server with a long blocker, pile jobs up
behind it, run to completion, and assert the processing order via
`job.servers_exit_at[server]`. Each scenario is constructed so the expected
order differs from arrival order. WINQ and RR additionally need a multi-server
layout where the candidates' next-queue workloads differ, so the rule's
look-ahead term is what determines the order.

## 8. Documentation

- `docs/tutorials/release-control-and-dispatching.md` — add the four rules to
  the dispatching-rules catalog with their formulas and usage.
- `docs/reference.md` — add API-reference entries.

Match the depth of the existing entries (formula, direction, references).

## 9. References (DOI-level, for docstrings)

- **ATC** — Vepsäläinen, A. P. J. & Morton, T. E. (1987). Priority rules for
  job shops with weighted tardiness costs. *Management Science*, 33(8),
  1035–1047. https://doi.org/10.1287/mnsc.33.8.1035
- **RR** — Raghu, T. S. & Rajendran, C. (1993). An efficient dynamic
  dispatching rule for scheduling in a job shop. *International Journal of
  Production Economics*, 32(3), 301–313.
  https://doi.org/10.1016/0925-5273(93)90044-L
- **COVERT** — Carroll, D. C. (1965). *Heuristic sequencing of single and
  multiple component jobs* (PhD thesis). MIT. Job-shop form: Russell, R. S.,
  Dar-El, E. M. & Taylor, B. W. (1987). A comparative analysis of the COVERT
  job sequencing rule using various shop performance measures. *International
  Journal of Production Research*, 25(10), 1523–1540.
- **WINQ** — Blackstone, J. H., Phillips, D. T. & Hogg, G. L. (1982). A
  state-of-the-art survey of dispatching rules for manufacturing job shop
  operations. *International Journal of Production Research*, 20(1), 27–45.
  Modern usage: Holthaus, O. & Rajendran, C. (1997). Efficient dispatching
  rules for scheduling in a job shop. *International Journal of Production
  Economics*, 48(1), 87–105.

## 10. Out of scope

- A job-level weight attribute (weighted rules default to `w = 1` with an
  optional weight function).
- Per-decision memoization of the `O(n²)` live scans (recorded in §6).
- Builder/factory integration (`build_*_system`) for the new rules.
- Variant forms beyond the documented conventions (e.g. in-service-inclusive
  WINQ, operational-due-date ATC, single-machine COVERT denominator) — noted in
  docstrings but not implemented.
