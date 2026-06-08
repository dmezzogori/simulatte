# Design: Pluggable distributions and SKU families for `Scenario`

- **Status:** Proposed (2026-06-08)
- **Branch:** `feature/benchmark-shop-environments`
- **Related:** Follow-up to the `Scenario` environment/method decoupling
  (`docs/superpowers/specs/2026-06-04-scenario-environment-decoupling-design.md`),
  which explicitly deferred a multi-SKU mix as a future *additive* extension.

## 1. Problem

The `Scenario` value object collapses two capabilities the `Router` already
supports into hardcoded scalars:

1. **The service-time distribution is fixed.** Every server samples from
   `truncated_2erlang(lam=service_rate, max_value=service_max)`. `Scenario`
   exposes only `service_rate` and `service_max`; a user cannot run a lognormal,
   exponential, deterministic, or higher-order Erlang service process. The
   `Router` accepts any `Callable[[], float]` per `(sku, server)`, so the
   constraint is purely in `Scenario`.
2. **The SKU is a single hardcoded label.** `Scenario` emits one SKU `"F1"` with
   `sku_distributions={"F1": 1}`. The `Router` already accepts a full multi-SKU
   mix (`sku_distributions`, `sku_routings`, `sku_service_times`,
   `due_date_offset_distribution`, `due_date_rule`), but `Scenario` exposes none
   of it, so the SKU dimension carries no modelling value.

A third, latent defect ties the two together: `Scenario.resolved_arrival_rate()`
computes the mean processing time as `2.0 / service_rate` — the *nominal* (un-
truncated) 2-Erlang mean, with the Erlang shape `2` hardcoded. Because the
service distribution is truncated at `service_max`, the true mean is smaller, so
the derived arrival rate (and therefore the realized utilization) is slightly
off target.

## 2. Goals

- Let users supply **arbitrary service-time distributions** while the
  utilization→arrival-rate derivation keeps working — which requires each
  distribution to report its **own analytical mean** `E[p]`.
- Make the **SKU a first-class product-family** dimension: each family owns its
  routing, service-time distribution, due-date rule, and mix weight, exposing the
  `Router`'s existing multi-SKU generality through `Scenario`.
- Make the **inter-arrival process** and **per-family due-date offset**
  pluggable as well, so the three stochastic inputs are uniformly user-definable.
- **Fix the truncation inaccuracy** as a by-product of distributions reporting
  their true mean, and remove the hardcoded Erlang shape `2.0`.
- Preserve the **one-liner ergonomics** of the common single-product case.

## 3. Non-goals

- **No backward compatibility.** Per the project's API-stability policy, the
  `Scenario` constructor changes freely (the flat service/SKU fields are removed).
- **No per-`(family, server)` service heterogeneity for now.** A family applies
  one service-time distribution to all servers in its routing. The `Router`'s
  `{sku: {server: dist}}` map is populated uniformly; per-server distributions
  remain a future additive extension (YAGNI).
- **No enforcement of the arrival-process mean.** The `arrival_process` factory
  is trusted to return a sampler with mean `1/rate`; this is documented, not
  validated (checking it would require sampling, hurting reproducibility).
- **No change to `builders.py`.** Builders only call `build_floor` /
  `build_router`, whose signatures are unchanged.

## 4. Design

### 4.1 The `Distribution` abstraction (`distributions.py`)

The existing alias `Distribution = Callable[[], T]` is renamed to **`Sampler`**
(the bare "draw a value" contract) in `typing.py`; the `Router`'s slots keep
using `Sampler`, so the `Router` needs only that annotation rename. The new,
richer **`Distribution`** protocol is defined in `distributions.py` next to its
built-ins — only `Scenario` reads `.mean`, and `scenario.py` already imports from
`distributions.py`, so no new cross-module dependency or import cycle is created:

```python
@runtime_checkable
class Distribution(Protocol):
    """A callable random variate that also reports its analytical mean."""
    def __call__(self) -> float: ...
    @property
    def mean(self) -> float: ...
```

Because a `Distribution` is callable, it satisfies the `Router`'s `Sampler`
slots unchanged — only `Scenario` reads `.mean`.

Concrete built-ins are frozen dataclasses; each `__call__` draws a sample and
each `.mean` is analytical:

| Built-in | `__call__` | `.mean` |
|---|---|---|
| `Exponential(rate)` | `random.expovariate(rate)` | `1 / rate` |
| `Erlang(rate, shape=2)` | `random.gammavariate(shape, 1/rate)` | `shape / rate` |
| `TruncatedErlang(rate, shape=2, max_value=inf)` | resample Erlang until `≤ max_value` | true conditional mean (see below) |
| `LogNormal(mu, sigma)` | `random.lognormvariate(mu, sigma)` | `exp(mu + sigma²/2)` |
| `Uniform(low, high)` | `random.uniform(low, high)` | `(low + high) / 2` |
| `Deterministic(value)` | `value` | `value` |

**`TruncatedErlang.mean`.** Rejection-resampling until `≤ c` realizes the
distribution `X | X ≤ c`, whose mean is

```
E[X | X ≤ c] = (k/λ) · P(k+1, λc) / P(k, λc)
```

where `P(s, x)` is the regularized lower incomplete gamma — equivalently, for the
integer shape `k`, the Erlang CDF `P(k, λc) = 1 − e^{−λc} Σ_{n=0}^{k−1} (λc)ⁿ/n!`.
This is computed with elementary functions only (no SciPy dependency).

`truncated_2erlang(lam, max_value)` is **removed**; callers migrate to
`TruncatedErlang(rate=lam, shape=2, max_value=max_value)`.

**Output churn (deliberate, minor).** The default service distribution
`TruncatedErlang(2.0, 2, 4.0)` has true mean ≈ `0.989`, versus the nominal `1.0`
used today. The default derived arrival rate therefore shifts by ≈ 1 %
(`λ = ρ·M / (E[L]·E[p])`), the same class of seeded-output churn accepted for the
earlier `1/0.648` → derived-rate refinement. Asserted tests are robust to it
(`count > 0`, caps, label presence); pinned gallery `## Output` blocks (not test-
asserted) are regenerated.

### 4.2 `SkuFamily` value object (`scenario.py`)

```python
@dataclass(frozen=True)
class SkuFamily:
    name: str = "F1"
    weight: float = 1.0                                  # relative mix weight (normalized internally)
    service_time: Distribution = TruncatedErlang(2.0, 2, 4.0)   # immutable → safe shared default
    routing_factory: RoutingFactory | None = None        # None ⇒ inherit shop_type
    expected_routing_length: float | None = None         # required iff routing_factory is set
    due_date_offset: Distribution | None = None          # None ⇒ shop-level Scenario.due_date_offset
    twk_allowance_factor: float | None = None            # if set, TWK rule (takes precedence)
```

- `routing_for(shop_type)` → `routing_factory or _ROUTING[shop_type]`.
- `mean_routing_length(shop_type, n_servers)` → `expected_routing_length` if set;
  else the shop-type formula (`n_servers` for PFS, `(n_servers + 1) / 2`
  otherwise). This `E[L]` and `service_time.mean` feed the mix-weighted
  derivation (§4.3).
- The "custom routing requires `expected_routing_length`" rule and its
  `ValueError` move here from `Scenario`.

### 4.3 `Scenario` changes (`scenario.py`)

```python
@dataclass(frozen=True)
class Scenario:
    shop_type: ShopType = ShopType.PJS
    n_servers: int = 6
    target_utilization: float = 0.90
    families: tuple[SkuFamily, ...] = (SkuFamily(),)         # default: one F1 family
    due_date_offset: Distribution = Uniform(30.0, 45.0)      # shop default for families not overriding
    arrival_process: Callable[[float], Sampler] = Exponential  # (rate) -> inter-arrival sampler
    arrival_rate: float | None = None                        # explicit override of the derivation
```

**Removed flat fields:** `service_rate`, `service_max`, `sku`, `routing_factory`,
`expected_routing_length`, `twk_allowance_factor`, `due_date_offset_range` — all
now live on `SkuFamily` or a `Distribution`. `arrival_rate` remains the escape
hatch; `expected_routing_length` moves to `SkuFamily`.

**Mix-weighted derivation:**

```python
def resolved_arrival_rate(self) -> float:
    if self.arrival_rate is not None:
        return self.arrival_rate
    total = sum(f.weight for f in self.families)
    expected_work = sum(
        (f.weight / total) * f.mean_routing_length(self.shop_type, self.n_servers) * f.service_time.mean
        for f in self.families
    )
    return self.target_utilization * self.n_servers / expected_work
```

`ρ = λ·E[work]/M`, with `E[work] = Σ wₙ·E[Lₙ]·E[pₙ]` over the normalized mix. A
single family reduces to today's formula (now with the true truncated mean).

**`build_router` maps families onto the `Router`'s existing per-SKU dicts:**

```python
rate = self.resolved_arrival_rate()
Router(
    ...,
    inter_arrival_distribution   = self.arrival_process(rate),
    sku_distributions            = {f.name: f.weight for f in self.families},
    sku_routings                 = {f.name: f.routing_for(self.shop_type)(servers) for f in self.families},
    sku_service_times            = {f.name: {s: f.service_time for s in servers} for f in self.families},
    due_date_offset_distribution = {f.name: (f.due_date_offset or self.due_date_offset) for f in self.families},
    due_date_rule                = {f.name: twk_due_date(f.twk_allowance_factor)
                                     for f in self.families if f.twk_allowance_factor is not None} or None,
)
```

`f.service_time` and `self.arrival_process(rate)` are callable, so they drop into
the `Router`'s `Sampler` slots without any `Router` change beyond the
`Distribution`→`Sampler` annotation rename.

**Ergonomics — `Scenario.single` + presets:**

```python
@classmethod
def single(cls, *, service_time: Distribution | None = None,   # None ⇒ SkuFamily default
           due_date_offset: Distribution | None = None,
           twk_allowance_factor: float | None = None,
           name: str = "F1", **shop) -> Scenario:
    # Forward only the family attributes the caller set, so SkuFamily's own
    # defaults fill the rest (avoids duplicating the default service dist here).
    family_kwargs = {k: v for k, v in {
        "name": name, "service_time": service_time,
        "due_date_offset": due_date_offset, "twk_allowance_factor": twk_allowance_factor,
    }.items() if v is not None}
    return cls(families=(SkuFamily(**family_kwargs),), **shop)
```

The three presets (`pure_job_shop` / `general_flow_shop` / `pure_flow_shop`)
stay as thin `shop_type=` setters accepting shop-level overrides. "Change just
the service rate" becomes `Scenario.single(service_time=Erlang(3.0, 2))`; "PJS
with a custom mix" is `Scenario.pure_job_shop(families=(SkuFamily(...), ...))`.

### 4.4 Module layout

- `typing.py`: `Sampler` replaces the old `Distribution` alias (the `Router`'s
  slots use `Sampler`).
- `distributions.py`: the `Distribution` **protocol** and its concrete built-ins,
  alongside the existing routing factories, `arrival_rate_for_utilization`, and
  `twk_due_date`.
- `scenario.py`: `SkuFamily` joins `ShopType` / `Scenario` (tightly coupled; the
  module stays small enough to keep them together).

## 5. Validation & error handling

- `SkuFamily`: `weight > 0`; `expected_routing_length` required (and `> 0`) when
  `routing_factory` is set, else `ValueError` (message preserved from `Scenario`).
- `Scenario`: `families` non-empty; **family names unique** (they are the
  `Router`'s dict keys — a collision would silently drop a family, so raise);
  `n_servers ≥ 1`; `target_utilization` in `(0, 1]`.
- Built-in distributions (`__post_init__`): `rate > 0`, `shape ≥ 1`,
  `max_value > 0`, `sigma > 0`, `Uniform` `low ≤ high`.
- `arrival_process`: documented to return a sampler with mean `1/rate`; not
  enforced.

## 6. Testing strategy (TDD)

- **Distributions:** each built-in's `.mean` equals its closed form *and* matches
  a large-sample empirical average within tolerance (the real guard the analytic
  mean is right). `TruncatedErlang`: every sample `≤ max_value`, and
  `.mean < shape/rate`.
- **`SkuFamily`:** routing inheritance vs. override; `mean_routing_length` per
  shop-type; custom-routing-without-`E[L]` raises.
- **`Scenario` derivation:** single-family reproduces the analytic rate; a
  **two-family mix** yields the hand-computed weight-blended rate; `arrival_rate`
  override bypasses derivation; a custom `arrival_process` (e.g. `Deterministic`)
  is wired through.
- **`build_router` multi-family wiring:** per-family `sku_distributions` /
  `sku_routings` / `sku_service_times` / `due_date_*`; a 2-family scenario runs to
  completion, producing jobs of *both* SKUs with the right routing structure and
  (for a TWK family) TWK due dates.
- **Behavior preservation:** default `Scenario()` still runs; existing
  builder/policy/gallery suites stay green (robust to the §4.1 true-mean rate
  shift). Pinned gallery `## Output` numbers regenerated.
- **Gates:** 99 % branch coverage, ruff (incl. RUF100), `ty` — all green.

## 7. Risks & verification points

- **True-mean rate shift (deliberate).** Default seeded outputs move ≈ 1 %. The
  asserted tests are robust; pinned `## Output` blocks are regenerated. (§4.1)
- **`Distribution`/`Sampler` rename blast radius.** `typing.py` and `router.py`
  annotations change; any other reference to the `Distribution` alias migrates to
  `Sampler`. Caught by `ty` and a repo grep.
- **`Scenario` constructor is breaking.** Every call site that set the removed
  flat fields (`service_rate`, `service_max`, `sku`, …) migrates to
  `families=` / `Scenario.single`. Known sites: `tests/core/test_builders.py`,
  `tests/core/test_focus.py`, and any docs/examples constructing `Scenario(...)`
  with those fields. A repo grep enumerates the full set before editing.
- **Protocol vs. runtime checks.** `@runtime_checkable` only checks method
  *presence*, not signatures; `.mean` correctness rests on the distribution unit
  tests, not on `isinstance`.

## 8. Alternatives considered

- **Callable + explicit `mean_processing_time`** (instead of a mean-carrying
  `Distribution`). Rejected: the sampler and its declared mean can silently
  desync, and truncation correctness becomes the user's burden.
- **Estimate the mean by Monte-Carlo sampling at construction.** Rejected:
  consumes the RNG stream (hurts reproducibility / seed comparability) and is
  only approximate.
- **`Distribution` as an ABC** (nominal typing). Rejected in favour of a
  Protocol: a structural contract lets a user's own callable-with-`.mean` satisfy
  it without subclassing.
- **Keep SKU single, make service distribution a flat `Scenario` field.**
  Rejected: leaves the SKU dimension valueless and does not expose the `Router`'s
  multi-SKU generality. The chosen per-family design makes the distribution's
  natural home the family.
- **Routing purely per-family (drop shop-level `shop_type`).** Rejected: the
  one-liner "pick a shop type" preset ergonomics are worth keeping; `shop_type`
  remains the shop-level default with per-family override.
