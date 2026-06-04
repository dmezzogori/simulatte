# Design: `Scenario` — decoupling the shop environment from the control method

- **Status:** Proposed (2026-06-04)
- **Branch:** `feature/benchmark-shop-environments`
- **Related:** GitHub issue #17 (benchmark shop environments)

## 1. Problem

The `build_*_system` functions in `src/simulatte/builders.py` each fuse two
orthogonal concerns into one function:

1. **The environment** — number of servers, routing structure, arrival process,
   service-time distribution, due-date rule, SKU mix.
2. **The control method** — push vs pull, WIP strategy, priority rule, release
   triggers, the policy object.

Two consequences follow:

- **Inflexibility.** Every builder hardcodes `pure_job_shop_routing`. A user who
  wants to run LumsCor, DRACO, etc. against a flow shop has to copy the builder
  source and re-implement it. The shop type and the control method cannot be
  varied independently.
- **Duplication.** The `ShopFloor` + servers + `Router` construction block is
  copy-pasted ~12 times (9 policy builders + 3 benchmark-shop builders), with
  only small variations. The arrival rate is hardcoded `1 / 0.648` in the policy
  builders but *derived* from a target utilization in the new benchmark builders
  — an inconsistency.

## 2. Goals

- Make the shop **environment** a first-class, reusable value object that any
  builder accepts, so `build_lumscor_system(env, scenario=Scenario.pure_flow_shop())`
  works without touching builder internals.
- Eliminate the duplicated `ShopFloor`/`Server`/`Router` assembly — it should
  exist exactly once.
- Make the **policy family consistent**: every policy self-wires in `__init__`
  (the existing `Slar`/`SlarLimit`/`Draco` model), so each builder is a uniform,
  short composition with no chance of mis-wiring.
- Reconcile the `arrival_rate` vs `target_utilization` split onto one mechanism.

## 3. Non-goals

- **No backward compatibility.** Per the project's API-stability policy, all APIs
  are unstable; we will change builder and policy signatures freely.
- **No two-axis `build_system(shop, policy)` entry point** (rejected: a uniform
  `Policy` protocol collides with the domain meaning of "policy", and the
  per-method builders are preferred as ergonomic entry points).
- **No multi-SKU mix** in `Scenario` for now (single configurable SKU; designed
  so a future `sku_distributions` extension is additive). YAGNI.

## 4. Design

### 4.1 `ShopType` enum + `Scenario` value object

New module `src/simulatte/scenario.py`.

```python
class ShopType(Enum):
    PJS = "pure_job_shop"       # random length U[1,M], undirected (random order)
    GFS = "general_flow_shop"   # random length U[1,M], directed (sorted)
    PFS = "pure_flow_shop"      # fixed length M, fully directed (all machines, fixed order)


@dataclass(frozen=True)
class Scenario:
    shop_type: ShopType = ShopType.PJS
    n_servers: int = 6
    target_utilization: float = 0.90
    service_rate: float = 2.0
    service_max: float = 4.0
    due_date_offset_range: tuple[float, float] = (30.0, 45.0)
    twk_allowance_factor: float | None = None
    sku: str = "F1"
    # Escape hatches for arbitrary shops:
    routing_factory: RoutingFactory | None = None      # custom routing generator
    arrival_rate: float | None = None                  # override derivation outright
    expected_routing_length: float | None = None       # E[L] for a custom routing_factory
```

**Derived arrival rate.** When `arrival_rate` is `None`, it is derived via
`arrival_rate_for_utilization(target_utilization, n_servers=n_servers,
mean_routing_length=E[L], mean_processing_time=2/service_rate)`, where `E[L]` is:

- `expected_routing_length` if explicitly provided (required for a custom
  `routing_factory`); else
- `n_servers` for `PFS`; else `(n_servers + 1) / 2` for `PJS`/`GFS`.

If `arrival_rate` is provided it is used verbatim (full control). If a custom
`routing_factory` is set with neither `arrival_rate` nor
`expected_routing_length`, construction raises a clear `ValueError`.

**Routing factory.** `routing_factory or _ROUTING[shop_type]`, where
`_ROUTING = {PJS: pure_job_shop_routing, GFS: general_flow_shop_routing,
PFS: pure_flow_shop_routing}` (the factories already in `distributions.py`).

**Named presets** (classmethods, thin): `Scenario.pure_job_shop(**overrides)`,
`Scenario.general_flow_shop(**overrides)`, `Scenario.pure_flow_shop(**overrides)`.

**The two assembly methods** (the de-duplicated core):

```python
def build_floor(self, env, *, collect_workload=False,
                collect_time_series=False, retain_job_history=False):
    """Return (ShopFloor, servers). Observability flags passed through by builders."""

def build_router(self, env, shop_floor, servers, *, psp, priority_policies=None):
    """Assemble the Router: derived arrival dist, routing factory, 2-Erlang service
    times, uniform-or-TWK due dates. The single home of the formerly-duplicated block."""
```

Note: `build_floor` does **not** set a WIP strategy — that is a policy concern
(see 4.2). It creates the floor and `n_servers` single-capacity servers.

### 4.2 Self-wiring policy convention

Three policies already self-wire in `__init__` (the template):

- `Slar(*, shopfloor, psp, router, allowance_factor)`
- `SlarLimit(*, shopfloor, psp, router, wl_norm, allowance_factor)`
- `Draco(*, shopfloor, router, psp, ...)`

Convert the three trigger-wired policies to the same shape. Self-wiring
**relocates the exact wiring lines** from the builder into `__init__` — same
mechanisms, so timing semantics are preserved.

**`LumsCor`** — new `__init__(*, shopfloor, psp, router, wl_norm, check_timeout,
allowance_factor)`:
- `shopfloor.set_wip_strategy(CorrectedWIPStrategy())`
- `self.wl_norm = wl_norm if dict else dict.fromkeys(shopfloor.servers, float(wl_norm))`
- `router.priority_policies = planned_slack_time(allowance=allowance_factor)`
- `psp.env.process(periodic_trigger(psp, check_timeout, self.periodic_release))`
- `shopfloor.on_processing_end(lambda job, server: self.starvation_release(job, psp))`
- `psp.on_arrival(starvation_avoidance)`

**`ConWIP`** — new `__init__(*, shopfloor, psp, wip_cap)` (no router; no priority):
- `psp.env.process(on_completion_trigger(shopfloor, psp, self.on_completion_release))`
- `psp.on_arrival(self.on_arrival_release)`

**`ContinuousRelease`** — new `__init__(*, shopfloor, psp, wl_norm,
allowance_factor=2)`:
- `shopfloor.set_wip_strategy(CorrectedWIPStrategy())`
- `self.wl_norm = ...` (scalar→dict from `shopfloor.servers`, or dict verbatim)
- `psp.env.process(on_completion_trigger(shopfloor, psp, self.on_completion_release))`
- `psp.on_arrival(self.on_arrival_release)`

**Scalar-or-dict norms.** `wl_norm` accepts `float | dict[Server, float]`. A
scalar is expanded to `dict.fromkeys(shopfloor.servers, float(level))` inside
`__init__`, so builders pass a level, not a dict. (`SlarLimit` gains the same
convenience.)

**WIP strategy: set, not check.** `LumsCor`/`ContinuousRelease` *set*
`CorrectedWIPStrategy` in `__init__`. `SlarLimit` currently *checks* (raises if
not set); it changes to *set* it too, for "impossible to mis-wire" consistency.
The now-redundant `_validate_wip_strategy` guards (and the tests that assert they
raise) are **removed** — once `__init__` guarantees the strategy they are
unreachable dead code, and the 99% branch-coverage gate forbids dead branches.

**Test consequence.** Self-wiring couples policy construction to a full
`(shopfloor, psp, router)` and activates `on_arrival(starvation_avoidance)` at
construction. The policy unit tests (`test_lumscor.py`, `test_conwip.py`,
`test_continuous_release.py`) are therefore rewritten to the existing `Slar`-test
style: construct the policy with `router=Mock()` where the router is incidental,
and advance the clock so the first server is busy before adding a PSP candidate
(so `starvation_avoidance` does not pre-empt the branch under test). The
"requires CorrectedWIPStrategy" tests are deleted.

The composable trigger functions (`periodic_trigger`, `on_completion_trigger`,
`on_arrival_trigger`) stay public and unchanged — policies use them internally,
and custom systems (e.g. `gallery_release_triggers.py`) still use them directly.

### 4.3 Builders after the refactor

Every builder takes `scenario: Scenario = Scenario()` (default = PJS at ρ=0.90, the
current default *configuration* — but see §6 on the small arrival-rate
refinement that follows from deriving the rate instead of hardcoding `1/0.648`).
Pull builders collapse to one uniform shape:

```python
def build_lumscor_system(env, *, scenario=Scenario(), check_timeout, wl_norm_level,
                         allowance_factor, collect_workload=False):
    sf, servers = scenario.build_floor(env, collect_workload=collect_workload)
    psp = PreShopPool(env=env, shopfloor=sf)
    router = scenario.build_router(env, sf, servers, psp=psp)
    LumsCor(shopfloor=sf, psp=psp, router=router, wl_norm=wl_norm_level,
            check_timeout=check_timeout, allowance_factor=allowance_factor)
    return psp, servers, sf, router
```

Push builders are three lines:

```python
def build_focus_system(env, *, scenario=Scenario(), focus_weights=(...)):
    sf, servers = scenario.build_floor(env)
    router = scenario.build_router(env, sf, servers, psp=None,
                               priority_policies=FocusPriorityRule(Focus(focus_weights), sf))
    return None, servers, sf, router
```

Method-specific knobs (`check_timeout`, `wl_norm_level`, `wip_cap`,
`focus_weights`, …) stay on their builder. Environment knobs (`n_servers`,
`service_rate`, due dates, utilization, shop type) move onto `Scenario`.

### 4.4 Fate of the three benchmark-shop builders

`build_pure_job_shop_system` / `build_general_flow_shop_system` /
`build_pure_flow_shop_system` become exactly
`build_immediate_release_system(env, scenario=Scenario.<preset>())`. They are
**removed**; the named *environments* now live on `Scenario` presets. The
`gallery_benchmark_shops.py` example and its docs page are updated to call
`build_immediate_release_system(env, scenario=Scenario.pure_flow_shop())` etc. (a
nicer illustration of the `scenario=` parameter). `twk_allowance_factor` and
`target_utilization` live on `Scenario`, so the TWK and utilization features are
preserved.

### 4.5 Module / naming

- `Scenario`, `ShopType`, and `RoutingFactory` (type alias) live in
  `src/simulatte/scenario.py`.
- Routing factories and `arrival_rate_for_utilization` / `twk_due_date` stay in
  `distributions.py`.

## 5. Testing strategy

- **Behavior preservation:** the existing builder/policy tests (e.g.
  `test_lumscor.py`, `test_conwip.py`, `test_continuous_release.py`,
  `test_builders*.py`, galleries) must stay green. Their assertions are robust
  to the §6 arrival-rate refinement (counts `> 0`, WIP caps, label presence,
  rows-differ — not pinned numbers), so they are the primary guard that
  self-wiring preserved the release *mechanism* and timing. Any pinned `## Output`
  numbers in gallery docs are regenerated (not test-asserted).
- **Policy `__init__` tests:** updated to the new self-wiring signatures; assert
  the wiring happened (e.g. `router.priority_policies` set; WIP strategy is
  `CorrectedWIPStrategy`; a periodic/completion trigger fires).
- **New `Scenario` tests** (TDD): preset routing structure (PJS/GFS/PFS),
  derived arrival rate per shop type (0.648 vs 1.111), `arrival_rate` /
  `expected_routing_length` overrides, custom `routing_factory`, the
  custom-without-E[L] `ValueError`, scalar-vs-dict norm expansion.
- **Cross-product smoke test:** each policy builder runs to completion on a
  non-default shop (e.g. `build_lumscor_system(env, scenario=Scenario.general_flow_shop())`).
- 99% branch-coverage gate, ruff, ty — all must stay green.

## 6. Risks & verification points

- **Arrival-rate refinement (deliberate, minor output churn).** The policy
  builders currently hardcode `arrival_rate = 1/0.648 ≈ 1.543210` (using the
  literature's 3-decimal *rounded* mean inter-arrival of 0.648). The default
  `Scenario` instead *derives* the rate at exactly ρ=0.90: `0.9·6/(3.5·1) =
  1.542857` (mean IAT `3.5/5.4 = 0.648148`). This ~2×10⁻⁴ change shifts seeded
  RNG streams, so exact per-run numbers move slightly. The asserted tests are
  robust to this (they check `count > 0`, WIP caps, label presence, and
  rows-differ — not pinned counts). Pinned `## Output` blocks in the gallery
  docs (which are *not* test-asserted) will be regenerated for accuracy. We
  accept the refinement because the derived value is the true 90% utilization and
  unifies the policy builders with the (already-derived) benchmark builders.
  (The three benchmark builders already derive 1.542857, so `gallery_benchmark_shops`
  output is unchanged.)
- **WIP-strategy ordering.** Policies now call `set_wip_strategy` in `__init__`
  (after servers exist) rather than before server creation. Must verify the
  lumscor/continuous/slar-limit tests still produce identical WIP values. (Low
  risk: the strategy is consulted at WIP-computation time during the run, not at
  server registration.)
- **Timing semantics.** Self-wiring must reuse the *same* mechanism per policy
  (`on_completion_trigger` for LumsCor/ConWIP/ContinuousRelease;
  `on_processing_end` for Slar/Draco) — not switch mechanisms — so completion
  timing is unchanged. The seeded behavior-preservation tests are the guard.
- **Larger blast radius** than the benchmark-shops work: touches 6 policy
  modules and ~12 builders plus their tests. Mitigated by TDD and the
  behavior-preservation suite.

## 7. Blast radius (files)

- **New:** `src/simulatte/scenario.py`; `tests/core/test_scenario.py`.
- **Changed internals + tests:** `policies/lumscor.py`, `policies/conwip.py`,
  `policies/continuous_release.py`, `policies/slar_limit.py` (scalar norm + set
  strategy); `tests/core/test_lumscor.py`, `test_conwip.py`,
  `test_continuous_release.py`, `test_slar_limit.py`.
- **Rewritten:** `src/simulatte/builders.py` (all builders thin + `scenario=` param;
  remove the 3 benchmark builders); `tests/core/test_builders*.py`.
- **Updated:** `examples/gallery_benchmark_shops.py` + `docs/examples/benchmark-shops.md`
  (use `build_immediate_release_system(env, scenario=...)`); `docs/api/utilities.md`
  (`Scenario` autodoc; drop the 3 benchmark-builder stubs);
  `skills/simulatte-dev/references/api-reference.md`.
- **Unchanged:** `router.py` (the `due_date_rule` hook stays), `distributions.py`
  (factories + helpers stay), `slar.py`, `draco.py`.

## 8. Alternatives considered

- **Option B — two-axis `build_system(shop, policy)`** with a uniform policy
  protocol. Rejected: "policy" collides with the domain term; bigger churn.
- **Option C — both B and thin presets.** Rejected: most surface area, two ways
  to do the same thing.
- **Env object only, no self-wiring.** Rejected: leaves the policy family
  half-consistent and the pull builders still carry trigger-wiring noise.
