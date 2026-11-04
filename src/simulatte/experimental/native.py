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

from simulatte._wire import canonical_pack, freeze, FrozenMap
from simulatte.collectors import ShopFloorKPIs
from simulatte.execution.backend import BackendCapabilities, BackendContext, ExecutionBackend

__all__ = ["Job", "JobHandle", "ExpiredHandleError", "NativeStream", "NativeProduction"]


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


def _capacities(servers: int, values: Iterable[int] | None) -> tuple[int, ...]:
    result = (1,) * servers if values is None else tuple(values)
    if len(result) != servers or any(type(n) is not int or not 0 < n < 0xFFFFFFFF for n in result):
        raise ValueError("capacities must contain one positive uint32 integer per server")
    if sum(result) >= 0xFFFFFFFF:
        raise ValueError("total server capacity must be below UINT32_MAX")
    return result


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


class ExpiredHandleError(KeyError):
    """A completed job was deliberately released by bounded retention."""


def _retention(value: str) -> str:
    if type(value) is not str or value not in ("full", "bounded"):
        raise ValueError("retention must be full or bounded")
    return value


def _detached(value: Any) -> Any:
    if isinstance(value, FrozenMap):
        return {k: _detached(v) for k, v in value.items()}
    if type(value) is tuple:
        return [_detached(v) for v in value]
    return value


class NativeStream:
    """Owned scalar stream. ``advance(t)`` leaves events exactly at t pending.

    Register arrivals in nondecreasing order, at/after current time. ``run()``
    seals input and drains once. Full retention (default) keeps completed state;
    bounded retention expires completed handles after each advance/run boundary.
    ``capacities`` fixes the number of parallel slots per server (default: one).
    """

    def __init__(
        self,
        servers: int,
        *,
        capacities: Iterable[int] | None = None,
        wip_cap: int | None = 12,
        policy: str = "spt",
        warmup: float = 0,
        retention: str = "full",
    ):
        self._engine: Any = None
        self._lock, self._owner = RLock(), object()
        self._generation = 0
        self._retention = _retention(retention)
        self._history = hashlib.sha256()
        n, cap, policy = _configuration(servers, wip_cap, policy)
        self._capacities = _capacities(n, capacities)
        warmup = _number(warmup, "warmup")
        if warmup < 0:
            raise ValueError("warmup must be nonnegative")
        try:
            self._provider: Any = importlib.import_module("simulatte_native")
        except ModuleNotFoundError as error:
            if error.name != "simulatte_native":
                raise
            raise ImportError("Install the optional matching simulatte[native] package to use NativeStream") from error
        if getattr(self._provider, "API_VERSION", None) != 2:
            raise ImportError("Install/rebuild the matching simulatte-native provider API 2")
        parallel = any(n != 1 for n in self.capacities)
        if parallel and getattr(self._provider, "CAPACITY_VERSION", None) != 1:
            raise ImportError("Rebuild/install the matching native provider with static server capacities v1")
        self._module = self._provider.load()
        if self._retention == "bounded":
            self._engine = self._module.retention_create(n, cap, int(policy == "spt"), 1, warmup)
        else:
            self._engine = self._module.create(n, cap, int(policy == "spt"), 0, None)
        try:
            if parallel:
                configure = (
                    self._module.retention_configure_capacities
                    if self.retention == "bounded"
                    else self._module.configure_capacities
                )
                configure(self._engine, self.capacities)
            if self.retention == "full":
                self._module.enable_stock_kpis(self._engine, warmup)
        except BaseException:
            self.close()
            raise

    def _check(self) -> None:
        if self._engine is None:
            raise RuntimeError("Native stream is closed")

    @property
    def retention(self) -> str:
        return self._retention

    @property
    def capacities(self) -> tuple[int, ...]:
        return self._capacities

    @property
    def now(self) -> float:
        with self._lock:
            self._check()
            return (self._module.retention_now if self.retention == "bounded" else self._module.now)(self._engine)

    def add(self, job: Job) -> JobHandle:
        with self._lock:
            self._check()
            (self._module.retention_accepting if self.retention == "bounded" else self._module.accepting)(self._engine)
            if type(job) is not Job:
                raise TypeError("add requires a validated native Job")
            add = self._module.retention_add if self.retention == "bounded" else self._module.add
            self._generation += 1
            identity = add(self._engine, job.arrival, job.due, job.route, job.durations)
            return JobHandle(self._owner, identity)

    def advance(self, until: float) -> None:
        with self._lock:
            self._check()
            until = _number(until, "until")
            self._generation += 1
            if self.retention == "bounded":
                self._commit(self._module.retention_advance(self._engine, until))
            else:
                self._module.advance(self._engine, until)

    def inspect(self, handle: JobHandle) -> dict[str, Any]:
        with self._lock:
            self._check()
            (self._module.retention_now if self.retention == "bounded" else self._module.now)(self._engine)
            if type(handle) is not JobHandle or handle._owner is not self._owner:
                raise ValueError("foreign native job handle")
            if self.retention == "bounded":
                try:
                    return self._module.retention_inspect(self._engine, handle.id)
                except KeyError as error:
                    raise ExpiredHandleError(handle.id) from error
            return self._module.inspect(self._engine, handle.id)

    def run(self) -> None:
        with self._lock:
            self._check()
            self._generation += 1
            if self.retention == "bounded":
                self._commit(self._module.retention_run(self._engine))
            else:
                self._module.run(self._engine)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._check()
            if self.retention == "bounded":
                start, end, closed, values = self._module.retention_snapshot(self._engine)
                return dict(
                    window=(start, end, closed),
                    scalars={
                        "shopfloor/" + kpi.name: value
                        for kpi, value in zip(ShopFloorKPIs.kpis, values)
                        if value is not None
                    },
                )
            return self._module.stock_kpis(self._engine)

    def result(self) -> dict[str, Any]:
        """Detached trajectories and legacy engine totals after successful run."""
        with self._lock:
            self._check()
            if self.retention == "bounded":
                raise RuntimeError("Full trajectories require retention='full'; use snapshot() for bounded streams")
            return self._module.result(self._engine)

    def state_projection(self) -> dict[str, Any]:
        """Detached versioned trajectory material, stock snapshot and native hash.

        Observes one owner boundary without advancing. Requires companion projection
        v1; full/bounded trajectory material has different encoding identities.
        The trajectory alone does not commit to future Python inputs or authorize
        reuse, reproduction or restoration.
        """
        with self._lock:
            self._check()
            project = getattr(self._provider, "project_state", None)
            if not callable(project):
                raise ImportError("Rebuild/install the matching native provider with state projection v1")
            value = project(
                self._engine,
                retention=self.retention,
                history=self._history.digest() if self.retention == "bounded" else None,
            )
            if type(value) is not dict or value.get("schema") != "native-stream-projection-v1":
                raise RuntimeError("Unsupported native stream projection")
            return value

    def state_bytes(self) -> bytes:
        with self._lock:
            self._check()
            if self.retention == "bounded":
                raw = self._module.retention_state(self._engine)
                return (
                    (b"NRST0002" if raw.startswith(b"NBST0002") else b"NRST0001")
                    + self._history.digest()
                    + len(raw).to_bytes(8, "little")
                    + raw
                    + canonical_pack(freeze(self.snapshot()))
                )
            return self._module.state_bytes(self._engine)

    def _commit(self, records: bytes) -> None:
        try:
            self._history.update(records)
        except BaseException:
            self.close()
            raise

    def storage_stats(self) -> dict[str, int]:
        """Detached high-water storage counters; excludes Python/OS allocator overhead."""
        with self._lock:
            self._check()
            return (self._module.retention_stats if self.retention == "bounded" else self._module.storage_stats)(
                self._engine
            )

    def verify(self) -> None:
        """Recheck installed bytes/manifest/ABI and actual loaded image locations."""
        with self._lock:
            self._provider.verify()

    def close(self) -> None:
        with self._lock:
            if self._engine is not None:
                self._generation += 1
                engine, self._engine = self._engine, None
                try:
                    (self._module.retention_close if self.retention == "bounded" else self._module.close)(engine)
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
    Bounded retention reclaims completed native execution data; lazy input is
    required to bound the whole workload. Full retention remains the default.
    """

    capabilities = BackendCapabilities("native-production", "native-production-v1")
    kpis = ShopFloorKPIs.kpis

    def __init__(
        self,
        servers: int,
        jobs: Iterable[Job] = (),
        *,
        capacities: Iterable[int] | None = None,
        wip_cap: int | None = 12,
        policy: str = "spt",
        input_state: Callable[[], Any] | None = None,
        retention: str = "full",
    ):
        _configuration(servers, wip_cap, policy)
        self._capacities = _capacities(servers, capacities)
        self._retention = _retention(retention)
        schema = "native-production" + ("-capacity" if any(n != 1 for n in self.capacities) else "")
        self.capabilities = BackendCapabilities(
            "native-production", schema + ("-bounded-v1" if retention == "bounded" else "-v1")
        )
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

    @property
    def capacities(self) -> tuple[int, ...]:
        return self._capacities

    def prepare(self, context: BackendContext) -> None:
        if self._closed or self._stream is not None:
            raise RuntimeError("Native backend is single-use")
        self._context = context
        self._stream = NativeStream(
            self.servers,
            capacities=self.capacities,
            wip_cap=self.wip_cap,
            policy=self.policy,
            warmup=context.stopping.warmup,
            retention=self._retention,
        )
        for job in self._jobs:
            self._stream.add(job)
        self._jobs = ()

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

    def storage_stats(self) -> dict[str, int]:
        """Live, detached native storage diagnostics; not part of semantic state."""
        self._synchronized()
        return self._live().storage_stats()

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
        prefix = b"NRST" if self._retention == "bounded" else b"NSTA"
        version = b"0002" if any(n != 1 for n in self.capacities) else b"0001"
        if not state.startswith(prefix + version):
            raise RuntimeError("Unsupported native state encoding")
        inputs = None if self._input_state is None else freeze(self._input_state())
        return hashlib.sha256(
            canonical_pack(
                freeze(
                    dict(
                        schema=self.capabilities.state_schema,
                        native=hashlib.sha256(state).hexdigest(),
                        inputs=inputs,
                    )
                )
            )
        ).hexdigest()

    def state_projection(self) -> dict[str, Any]:
        """Observe versioned native material/reporting plus the full state authority.

        Available only while live at a synchronized managed boundary. The provider
        owns native serialization; callers need no binary-layout or owner access.
        ``input_state`` must be pure, as for the existing authoritative digest.
        Model-specific action-history composition stays with the model.
        """
        stream = self._live()
        with stream._lock:
            self._synchronized()
            generation = stream._generation
            inputs = None if self._input_state is None else freeze(self._input_state())
            value = stream.state_projection()
            after = None if self._input_state is None else freeze(self._input_state())
            if stream._generation != generation or canonical_pack(inputs) != canonical_pack(after):
                raise ValueError("Impure input_state during native projection")
            self._synchronized()
            assert self._context is not None
            preimage = dict(schema=self.capabilities.state_schema, native=value["native_sha256"], inputs=inputs)
            authority = dict(
                schema=self.capabilities.state_schema,
                digest=hashlib.sha256(canonical_pack(freeze(preimage))).hexdigest(),
            )
            result = dict(
                schema="native-production-projection-v1",
                trajectory=value["trajectory"],
                reporting=dict(
                    configuration=self._context.stopping.to_dict(),
                    catalog=[
                        dict(name=k.name, unit=k.unit, observation=k.observation, aggregation=k.aggregation)
                        for k in self.kpis
                    ],
                    snapshot=value["snapshot"],
                ),
                scope=dict(
                    backend=self.capabilities.name, scope=self._context.scope, time_unit=self._context.env.time_unit
                ),
                authority=authority,
                authority_preimage=preimage,
                native_bytes=value["native_bytes"],
            )
            return _detached(freeze(result))

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
