# ATC, RR, WINQ, COVERT Dispatching Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four literature dispatching rules — WINQ, ATC, COVERT, RR — to `simulatte.dispatching_rules`, following the package's existing `(job, server) -> float` conventions.

**Architecture:** Three new family modules (`work_content.py`, `tardiness_cost.py`, `composite.py`) plus `__init__.py` wiring. WINQ is a plain function; ATC/COVERT/RR are factories. Shop-derived quantities (ATC's `p̄`, RR's `u`) default to live computation with an optional fixed override. No `ShopFloor` binding is needed — every quantity is reachable from `(job, server)`. Lower returned value = served first; ATC and COVERT negate their (positive) cost index, WINQ and RR (a minimum-Z rule) do not.

**Tech Stack:** Python 3.12+, SimPy, pytest, `uv`, ruff, ty (type checker). Spec: `docs/superpowers/specs/2026-05-30-dispatching-rules-atc-rr-winq-covert-design.md`.

**Notes for the executor:**
- Run tests with `uv run pytest`. Lint/format/type-check via the pre-commit hooks that fire on `git commit` (ruff, ruff-format, ty, pytest).
- Commits are signed via the repo's existing SSH/1Password config; ensure 1Password is unlocked before committing, or the commit will fail with `1Password: failed to fill whole buffer`.
- Work on branch `feature/dispatching-rules-atc-rr-winq-covert` (already created).
- Symbols at decision time: `p = job.routing[server]`, `d = job.due_date`, `now = server.env.now`, `RPT = sum(job.routing[s] for s in job.unfinished_routing)`.

---

## Task 1: WINQ — `work_content.py`

**Files:**
- Create: `src/simulatte/dispatching_rules/work_content.py`
- Test: `tests/core/test_work_content_rules.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_work_content_rules.py`:

```python
"""Tests for work-content dispatching rules in ``simulatte.dispatching_rules.work_content``."""

from __future__ import annotations

from simulatte.dispatching_rules import work_in_next_queue
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


class TestWorkInNextQueue:
    """WINQ — Blackstone, Phillips & Hogg (1982)."""

    def test_returns_zero_on_last_operation(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s_a = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="P", servers=[s_a], processing_times=[2.0], due_date=50.0)
        assert work_in_next_queue(job, s_a) == 0.0

    def test_returns_zero_for_unrouted_server(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s_a = Server(env=env, capacity=1, shopfloor=sf)
        s_b = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="P", servers=[s_a], processing_times=[2.0], due_date=50.0)
        assert work_in_next_queue(job, s_b) == 0.0

    def test_returns_zero_when_next_queue_empty(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s_a = Server(env=env, capacity=1, shopfloor=sf)
        s_b = Server(env=env, capacity=1, shopfloor=sf)
        probe = ProductionJob(env=env, sku="P", servers=[s_a, s_b], processing_times=[2.0, 5.0], due_date=50.0)
        assert work_in_next_queue(probe, s_a) == 0.0

    def test_sums_work_in_next_machine_queue(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s_a = Server(env=env, capacity=1, shopfloor=sf)
        s_b = Server(env=env, capacity=1, shopfloor=sf)

        # Block s_b so jobs routed through it pile up in its queue.
        blocker = ProductionJob(env=env, sku="BLOCK", servers=[s_b], processing_times=[100.0], due_date=1000.0)
        sf.add(blocker)
        env.run(until=0.01)

        # Two jobs queue at s_b (work content 3 and 4).
        q1 = ProductionJob(env=env, sku="Q1", servers=[s_b], processing_times=[3.0], due_date=50.0)
        q2 = ProductionJob(env=env, sku="Q2", servers=[s_b], processing_times=[4.0], due_date=50.0)
        sf.add(q1)
        sf.add(q2)
        env.run(until=0.02)

        # Probe currently at s_a, next machine s_b: WINQ = 3 + 4 = 7 (blocker in service is excluded).
        probe = ProductionJob(env=env, sku="P", servers=[s_a, s_b], processing_times=[2.0, 5.0], due_date=50.0)
        assert work_in_next_queue(probe, s_a) == 7.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_work_content_rules.py -v`
Expected: FAIL — `ImportError: cannot import name 'work_in_next_queue'`.

- [ ] **Step 3: Implement `work_content.py`**

Create `src/simulatte/dispatching_rules/work_content.py`:

```python
"""Work-content dispatching rules.

Look-ahead rules that order a queue by the workload a job will encounter at the
next machine on its route. Lower numeric value = served first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.job import BaseJob
    from simulatte.server import Server


def _next_server_after(job: BaseJob, server: Server) -> Server | None:
    """The server after *server* in *job*'s routing, or ``None`` if last/unrouted.

    Mirrors the equivalent helper in ``focus`` but is kept local so the
    work-content family does not depend on the focus module.
    """
    servers = job.servers
    try:
        idx = servers.index(server)
    except ValueError:
        return None
    if idx + 1 >= len(servers):
        return None
    return servers[idx + 1]


def _work_in_next_queue(job: BaseJob, server: Server) -> float:
    """Total work content queued at the machine after *server* in *job*'s routing.

    Sums the imminent processing time of every job currently waiting in the
    next machine's queue (queue-only: the job in service there is excluded).
    Returns ``0.0`` when *server* is the last operation in the routing, or not
    in it (no downstream machine). Shared with the composite RR rule.
    """
    next_server = _next_server_after(job, server)
    if next_server is None:
        return 0.0
    return sum(q.routing[next_server] for q in next_server.queueing_jobs)


def work_in_next_queue(job: BaseJob, server: Server) -> float:
    """Work In Next Queue (WINQ).

    Returns the total processing time of the jobs waiting in the queue of the
    next machine on *job*'s routing. Jobs whose next machine has less queued
    work are served first, feeding soon-to-starve downstream machines and
    adding look-ahead information that SPT lacks.

    Queue-only convention: excludes the job currently in service at the next
    machine. A job on its last operation has no downstream queue and returns
    ``0.0``.

    Reference: Blackstone, Phillips & Hogg (1982), A state-of-the-art survey of
    dispatching rules for manufacturing job shop operations, IJPR 20(1), 27-45.
    https://doi.org/10.1080/00207548208947745
    """
    return _work_in_next_queue(job, server)
```

- [ ] **Step 4: Add the export to `__init__.py`** (minimal, so the tests import cleanly)

In `src/simulatte/dispatching_rules/__init__.py`, add the import (keep imports grouped/sorted) and the `__all__` entry:

```python
from .work_content import work_in_next_queue
```

Add `"work_in_next_queue",` to `__all__` (alphabetically, after `"slack_per_remaining_operation",`).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_work_content_rules.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add src/simulatte/dispatching_rules/work_content.py src/simulatte/dispatching_rules/__init__.py tests/core/test_work_content_rules.py
git commit -m "feat(dispatching_rules): add WINQ (work in next queue) rule"
```

---

## Task 2: ATC — `tardiness_cost.py` (Apparent Tardiness Cost)

**Files:**
- Create: `src/simulatte/dispatching_rules/tardiness_cost.py`
- Test: `tests/core/test_tardiness_cost_rules.py`

- [ ] **Step 1: Write the failing ATC tests**

Create `tests/core/test_tardiness_cost_rules.py`:

```python
"""Tests for tardiness-cost dispatching rules in ``simulatte.dispatching_rules.tardiness_cost``."""

from __future__ import annotations

import math

import pytest

from simulatte.dispatching_rules import apparent_tardiness_cost
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


class TestApparentTardinessCost:
    """ATC — Vepsäläinen & Morton (1987)."""

    def test_rejects_nonpositive_lookahead(self) -> None:
        with pytest.raises(ValueError, match="lookahead must be > 0"):
            apparent_tardiness_cost(lookahead=0.0)

    def test_rejects_nonpositive_avg_processing(self) -> None:
        with pytest.raises(ValueError, match="avg_processing must be > 0"):
            apparent_tardiness_cost(lookahead=2.0, avg_processing=0.0)

    def test_negated_index_with_fixed_avg_processing(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s], processing_times=[2.0], due_date=10.0)
        rule = apparent_tardiness_cost(lookahead=2.0, avg_processing=4.0)
        # slack = max(0, 10 - 2 - 0) = 8; I = (1/2)*exp(-8 / (2*4)) = 0.5*exp(-1); return -I.
        assert rule(job, s) == pytest.approx(-(0.5 * math.exp(-1.0)))

    def test_applies_weight_function(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s], processing_times=[2.0], due_date=10.0)
        rule = apparent_tardiness_cost(lookahead=2.0, avg_processing=4.0, weight=lambda _job: 3.0)
        # I = (3/2)*exp(-8/(2*4)) = 1.5*exp(-1); return -I.
        assert rule(job, s) == pytest.approx(-(1.5 * math.exp(-1.0)))

    def test_live_pbar_falls_back_to_p_when_queue_empty(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s], processing_times=[2.0], due_date=10.0)
        rule = apparent_tardiness_cost(lookahead=2.0)  # live p_bar, empty queue -> p_bar = p = 2
        # slack = 8; I = 0.5*exp(-8 / (2*2)) = 0.5*exp(-2); return -I.
        assert rule(job, s) == pytest.approx(-(0.5 * math.exp(-2.0)))

    def test_live_pbar_uses_queue_mean(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        rule = apparent_tardiness_cost(lookahead=2.0)

        blocker = ProductionJob(env=env, sku="BLOCK", servers=[s], processing_times=[100.0], due_date=1000.0)
        sf.add(blocker)
        env.run(until=0.01)

        # Two jobs queue at s with processing 4 and 6 -> live p_bar = (4+6)/2 = 5.
        j1 = ProductionJob(env=env, sku="J1", servers=[s], processing_times=[4.0], due_date=50.0, priority_policy=rule)
        j2 = ProductionJob(env=env, sku="J2", servers=[s], processing_times=[6.0], due_date=50.0, priority_policy=rule)
        sf.add(j1)
        sf.add(j2)
        env.run(until=0.02)

        # For j1: p=4, p_bar=5, slack = max(0, 50 - 4 - now); I = (1/4)*exp(-slack/(2*5)); return -I.
        slack = max(0.0, 50.0 - 4.0 - env.now)
        assert rule(j1, s) == pytest.approx(-((1 / 4.0) * math.exp(-slack / (2.0 * 5.0))))

    def test_returns_neg_inf_for_zero_processing(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s], processing_times=[0.0], due_date=10.0)
        rule = apparent_tardiness_cost(lookahead=2.0, avg_processing=4.0)
        assert rule(job, s) == float("-inf")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_tardiness_cost_rules.py -v`
Expected: FAIL — `ImportError: cannot import name 'apparent_tardiness_cost'`.

- [ ] **Step 3: Implement `tardiness_cost.py` (ATC only for now)**

Create `src/simulatte/dispatching_rules/tardiness_cost.py`:

```python
"""Tardiness-cost dispatching rules (ATC, COVERT).

Index rules that estimate a job's marginal tardiness cost per unit of imminent
processing time. Both are parameterized factories; call with a look-ahead
parameter to obtain the ``(job, server) -> float`` callable. The returned
callable yields the *negated* priority index, because simulatte serves the
lowest key first while these indices rank the most urgent job highest.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

    from simulatte.job import BaseJob
    from simulatte.server import Server


def apparent_tardiness_cost(
    lookahead: float,
    *,
    avg_processing: float | None = None,
    weight: Callable[[BaseJob], float] | None = None,
) -> Callable[[BaseJob, Server], float]:
    """Build an Apparent Tardiness Cost (ATC) dispatching rule.

    Priority index (Vepsäläinen & Morton 1987):

    ``I_j = (w_j / p_j) * exp(-max(0, d_j - p_j - t) / (k * p_bar))``

    where ``p_j`` is the imminent-operation processing time, ``d_j`` the due
    date, ``t`` the current time, ``w_j`` the job weight, ``k`` the look-ahead
    (scaling) parameter and ``p_bar`` the average processing time of the jobs
    queued at the machine. Higher ``I_j`` = more urgent; the returned callable
    yields ``-I_j`` so the lowest key is served first.

    The slack uses the imminent operation (``d_j - p_j - t``), the canonical
    single-machine Vepsäläinen-Morton form (not a remaining-work or operational
    due-date variant).

    Args:
        lookahead: Scaling parameter ``k`` (> 0). Vepsäläinen & Morton suggest
            roughly 1.5-4.5 when slack is tight.
        avg_processing: Fixed ``p_bar`` override. When ``None`` (default),
            ``p_bar`` is computed live as the mean imminent processing time of
            the jobs queued at the server, falling back to ``p_j`` when the
            queue is empty or that mean is non-positive.
        weight: Optional ``job -> weight`` callable. When ``None``, ``w_j = 1``.

    Returns:
        A ``(job, server) -> float`` callable yielding ``-I_j``.

    Raises:
        ValueError: If ``lookahead <= 0``, or ``avg_processing`` is given and
            ``<= 0``.

    Reference: Vepsäläinen & Morton (1987), Priority rules for job shops with
    weighted tardiness costs, Management Science 33(8), 1035-1047.
    https://doi.org/10.1287/mnsc.33.8.1035
    """
    if lookahead <= 0:
        msg = f"lookahead must be > 0, got {lookahead}"
        raise ValueError(msg)
    if avg_processing is not None and avg_processing <= 0:
        msg = f"avg_processing must be > 0, got {avg_processing}"
        raise ValueError(msg)

    def _atc(job: BaseJob, server: Server) -> float:
        p = job.routing[server]
        if p <= 0:
            return float("-inf")
        w = weight(job) if weight is not None else 1.0
        if avg_processing is not None:
            p_bar = avg_processing
        else:
            queued = [q.routing[server] for q in server.queueing_jobs]
            p_bar = sum(queued) / len(queued) if queued else p
            if p_bar <= 0:
                p_bar = p
        slack = max(0.0, job.due_date - p - server.env.now)
        index = (w / p) * math.exp(-slack / (lookahead * p_bar))
        return -index

    return _atc
```

- [ ] **Step 4: Add the export to `__init__.py`**

Add `from .tardiness_cost import apparent_tardiness_cost` (sorted with the other imports) and `"apparent_tardiness_cost",` to `__all__` (first lowercase entry, before `"cost_over_time"` which Task 3 adds — for now it precedes `"critical_ratio"`).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_tardiness_cost_rules.py -v`
Expected: PASS (7 passed).

- [ ] **Step 6: Commit**

```bash
git add src/simulatte/dispatching_rules/tardiness_cost.py src/simulatte/dispatching_rules/__init__.py tests/core/test_tardiness_cost_rules.py
git commit -m "feat(dispatching_rules): add ATC (apparent tardiness cost) rule"
```

---

## Task 3: COVERT — extend `tardiness_cost.py` (Cost Over Time)

**Files:**
- Modify: `src/simulatte/dispatching_rules/tardiness_cost.py`
- Modify: `tests/core/test_tardiness_cost_rules.py`

- [ ] **Step 1: Write the failing COVERT tests**

Append to `tests/core/test_tardiness_cost_rules.py` (and add `cost_over_time` to the existing import line: `from simulatte.dispatching_rules import apparent_tardiness_cost, cost_over_time`):

```python
class TestCostOverTime:
    """COVERT — Carroll (1965); job-shop form Russell, Dar-El & Taylor (1987)."""

    def test_rejects_nonpositive_lookahead(self) -> None:
        with pytest.raises(ValueError, match="lookahead must be > 0"):
            cost_over_time(lookahead=-1.0)

    def test_not_urgent_returns_zero(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        # rpt = 2; slack = max(0, 100 - 0 - 2) = 98; k*rpt = 4; 1 - 98/4 < 0 -> cost 0.
        job = ProductionJob(env=env, sku="A", servers=[s], processing_times=[2.0], due_date=100.0)
        rule = cost_over_time(lookahead=2.0)
        assert rule(job, s) == 0.0

    def test_intermediate_value(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        # rpt = 4; slack = max(0, 6 - 0 - 4) = 2; k*rpt = 4; cost = (1 - 2/4)/2 = 0.25; return -cost.
        job = ProductionJob(env=env, sku="A", servers=[s, s2], processing_times=[2.0, 2.0], due_date=6.0)
        rule = cost_over_time(lookahead=1.0)
        assert rule(job, s) == pytest.approx(-0.25)

    def test_tardy_reduces_to_wspt(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        # rpt = 5; slack = max(0, 1 - 0 - 5) = 0; cost = (1 - 0)/2 = 0.5 (WSPT-like); return -cost.
        job = ProductionJob(env=env, sku="A", servers=[s, s2], processing_times=[2.0, 3.0], due_date=1.0)
        rule = cost_over_time(lookahead=2.0)
        assert rule(job, s) == pytest.approx(-0.5)

    def test_applies_weight_function(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        # rpt = 4; slack = 2; cost = 4 * (1 - 2/4)/2 = 1.0; return -cost.
        job = ProductionJob(env=env, sku="A", servers=[s, s2], processing_times=[2.0, 2.0], due_date=6.0)
        rule = cost_over_time(lookahead=1.0, weight=lambda _job: 4.0)
        assert rule(job, s) == pytest.approx(-1.0)

    def test_returns_neg_inf_for_zero_processing(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s], processing_times=[0.0], due_date=10.0)
        rule = cost_over_time(lookahead=2.0)
        assert rule(job, s) == float("-inf")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_tardiness_cost_rules.py::TestCostOverTime -v`
Expected: FAIL — `ImportError: cannot import name 'cost_over_time'`.

- [ ] **Step 3: Implement `cost_over_time` in `tardiness_cost.py`**

Append this function to `src/simulatte/dispatching_rules/tardiness_cost.py`:

```python
def cost_over_time(
    lookahead: float,
    *,
    weight: Callable[[BaseJob], float] | None = None,
) -> Callable[[BaseJob, Server], float]:
    """Build a Cost Over Time (COVERT) dispatching rule.

    Priority index:

    ``C_j = w_j * max(0, 1 - max(0, d_j - t - RPT_j) / (k * RPT_j)) / p_j``

    where ``RPT_j`` is the remaining processing time (sum over
    ``unfinished_routing``, including the current operation), ``p_j`` the
    imminent-operation processing time, ``d_j`` the due date, ``t`` the current
    time and ``k`` the look-ahead parameter. Higher ``C_j`` = more urgent; the
    returned callable yields ``-C_j``.

    Denominator ``k * RPT_j`` is the remaining-work waiting allowance (job-shop
    convention; the single-machine variant uses ``k * p_j``). When the job is
    tardy or just-in-time (slack <= 0) the rule reduces to a WSPT-like
    ``w_j / p_j``; when slack >= ``k * RPT_j`` the cost is ``0``.

    Args:
        lookahead: Look-ahead parameter ``k`` (> 0).
        weight: Optional ``job -> weight`` callable. When ``None``, ``w_j = 1``.

    Returns:
        A ``(job, server) -> float`` callable yielding ``-C_j``.

    Raises:
        ValueError: If ``lookahead <= 0``.

    Reference: Carroll (1965), Heuristic sequencing of single and multiple
    component jobs (PhD thesis, MIT). Job-shop form: Russell, Dar-El & Taylor
    (1987), A comparative analysis of the COVERT job sequencing rule using
    various shop performance measures, IJPR 25(10), 1523-1540.
    """
    if lookahead <= 0:
        msg = f"lookahead must be > 0, got {lookahead}"
        raise ValueError(msg)

    def _covert(job: BaseJob, server: Server) -> float:
        p = job.routing[server]
        if p <= 0:
            return float("-inf")
        w = weight(job) if weight is not None else 1.0
        rpt = sum(job.routing[s] for s in job.unfinished_routing)
        if rpt <= 0:
            return 0.0
        slack = max(0.0, job.due_date - server.env.now - rpt)
        cost = w * max(0.0, 1.0 - slack / (lookahead * rpt)) / p
        return -cost

    return _covert
```

- [ ] **Step 4: Add the export to `__init__.py`**

Add `cost_over_time` to the `tardiness_cost` import line:

```python
from .tardiness_cost import apparent_tardiness_cost, cost_over_time
```

Add `"cost_over_time",` to `__all__` (alphabetically, after `"apparent_tardiness_cost",`).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_tardiness_cost_rules.py -v`
Expected: PASS (13 passed — 7 ATC + 6 COVERT).

- [ ] **Step 6: Commit**

```bash
git add src/simulatte/dispatching_rules/tardiness_cost.py src/simulatte/dispatching_rules/__init__.py tests/core/test_tardiness_cost_rules.py
git commit -m "feat(dispatching_rules): add COVERT (cost over time) rule"
```

---

## Task 4: RR — `composite.py` (Raghu & Rajendran)

**Files:**
- Create: `src/simulatte/dispatching_rules/composite.py`
- Test: `tests/core/test_composite_rules.py`

Depends on Task 1 (`_work_in_next_queue`).

- [ ] **Step 1: Write the failing RR tests**

Create `tests/core/test_composite_rules.py`:

```python
"""Tests for composite dispatching rules in ``simulatte.dispatching_rules.composite``."""

from __future__ import annotations

import math

import pytest

from simulatte.dispatching_rules import raghu_rajendran
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


class TestRaghuRajendran:
    """RR — Raghu & Rajendran (1993)."""

    def test_rejects_out_of_range_utilization(self) -> None:
        with pytest.raises(ValueError, match=r"utilization must be in \[0, 1\]"):
            raghu_rajendran(utilization=1.5)

    def test_fixed_zero_utilization(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s, s2], processing_times=[2.0, 3.0], due_date=20.0)
        rule = raghu_rajendran(utilization=0.0)
        # u=0 -> exp(0)=1; p=2; rpt=5; s_slack = 20 - 5 - 0 = 15; winq=0 (s2 queue empty).
        # Z = 1*2 + (15/5)*1*2 + 0 = 2 + 6 = 8.
        assert rule(job, s) == pytest.approx(8.0)

    def test_fixed_nonzero_utilization(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s, s2], processing_times=[2.0, 3.0], due_date=20.0)
        rule = raghu_rajendran(utilization=0.5)
        # Z = exp(0.5)*2 + (15/5)*exp(-0.5)*2 + 0 = 2*exp(0.5) + 6*exp(-0.5).
        assert rule(job, s) == pytest.approx(2.0 * math.exp(0.5) + 6.0 * math.exp(-0.5))

    def test_negative_raw_slack_lowers_index(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        # due_date=1 -> raw slack s = 1 - 5 - 0 = -4 (negative); Z = 2 + (-4/5)*2 + 0 = 0.4.
        job = ProductionJob(env=env, sku="A", servers=[s, s2], processing_times=[2.0, 3.0], due_date=1.0)
        rule = raghu_rajendran(utilization=0.0)
        assert rule(job, s) == pytest.approx(0.4)

    def test_live_utilization_defaults_to_server_rate(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s, s2], processing_times=[2.0, 3.0], due_date=20.0)
        rule = raghu_rajendran()  # live: at now=0, server.utilization_rate == 0 -> same as u=0.
        assert rule(job, s) == pytest.approx(8.0)

    def test_includes_work_in_next_queue(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)

        # Block s2 and queue a job there (work content 4) so WINQ(job via s2) = 4.
        blocker = ProductionJob(env=env, sku="BLOCK", servers=[s2], processing_times=[100.0], due_date=1000.0)
        sf.add(blocker)
        env.run(until=0.01)
        q = ProductionJob(env=env, sku="Q", servers=[s2], processing_times=[4.0], due_date=50.0)
        sf.add(q)
        env.run(until=0.02)

        job = ProductionJob(env=env, sku="A", servers=[s, s2], processing_times=[2.0, 3.0], due_date=20.0)
        rule = raghu_rajendran(utilization=0.0)
        # rpt=5; s_slack = 20 - 5 - now; winq = 4; Z = 2 + (s_slack/5)*2 + 4.
        s_slack = 20.0 - 5.0 - env.now
        assert rule(job, s) == pytest.approx(2.0 + (s_slack / 5.0) * 2.0 + 4.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_composite_rules.py -v`
Expected: FAIL — `ImportError: cannot import name 'raghu_rajendran'`.

- [ ] **Step 3: Implement `composite.py`**

Create `src/simulatte/dispatching_rules/composite.py`:

```python
"""Composite dispatching rules.

Rules that combine several scheduling signals — processing time, due-date
slack, machine utilization, downstream queue load — into one priority index.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from simulatte.dispatching_rules.work_content import _work_in_next_queue

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

    from simulatte.job import BaseJob
    from simulatte.server import Server


def raghu_rajendran(
    *,
    utilization: float | None = None,
) -> Callable[[BaseJob, Server], float]:
    """Build a Raghu & Rajendran (RR) dispatching rule.

    Priority index (Raghu & Rajendran 1993):

    ``Z_j = exp(u) * p_j + (s_j / RPT_j) * exp(-u) * p_j + WINQ_j``

    where ``p_j`` is the imminent-operation processing time, ``u`` the current
    machine's utilization, ``s_j = d_j - RPT_j - t`` the raw slack (may be
    negative), ``RPT_j`` the remaining processing time (sum over
    ``unfinished_routing``) and ``WINQ_j`` the work content in the next
    machine's queue. RR is a minimum-``Z`` rule, so the index is returned
    directly (lowest served first, no negation).

    The exponential weighting of the processing-time and due-date terms by the
    machine's own utilization is RR's defining feature: the balance differs
    machine to machine. A negative ``s_j`` (tardy job) lowers ``Z_j``, giving
    tardy jobs strong priority.

    Args:
        utilization: Fixed machine utilization ``u`` override in ``[0, 1]``.
            When ``None`` (default), ``u`` is read live from
            ``server.utilization_rate``; early in a run this is ``~= 0``, where
            ``exp(0) = 1`` degrades the rule gracefully to
            ``p_j + (s_j / RPT_j) * p_j + WINQ_j``.

    Returns:
        A ``(job, server) -> float`` callable yielding ``Z_j``.

    Raises:
        ValueError: If ``utilization`` is given and outside ``[0, 1]``.

    Reference: Raghu & Rajendran (1993), An efficient dynamic dispatching rule
    for scheduling in a job shop, IJPE 32(3), 301-313.
    https://doi.org/10.1016/0925-5273(93)90044-L
    """
    if utilization is not None and not (0.0 <= utilization <= 1.0):
        msg = f"utilization must be in [0, 1], got {utilization}"
        raise ValueError(msg)

    def _rr(job: BaseJob, server: Server) -> float:
        p = job.routing[server]
        u = utilization if utilization is not None else server.utilization_rate
        rpt = sum(job.routing[s] for s in job.unfinished_routing)
        winq = _work_in_next_queue(job, server)
        if rpt <= 0:
            return math.exp(u) * p + winq
        s = job.due_date - rpt - server.env.now
        return math.exp(u) * p + (s / rpt) * math.exp(-u) * p + winq

    return _rr
```

- [ ] **Step 4: Add the export to `__init__.py`**

Add `from .composite import raghu_rajendran` (sorted first among the relative imports) and `"raghu_rajendran",` to `__all__` (alphabetically, after `"planned_slack_time",`).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_composite_rules.py -v`
Expected: PASS (6 passed).

- [ ] **Step 6: Commit**

```bash
git add src/simulatte/dispatching_rules/composite.py src/simulatte/dispatching_rules/__init__.py tests/core/test_composite_rules.py
git commit -m "feat(dispatching_rules): add RR (Raghu-Rajendran) rule"
```

---

## Task 5: Finalize package wiring (`__init__.py` docstring + ordering)

**Files:**
- Modify: `src/simulatte/dispatching_rules/__init__.py`

The previous tasks added imports and `__all__` entries incrementally. This task verifies the final state and updates the module docstring's family list.

- [ ] **Step 1: Verify the final `__init__.py` contents**

Confirm the file matches exactly (imports sorted, `__all__` sorted with capitalized `Focus*` entries first):

```python
"""Common dispatching rules from the production-planning literature.

This package hosts dispatching rules — pure ``(job, server) -> float``
callables for queue ordering. Pass them as the ``priority_policies``
argument to ``Router``, or as the ``priority_policy`` argument to
``ProductionJob``. Lower numeric value = served first.

Rules are grouped by scheduling family:

- ``processing`` — processing-time and baseline rules.
- ``due_date`` — due-date-based rules.
- ``slack`` — slack- and ratio-based rules, including the parameterized factories.
- ``focus`` — the FOCUS self-establishing weighted-mechanism rule.
- ``work_content`` — work-content / look-ahead rules (WINQ).
- ``tardiness_cost`` — tardiness-cost rules (ATC, COVERT).
- ``composite`` — composite rules combining multiple signals (RR).
"""

from __future__ import annotations

from .composite import raghu_rajendran
from .due_date import earliest_due_date, modified_operational_due_date, operational_due_date
from .focus import Focus, FocusContext, FocusPriorityRule
from .processing import first_come_first_served, shortest_processing_time
from .slack import critical_ratio, planned_slack_time, slack_per_remaining_operation
from .tardiness_cost import apparent_tardiness_cost, cost_over_time
from .work_content import work_in_next_queue

__all__ = [
    "Focus",
    "FocusContext",
    "FocusPriorityRule",
    "apparent_tardiness_cost",
    "cost_over_time",
    "critical_ratio",
    "earliest_due_date",
    "first_come_first_served",
    "modified_operational_due_date",
    "operational_due_date",
    "planned_slack_time",
    "raghu_rajendran",
    "shortest_processing_time",
    "slack_per_remaining_operation",
    "work_in_next_queue",
]
```

- [ ] **Step 2: Run the full dispatching-rules unit suite**

Run: `uv run pytest tests/core/test_work_content_rules.py tests/core/test_tardiness_cost_rules.py tests/core/test_composite_rules.py tests/core/test_processing_rules.py tests/core/test_due_date_rules.py tests/core/test_slack_rules.py -v`
Expected: PASS (all green).

- [ ] **Step 3: Commit (only if the docstring changed since Task 4)**

```bash
git add src/simulatte/dispatching_rules/__init__.py
git commit -m "docs(dispatching_rules): list new rule families in package docstring"
```

---

## Task 6: Integration tests (dispatch order through a real queue)

**Files:**
- Modify: `tests/core/test_dispatching_rules_integration.py`

This task wires each rule in as a `priority_policy` and asserts dispatch order via `job.servers_exit_at[server]`, mirroring the existing pattern (blocker seizes the server; jobs pile up; expected order differs from arrival order). For `now`-dependent rules the dispatch reference time is `now ~= 100` (the `BLOCKER_PROCESSING_TIME`); values are chosen with drift-invariant gaps. **WINQ and RR need a multi-server fixture with differing pre-loaded downstream queues — that is the discriminating signal and the part most likely to be written so it passes trivially. Do not shortcut it.**

- [ ] **Step 1: Extend the imports**

Update the import block at the top of `tests/core/test_dispatching_rules_integration.py` to add the four new rules:

```python
from simulatte.dispatching_rules import (
    apparent_tardiness_cost,
    cost_over_time,
    critical_ratio,
    earliest_due_date,
    modified_operational_due_date,
    operational_due_date,
    planned_slack_time,
    raghu_rajendran,
    shortest_processing_time,
    slack_per_remaining_operation,
    work_in_next_queue,
)
```

- [ ] **Step 2: Write the failing integration tests**

Append these four test classes to `tests/core/test_dispatching_rules_integration.py`:

```python
class TestApparentTardinessCostIntegration:
    """ATC: jobs leave the contended queue highest-apparent-cost first."""

    def test_dispatches_highest_apparent_cost_first(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        rule = apparent_tardiness_cost(lookahead=2.0, avg_processing=5.0)

        blocker = ProductionJob(
            env=env,
            sku="BLOCK",
            servers=[server],
            processing_times=[BLOCKER_PROCESSING_TIME],
            due_date=10_000.0,
            priority_policy=rule,
        )
        sf.add(blocker)
        env.run(until=ARRIVAL_STEP)

        jobs: list[ProductionJob] = []
        # At dispatch now ~= 100, all p=5, fixed p_bar=5, k=2 (denom 10). Equal p means
        # ATC orders by slack = d - 5 - now: smaller slack -> larger index -> served first.
        #   A: 300-5-100 = 195   B: 200-5-100 = 95   C: 140-5-100 = 35
        # Index order (urgent first): C > B > A -> ["C", "B", "A"]. Gaps (60, 100) dwarf the
        # ~5-unit drift per dispatch, and the order differs from arrival ["A","B","C"].
        for sku, due_date in [("A", 300.0), ("B", 200.0), ("C", 140.0)]:
            job = ProductionJob(
                env=env,
                sku=sku,
                servers=[server],
                processing_times=[5.0],
                due_date=due_date,
                priority_policy=rule,
            )
            sf.add(job)
            env.run(until=env.now + ARRIVAL_STEP)
            jobs.append(job)

        env.run()
        assert _processed_order(jobs, server) == ["C", "B", "A"]


class TestCostOverTimeIntegration:
    """COVERT: jobs leave the contended queue highest-expected-cost first."""

    def test_dispatches_highest_cost_first(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        rule = cost_over_time(lookahead=4.0)

        blocker = ProductionJob(
            env=env,
            sku="BLOCK",
            servers=[s1],
            processing_times=[BLOCKER_PROCESSING_TIME],
            due_date=10_000.0,
            priority_policy=rule,
        )
        sf.add(blocker)
        env.run(until=ARRIVAL_STEP)

        jobs: list[ProductionJob] = []
        # Routing [s1, s2], p=[5,5] -> RPT=10, denom k*RPT=40. At dispatch now ~= 100,
        # slack = d - now - 10, cost = max(0, 1 - slack/40)/5. Smaller slack -> higher cost.
        #   A: slack 35 -> cost (1-35/40)/5 = 0.025
        #   B: slack 20 -> cost (1-20/40)/5 = 0.100
        #   C: slack  5 -> cost (1- 5/40)/5 = 0.175
        # Cost order (urgent first): C > B > A -> ["C", "B", "A"]; differs from arrival.
        for sku, due_date in [("A", 145.0), ("B", 130.0), ("C", 115.0)]:
            job = ProductionJob(
                env=env,
                sku=sku,
                servers=[s1, s2],
                processing_times=[5.0, 5.0],
                due_date=due_date,
                priority_policy=rule,
            )
            sf.add(job)
            env.run(until=env.now + ARRIVAL_STEP)
            jobs.append(job)

        env.run()
        assert _processed_order(jobs, s1) == ["C", "B", "A"]


class TestWorkInNextQueueIntegration:
    """WINQ: jobs whose next machine has the least queued work go first.

    Three candidates share the contended queue at s1 but route onward to three
    DIFFERENT next machines (s2, s3, s4), each pre-loaded with a different
    amount of queued work. WINQ depends only on that downstream load, so it is
    the sole discriminator — a rule that ignored it would fall back to arrival
    order and fail.
    """

    def test_dispatches_least_next_queue_work_first(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        s3 = Server(env=env, capacity=1, shopfloor=sf)
        s4 = Server(env=env, capacity=1, shopfloor=sf)

        # Long downstream blockers (>> 100) keep s2/s3/s4 busy past the s1 dispatch,
        # so their queued work stays intact while the candidates are scored.
        for downstream in (s2, s3, s4):
            db = ProductionJob(
                env=env,
                sku="DBLOCK",
                servers=[downstream],
                processing_times=[1000.0],
                due_date=10_000.0,
            )
            sf.add(db)
        env.run(until=ARRIVAL_STEP)

        # Pre-load each downstream queue with a distinct work content: s2->3, s3->9, s4->6.
        for downstream, work in [(s2, 3.0), (s3, 9.0), (s4, 6.0)]:
            filler = ProductionJob(
                env=env,
                sku=f"FILL{int(work)}",
                servers=[downstream],
                processing_times=[work],
                due_date=10_000.0,
            )
            sf.add(filler)
        env.run(until=env.now + ARRIVAL_STEP)

        # Block s1 so the candidates pile up behind it.
        blocker = ProductionJob(
            env=env,
            sku="BLOCK",
            servers=[s1],
            processing_times=[BLOCKER_PROCESSING_TIME],
            due_date=10_000.0,
            priority_policy=work_in_next_queue,
        )
        sf.add(blocker)
        env.run(until=env.now + ARRIVAL_STEP)

        jobs: list[ProductionJob] = []
        # Candidates arrive A, B, C; their next machines carry work 3, 9, 6.
        # WINQ ascending: A(3) < C(6) < B(9) -> ["A", "C", "B"]; differs from arrival order.
        for sku, nxt in [("A", s2), ("B", s3), ("C", s4)]:
            job = ProductionJob(
                env=env,
                sku=sku,
                servers=[s1, nxt],
                processing_times=[5.0, 5.0],
                due_date=10_000.0,
                priority_policy=work_in_next_queue,
            )
            sf.add(job)
            env.run(until=env.now + ARRIVAL_STEP)
            jobs.append(job)

        env.run()
        assert _processed_order(jobs, s1) == ["A", "C", "B"]


class TestRaghuRajendranIntegration:
    """RR: with PT and slack held equal across candidates, WINQ breaks the tie.

    Same multi-server fixture as WINQ, but scored by RR with fixed u=0 and
    identical p / due date / routing length, so exp(u)*p and (s/RPT)*exp(-u)*p
    are equal across candidates and the WINQ term alone sets the order. This
    proves RR actually incorporates the next-queue look-ahead.
    """

    def test_winq_term_breaks_ties_by_next_queue_work(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        s3 = Server(env=env, capacity=1, shopfloor=sf)
        s4 = Server(env=env, capacity=1, shopfloor=sf)
        rule = raghu_rajendran(utilization=0.0)

        for downstream in (s2, s3, s4):
            db = ProductionJob(
                env=env,
                sku="DBLOCK",
                servers=[downstream],
                processing_times=[1000.0],
                due_date=10_000.0,
            )
            sf.add(db)
        env.run(until=ARRIVAL_STEP)

        for downstream, work in [(s2, 3.0), (s3, 9.0), (s4, 6.0)]:
            filler = ProductionJob(
                env=env,
                sku=f"FILL{int(work)}",
                servers=[downstream],
                processing_times=[work],
                due_date=10_000.0,
            )
            sf.add(filler)
        env.run(until=env.now + ARRIVAL_STEP)

        blocker = ProductionJob(
            env=env,
            sku="BLOCK",
            servers=[s1],
            processing_times=[BLOCKER_PROCESSING_TIME],
            due_date=10_000.0,
            priority_policy=rule,
        )
        sf.add(blocker)
        env.run(until=env.now + ARRIVAL_STEP)

        jobs: list[ProductionJob] = []
        # Identical p=[5,5] (RPT=10) and due_date=500 across candidates -> equal exp(u)*p and
        # (s/RPT)*exp(-u)*p terms; only WINQ differs (3, 9, 6). Z order: A(3) < C(6) < B(9).
        for sku, nxt in [("A", s2), ("B", s3), ("C", s4)]:
            job = ProductionJob(
                env=env,
                sku=sku,
                servers=[s1, nxt],
                processing_times=[5.0, 5.0],
                due_date=500.0,
                priority_policy=rule,
            )
            sf.add(job)
            env.run(until=env.now + ARRIVAL_STEP)
            jobs.append(job)

        env.run()
        assert _processed_order(jobs, s1) == ["A", "C", "B"]
```

- [ ] **Step 3: Run the integration tests to verify they pass**

Run: `uv run pytest tests/core/test_dispatching_rules_integration.py -v`
Expected: PASS (existing tests + 4 new classes all green).

If WINQ/RR fail with an unexpected order, first confirm the downstream blockers are still in service at the s1 dispatch (work content intact) by checking `s2.queueing_jobs` etc. — the long (1000) downstream blockers exist precisely to prevent the downstream queues draining before `now ~= 100`.

- [ ] **Step 4: Commit**

```bash
git add tests/core/test_dispatching_rules_integration.py
git commit -m "test(dispatching_rules): integration coverage for ATC, COVERT, WINQ, RR"
```

---

## Task 7: Documentation

**Files:**
- Modify: `docs/tutorials/release-control-and-dispatching.md`
- Modify: `docs/reference.md`

- [ ] **Step 1: Locate the existing dispatching-rules catalog**

Run: `uv run grep -n "critical_ratio\|shortest_processing_time\|dispatching" docs/tutorials/release-control-and-dispatching.md docs/reference.md`

Read the surrounding sections to match the existing entry format (each rule typically has a name, a one-line description, its formula, and a literature reference).

- [ ] **Step 2: Add catalog entries for the four new rules**

In `docs/tutorials/release-control-and-dispatching.md`, extend the dispatching-rules catalog with entries that mirror the existing style. Use this content (adapt formatting to the surrounding markdown):

- **WINQ — `work_in_next_queue`**: orders by the total processing time waiting in the next machine's queue; jobs feeding a less-loaded downstream machine go first; terminal operation → 0. Ref: Blackstone, Phillips & Hogg (1982).
- **ATC — `apparent_tardiness_cost(lookahead, *, avg_processing=None, weight=None)`**: `(w/p)·exp(−max(0, d−p−t)/(k·p̄))`, higher index = served first; `p̄` live by default (override with `avg_processing`), `k` is the look-ahead. Ref: Vepsäläinen & Morton (1987).
- **COVERT — `cost_over_time(lookahead, *, weight=None)`**: `max(0, 1−max(0, slack)/(k·RPT))/p`, higher = served first; reduces to WSPT when tardy. Ref: Carroll (1965); Russell, Dar-El & Taylor (1987).
- **RR — `raghu_rajendran(*, utilization=None)`**: `exp(u)·p + (s/RPT)·exp(−u)·p + WINQ`, lower = served first; raw slack `s` may be negative; `u` live (current machine) by default, override with `utilization`. Ref: Raghu & Rajendran (1993).

Note in the prose that the factory rules (`apparent_tardiness_cost`, `cost_over_time`, `raghu_rajendran`) must be *called* to obtain the `(job, server) -> float` callable, like `planned_slack_time`.

- [ ] **Step 3: Add API-reference entries**

In `docs/reference.md`, add the four callables to the dispatching-rules reference list in the same format as the existing entries (e.g. autodoc directive or signature + summary, matching what is already there).

- [ ] **Step 4: Build the docs to verify they render**

Run: `uv run zensical build`
Expected: build completes without errors referencing the edited files.

- [ ] **Step 5: Commit**

```bash
git add docs/tutorials/release-control-and-dispatching.md docs/reference.md
git commit -m "docs: document ATC, RR, WINQ, COVERT dispatching rules"
```

---

## Task 8: Full verification

- [ ] **Step 1: Run the entire test suite**

Run: `uv run pytest`
Expected: all tests pass (no regressions in existing modules).

- [ ] **Step 2: Run lint, format, and type checks**

Run: `uv run pre-commit run --all-files`
Expected: ruff, ruff-format, and ty all pass. Fix any issues, re-run, and amend the relevant commit if needed.

- [ ] **Step 3: Confirm the public API**

Run: `uv run python -c "from simulatte.dispatching_rules import apparent_tardiness_cost, cost_over_time, raghu_rajendran, work_in_next_queue; print('ok')"`
Expected: prints `ok`.

---

## Self-Review (completed by plan author)

**Spec coverage:** All spec sections are mapped — §4 module org → Tasks 1-5; §5.1 WINQ → Task 1; §5.2 ATC → Task 2; §5.3 COVERT → Task 3; §5.4 RR → Task 4; §5.5 validation/guards → covered in the implementations and unit tests (ValueError tests, `p<=0` sentinel tests, `rpt<=0` guard, `p̄` fallback); §6 complexity → documented in code/docstrings; §7 testing → Tasks 1-4 (unit) + Task 6 (integration); §8 docs → Task 7; §9 references → in every docstring.

**Placeholder scan:** No TBD/TODO; every code and test step contains complete code; every command has an expected result.

**Type/name consistency:** `work_in_next_queue`, `_work_in_next_queue`, `_next_server_after`, `apparent_tardiness_cost`, `cost_over_time`, `raghu_rajendran` are used identically across tasks; `composite.py` imports `_work_in_next_queue` exactly as defined in Task 1; factory keyword args (`lookahead`, `avg_processing`, `weight`, `utilization`) are consistent between implementation, tests, and docs.

**Known fragility (flagged):** WINQ/RR integration relies on long (1000) downstream blockers so the pre-loaded next-queue work survives until the `now~=100` s1 dispatch; Task 6 Step 3 includes the diagnostic if order is unexpected.
