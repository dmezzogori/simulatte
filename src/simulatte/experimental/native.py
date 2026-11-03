"""Optional experimental native production stream and managed scalar backend.

Install the matching ``simulatte[native]`` extra on GIL-enabled CPython/macOS or
Linux. Importing this module does not load native code. No fallback changes an
explicitly selected native simulation into a Python simulation.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
import hashlib
import importlib
import math
from threading import RLock
from typing import Any

from simulatte._wire import canonical_pack, freeze
from simulatte.collectors import ShopFloorKPIs
from simulatte.execution.backend import BackendCapabilities, BackendContext, ExecutionBackend

__all__ = ["Job", "JobHandle", "NativeStream", "NativeProduction"]


def _number(value: float, name: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(name + " must be a finite primitive number")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError(name + " must be a finite primitive number") from error
    if not math.isfinite(result):
        raise ValueError(name + " must be a finite primitive number")
    return result


def _count(value: int, name: str) -> int:
    if type(value) is not int or not 0 < value < 0xFFFFFFFF:
        raise ValueError(name + " must be a positive uint32 count")
    return value


def _configuration(servers: int, wip_cap: int | None, policy: str) -> tuple[int, int, str]:
    servers = _count(servers, "servers")
    cap = 0 if wip_cap is None else _count(wip_cap, "wip_cap")
    if type(policy) is not str or policy not in ("fifo", "spt"):
        raise ValueError("policy must be fifo or spt")
    return servers, cap, policy


@dataclass(frozen=True, slots=True)
class Job:
    """Copied job input. Route indices are zero-based and cannot repeat."""

    arrival: float
    route: tuple[int, ...]
    durations: tuple[float, ...]
    due: float

    def __post_init__(self):
        arrival, due = _number(self.arrival, "arrival"), _number(self.due, "due")
        route, durations = tuple(self.route), tuple(_number(v, "duration") for v in self.durations)
        if arrival < 0 or not route or len(route) != len(durations) or any(v <= 0 for v in durations):
            raise ValueError("Job requires nonnegative arrival and matching nonempty positive durations")
        if any(type(v) is not int or not 0 <= v < 0xFFFFFFFF for v in route) or len(set(route)) != len(route):
            raise ValueError("Route requires unique uint32 server indices")
        for name, value in (("arrival", arrival), ("due", due), ("route", route), ("durations", durations)):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class JobHandle:
    """Opaque owner-scoped identity, never a pointer into native storage."""

    _owner: object = field(repr=False)
    id: int


class NativeStream:
    """Owned scalar stream. ``advance(t)`` leaves events exactly at t pending.

    Register arrivals in nondecreasing order, at/after current time. ``run()``
    seals input and drains once. Completed state is retained until ``close()``.
    """

    def __init__(self, servers: int, *, wip_cap: int | None = 12, policy: str = "spt", warmup: float = 0):
        self._engine: Any = None
        self._lock, self._owner = RLock(), object()
        n, cap, policy = _configuration(servers, wip_cap, policy)
        warmup = _number(warmup, "warmup")
        if warmup < 0:
            raise ValueError("warmup must be nonnegative")
        try:
            self._provider: Any = importlib.import_module("simulatte_native")
        except ModuleNotFoundError as error:
            if error.name != "simulatte_native":
                raise
            raise ImportError("Install the optional matching simulatte[native] package to use NativeStream") from error
        self._module = self._provider.load()
        self._engine = self._module.create(n, cap, int(policy == "spt"), 0, None)
        try:
            self._module.enable_stock_kpis(self._engine, warmup)
        except BaseException:
            self.close()
            raise

    def _check(self) -> None:
        if self._engine is None:
            raise RuntimeError("Native stream is closed")

    @property
    def now(self) -> float:
        with self._lock:
            self._check()
            return self._module.now(self._engine)

    def add(self, job: Job) -> JobHandle:
        with self._lock:
            self._check()
            self._module.accepting(self._engine)
            if type(job) is not Job:
                raise TypeError("add requires a validated native Job")
            identity = self._module.add(self._engine, job.arrival, job.due, job.route, job.durations)
            return JobHandle(self._owner, identity)

    def advance(self, until: float) -> None:
        with self._lock:
            self._check()
            self._module.advance(self._engine, _number(until, "until"))

    def inspect(self, handle: JobHandle) -> dict[str, Any]:
        with self._lock:
            self._check()
            self._module.now(self._engine)
            if type(handle) is not JobHandle or handle._owner is not self._owner:
                raise ValueError("foreign native job handle")
            return self._module.inspect(self._engine, handle.id)

    def run(self) -> None:
        with self._lock:
            self._check()
            self._module.run(self._engine)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._check()
            return self._module.stock_kpis(self._engine)

    def result(self) -> dict[str, Any]:
        """Detached trajectories and legacy engine totals after successful run."""
        with self._lock:
            self._check()
            return self._module.result(self._engine)

    def state_bytes(self) -> bytes:
        with self._lock:
            self._check()
            return self._module.state_bytes(self._engine)

    def verify(self) -> None:
        """Recheck installed bytes/manifest/ABI and actual loaded image locations."""
        with self._lock:
            self._provider.verify()

    def close(self) -> None:
        with self._lock:
            if self._engine is not None:
                engine, self._engine = self._engine, None
                try:
                    self._module.close(engine)
                finally:
                    self._provider.verify()

    def __enter__(self) -> NativeStream:
        self._check()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class NativeProduction(ExecutionBackend):
    """Managed Horizon backend, fixed jobs or explicitly fingerprinted Python input.

    Attach with ``env.attach_execution_backend(backend)`` during build. Dynamic
    ``add`` is allowed only with a pure ``input_state`` callback covering future
    Python inputs/controller state. Fixed jobs are copied and registered eagerly.
    """

    capabilities = BackendCapabilities("native-production", "native-production-v1")
    kpis = ShopFloorKPIs.kpis

    def __init__(
        self,
        servers: int,
        jobs: Iterable[Job] = (),
        *,
        wip_cap: int | None = 12,
        policy: str = "spt",
        input_state: Callable[[], Any] | None = None,
    ):
        _configuration(servers, wip_cap, policy)
        self.servers, self.wip_cap, self.policy = servers, wip_cap, policy
        self._jobs = tuple(jobs)
        if any(type(job) is not Job for job in self._jobs):
            raise TypeError("jobs must contain validated native Jobs")
        if any(any(v >= servers for v in job.route) for job in self._jobs):
            raise ValueError("Job server outside this native production backend")
        if any(a.arrival > b.arrival for a, b in zip(self._jobs, self._jobs[1:])):
            raise ValueError("Job arrivals must be nondecreasing")
        if input_state is not None and not callable(input_state):
            raise TypeError("input_state must be a pure data callback")
        self._input_state = input_state
        self._stream: NativeStream | None = None
        self._context: BackendContext | None = None
        self._snapshot: dict[str, Any] | None = None
        self._state: str | None = None
        self._closed = self._finished = False

    def prepare(self, context: BackendContext) -> None:
        if self._closed or self._stream is not None:
            raise RuntimeError("Native backend is single-use")
        self._context = context
        self._stream = NativeStream(
            self.servers, wip_cap=self.wip_cap, policy=self.policy, warmup=context.stopping.warmup
        )
        for job in self._jobs:
            self._stream.add(job)

    def _live(self) -> NativeStream:
        if self._closed or self._finished or self._stream is None:
            raise RuntimeError("Native backend is not prepared, or is finalized/closed")
        return self._stream

    def add(self, job: Job) -> JobHandle:
        stream = self._live()
        if self._input_state is None:
            raise ValueError("Dynamic native admission requires an input_state callback for future Python state")
        return stream.add(job)

    def inspect(self, handle: JobHandle) -> dict[str, Any]:
        return self._live().inspect(handle)

    def advance(self, until: float) -> None:
        self._live().advance(until)

    def kpi_values(self) -> Mapping[str, float | None]:
        if self._snapshot is None:
            self._synchronized()
            snapshot = self._live().snapshot()
        else:
            snapshot = self._snapshot
        return {key.partition("/")[2]: value for key, value in snapshot["scalars"].items()}

    def _synchronized(self) -> None:
        stream = self._live()
        if self._context is None or stream.now != self._context.env.now:
            raise RuntimeError("Native observation requires a synchronized Python boundary")

    def state_digest(self) -> str:
        if self._state is not None:
            return self._state
        self._synchronized()
        state = self._live().state_bytes()
        if not state.startswith(b"NSTA0001"):
            raise RuntimeError("Unsupported native state encoding")
        inputs = None if self._input_state is None else freeze(self._input_state())
        return hashlib.sha256(
            canonical_pack(
                freeze(
                    dict(
                        schema="native-production-v1",
                        native=hashlib.sha256(state).hexdigest(),
                        inputs=inputs,
                    )
                )
            )
        ).hexdigest()

    def finish(self) -> None:
        stream = self._live()
        self._state = self.state_digest()
        self._snapshot = stream.snapshot()
        stream.verify()
        self._finished = True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._stream is not None:
            self._stream.close()
