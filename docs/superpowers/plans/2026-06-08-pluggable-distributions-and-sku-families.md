# Pluggable Distributions and SKU Families Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make service/arrival/due-date distributions user-definable through a mean-carrying `Distribution` protocol, and promote the SKU to a first-class per-family product mix on `Scenario`.

**Architecture:** A new `Distribution` protocol (`__call__() -> float` + a `.mean` property) with frozen-dataclass built-ins lives in `distributions.py`; the bare callable alias is renamed `Distribution → Sampler` in `typing.py`. A `SkuFamily` value object owns routing + service distribution + due-date + weight. `Scenario` holds `families: tuple[SkuFamily, ...]`, a pluggable `arrival_process`, and a mix-weighted ρ→λ derivation; `build_router` maps families onto the `Router`'s already-general per-SKU dicts.

**Tech Stack:** Python 3.11+, SimPy, `uv`, `pytest` (99% branch coverage gate), `ruff` (incl. RUF100), `ty`.

**Spec:** `docs/superpowers/specs/2026-06-08-pluggable-distributions-and-sku-families-design.md`

---

## File Structure

- `src/simulatte/typing.py` — rename `Distribution` alias → `Sampler` (the callable contract the `Router` uses).
- `src/simulatte/distributions.py` — add the `Distribution` protocol + built-ins (`Exponential`, `Erlang`, `TruncatedErlang`, `LogNormal`, `Uniform`, `Deterministic`) and the `_erlang_cdf` helper; later remove the `truncated_2erlang` free function.
- `src/simulatte/router.py` — update 3 annotations from `Distribution[float]` to `Sampler[float]`.
- `src/simulatte/scenario.py` — add `SkuFamily` + `RoutingFactory` alias; rewrite `Scenario` (families, `arrival_process`, mix derivation, `build_router`, `single`, presets).
- `tests/core/test_distributions.py` — replace the two `truncated_2erlang` tests with `Distribution` built-in tests.
- `tests/core/test_scenario.py` — rewrite for `SkuFamily` + the new `Scenario` API.
- Migration touch points (Task 4–5): `tests/core/test_builders.py`, `examples/gallery_dispatching_{stateless,parameterized,focus}.py` + their `docs/examples/*.md` run-blocks, `examples/gallery_release_triggers.py` + `docs/examples/release-triggers.md`, `docs/api/utilities.md`, `skills/simulatte-dev/SKILL.md`, `skills/simulatte-dev/references/api-reference.md`, `docs/tutorials/release-control-and-dispatching.md`.

---

## Task 1: Rename the `Distribution` alias to `Sampler`

Frees the name `Distribution` for the protocol (Task 2). The `Router`'s slots are bare callables, so they keep using the renamed `Sampler`. Pure refactor — guarded by `ty` and the existing suite.

**Files:**
- Modify: `src/simulatte/typing.py`
- Modify: `src/simulatte/router.py`

- [ ] **Step 1: Rename the alias and `__all__` entry in `typing.py`**

In `src/simulatte/typing.py`, change the alias definition:

```python
Sampler: TypeAlias = Callable[[], T]
DiscreteDistribution: TypeAlias = dict[K, T]
Builder: TypeAlias = Callable[..., S]
```

and update `__all__` (replace `"Distribution"` with `"Sampler"`, keep alphabetical order):

```python
__all__ = [
    "Builder",
    "DiscreteDistribution",
    "ProcessGenerator",
    "PullSystem",
    "PushSystem",
    "Sampler",
    "System",
]
```

- [ ] **Step 2: Update `router.py` to import and use `Sampler`**

In `src/simulatte/router.py` line 17, change the import:

```python
from simulatte.typing import DiscreteDistribution, Sampler
```

and the three annotations in `Router.__init__` (lines ~49, ~54, ~56):

```python
        inter_arrival_distribution: Sampler[float],
        sku_distributions: DiscreteDistribution[str, float],
        sku_routings: dict[str, Callable[[], Sequence[Server]]],
        sku_service_times: dict[
            str,
            DiscreteDistribution[Server, Sampler[float]],
        ],
        due_date_offset_distribution: dict[str, Sampler[float]],
```

- [ ] **Step 3: Verify no other references to the old alias remain**

Run: `grep -rn "\bDistribution\b" src/ | grep -v "DiscreteDistribution\|distributions"`
Expected: no matches that refer to the type alias (only module-name mentions, if any). If any `Distribution[...]` annotation remains, change it to `Sampler[...]`.

- [ ] **Step 4: Run type-check and the full suite**

Run: `uv run ty check src && uv run pytest -q`
Expected: `All checks passed!` and the suite green (878 passed).

- [ ] **Step 5: Commit**

```bash
git add src/simulatte/typing.py src/simulatte/router.py
git commit -m "refactor(typing): rename Distribution alias to Sampler"
```

---

## Task 2: Add the `Distribution` protocol and built-in distributions

Additive — `truncated_2erlang` stays for now (removed in Task 5). New built-ins are frozen dataclasses; each is callable (drops into the `Router`'s `Sampler` slots) and exposes an analytical `.mean`.

**Files:**
- Modify: `src/simulatte/distributions.py`
- Test: `tests/core/test_distributions.py`

- [ ] **Step 1: Write the failing tests for the built-ins**

Append to `tests/core/test_distributions.py` (and add the imports to the existing `from simulatte.distributions import (...)` block: `Deterministic`, `Distribution`, `Erlang`, `Exponential`, `LogNormal`, `TruncatedErlang`, `Uniform`):

```python
import math
import statistics


def _empirical_mean(dist: Distribution, n: int = 50_000) -> float:
    random.seed(7)
    return statistics.fmean(dist() for _ in range(n))


def test_exponential_mean_and_sampling() -> None:
    d = Exponential(rate=2.0)
    assert d.mean == pytest.approx(0.5)
    assert _empirical_mean(d) == pytest.approx(0.5, rel=0.05)


def test_erlang_mean_and_sampling() -> None:
    d = Erlang(rate=2.0, shape=2)
    assert d.mean == pytest.approx(1.0)
    assert _empirical_mean(d) == pytest.approx(1.0, rel=0.05)


def test_deterministic_is_constant() -> None:
    d = Deterministic(value=3.0)
    assert d.mean == 3.0
    assert {d() for _ in range(10)} == {3.0}


def test_uniform_mean() -> None:
    d = Uniform(low=10.0, high=18.0)
    assert d.mean == pytest.approx(14.0)
    assert _empirical_mean(d) == pytest.approx(14.0, rel=0.05)


def test_lognormal_mean() -> None:
    d = LogNormal(mu=0.0, sigma=0.5)
    assert d.mean == pytest.approx(math.exp(0.125))
    assert _empirical_mean(d) == pytest.approx(math.exp(0.125), rel=0.05)


def test_truncated_erlang_respects_cap_and_true_mean() -> None:
    d = TruncatedErlang(rate=2.0, shape=2, max_value=4.0)
    random.seed(42)
    samples = [d() for _ in range(2000)]
    assert all(0.0 <= s <= 4.0 for s in samples)
    # The true conditional mean is below the nominal shape/rate = 1.0 ...
    assert d.mean < 1.0
    assert d.mean == pytest.approx(0.989232, abs=1e-4)
    # ... and matches the empirical mean of the truncated sampler.
    assert _empirical_mean(d) == pytest.approx(d.mean, rel=0.03)


def test_untruncated_erlang_mean_equals_nominal() -> None:
    # max_value=inf ⇒ no truncation ⇒ mean is exactly shape/rate.
    assert TruncatedErlang(rate=2.0, shape=2, max_value=math.inf).mean == pytest.approx(1.0)


def test_distribution_validation() -> None:
    for bad in (lambda: Exponential(rate=0.0), lambda: Erlang(rate=2.0, shape=0),
                lambda: TruncatedErlang(rate=2.0, shape=2, max_value=0.0),
                lambda: LogNormal(mu=0.0, sigma=0.0), lambda: Uniform(low=5.0, high=1.0)):
        with pytest.raises(ValueError):  # noqa: PT011
            bad()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_distributions.py -q`
Expected: FAIL with `ImportError` / `cannot import name 'Exponential'`.

- [ ] **Step 3: Implement the protocol and built-ins**

In `src/simulatte/distributions.py`, update the top-of-file imports:

```python
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable
```

Then insert the following near the top of the module (before the routing factories):

```python
@runtime_checkable
class Distribution(Protocol):
    """A callable random variate that also reports its analytical mean.

    Any object that is callable with no arguments returning a ``float`` and
    exposes a ``mean`` property satisfies this protocol — the built-ins below,
    or a user's own class. The ``Router`` only needs the callable side (the
    ``Sampler`` contract); ``Scenario`` reads ``.mean`` to derive the arrival
    rate for a target utilization.
    """

    def __call__(self) -> float: ...

    @property
    def mean(self) -> float: ...


def _erlang_cdf(shape: int, rate: float, x: float) -> float:
    """Regularized lower incomplete gamma P(shape, rate*x) for integer shape.

    Equivalent to the Erlang CDF ``1 - e^{-λx} Σ_{n=0}^{k-1} (λx)^n / n!``.
    Computed with elementary functions (no SciPy).
    """
    lam_x = rate * x
    total = 0.0
    term = 1.0  # (lam_x)^0 / 0!
    for n in range(shape):
        if n > 0:
            term *= lam_x / n
        total += term
    return 1.0 - math.exp(-lam_x) * total


@dataclass(frozen=True)
class Exponential:
    """Exponential service/inter-arrival distribution with rate ``rate`` (mean ``1/rate``)."""

    rate: float

    def __post_init__(self) -> None:
        if self.rate <= 0:
            msg = f"rate must be positive, got {self.rate}"
            raise ValueError(msg)

    def __call__(self) -> float:
        return random.expovariate(self.rate)  # noqa: S311

    @property
    def mean(self) -> float:
        return 1.0 / self.rate


@dataclass(frozen=True)
class Erlang:
    """Erlang (Gamma with integer ``shape``) distribution, mean ``shape/rate``."""

    rate: float
    shape: int = 2

    def __post_init__(self) -> None:
        if self.rate <= 0:
            msg = f"rate must be positive, got {self.rate}"
            raise ValueError(msg)
        if self.shape < 1:
            msg = f"shape must be >= 1, got {self.shape}"
            raise ValueError(msg)

    def __call__(self) -> float:
        return random.gammavariate(self.shape, 1.0 / self.rate)  # noqa: S311

    @property
    def mean(self) -> float:
        return self.shape / self.rate


@dataclass(frozen=True)
class TruncatedErlang:
    """Erlang truncated to ``[0, max_value]`` by rejection resampling.

    The default ``shape=2`` reproduces the classic truncated 2-Erlang service
    process. ``mean`` is the TRUE conditional mean ``E[X | X <= max_value]``,
    which is strictly below the nominal ``shape/rate`` whenever ``max_value`` is
    finite.
    """

    rate: float
    shape: int = 2
    max_value: float = math.inf

    def __post_init__(self) -> None:
        if self.rate <= 0:
            msg = f"rate must be positive, got {self.rate}"
            raise ValueError(msg)
        if self.shape < 1:
            msg = f"shape must be >= 1, got {self.shape}"
            raise ValueError(msg)
        if self.max_value <= 0:
            msg = f"max_value must be positive, got {self.max_value}"
            raise ValueError(msg)

    def __call__(self) -> float:
        while True:
            sample = sum(random.expovariate(self.rate) for _ in range(self.shape))  # noqa: S311
            if sample <= self.max_value:
                return sample

    @property
    def mean(self) -> float:
        if math.isinf(self.max_value):
            return self.shape / self.rate
        # E[X | X <= c] = (k/λ) · P(k+1, λc) / P(k, λc)
        numerator = _erlang_cdf(self.shape + 1, self.rate, self.max_value)
        denominator = _erlang_cdf(self.shape, self.rate, self.max_value)
        return (self.shape / self.rate) * numerator / denominator


@dataclass(frozen=True)
class LogNormal:
    """Lognormal distribution with underlying-normal params ``mu``/``sigma``."""

    mu: float
    sigma: float

    def __post_init__(self) -> None:
        if self.sigma <= 0:
            msg = f"sigma must be positive, got {self.sigma}"
            raise ValueError(msg)

    def __call__(self) -> float:
        return random.lognormvariate(self.mu, self.sigma)  # noqa: S311

    @property
    def mean(self) -> float:
        return math.exp(self.mu + self.sigma**2 / 2.0)


@dataclass(frozen=True)
class Uniform:
    """Continuous uniform distribution on ``[low, high]``, mean ``(low+high)/2``."""

    low: float
    high: float

    def __post_init__(self) -> None:
        if self.low > self.high:
            msg = f"low must be <= high, got low={self.low}, high={self.high}"
            raise ValueError(msg)

    def __call__(self) -> float:
        return random.uniform(self.low, self.high)  # noqa: S311

    @property
    def mean(self) -> float:
        return (self.low + self.high) / 2.0


@dataclass(frozen=True)
class Deterministic:
    """Degenerate distribution that always returns ``value``."""

    value: float

    def __call__(self) -> float:
        return self.value

    @property
    def mean(self) -> float:
        return self.value
```

> Note on `# noqa: S311`: the repo enables RUF100, which flags inert noqa. `S` (bandit) is NOT in `ruff`'s `select`, so these would be flagged as unused. **Do not add them** — they are shown above only to mark the RNG calls; omit the `# noqa` comments entirely when implementing. (Step 5 runs ruff to confirm.)

- [ ] **Step 4: Run the distribution tests**

Run: `uv run pytest tests/core/test_distributions.py -q`
Expected: PASS (new tests green; the existing `truncated_2erlang` tests still pass — that function is untouched).

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check src/simulatte/distributions.py tests/core/test_distributions.py && uv run ty check src`
Expected: `All checks passed!` (zero ruff findings — confirms no stray `# noqa`).

- [ ] **Step 6: Commit**

```bash
git add src/simulatte/distributions.py tests/core/test_distributions.py
git commit -m "feat(distributions): add Distribution protocol and built-in variates"
```

---

## Task 3: Add the `SkuFamily` value object

Additive — `Scenario` is unchanged until Task 4. `SkuFamily` owns a family's routing, service distribution, due-date, and mix weight, and resolves its own `E[L]`.

**Files:**
- Modify: `src/simulatte/scenario.py`
- Test: `tests/core/test_scenario.py`

- [ ] **Step 1: Write the failing tests for `SkuFamily`**

Append to `tests/core/test_scenario.py` (add imports: `from simulatte.distributions import TruncatedErlang, Erlang` and `from simulatte.scenario import SkuFamily`):

```python
def test_skufamily_defaults() -> None:
    f = SkuFamily()
    assert f.name == "F1"
    assert f.weight == 1.0
    assert isinstance(f.service_time, TruncatedErlang)
    assert f.service_time.mean == pytest.approx(0.989232, abs=1e-4)


def test_skufamily_mean_routing_length_inherits_shop_type() -> None:
    f = SkuFamily()
    assert f.mean_routing_length(ShopType.PJS, n_servers=6) == 3.5
    assert f.mean_routing_length(ShopType.GFS, n_servers=6) == 3.5
    assert f.mean_routing_length(ShopType.PFS, n_servers=6) == 6.0


def test_skufamily_routing_override_requires_expected_length() -> None:
    with pytest.raises(ValueError, match="expected_routing_length"):
        SkuFamily(routing_factory=pure_job_shop_routing)
    f = SkuFamily(routing_factory=pure_job_shop_routing, expected_routing_length=2.0)
    assert f.mean_routing_length(ShopType.PFS, n_servers=6) == 2.0  # override wins over shop type
    assert f.routing_for(ShopType.PFS) is pure_job_shop_routing


def test_skufamily_routing_for_inherits_shop_type() -> None:
    assert SkuFamily().routing_for(ShopType.GFS) is general_flow_shop_routing


def test_skufamily_weight_must_be_positive() -> None:
    with pytest.raises(ValueError, match="weight"):
        SkuFamily(weight=0.0)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/core/test_scenario.py -q`
Expected: FAIL with `cannot import name 'SkuFamily'`.

- [ ] **Step 3: Implement `SkuFamily` and the `RoutingFactory` alias**

In `src/simulatte/scenario.py`, update imports to add the built-ins and a due-date default, and `field`:

```python
from dataclasses import dataclass, field
...
from simulatte.distributions import (
    Distribution,
    TruncatedErlang,
    Uniform,
    arrival_rate_for_utilization,
    general_flow_shop_routing,
    pure_flow_shop_routing,
    pure_job_shop_routing,
    twk_due_date,
)
```

Add the alias near the top (after the `_ROUTING` map), then the dataclass:

```python
if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Sequence

    from simulatte.server import Server

RoutingFactory = "Callable[[Sequence[Server]], Callable[[], Sequence[Server]]]"
# (use the existing inline annotation style already present in the module;
#  define a module-level alias only if the module already favors aliases.)


_DEFAULT_SERVICE_TIME = TruncatedErlang(rate=2.0, shape=2, max_value=4.0)


@dataclass(frozen=True)
class SkuFamily:
    """One product family: its routing, service-time distribution, due-date, and mix weight."""

    name: str = "F1"
    weight: float = 1.0
    service_time: Distribution = _DEFAULT_SERVICE_TIME
    routing_factory: Callable[[Sequence[Server]], Callable[[], Sequence[Server]]] | None = None
    expected_routing_length: float | None = None
    due_date_offset: Distribution | None = None
    twk_allowance_factor: float | None = None

    def __post_init__(self) -> None:
        if self.weight <= 0:
            msg = f"weight must be positive, got {self.weight}"
            raise ValueError(msg)
        if self.routing_factory is not None and self.expected_routing_length is None:
            msg = "SkuFamily with a custom routing_factory must set expected_routing_length."
            raise ValueError(msg)
        if self.expected_routing_length is not None and self.expected_routing_length <= 0:
            msg = f"expected_routing_length must be positive, got {self.expected_routing_length}"
            raise ValueError(msg)

    def routing_for(self, shop_type: ShopType) -> Callable[[Sequence[Server]], Callable[[], Sequence[Server]]]:
        """This family's routing factory (custom override or the shop-type default)."""
        return self.routing_factory or _ROUTING[shop_type]

    def mean_routing_length(self, shop_type: ShopType, n_servers: int) -> float:
        """Expected operations per order, E[L] (explicit override, else shop-type formula)."""
        if self.expected_routing_length is not None:
            return self.expected_routing_length
        if shop_type is ShopType.PFS:
            return float(n_servers)
        return (n_servers + 1) / 2
```

> Implementation note: keep the inline `Callable[[Sequence[Server]], ...]` annotation style the module already uses (do not introduce a string-typed alias). The `RoutingFactory = "..."` line above is illustrative; if the team prefers a real alias, add `RoutingFactory: TypeAlias = Callable[[Sequence[Server]], Callable[[], Sequence[Server]]]` under `TYPE_CHECKING` and use it in both `SkuFamily` and `Scenario`.

- [ ] **Step 4: Run the `SkuFamily` tests**

Run: `uv run pytest tests/core/test_scenario.py -q -k skufamily`
Expected: the 5 new `skufamily` tests PASS. (The legacy `Scenario` tests still pass — `Scenario` is unchanged.)

- [ ] **Step 5: Commit**

```bash
git add src/simulatte/scenario.py tests/core/test_scenario.py
git commit -m "feat(scenario): add SkuFamily value object"
```

---

## Task 4: Rewrite `Scenario` for families + pluggable distributions (atomic breaking change)

This is the breaking change: the flat service/SKU fields are removed and `families` / `arrival_process` / `due_date_offset` are added. Because there is no shim, **the `Scenario` rewrite, the `test_scenario.py` rewrite, and the in-repo `Scenario(...)` call-site migrations all land in this one commit** so the suite stays green.

**Files:**
- Modify: `src/simulatte/scenario.py`
- Rewrite: `tests/core/test_scenario.py`
- Migrate: `tests/core/test_builders.py`, `examples/gallery_dispatching_stateless.py`, `examples/gallery_dispatching_parameterized.py`, `examples/gallery_dispatching_focus.py`, and their doc run-blocks `docs/examples/dispatching-stateless.md`, `docs/examples/dispatching-parameterized.md`, `docs/examples/dispatching-focus.md`.

- [ ] **Step 1: Rewrite the `Scenario` dataclass, derivation, and `build_router`**

Replace the `Scenario` class body in `src/simulatte/scenario.py` with:

```python
@dataclass(frozen=True)
class Scenario:
    """Immutable description of a shop environment and its order stream."""

    shop_type: ShopType = ShopType.PJS
    n_servers: int = 6
    target_utilization: float = 0.90
    families: tuple[SkuFamily, ...] = (SkuFamily(),)
    due_date_offset: Distribution = Uniform(low=30.0, high=45.0)
    arrival_process: Callable[[float], Callable[[], float]] = Exponential
    arrival_rate: float | None = None

    def __post_init__(self) -> None:
        if not self.families:
            msg = "Scenario must have at least one SkuFamily."
            raise ValueError(msg)
        names = [f.name for f in self.families]
        if len(set(names)) != len(names):
            msg = f"SkuFamily names must be unique, got {names}"
            raise ValueError(msg)
        if self.n_servers < 1:
            msg = f"n_servers must be >= 1, got {self.n_servers}"
            raise ValueError(msg)
        if not 0 < self.target_utilization <= 1:
            msg = f"target_utilization must be in (0, 1], got {self.target_utilization}"
            raise ValueError(msg)

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

    @classmethod
    def single(
        cls,
        *,
        service_time: Distribution | None = None,
        due_date_offset: Distribution | None = None,
        twk_allowance_factor: float | None = None,
        name: str = "F1",
        **shop: object,
    ) -> Scenario:
        """Convenience for the common one-product case: build a single-family Scenario.

        Only the family attributes the caller sets are forwarded, so ``SkuFamily``'s
        own defaults fill the rest. ``**shop`` forwards shop-level kwargs
        (``shop_type``, ``n_servers``, ``target_utilization``, ``arrival_rate``, ...).
        """
        family_kwargs = {
            k: v
            for k, v in {
                "name": name,
                "service_time": service_time,
                "due_date_offset": due_date_offset,
                "twk_allowance_factor": twk_allowance_factor,
            }.items()
            if v is not None
        }
        return cls(families=(SkuFamily(**family_kwargs),), **shop)  # type: ignore[arg-type]

    def resolved_arrival_rate(self) -> float:
        """The exponential arrival rate (explicit override, else mix-weighted derivation)."""
        if self.arrival_rate is not None:
            return self.arrival_rate
        total_weight = sum(f.weight for f in self.families)
        expected_work = sum(
            (f.weight / total_weight)
            * f.mean_routing_length(self.shop_type, self.n_servers)
            * f.service_time.mean
            for f in self.families
        )
        return arrival_rate_for_utilization(
            self.target_utilization,
            n_servers=self.n_servers,
            mean_routing_length=expected_work,
            mean_processing_time=1.0,
        )

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
        """Assemble the Router from the family mix: arrival process, per-family routing,
        service-time distributions, and due-date offsets/rules."""
        rate = self.resolved_arrival_rate()
        due_date_rule = {
            f.name: twk_due_date(f.twk_allowance_factor)
            for f in self.families
            if f.twk_allowance_factor is not None
        } or None
        return Router(
            env=env,
            shopfloor=shop_floor,
            servers=servers,
            psp=psp,
            inter_arrival_distribution=self.arrival_process(rate),
            sku_distributions={f.name: f.weight for f in self.families},
            sku_routings={f.name: f.routing_for(self.shop_type)(servers) for f in self.families},
            sku_service_times={f.name: {server: f.service_time for server in servers} for f in self.families},
            due_date_offset_distribution={
                f.name: (f.due_date_offset or self.due_date_offset) for f in self.families
            },
            due_date_rule=due_date_rule,
            priority_policies=priority_policies,
        )
```

Update the module imports accordingly: add `Exponential` to the `simulatte.distributions` import; remove the now-unused `truncated_2erlang` import; remove the old `random` import if it is no longer referenced (the inter-arrival lambda and `random.uniform`/`random.expovariate` calls are gone). Drop the now-removed `mean_routing_length`/`routing_for`/`resolved_arrival_rate` logic that referenced the old flat fields, and delete the old flat fields (`service_rate`, `service_max`, `due_date_offset_range`, `sku`, `routing_factory`, `expected_routing_length`, `twk_allowance_factor`) from the class.

- [ ] **Step 2: Rewrite `tests/core/test_scenario.py`**

Replace the legacy `Scenario`-level tests (the ones using `routing_factory=`, `mean_routing_length` as a `Scenario` property, `routing_for()` with no args, and the pinned `0.648`/`1.111` constants) with the following; keep the `SkuFamily` tests from Task 3 and the `build_floor` test:

```python
def test_default_scenario_is_pure_job_shop() -> None:
    s = Scenario()
    assert s.shop_type is ShopType.PJS
    assert s.n_servers == 6
    assert s.target_utilization == 0.90
    assert len(s.families) == 1 and s.families[0].name == "F1"


def test_presets_select_shop_type() -> None:
    assert Scenario.pure_job_shop().shop_type is ShopType.PJS
    assert Scenario.general_flow_shop().shop_type is ShopType.GFS
    assert Scenario.pure_flow_shop(n_servers=12).n_servers == 12


def test_derived_rate_uses_true_truncated_mean() -> None:
    # The derivation now uses the TRUE truncated mean (≈0.989), not the nominal 1.0,
    # so the classic literature constants (0.648 PJS, 1.111 PFS) shift by ≈1%.
    e_p = TruncatedErlang(rate=2.0, shape=2, max_value=4.0).mean
    expected_pjs = 3.5 * e_p / (0.9 * 6)
    expected_pfs = 6.0 * e_p / (0.9 * 6)
    assert 1 / Scenario.pure_job_shop().resolved_arrival_rate() == pytest.approx(expected_pjs)
    assert 1 / Scenario.pure_flow_shop().resolved_arrival_rate() == pytest.approx(expected_pfs)


def test_explicit_arrival_rate_overrides_derivation() -> None:
    assert Scenario(arrival_rate=2.0).resolved_arrival_rate() == 2.0


def test_two_family_mix_weighted_derivation() -> None:
    fast = SkuFamily(name="A", weight=3.0, service_time=Erlang(rate=4.0, shape=2))   # E[p]=0.5
    slow = SkuFamily(name="B", weight=1.0, service_time=Erlang(rate=2.0, shape=2))   # E[p]=1.0
    s = Scenario.pure_flow_shop(families=(fast, slow))  # E[L]=n_servers=6 for both
    expected_work = (3 / 4) * 6 * 0.5 + (1 / 4) * 6 * 1.0
    assert s.resolved_arrival_rate() == pytest.approx(0.9 * 6 / expected_work)


def test_single_convenience_builds_one_family() -> None:
    s = Scenario.single(service_time=Erlang(rate=3.0, shape=2), shop_type=ShopType.PJS, n_servers=4)
    assert s.shop_type is ShopType.PJS and s.n_servers == 4
    assert len(s.families) == 1 and isinstance(s.families[0].service_time, Erlang)


def test_duplicate_family_names_raise() -> None:
    with pytest.raises(ValueError, match="unique"):
        Scenario(families=(SkuFamily(name="X"), SkuFamily(name="X")))


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
            assert list(job.servers) == list(servers)


def test_build_router_applies_twk_due_dates() -> None:
    random.seed(42)
    k = 8.0
    with Environment() as env:
        scenario = Scenario.single(shop_type=ShopType.PJS, twk_allowance_factor=k)
        sf, servers = scenario.build_floor(env)
        psp = PreShopPool(env=env, shopfloor=sf)
        scenario.build_router(env, sf, servers, psp=psp)
        env.run(until=50.0)
        job = next(iter(psp.jobs))
        assert job.due_date == pytest.approx(job.created_at + k * sum(job.processing_times))


def test_build_router_runs_two_family_mix() -> None:
    random.seed(42)
    families = (SkuFamily(name="A", weight=1.0), SkuFamily(name="B", weight=1.0))
    with Environment() as env:
        scenario = Scenario.pure_job_shop(families=families)
        sf, servers = scenario.build_floor(env)
        scenario.build_router(env, sf, servers, psp=None)
        env.run(until=500.0)
        skus = {job.sku for job in sf.jobs_done}
        assert skus == {"A", "B"}
```

Remove the now-obsolete imports from the test file's distributions import (`general_flow_shop_routing`, `pure_flow_shop_routing` are still used by the Task-3 `SkuFamily` tests; keep `pure_job_shop_routing`). Ensure `Erlang` is imported.

- [ ] **Step 3: Migrate the breaking in-repo `Scenario(...)` call sites**

`tests/core/test_builders.py:54` — drop the redundant `service_rate=2.0` (it equals the default service distribution):

```python
            scenario=Scenario(n_servers=2, arrival_rate=0.5),
```

`examples/gallery_dispatching_stateless.py:46`, `examples/gallery_dispatching_parameterized.py:40`, `examples/gallery_dispatching_focus.py:44` — replace `due_date_offset_range=(10.0, 18.0)` with `due_date_offset=Uniform(10.0, 18.0)`, and add `Uniform` to each file's `from simulatte.distributions import ...` line:

```python
scenario = Scenario(due_date_offset=Uniform(10.0, 18.0))
```

Apply the identical edit to the embedded `{ .run }` blocks in `docs/examples/dispatching-stateless.md`, `docs/examples/dispatching-parameterized.md`, and `docs/examples/dispatching-focus.md` (they must stay byte-identical to their scripts — `tests/test_docs_run_blocks.py` checks this).

- [ ] **Step 4: Run scenario, builders, focus, and docs-block tests**

Run: `uv run pytest tests/core/test_scenario.py tests/core/test_builders.py tests/core/test_focus.py tests/test_docs_run_blocks.py -q`
Expected: PASS.

- [ ] **Step 5: Regenerate pinned gallery `## Output` blocks affected by the true-mean rate shift**

The derived arrival rate now uses ≈0.989 (not 1.0), so seeded outputs for Scenario-derived galleries move. Regenerate each and paste its stdout into the matching doc's `## Output` block:

Run for each: `uv run python examples/gallery_benchmark_shops.py`, `gallery_dispatching_stateless.py`, `gallery_dispatching_parameterized.py`, `gallery_dispatching_focus.py`, `gallery_release_wip.py`, `gallery_release_workload.py`.
Then update the `## Output` text in `docs/examples/benchmark-shops.md`, `dispatching-stateless.md`, `dispatching-parameterized.md`, `dispatching-focus.md`, `release-wip.md`, `release-workload.md` to match. (`gallery_release_triggers.py` builds its `Router` manually with a fixed `ARRIVAL_RATE`, so its output does NOT change — leave it.)

Run: `uv run pytest tests/core/test_gallery_examples.py -q`
Expected: PASS.

- [ ] **Step 6: Full gate**

Run: `uv run pytest -q && uv run ruff check && uv run ty check src`
Expected: suite green at 99%+ coverage; ruff and ty clean.

- [ ] **Step 7: Commit**

```bash
git add src/simulatte/scenario.py tests/core/test_scenario.py tests/core/test_builders.py \
        examples/gallery_dispatching_stateless.py examples/gallery_dispatching_parameterized.py \
        examples/gallery_dispatching_focus.py docs/examples/
git commit -m "feat(scenario): per-family SKU mix with pluggable distributions and arrival process"
```

---

## Task 5: Remove `truncated_2erlang`, migrate remaining users, refresh docs

After Task 4 nothing in `src/` uses the free `truncated_2erlang` function; only an example, two docs, the skill reference, and the autodoc page still reference it. Remove it and migrate them to `TruncatedErlang` (sampling is bit-identical, so no seeded output changes).

**Files:**
- Modify: `src/simulatte/distributions.py` (remove `truncated_2erlang`)
- Modify: `tests/core/test_distributions.py` (drop the two `truncated_2erlang` tests + the import)
- Modify: `examples/gallery_release_triggers.py` + `docs/examples/release-triggers.md`
- Modify: `docs/api/utilities.md`, `skills/simulatte-dev/references/api-reference.md`, `skills/simulatte-dev/SKILL.md`, `docs/tutorials/release-control-and-dispatching.md`

- [ ] **Step 1: Migrate `gallery_release_triggers.py` and its doc block**

In `examples/gallery_release_triggers.py`: change the import to `from simulatte.distributions import pure_job_shop_routing, TruncatedErlang` and replace the service-time line:

```python
        sku_service_times={"F1": {s: TruncatedErlang(rate=SERVICE_RATE, shape=2, max_value=4.0) for s in servers}},
```

(The `TruncatedErlang` instance is callable, so the `lambda:` wrapper is dropped.) Apply the identical edit to the `{ .run }` block in `docs/examples/release-triggers.md` (byte-for-byte).

- [ ] **Step 2: Remove the `truncated_2erlang` function and its tests**

Delete the `truncated_2erlang` function from `src/simulatte/distributions.py`. In `tests/core/test_distributions.py`, remove `truncated_2erlang` from the imports and delete `test_truncated_2erlang_within_bounds` and `test_truncated_2erlang_custom_max_value` (the `TruncatedErlang` tests from Task 2 supersede them).

- [ ] **Step 3: Verify no `truncated_2erlang` references remain in code**

Run: `grep -rn "truncated_2erlang" src/ tests/ examples/ | grep -v "docs/superpowers"`
Expected: no matches.

- [ ] **Step 4: Refresh the docs and skill references**

- `docs/api/utilities.md:70`: replace `::: simulatte.distributions.truncated_2erlang` with autodoc for the new API, e.g.:

```markdown
::: simulatte.distributions.Distribution
::: simulatte.distributions.Exponential
::: simulatte.distributions.Erlang
::: simulatte.distributions.TruncatedErlang
::: simulatte.distributions.LogNormal
::: simulatte.distributions.Uniform
::: simulatte.distributions.Deterministic
```

- `skills/simulatte-dev/references/api-reference.md`: update lines ~189–190 (the `sku_service_times` example) to `TruncatedErlang(rate=2.0, shape=2, max_value=4.0)`; update line ~708 import and line ~720 (`truncated_2erlang(...) -> float` bullet) to describe the `Distribution` built-ins; and update the `Scenario(...)` field block (lines ~430, ~477+) and the prose (≈1.542857 default rate, the `service_rate=` mention) to the new `families=` / `Scenario.single` / `arrival_process` API and the new default derived rate (≈ `1/0.6412`).
- `skills/simulatte-dev/SKILL.md`: update the `Scenario(n_servers=..., service_rate=...)` prose (lines ~171, ~479) to the families / `Scenario.single` API.
- `docs/tutorials/release-control-and-dispatching.md:130`: the "6-machine pure job shop at ρ=0.90" sentence stays accurate; no numeric change needed unless a derived rate is quoted.

- [ ] **Step 5: Full gate**

Run: `uv run pytest -q && uv run ruff check && uv run ty check src`
Expected: suite green at 99%+ coverage; ruff (incl. RUF100) and ty clean.

- [ ] **Step 6: Commit**

```bash
git add src/simulatte/distributions.py tests/core/test_distributions.py \
        examples/gallery_release_triggers.py docs/ skills/
git commit -m "refactor(distributions): drop truncated_2erlang in favor of TruncatedErlang"
```

---

## Self-Review (completed by author)

**Spec coverage:**
- §4.1 `Distribution` protocol + built-ins + `Sampler` rename + true truncated mean → Tasks 1–2.
- §4.2 `SkuFamily` (routing inheritance/override, `mean_routing_length`, validation) → Task 3.
- §4.3 `Scenario` families, `arrival_process`, mix-weighted derivation, `build_router`, `Scenario.single`, presets → Task 4.
- §4.4 module layout (`Distribution`/built-ins in `distributions.py`, `Sampler` in `typing.py`, `SkuFamily` in `scenario.py`) → Tasks 1–3.
- §5 validation (positive weight, custom-routing-needs-E[L], unique family names, distribution param checks) → Tasks 2–4.
- §6 testing (analytic+empirical means, truncation cap, single/mix derivation, multi-family wiring, TWK, behavior preservation, regenerated outputs) → Tasks 2, 4.
- §7 risks (true-mean rate shift → Task 4 Step 5; `Distribution`/`Sampler` rename blast radius → Task 1 Step 3; breaking `Scenario` constructor → Task 4 Step 3; removal of `truncated_2erlang` → Task 5).

**Placeholder scan:** No "TBD"/"add validation"/"similar to" — all steps carry concrete code or exact commands. The one `# noqa: S311`-looking snippet in Task 2 is explicitly annotated as "do NOT add" with a rationale.

**Type/name consistency:** `Distribution` (protocol), `Sampler` (alias), `SkuFamily`, `TruncatedErlang`/`Erlang`/`Exponential`/`LogNormal`/`Uniform`/`Deterministic`, `arrival_process: Callable[[float], Callable[[], float]]` (default `Exponential`), `resolved_arrival_rate`, `mean_routing_length(shop_type, n_servers)`, `routing_for(shop_type)`, `Scenario.single` — used consistently across tasks and matching the spec.
