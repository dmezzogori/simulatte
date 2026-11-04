"""Opt-in managed execution backend contract, initially scalar/Horizon only.

Models register during build with Environment.attach_execution_backend(). The
managed stopping loop owns advancement/finalization; Environment owns close.
State/scalar queries must be pure. Backends never replace environment methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
import re
from typing import TYPE_CHECKING, ClassVar
from types import MappingProxyType

from simulatte.events import Deltas
from simulatte.kpi import Collector, KPI
from simulatte.stopping import Horizon
from .backend_events import BackendState, BackendStateCaptured

if TYPE_CHECKING:
    from simulatte.environment import Environment
    from .native_assets import VerifiedNativeBundle


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    name: str
    state_schema: str

    def __post_init__(self):
        for value in (self.name, self.state_schema):
            if type(value) is not str or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", value) is None:
                raise ValueError("Backend name and state schema must be bounded identifiers")

    def to_dict(self):
        return {
            "schema": 1,
            "name": self.name,
            "state_schema": self.state_schema,
            "trace_levels": ["kpi"],
            "stopping": ["horizon"],
            "state_projection": "opaque_digest",
        }


@dataclass(frozen=True, slots=True)
class BackendContext:
    env: Environment
    stopping: Horizon
    scope: str
    native_assets: Mapping[str, VerifiedNativeBundle] = field(default_factory=lambda: MappingProxyType({}))


class ExecutionBackend(ABC):
    """One owned backend per managed environment. Close must release resources
    after any partially failed prepare and must not advance simulation time.
    finish runs only on successful stopping; queries stay valid until close.
    State hashes are schema-scoped SHA-256 lower-case hex strings, excluding
    volatile host/timing/allocator/observer details.
    """

    capabilities: BackendCapabilities
    _simulatte_backend_claimed: bool = False
    kpis: ClassVar[tuple[KPI, ...]] = ()

    @abstractmethod
    def prepare(self, context: BackendContext) -> None: ...

    @abstractmethod
    def advance(self, until: float) -> None: ...

    @abstractmethod
    def kpi_values(self) -> Mapping[str, float | None]: ...

    @abstractmethod
    def state_digest(self) -> str: ...

    def finish(self) -> None:
        """Seal successful results/write deterministic artifacts; no advancement."""

    @abstractmethod
    def close(self) -> None: ...


class _BackendKPIs(Collector):
    def __init__(self, scope, binding):
        self.binding = binding
        super().__init__(scope)

    def scalar_values(self):
        return self.binding.kpi_values()


class BackendBinding:
    """Framework-owned lifecycle and projection, not a backend implementation."""

    def __init__(self, env, backend, context):
        self.env, self.backend, self.context = env, backend, context
        self.capabilities = backend.capabilities
        self.closed = self.finished = False
        self._values = None
        self.scope = BackendState(env, context.scope, backend.capabilities)

    def prepare(self):
        self.backend.prepare(self.context)
        if self.closed or self.env._closed:
            raise RuntimeError("Backend closed during preparation")
        collector = type("BackendKPIs", (_BackendKPIs,), {"kpis": self.backend.kpis})
        collector(self.scope, self).attach(self.env)
        self.env.on_activate(self.activate)

    def activate(self):
        self.validate_stopping(self.context.stopping)
        if self.env.warmup != self.context.stopping.warmup:
            raise ValueError("Backend warm-up differs from stopping policy")
        self.scope.state_digest = self._digest()

    def validate_stopping(self, policy):
        if policy != self.context.stopping:
            raise ValueError("Backend stopping policy cannot change after registration")

    def _digest(self):
        if self.backend.capabilities != self.capabilities:
            raise ValueError("Backend capabilities cannot change after registration")
        value = self.backend.state_digest()
        if type(value) is not str or re.fullmatch("[0-9a-f]{64}", value) is None:
            raise ValueError("Backend state digest must be SHA-256 lower-case hex")
        return value

    def advance(self, until):
        if self.closed or self.finished:
            raise RuntimeError("Backend is closed or finalized")
        self.backend.advance(until)

    def kpi_values(self):
        if self._values is not None:
            return dict(self._values)
        if self.closed:
            raise RuntimeError("Backend closed after failure")
        return self.backend.kpi_values()

    def finish(self):
        if self.closed or self.finished:
            raise RuntimeError("Backend is closed or finalized")
        self.backend.finish()
        if self.closed:
            raise RuntimeError("Backend closed during finalization")
        digest = self._digest()
        self._values = dict(self.backend.kpi_values())
        if digest != self.scope.state_digest:
            self.scope.state_digest = digest
            if self.env.wants(BackendStateCaptured):
                self.env.emit(
                    BackendStateCaptured(
                        scope=self.scope.id,
                        state_digest=digest,
                        deltas=Deltas.build().set(self.scope.id, "state_digest", digest).done(),
                    )
                )
        self.finished = True

    def close(self):
        if self.closed:
            return
        try:
            if self._values is None and not self.env._run_failed:
                self._values = dict(self.backend.kpi_values())
        finally:
            self.closed = True
            self.backend.close()


def bind_backend(env, backend, scope):
    if not isinstance(backend, ExecutionBackend) or type(backend.capabilities) is not BackendCapabilities:
        raise TypeError("Expected an ExecutionBackend with BackendCapabilities")
    if backend._simulatte_backend_claimed:
        raise RuntimeError("Backend instances are single-use and cannot share environments")
    if env._closed or env._activation_started or env._execution_backend is not None:
        raise RuntimeError("Backend registration requires a fresh open environment")
    if type(env._execution_policy) is not Horizon:
        raise ValueError("unsupported backend stopping policy: Horizon required")
    if env.entities.live() or env.collectors or env._queue:
        raise ValueError("unsupported nonempty backend environment")
    if env.layout is not None and env.layout.to_dict() != {"schema_version": "1.0"}:
        raise ValueError("unsupported backend layout")
    if any(recorder._level != "kpi" for recorder in env._recorders):
        raise ValueError("unsupported full tracing for scalar backend")
    if env.warmup != env._execution_policy.warmup:
        raise ValueError("unsupported backend warm-up mismatch")
    context = BackendContext(env, env._execution_policy, scope, MappingProxyType(dict(env._native_assets)))
    object.__setattr__(backend, "_simulatte_backend_claimed", True)
    binding = BackendBinding(env, backend, context)
    env._execution_backend = binding
    try:
        binding.prepare()
    except BaseException:
        try:
            binding.close()
        except BaseException:
            pass  # Retain the preparation error; resource close was attempted.
        raise
