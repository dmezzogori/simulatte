"""Serializable stopping policies. Population execution is opt-in."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from typing import Any

from simulatte._wire import MAX_SAFE_INT


def _finite(value: Any, name: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be finite")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} must be finite") from error
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


@dataclass(frozen=True, slots=True)
class Horizon:
    until: float
    warmup: float = 0.0

    def __post_init__(self):
        until, warmup = _finite(self.until, "horizon"), _finite(self.warmup, "warmup")
        if not 0 <= warmup < until:
            raise ValueError("Require horizon > warmup >= 0")
        object.__setattr__(self, "until", until)
        object.__setattr__(self, "warmup", warmup)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "horizon", "horizon": self.until, "warmup": self.warmup}


@dataclass(frozen=True, slots=True, kw_only=True)
class FinitePopulation:
    safety_limit: float
    arrivals: int | None = None
    cutoff: float | None = None

    def __post_init__(self):
        if (self.arrivals is None) == (self.cutoff is None):
            raise ValueError("Specify exactly one of arrivals or cutoff")
        safety = _finite(self.safety_limit, "safety_limit")
        if safety <= 0:
            raise ValueError("safety_limit must be positive")
        object.__setattr__(self, "safety_limit", safety)
        if self.arrivals is not None and (type(self.arrivals) is not int or not 0 <= self.arrivals <= MAX_SAFE_INT):
            raise ValueError("arrivals must be a nonnegative safe integer")
        if self.cutoff is not None:
            cutoff = _finite(self.cutoff, "cutoff")
            if not 0 <= cutoff < safety:
                raise ValueError("Require safety_limit > cutoff >= 0")
            object.__setattr__(self, "cutoff", cutoff)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "finite_population",
            "arrivals": self.arrivals,
            "cutoff": self.cutoff,
            "safety_limit": self.safety_limit,
        }


def stopping_from_dict(data: Mapping[str, Any]) -> Horizon | FinitePopulation:
    if data.get("type") == "horizon" and set(data) == {"type", "horizon", "warmup"}:
        return Horizon(data["horizon"], data["warmup"])
    if data.get("type") == "finite_population" and set(data) == {"type", "arrivals", "cutoff", "safety_limit"}:
        return FinitePopulation(arrivals=data["arrivals"], cutoff=data["cutoff"], safety_limit=data["safety_limit"])
    raise ValueError("Unknown or malformed stopping policy")


class StoppingError(RuntimeError):
    """A finite run failed to reach its declared stopping condition."""


@dataclass(frozen=True, slots=True)
class StopResult:
    policy: Horizon | FinitePopulation
    reason: str
    actual_end: float


class Population:
    """Explicit population accounting owned by the model, not an observer.

    Bracket scheduled arrivals with pending/unpending, admit a key when work
    enters, and complete it after its final domain transition. Keys cannot be
    reused. Admission closes at the count/time cutoff even with work in flight.
    """

    def __init__(self, env: Any, policy: FinitePopulation | None = None) -> None:
        import simpy

        policy = policy if policy is not None else env._execution_policy
        if type(policy) is not FinitePopulation:
            raise ValueError("Population requires a finite stopping policy")
        if env.activated or env._population is not None:
            raise ValueError("Bind one Population before activation")
        self.env = env
        self.policy = policy
        self.closed = simpy.Event(env)
        self._admitted: set[str] = set()
        self._active: set[str] = set()
        self._pending: set[str] = set()
        env._population = self
        if policy.arrivals == 0 or policy.cutoff == 0:
            self._close()

    def _close(self) -> None:
        if not self.closed.triggered:
            self.closed.succeed()

    @property
    def open(self) -> bool:
        if self.policy.cutoff is not None and self.env.now >= self.policy.cutoff:
            self._close()
        return not self.closed.triggered

    @staticmethod
    def _key(key: str) -> None:
        if type(key) is not str or not key:
            raise ValueError("Population key must be nonempty text")

    def admit(self, key: str) -> bool:
        self._key(key)
        if key in self._admitted:
            raise ValueError("Population key was already admitted")
        if not self.open:
            return False
        self._admitted.add(key)
        self._active.add(key)
        if self.policy.arrivals is not None and len(self._admitted) >= self.policy.arrivals:
            self._close()
        return True

    def complete(self, key: str) -> None:
        self._key(key)
        if key not in self._active:
            raise ValueError("Population key is not active")
        self._active.remove(key)

    def pending(self, key: str) -> None:
        self._key(key)
        if key in self._pending:
            raise ValueError("Arrival key is already pending")
        self._pending.add(key)

    def unpending(self, key: str) -> None:
        self._key(key)
        if key not in self._pending:
            raise ValueError("Arrival key is not pending")
        self._pending.remove(key)

    @property
    def drained(self) -> bool:
        return not self.open and not self._active and not self._pending


def execute_until(env: Any, policy: Horizon | FinitePopulation, control: Any = None) -> StopResult:
    """Advance one step at a time with independent wall-time cancellation checks.

    Control is a zero-argument callable that raises on cancellation/deadline.
    It cannot stop a blocked native call; the supervisor's process escalation can.
    """
    from datetime import UTC, datetime
    import time

    import simpy
    from simulatte._wire import freeze

    if type(policy) not in (Horizon, FinitePopulation):
        raise ValueError("Unsupported stopping policy")
    if env.activated:
        raise ValueError("Execution requires a fresh environment")
    population = env._population
    if isinstance(policy, FinitePopulation) and (population is None or population.policy != policy):
        raise ValueError("Finite execution requires its matching Population")
    env.configure_kpis(warmup=policy.warmup if isinstance(policy, Horizon) else 0.0)
    env._stopping_policy = freeze(policy.to_dict())
    env._wall_clock_start = datetime.now(UTC).isoformat()
    reached = False
    owned = set()

    def boundary(at: float, priority: int, callback: Any) -> None:
        event = simpy.Event(env)
        event._ok = True
        event._value = None
        event.callbacks.append(lambda _: callback())
        owned.add(event)
        env.schedule(event, priority=priority, delay=at - env.now)

    def end() -> None:
        nonlocal reached
        reached = True

    started = time.perf_counter()
    try:
        if control is not None:
            control()
        env.activate()
        if isinstance(policy, Horizon):
            boundary(policy.until, 0, end)
        else:
            if policy.cutoff is not None and policy.cutoff > env.now:
                boundary(policy.cutoff, -1, population._close)
            boundary(policy.safety_limit, 2, end)
        while True:
            if control is not None:
                control()
            if isinstance(policy, FinitePopulation):
                if population.drained:
                    return StopResult(policy, "drained", float(env.now))
                if reached:
                    raise StoppingError("safety_limit: population did not drain")
                # A future time cutoff is meaningful work even in an empty model.
                has_cutoff = policy.cutoff is not None and env.now < policy.cutoff
                if (
                    not has_cutoff
                    and len(env._queue) <= len(owned)
                    and not any(event not in owned for _, _, _, event in env._queue)
                ):
                    raise StoppingError("population_incomplete: event queue exhausted before drain")
            elif reached:
                return StopResult(policy, "horizon", float(env.now))
            env.step()
    except BaseException:
        env._run_failed = True
        raise
    finally:
        env._run_seconds += time.perf_counter() - started
