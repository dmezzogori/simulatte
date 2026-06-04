# Scenario Environment/Method Decoupling — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate the shop *environment* (a reusable `Scenario` value object) from the *control method*, convert the three trigger-wired policies to self-wiring `__init__` (like `Slar`), and collapse the duplicated builder assembly so any method runs on any shop.

**Architecture:** A frozen `Scenario` dataclass owns shop type, machine count, derived arrival rate, service/due-date params, and the formerly-duplicated `ShopFloor`/`Server`/`Router` assembly (`build_floor`/`build_router`). Every `build_*_system` takes `scenario=Scenario()` and collapses to a uniform 3–5 line shape. `LumsCor`/`ConWIP`/`ContinuousRelease` gain self-wiring `__init__` matching `Slar`/`SlarLimit`/`Draco`.

**Tech Stack:** Python 3.11+ (no PEP-695 generics), SimPy, pytest + pytest-cov (99% branch gate), ruff (`select=[E,F,FA,UP]`), `ty`. Spec: `docs/superpowers/specs/2026-06-04-scenario-environment-decoupling-design.md`.

**Conventions (read once):**
- TDD: write the failing test, watch it fail, minimal code, watch it pass, commit.
- Fast iteration: run a single test file with `uv run pytest <path> -o addopts="" -q` (clears the coverage gate). Run the full gate (`uv run pytest`) only at task ends / final.
- Every test module starts with `from __future__ import annotations`.
- Tests freely access private members (ruff `SLF001` ignored under `tests/`).
- `# noqa: S311` on `random.uniform/sample/randint/choices` is inert (S not selected) but matches house style — keep it on `random.uniform` lines.

---

## File Structure

- **Create** `src/simulatte/scenario.py` — `ShopType` enum, `RoutingFactory` alias, `Scenario` dataclass (fields + presets + `build_floor`/`build_router`). One responsibility: describe and assemble a shop environment.
- **Create** `tests/core/test_scenario.py` — `Scenario` unit tests.
- **Modify** `src/simulatte/policies/lumscor.py`, `conwip.py`, `continuous_release.py`, `slar_limit.py` — self-wiring `__init__`; remove redundant `_validate_wip_strategy` guards (lumscor/continuous).
- **Modify** `tests/core/test_lumscor.py`, `test_conwip.py`, `test_continuous_release.py`, `test_slar_limit.py` — Slar-test style; delete the "requires CorrectedWIPStrategy" tests.
- **Rewrite** `src/simulatte/builders.py` — all builders take `scenario=`, become thin; delete the 3 benchmark builders.
- **Modify** `tests/core/test_builders.py`, `test_builders_new.py` — adapt to thin builders + `scenario=`; move benchmark-shop assertions onto `Scenario` presets via `build_immediate_release_system`.
- **Modify** `examples/gallery_benchmark_shops.py`, `docs/examples/benchmark-shops.md`, `docs/api/utilities.md`, `skills/simulatte-dev/references/api-reference.md` — use `build_immediate_release_system(env, scenario=Scenario.pure_flow_shop())`; `Scenario` autodoc.

---

## Task 1: `Scenario` core — `ShopType`, presets, derived arrival rate

**Files:**
- Create: `src/simulatte/scenario.py`
- Test: `tests/core/test_scenario.py`

- [ ] **Step 1: Write failing tests for shop typing, presets, and arrival-rate derivation**

```python
# tests/core/test_scenario.py
from __future__ import annotations

import pytest

from simulatte.distributions import general_flow_shop_routing, pure_flow_shop_routing, pure_job_shop_routing
from simulatte.scenario import Scenario, ShopType


def test_default_scenario_is_pure_job_shop() -> None:
    s = Scenario()
    assert s.shop_type is ShopType.PJS
    assert s.n_servers == 6
    assert s.target_utilization == 0.90


def test_presets_select_shop_type_and_routing() -> None:
    assert Scenario.pure_job_shop().shop_type is ShopType.PJS
    assert Scenario.general_flow_shop().shop_type is ShopType.GFS
    assert Scenario.pure_flow_shop(n_servers=12).shop_type is ShopType.PFS
    assert Scenario.pure_flow_shop(n_servers=12).n_servers == 12
    assert Scenario().routing_for() is not None  # callable factory selected by shop_type


def test_mean_routing_length_per_shop_type() -> None:
    assert Scenario.pure_job_shop(n_servers=6).mean_routing_length == 3.5
    assert Scenario.general_flow_shop(n_servers=6).mean_routing_length == 3.5
    assert Scenario.pure_flow_shop(n_servers=6).mean_routing_length == 6.0


def test_derived_arrival_rate_matches_literature_constants() -> None:
    # PJS/GFS: E[L]=3.5 -> mean IAT 0.648
    assert 1 / Scenario.pure_job_shop().resolved_arrival_rate() == pytest.approx(0.648, abs=1e-3)
    # PFS: E[L]=6 -> mean IAT 1.111
    assert 1 / Scenario.pure_flow_shop().resolved_arrival_rate() == pytest.approx(1.111, abs=1e-3)


def test_explicit_arrival_rate_overrides_derivation() -> None:
    assert Scenario(arrival_rate=2.0).resolved_arrival_rate() == 2.0


def test_custom_routing_factory_requires_length_or_rate() -> None:
    custom = Scenario(routing_factory=pure_job_shop_routing, target_utilization=0.9)
    with pytest.raises(ValueError, match="expected_routing_length"):
        custom.resolved_arrival_rate()
    # ok with explicit E[L]
    ok = Scenario(routing_factory=pure_job_shop_routing, expected_routing_length=3.5)
    assert ok.resolved_arrival_rate() > 0
    # ok with explicit arrival rate
    assert Scenario(routing_factory=pure_flow_shop_routing, arrival_rate=1.0).resolved_arrival_rate() == 1.0


def test_routing_for_maps_shop_type_to_factory() -> None:
    assert Scenario.pure_job_shop().routing_for() is pure_job_shop_routing
    assert Scenario.general_flow_shop().routing_for() is general_flow_shop_routing
    assert Scenario.pure_flow_shop().routing_for() is pure_flow_shop_routing
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run pytest tests/core/test_scenario.py -o addopts="" -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'simulatte.scenario'`.

- [ ] **Step 3: Create `scenario.py` with the core (no build methods yet)**

```python
# src/simulatte/scenario.py
"""Scenario: a reusable description of a shop environment and its order stream.

A Scenario captures everything about the *environment* — shop type (routing
structure), machine count, arrival process, service-time distribution, and
due-date rule — independent of the *control method* (immediate, LumsCor, DRACO,
…). Any ``build_*_system`` in :mod:`simulatte.builders` accepts a Scenario, so
methods and shops vary independently.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from simulatte.distributions import (
    arrival_rate_for_utilization,
    general_flow_shop_routing,
    pure_flow_shop_routing,
    pure_job_shop_routing,
    truncated_2erlang,
    twk_due_date,
)
from simulatte.router import Router
from simulatte.server import Server
from simulatte.shopfloor import CurrentWorkLoadCollector, ShopFloor

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Sequence

    from simulatte.environment import Environment
    from simulatte.psp import PreShopPool

# A routing factory takes the server pool and returns a per-job routing generator.
RoutingFactory = "Callable[[Sequence[Server]], Callable[[], Sequence[Server]]]"


class ShopType(Enum):
    """Standard workload-control benchmark shop types (by routing directedness)."""

    PJS = "pure_job_shop"      # random length U[1,M], undirected (random order)
    GFS = "general_flow_shop"  # random length U[1,M], directed (sorted by index)
    PFS = "pure_flow_shop"     # fixed length M, fully directed (all machines, fixed order)


_ROUTING = {
    ShopType.PJS: pure_job_shop_routing,
    ShopType.GFS: general_flow_shop_routing,
    ShopType.PFS: pure_flow_shop_routing,
}


@dataclass(frozen=True)
class Scenario:
    """Immutable description of a shop environment and its order stream."""

    shop_type: ShopType = ShopType.PJS
    n_servers: int = 6
    target_utilization: float = 0.90
    service_rate: float = 2.0
    service_max: float = 4.0
    due_date_offset_range: tuple[float, float] = (30.0, 45.0)
    twk_allowance_factor: float | None = None
    sku: str = "F1"
    routing_factory: Callable[[Sequence[Server]], Callable[[], Sequence[Server]]] | None = None
    arrival_rate: float | None = None
    expected_routing_length: float | None = None

    @classmethod
    def pure_job_shop(cls, **overrides: object) -> Scenario:
        """Pure Job Shop preset (undirected routing)."""
        return cls(shop_type=ShopType.PJS, **overrides)  # type: ignore[arg-type]

    @classmethod
    def general_flow_shop(cls, **overrides: object) -> Scenario:
        """General Flow Shop preset (directed/sorted routing)."""
        return cls(shop_type=ShopType.GFS, **overrides)  # type: ignore[arg-type]

    @classmethod
    def pure_flow_shop(cls, **overrides: object) -> Scenario:
        """Pure Flow Shop preset (all machines, fixed order)."""
        return cls(shop_type=ShopType.PFS, **overrides)  # type: ignore[arg-type]

    @property
    def mean_routing_length(self) -> float:
        """Expected number of operations per order, ``E[L]``."""
        if self.expected_routing_length is not None:
            return self.expected_routing_length
        if self.routing_factory is not None:
            msg = "Scenario with a custom routing_factory must set expected_routing_length or arrival_rate."
            raise ValueError(msg)
        if self.shop_type is ShopType.PFS:
            return float(self.n_servers)
        return (self.n_servers + 1) / 2

    def routing_for(self) -> Callable[[Sequence[Server]], Callable[[], Sequence[Server]]]:
        """The routing factory for this scenario (custom override or shop-type default)."""
        return self.routing_factory or _ROUTING[self.shop_type]

    def resolved_arrival_rate(self) -> float:
        """The exponential arrival rate (explicit override, else derived from utilization)."""
        if self.arrival_rate is not None:
            return self.arrival_rate
        return arrival_rate_for_utilization(
            self.target_utilization,
            n_servers=self.n_servers,
            mean_routing_length=self.mean_routing_length,
            mean_processing_time=2.0 / self.service_rate,
        )
```

Note: the `RoutingFactory = "..."` string alias line is illustrative; in the
real file delete it and annotate fields/returns with the full
`Callable[[Sequence[Server]], Callable[[], Sequence[Server]]]` (as written) to
keep `ty` happy under `from __future__ import annotations`.

- [ ] **Step 4: Run tests, verify they pass**

Run: `uv run pytest tests/core/test_scenario.py -o addopts="" -q`
Expected: PASS (7 tests). If `ty` later complains about `**overrides`, the `# type: ignore[arg-type]` comments cover it.

- [ ] **Step 5: Commit**

```bash
git add src/simulatte/scenario.py tests/core/test_scenario.py
git commit -m "feat(scenario): add Scenario value object (shop type, presets, derived arrival rate)"
```

---

## Task 2: `Scenario.build_floor` and `build_router`

**Files:**
- Modify: `src/simulatte/scenario.py`
- Test: `tests/core/test_scenario.py`

- [ ] **Step 1: Write failing tests for the assembly methods**

```python
# append to tests/core/test_scenario.py
import random

from simulatte.environment import Environment
from simulatte.psp import PreShopPool
from simulatte.shopfloor import ShopFloor


def test_build_floor_creates_servers() -> None:
    with Environment() as env:
        sf, servers = Scenario(n_servers=4).build_floor(env)
        assert isinstance(sf, ShopFloor)
        assert len(servers) == 4


def test_build_router_runs_pure_flow_shop_routing() -> None:
    random.seed(42)
    with Environment() as env:
        scenario = Scenario.pure_flow_shop(n_servers=6)
        sf, servers = scenario.build_floor(env)
        scenario.build_router(env, sf, servers, psp=None)
        env.run(until=200.0)
        assert len(sf.jobs_done) > 0
        for job in sf.jobs_done:
            assert list(job.servers) == list(servers)  # PFS: all servers, fixed order


def test_build_router_applies_twk_due_dates() -> None:
    random.seed(42)
    k = 8.0
    with Environment() as env:
        scenario = Scenario.pure_job_shop(twk_allowance_factor=k)
        sf, servers = scenario.build_floor(env)
        psp = PreShopPool(env=env, shopfloor=sf)
        scenario.build_router(env, sf, servers, psp=psp)
        env.run(until=50.0)
        job = next(iter(psp.jobs))
        assert job.due_date == pytest.approx(job.created_at + k * sum(job.processing_times))
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/core/test_scenario.py -o addopts="" -q`
Expected: FAIL — `AttributeError: 'Scenario' object has no attribute 'build_floor'`.

- [ ] **Step 3: Add the assembly methods to `Scenario`**

```python
# add to the Scenario class in src/simulatte/scenario.py
    def build_floor(
        self,
        env: Environment,
        *,
        collect_workload: bool = False,
        collect_time_series: bool = False,
        retain_job_history: bool = False,
    ) -> tuple[ShopFloor, tuple[Server, ...]]:
        """Create the ShopFloor and ``n_servers`` single-capacity servers."""
        shop_floor = ShopFloor(
            env=env,
            time_series_collector=CurrentWorkLoadCollector() if collect_workload else None,
        )
        servers = tuple(
            Server(
                env=env,
                capacity=1,
                shopfloor=shop_floor,
                collect_time_series=collect_time_series,
                retain_job_history=retain_job_history,
            )
            for _ in range(self.n_servers)
        )
        return shop_floor, servers

    def build_router(
        self,
        env: Environment,
        shop_floor: ShopFloor,
        servers: Sequence[Server],
        *,
        psp: PreShopPool | None,
        priority_policies: Callable[..., float] | None = None,
    ) -> Router:
        """Assemble the Router: derived arrival rate, routing factory, 2-Erlang service times, due dates."""
        rate = self.resolved_arrival_rate()
        due_date_rule = (
            {self.sku: twk_due_date(self.twk_allowance_factor)} if self.twk_allowance_factor is not None else None
        )
        factory = self.routing_for()
        return Router(
            env=env,
            shopfloor=shop_floor,
            servers=servers,
            psp=psp,
            inter_arrival_distribution=lambda: random.expovariate(rate),
            sku_distributions={self.sku: 1},
            sku_routings={self.sku: factory(servers)},
            sku_service_times={
                self.sku: {
                    server: lambda: truncated_2erlang(lam=self.service_rate, max_value=self.service_max)
                    for server in servers
                },
            },
            due_date_offset_distribution={self.sku: lambda: random.uniform(*self.due_date_offset_range)},  # noqa: S311
            due_date_rule=due_date_rule,
            priority_policies=priority_policies,
        )
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/core/test_scenario.py -o addopts="" -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Verify lint/type on the new module**

Run: `uv run ruff check src/simulatte/scenario.py && uv run ty check src`
Expected: All checks pass. (If `ty` flags `**overrides`, keep the `# type: ignore[arg-type]`.)

- [ ] **Step 6: Commit**

```bash
git add src/simulatte/scenario.py tests/core/test_scenario.py
git commit -m "feat(scenario): add build_floor/build_router assembly (the de-duplicated core)"
```

---

## Task 3: Convert `LumsCor` to self-wiring `__init__`

**Files:**
- Modify: `src/simulatte/policies/lumscor.py`
- Modify: `tests/core/test_lumscor.py`

LumsCor's new constructor self-wires everything (matching `Slar`). The
`_validate_wip_strategy` guard and its two tests are removed (unreachable once
`__init__` sets the strategy).

- [ ] **Step 1: Rewrite `LumsCor.__init__` (and drop the guard)**

Replace the imports/`__init__`/`_validate_wip_strategy` region of
`src/simulatte/policies/lumscor.py`. New top-of-module imports:

```python
from simulatte.dispatching_rules import planned_slack_time
from simulatte.policies.starvation_avoidance import starvation_avoidance
from simulatte.policies.triggers import periodic_trigger
from simulatte.shopfloor import CorrectedWIPStrategy
```

Add `Router` to the `TYPE_CHECKING` block. New `__init__` (replaces the old one;
delete `_validate_wip_strategy` and the two `self._validate_wip_strategy(...)`
call sites in `periodic_release`/`starvation_release`):

```python
    def __init__(
        self,
        *,
        shopfloor: ShopFloor,
        psp: PreShopPool,
        router: Router,
        wl_norm: float | dict[Server, float],
        check_timeout: float,
        allowance_factor: int,
    ) -> None:
        """Initialize LUMS-COR and wire it into the system.

        Self-wiring (like ``Slar``): sets ``CorrectedWIPStrategy``, the PST
        priority rule on ``router``, a periodic release trigger, a
        completion-triggered starvation release, and ``starvation_avoidance`` on
        PSP arrival.

        Args:
            shopfloor: The shopfloor; its WIP strategy is set to corrected here.
            psp: The Pre-Shop Pool to release from.
            router: The router whose ``priority_policies`` is set to PST.
            wl_norm: Per-server workload norm. A scalar is expanded to every
                shopfloor server; a dict is used verbatim.
            check_timeout: Periodic release interval.
            allowance_factor: Buffer time per server for planned release dates.
        """
        shopfloor.set_wip_strategy(CorrectedWIPStrategy())
        self.wl_norm = wl_norm if isinstance(wl_norm, dict) else dict.fromkeys(shopfloor.servers, float(wl_norm))
        self.allowance_factor = allowance_factor
        router.priority_policies = planned_slack_time(allowance=float(allowance_factor))
        psp.env.process(periodic_trigger(psp, float(check_timeout), self.periodic_release))
        shopfloor.on_processing_end(lambda job, server: self.starvation_release(job, psp))
        psp.on_arrival(starvation_avoidance)
```

Then delete `_validate_wip_strategy` and remove the
`self._validate_wip_strategy(...)` line at the start of both `periodic_release`
and `starvation_release`.

- [ ] **Step 2: Rewrite `test_lumscor.py` to the self-wiring (Slar-test) style**

Delete `test_lumscor_requires_corrected_wip_strategy` and
`test_lumscor_starvation_release_requires_corrected_wip_strategy` (the guard is
gone). Convert the remaining tests: construct with the full signature and a
`Mock()` router, and (where a PSP candidate must *not* be released on arrival)
advance the clock so the first server is busy first. Template (one converted
test shown fully; adapt the rest the same way, running the file after each):

```python
# top of tests/core/test_lumscor.py
from unittest.mock import Mock
# ... keep ProductionJob, Environment, Server, PreShopPool, ShopFloor, CorrectedWIPStrategy imports
# (drop StandardWIPStrategy and on_completion_trigger imports once no test uses them)

def _lumscor(env, sf, psp, *, wl_norm, allowance_factor=2, check_timeout=10_000.0) -> LumsCor:
    # Large check_timeout keeps the periodic trigger dormant in unit tests.
    return LumsCor(shopfloor=sf, psp=psp, router=Mock(), wl_norm=wl_norm,
                   check_timeout=check_timeout, allowance_factor=allowance_factor)


def test_lumscor_starvation_release_when_empty() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    sf.set_wip_strategy(CorrectedWIPStrategy())  # set before servers register, as before
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    _lumscor(env, sf, psp, wl_norm={server: 100.0})

    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    sf.add(job1)
    env.run(until=0.01)  # server busy -> starvation_avoidance won't grab job2 on arrival

    job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=20.0)
    psp.add(job2)
    assert job2 in psp.jobs
    env.run(until=2)
    assert job2 not in psp.jobs
```

For the pure decision-method tests (`test_lumscor_release_under_norm`,
`_respects_norm`, `_order_by_planned_release_date`): construct via `_lumscor(...)`
then call `lumscor.periodic_release(psp)` directly (note construction now sets
the strategy, so the explicit `sf.set_wip_strategy` line is optional but
harmless). The `wl_norm` may now be passed as a scalar where a single server is
used. Add one new test asserting the scalar→dict expansion:

```python
def test_lumscor_scalar_norm_expands_to_all_servers() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    lumscor = _lumscor(env, sf, psp, wl_norm=7.5)
    assert lumscor.wl_norm == {s1: 7.5, s2: 7.5}
```

- [ ] **Step 3: Run the lumscor tests**

Run: `uv run pytest tests/core/test_lumscor.py -o addopts="" -q`
Expected: PASS. Iterate test-by-test (the timing idiom mirrors `test_slar.py`).

- [ ] **Step 4: Commit**

```bash
git add src/simulatte/policies/lumscor.py tests/core/test_lumscor.py
git commit -m "refactor(lumscor): self-wiring __init__ (like Slar); scalar norm; drop redundant strategy guard"
```

---

## Task 4: Convert `ConWIP` to self-wiring `__init__`

**Files:**
- Modify: `src/simulatte/policies/conwip.py`
- Modify: `tests/core/test_conwip.py`

ConWIP needs no router/priority. New `__init__(*, shopfloor, psp, wip_cap)` wires
the completion trigger and the on-arrival release.

- [ ] **Step 1: Rewrite `ConWIP.__init__`**

Add to `conwip.py` (top): `from simulatte.policies.triggers import on_completion_trigger`, and `ShopFloor` to the `TYPE_CHECKING` block.

```python
    def __init__(self, *, shopfloor: ShopFloor, psp: PreShopPool, wip_cap: int) -> None:
        """Initialize ConWIP and wire it into the system.

        Args:
            shopfloor: The shopfloor whose completions drive release.
            psp: The Pre-Shop Pool to release from.
            wip_cap: Maximum jobs on the floor at once. Must be >= 1.

        Raises:
            ValueError: If wip_cap < 1.
        """
        if wip_cap < 1:
            msg = f"wip_cap must be >= 1, got {wip_cap}"
            raise ValueError(msg)
        self.wip_cap = wip_cap
        psp.env.process(on_completion_trigger(shopfloor, psp, self.on_completion_release))
        psp.on_arrival(self.on_arrival_release)
```

- [ ] **Step 2: Update `test_conwip.py`**

The `wip_cap < 1` tests now must pass `shopfloor`/`psp` too:

```python
def test_conwip_rejects_zero_cap() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    psp = PreShopPool(env=env, shopfloor=sf)
    with pytest.raises(ValueError, match="wip_cap must be >= 1"):
        ConWIP(shopfloor=sf, psp=psp, wip_cap=0)
```

For behavior tests, replace `conwip = ConWIP(wip_cap=2); env.process(on_completion_trigger(...))`
with `ConWIP(shopfloor=sf, psp=psp, wip_cap=2)` (construction wires both the
completion trigger and on-arrival release). Where a PSP candidate must not be
released on arrival, advance the clock so the first server is busy first (Slar
idiom). Drop the now-unused `on_completion_trigger` import.

- [ ] **Step 3: Run**

Run: `uv run pytest tests/core/test_conwip.py -o addopts="" -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/simulatte/policies/conwip.py tests/core/test_conwip.py
git commit -m "refactor(conwip): self-wiring __init__ (shopfloor, psp, wip_cap)"
```

---

## Task 5: Convert `ContinuousRelease` to self-wiring `__init__`

**Files:**
- Modify: `src/simulatte/policies/continuous_release.py`
- Modify: `tests/core/test_continuous_release.py`

- [ ] **Step 1: Rewrite `ContinuousRelease.__init__`; drop `_validate_wip_strategy`**

Add `from simulatte.policies.triggers import on_completion_trigger` and `Router`
not needed (no priority). New `__init__`:

```python
    def __init__(
        self,
        *,
        shopfloor: ShopFloor,
        psp: PreShopPool,
        wl_norm: float | dict[Server, float],
        allowance_factor: int = 2,
    ) -> None:
        """Initialize ContinuousRelease and wire it into the system.

        Args:
            shopfloor: The shopfloor; its WIP strategy is set to corrected here.
            psp: The Pre-Shop Pool to release from.
            wl_norm: Per-server workload norm (scalar expanded to all servers, or
                dict verbatim). All values must be positive and finite.
            allowance_factor: Buffer time per server for planned release dates.

        Raises:
            ValueError: If norms are empty or contain non-positive/infinite values.
        """
        shopfloor.set_wip_strategy(CorrectedWIPStrategy())
        norms = wl_norm if isinstance(wl_norm, dict) else dict.fromkeys(shopfloor.servers, float(wl_norm))
        if not norms:
            msg = "wl_norm must not be empty"
            raise ValueError(msg)
        for server, norm in norms.items():
            if norm <= 0 or not math.isfinite(norm):
                msg = f"All workload norms must be positive and finite, got {norm} for {server}"
                raise ValueError(msg)
        self.wl_norm = norms
        self.allowance_factor = allowance_factor
        psp.env.process(on_completion_trigger(shopfloor, psp, self.on_completion_release))
        psp.on_arrival(self.on_arrival_release)
```

Delete `validate_strategy`, `_validate_wip_strategy`, and the
`self._validate_wip_strategy(shopfloor)` call at the start of
`on_completion_release`/`on_arrival_release`. (Keep `_fits_norms`.)

- [ ] **Step 2: Update `test_continuous_release.py`**

Delete the "requires CorrectedWIPStrategy" / `validate_strategy` tests. Construct
`ContinuousRelease(shopfloor=sf, psp=psp, wl_norm=..., allowance_factor=2)`
(wires both triggers). Keep the empty-norm / non-positive-norm `ValueError`
tests but pass `shopfloor=sf, psp=psp` (with a dict norm so the validation path
runs). Add a scalar-expansion test mirroring Task 3 Step 2. Use the busy-server
idiom where on-arrival release must not pre-empt.

- [ ] **Step 3: Run**

Run: `uv run pytest tests/core/test_continuous_release.py -o addopts="" -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/simulatte/policies/continuous_release.py tests/core/test_continuous_release.py
git commit -m "refactor(continuous_release): self-wiring __init__; scalar norm; drop redundant strategy guard"
```

---

## Task 6: `SlarLimit` — set strategy + scalar norm

**Files:**
- Modify: `src/simulatte/policies/slar_limit.py`
- Modify: `tests/core/test_slar_limit.py`

`SlarLimit.__init__` currently *checks* the strategy (raises) and requires a dict
norm covering all servers. Change it to *set* the strategy and accept a scalar.

- [ ] **Step 1: Rewrite the validation block of `SlarLimit.__init__`**

Replace lines `slar_limit.py:93-114` (the validation + `super().__init__`). New
body (the `wl_norm` param type becomes `float | dict[Server, float]`):

```python
        shopfloor.set_wip_strategy(CorrectedWIPStrategy())
        norms = wl_norm if isinstance(wl_norm, dict) else dict.fromkeys(shopfloor.servers, float(wl_norm))
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
        self.wl_norm = norms
        super().__init__(shopfloor=shopfloor, psp=psp, router=router, allowance_factor=allowance_factor)
```

Update the `__init__` signature's `wl_norm: dict[Server, float]` →
`wl_norm: float | dict[Server, float]` and adjust the docstring `Raises:` (drop
the `TypeError`).

- [ ] **Step 2: Update `test_slar_limit.py`**

Delete any "requires CorrectedWIPStrategy raises TypeError" test (it now *sets*
the strategy). Keep the missing-norm / non-positive ValueError tests but, where
they relied on a pre-set strategy, drop that setup. Add a scalar-norm test.

- [ ] **Step 3: Run**

Run: `uv run pytest tests/core/test_slar_limit.py -o addopts="" -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/simulatte/policies/slar_limit.py tests/core/test_slar_limit.py
git commit -m "refactor(slar_limit): set CorrectedWIPStrategy in __init__; accept scalar norm"
```

---

## Task 7: Rewrite all builders to use `Scenario`

**Files:**
- Modify: `src/simulatte/builders.py`
- Modify: `tests/core/test_builders.py`, `tests/core/test_builders_new.py`

Every builder takes `scenario: Scenario = Scenario()` and delegates assembly. Pull
builders call the now-self-wiring policy; push builders pass `priority_policies`
to `build_router`. Drop the old per-builder env kwargs (`arrival_rate`,
`service_rate`, `n_servers`, due-date range) — they live on `Scenario` now.

- [ ] **Step 1: Replace the imports + all builder bodies in `builders.py`**

New imports (drop `pure_job_shop_routing`, `truncated_2erlang`, trigger
functions, `planned_slack_time`, `starvation_avoidance`, `CorrectedWIPStrategy`,
`PreShopPool` only if unused — keep `PreShopPool`; keep `Focus`/`FocusPriorityRule`):

```python
from simulatte.dispatching_rules import Focus, FocusPriorityRule
from simulatte.environment import Environment
from simulatte.policies.continuous_release import ContinuousRelease
from simulatte.policies.conwip import ConWIP
from simulatte.policies.draco import Draco
from simulatte.policies.lumscor import LumsCor
from simulatte.policies.slar import Slar
from simulatte.policies.slar_limit import SlarLimit
from simulatte.psp import PreShopPool
from simulatte.scenario import Scenario
```

Rewrite each builder to the uniform shape. Examples (write all 9; `**` marks the
only line that differs between pull builders):

```python
def build_immediate_release_system(
    env: Environment,
    *,
    scenario: Scenario = Scenario(),
    priority_policies: Callable[..., float] | None = None,
    collect_workload: bool = False,
    collect_time_series: bool = False,
    retain_job_history: bool = False,
) -> PushSystem:
    sf, servers = scenario.build_floor(
        env, collect_workload=collect_workload,
        collect_time_series=collect_time_series, retain_job_history=retain_job_history,
    )
    router = scenario.build_router(env, sf, servers, psp=None, priority_policies=priority_policies)
    return None, servers, sf, router


def build_focus_system(
    env: Environment,
    *,
    scenario: Scenario = Scenario(),
    focus_weights: tuple[float, float, float, float, float] = (0.25, 0.25, 0.25, 0.25, 0.0),
    collect_workload: bool = False,
) -> PushSystem:
    sf, servers = scenario.build_floor(env, collect_workload=collect_workload)
    priority = FocusPriorityRule(Focus(weights=focus_weights), sf)
    router = scenario.build_router(env, sf, servers, psp=None, priority_policies=priority)
    return None, servers, sf, router


def build_lumscor_system(
    env: Environment,
    *,
    scenario: Scenario = Scenario(),
    check_timeout: float,
    wl_norm_level: float,
    allowance_factor: int,
    collect_workload: bool = False,
) -> PullSystem:
    sf, servers = scenario.build_floor(env, collect_workload=collect_workload)
    psp = PreShopPool(env=env, shopfloor=sf)
    router = scenario.build_router(env, sf, servers, psp=psp)
    LumsCor(shopfloor=sf, psp=psp, router=router, wl_norm=wl_norm_level,
            check_timeout=check_timeout, allowance_factor=allowance_factor)
    return psp, servers, sf, router


def build_slar_system(
    env: Environment, allowance_factor: float, *, scenario: Scenario = Scenario(),
    collect_workload: bool = False,
) -> PullSystem:
    sf, servers = scenario.build_floor(env, collect_workload=collect_workload)
    psp = PreShopPool(env=env, shopfloor=sf)
    router = scenario.build_router(env, sf, servers, psp=psp)
    Slar(shopfloor=sf, psp=psp, router=router, allowance_factor=allowance_factor)
    return psp, servers, sf, router


def build_slar_limit_system(
    env: Environment, allowance_factor: float, *, wl_norm_level: float,
    scenario: Scenario = Scenario(), collect_workload: bool = False,
) -> PullSystem:
    sf, servers = scenario.build_floor(env, collect_workload=collect_workload)
    psp = PreShopPool(env=env, shopfloor=sf)
    router = scenario.build_router(env, sf, servers, psp=psp)
    SlarLimit(shopfloor=sf, psp=psp, router=router, wl_norm=wl_norm_level, allowance_factor=allowance_factor)
    return psp, servers, sf, router


def build_draco_system(
    env: Environment, *, wip_target: int, loop_target: int,
    focus_weights: tuple[float, float, float, float, float] = (0.25, 0.25, 0.25, 0.25, 0.0),
    total_impact_weights: tuple[float, float, float] = (0.25, 0.25, 0.5),
    scenario: Scenario = Scenario(), collect_workload: bool = False,
) -> PullSystem:
    sf, servers = scenario.build_floor(env, collect_workload=collect_workload)
    psp = PreShopPool(env=env, shopfloor=sf)
    router = scenario.build_router(env, sf, servers, psp=psp)
    Draco(shopfloor=sf, router=router, psp=psp, focus_weights=focus_weights,
          total_impact_weights=total_impact_weights, wip_target=wip_target, loop_target=loop_target)
    return psp, servers, sf, router


def build_conwip_system(
    env: Environment, *, wip_cap: int, scenario: Scenario = Scenario(), collect_workload: bool = False,
) -> PullSystem:
    sf, servers = scenario.build_floor(env, collect_workload=collect_workload)
    psp = PreShopPool(env=env, shopfloor=sf)
    router = scenario.build_router(env, sf, servers, psp=psp)
    ConWIP(shopfloor=sf, psp=psp, wip_cap=wip_cap)
    return psp, servers, sf, router


def build_continuous_release_system(
    env: Environment, *, wl_norm_level: float, allowance_factor: int = 2,
    scenario: Scenario = Scenario(), collect_workload: bool = False,
) -> PullSystem:
    sf, servers = scenario.build_floor(env, collect_workload=collect_workload)
    psp = PreShopPool(env=env, shopfloor=sf)
    router = scenario.build_router(env, sf, servers, psp=psp)
    ContinuousRelease(shopfloor=sf, psp=psp, wl_norm=wl_norm_level, allowance_factor=allowance_factor)
    return psp, servers, sf, router


def build_starvation_avoidance_system(
    env: Environment, *, scenario: Scenario = Scenario(), collect_workload: bool = False,
) -> PullSystem:
    sf, servers = scenario.build_floor(env, collect_workload=collect_workload)
    psp = PreShopPool(env=env, shopfloor=sf)
    router = scenario.build_router(env, sf, servers, psp=psp)

    def _release_idle_first_server(_triggering_job: ProductionJob, _server: Server) -> None:
        for job in list(psp.jobs):
            if job.servers[0].is_idle:
                psp.release(job)

    psp.on_arrival(starvation_avoidance)
    shop_floor_on_end = sf.on_processing_end
    shop_floor_on_end(_release_idle_first_server)
    return psp, servers, sf, router
```

Note: `build_starvation_avoidance_system` still needs `starvation_avoidance` and
`ProductionJob`/`Server` (TYPE_CHECKING) imports — keep those. Keep
`Callable`, `ProductionJob`, `Server`, `PullSystem`, `PushSystem` in the
`TYPE_CHECKING` block; add nothing else. **Delete** the three benchmark builders
and `_build_benchmark_shop` (handled in Task 8) — or leave for Task 8 and just
do the rewrites here; either order works, but do not leave dangling imports.

- [ ] **Step 2: Update builder tests**

`test_builders.py` (attribute-style) and `test_builders_new.py` (run-style):
replace any `arrival_rate=`/`n_servers=`/`service_rate=` builder kwargs with a
`scenario=Scenario(...)` argument. Most calls use defaults and need no change.
Add a cross-shop smoke test:

```python
def test_lumscor_runs_on_general_flow_shop() -> None:
    random.seed(42)
    with Environment() as env:
        psp, servers, sf, router = build_lumscor_system(
            env, scenario=Scenario.general_flow_shop(),
            check_timeout=10.0, wl_norm_level=6.0, allowance_factor=2,
        )
        env.run(until=1000.0)
    assert len(sf.jobs_done) > 0
    for job in sf.jobs_done:  # GFS: directed routings
        idx = [servers.index(s) for s in job.servers]
        assert idx == sorted(idx)
```

- [ ] **Step 3: Run the builder + policy suites**

Run: `uv run pytest tests/core/test_builders.py tests/core/test_builders_new.py tests/core/test_lumscor.py tests/core/test_conwip.py tests/core/test_continuous_release.py tests/core/test_slar.py tests/core/test_slar_limit.py tests/core/test_draco.py tests/core/test_focus.py -o addopts="" -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/simulatte/builders.py tests/core/test_builders.py tests/core/test_builders_new.py
git commit -m "refactor(builders): thin uniform builders driven by Scenario + self-wiring policies"
```

---

## Task 8: Fold the benchmark builders into `Scenario` presets

**Files:**
- Modify: `src/simulatte/builders.py` (delete `build_pure_job_shop_system`, `build_general_flow_shop_system`, `build_pure_flow_shop_system`, `_build_benchmark_shop` if not already removed in Task 7)
- Modify: `examples/gallery_benchmark_shops.py`, `docs/examples/benchmark-shops.md`
- Modify: `tests/core/test_builders_new.py`, `docs/api/utilities.md`, `skills/simulatte-dev/references/api-reference.md`

- [ ] **Step 1: Remove the three benchmark builders + helper** from `builders.py` (if still present).

- [ ] **Step 2: Point the gallery example at `build_immediate_release_system` + presets**

In `examples/gallery_benchmark_shops.py`, change imports and the `SYSTEMS` dict:

```python
from simulatte.builders import build_immediate_release_system
from simulatte.scenario import Scenario

SYSTEMS = {
    "PureJobShop": lambda env: build_immediate_release_system(env, scenario=Scenario.pure_job_shop()),
    "GeneralFlowShop": lambda env: build_immediate_release_system(env, scenario=Scenario.general_flow_shop()),
    "PureFlowShop": lambda env: build_immediate_release_system(env, scenario=Scenario.pure_flow_shop()),
}
```

The derived arrival rates are unchanged from the old benchmark builders (they
already derived from `target_utilization`), so the printed output is identical.

- [ ] **Step 3: Run the gallery example and confirm output unchanged**

Run: `uv run python examples/gallery_benchmark_shops.py`
Expected: same three rows as before (PureJobShop ~3026, GeneralFlowShop ~3036, PureFlowShop ~1752).

- [ ] **Step 4: Sync the embedded `{ .run }` block in the docs page**

Update `docs/examples/benchmark-shops.md` so its `python { .run }` block is
byte-for-byte the new `gallery_benchmark_shops.py` (the
`test_run_blocks_match_example_files` test enforces this), and update the prose
to mention the `scenario=` parameter.

- [ ] **Step 5: Update the benchmark-builder tests**

In `test_builders_new.py`, replace the three `build_*_shop_system` tests with
equivalents calling `build_immediate_release_system(env, scenario=Scenario.<preset>())`
(same assertions: PJS subset/no-reentry, GFS directed, PFS all-in-order, TWK via
`scenario=Scenario.pure_job_shop(twk_allowance_factor=8.74)`).

- [ ] **Step 6: Update API docs + skill reference**

In `docs/api/utilities.md`: remove the three `build_*_shop_system` autodoc stubs;
add a `::: simulatte.scenario.Scenario` stub (and `ShopType`). In
`skills/simulatte-dev/references/api-reference.md`: replace the benchmark-builder
subsection with a short `Scenario` description (`build_immediate_release_system(env, scenario=Scenario.pure_flow_shop())`).

- [ ] **Step 7: Run the docs + gallery tests**

Run: `uv run pytest tests/test_docs_run_blocks.py tests/core/test_gallery_examples.py tests/core/test_builders_new.py -o addopts="" -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(builders): fold benchmark shop builders into Scenario presets; update example/docs"
```

---

## Task 9: Full verification + regenerate pinned gallery outputs

**Files:**
- Modify: gallery docs `## Output` blocks if their numbers drifted (non-asserted).

- [ ] **Step 1: Regenerate any pinned outputs that drifted**

The policy builders now derive the arrival rate (1.542857) instead of `1/0.648`
(1.543210). Re-run the affected galleries and paste fresh numbers into their
`## Output` blocks (these are not test-asserted, but keep them accurate):

```bash
uv run python examples/gallery_release_workload.py
uv run python examples/gallery_release_wip.py
uv run python examples/gallery_release_triggers.py
```

Update the `## Output` text in `docs/examples/release-workload.md`,
`release-wip.md`, `release-triggers.md` to match.

- [ ] **Step 2: Full gate — tests + coverage**

Run: `uv run pytest`
Expected: PASS; **coverage ≥ 99%**. If a new branch is uncovered (e.g. the
`mean_routing_length` ValueError, the scalar-vs-dict norm branches, the
`arrival_rate` override), add a targeted test for it.

- [ ] **Step 3: Lint + type**

Run: `uv run ruff check src tests examples && uv run ruff format --check src tests examples && uv run ty check src`
Expected: all pass.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "docs: regenerate pinned gallery outputs after arrival-rate derivation"
```

---

## Self-Review (completed by author)

**Spec coverage:** §4.1 Scenario+presets+derivation → Task 1; build_floor/router → Task 2; §4.2 self-wiring LumsCor/ConWIP/ContinuousRelease + SlarLimit + guard removal → Tasks 3–6; §4.3 thin builders + `scenario=` → Task 7; §4.4 fold benchmark builders → Task 8; §6 arrival-rate refinement / regenerate outputs → Task 9. All covered.

**Type consistency:** Method names are stable across tasks — `Scenario.build_floor`, `build_router`, `routing_for`, `resolved_arrival_rate`, `mean_routing_length`; policy `__init__` keyword params (`shopfloor`, `psp`, `router`, `wl_norm`, `check_timeout`, `allowance_factor`, `wip_cap`) match their builder call sites in Task 7.

**Known follow-ups for the executor:** (a) confirm `ty` accepts `**overrides` (the `# type: ignore[arg-type]` is in place); (b) the policy-test timing rewrites are TDD-against-the-runner using the `test_slar.py` busy-server idiom — adapt each remaining test until green rather than assuming the pre-written assertion; (c) verify setting `CorrectedWIPStrategy` inside `__init__` (after servers exist) reproduces WIP values — the converted lumscor/continuous/slar_limit tests are the guard.
