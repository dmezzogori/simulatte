"""Pure preparation coordinator, shared by the eventual Environment/Layout facade.

Hooks perform compilation and binding; this module never schedules simulation work.
Target attachment must be reported by the host, whose registry remains authoritative.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from typing import Any

from .dependencies import MISSING, DependencyClosure, evaluate_query
from .errors import LayoutError, LayoutNotReady
from .layers import FileLayer, ResolvedValues, resolve_layers
from .refs import GraphRef, NodeRef, PortRef, ValueRef
from .stages import BindContext, StageContext, StageSpec, ordered_stages


class PreparationState(StrEnum):
    DECLARING = "declaring"
    RESOLVING = "resolving"
    VALIDATING = "validating"
    BINDING = "binding"
    FINALIZED = "finalized"
    FAILED = "failed"


class PreparationCoordinator:
    """Enforce declaration, stage, graph-barrier and closure lifetime contracts.

    ``network_builder(values, closure)`` returns a compiled result; ``graph_of``
    explicitly extracts its concrete graph. ``validate(coordinator)`` may return
    the final presentation-filled ResolvedValues. ``bind(coordinator)`` runs
    built-in binders before custom binders. ``finalize(coordinator)`` builds the
    published object after closure sealing. Host code supplies ``node_resolver``
    and records concrete endpoint/connectivity data through ``closure.bind``.
    """

    def __init__(
        self,
        env: Any,
        *,
        file_layer: FileLayer | None = None,
        owner: object | None = None,
        host: Any = None,
        network_builder: Callable[[ResolvedValues, DependencyClosure], Any] | None = None,
        graph_of: Callable[[Any], Any] | None = None,
        node_resolver: Callable[[NodeRef | PortRef], Any] | None = None,
        node_identity: Callable[[Any], Any] | None = None,
        validate: Callable[[PreparationCoordinator], ResolvedValues | None] | None = None,
        bind: Callable[[PreparationCoordinator], None] | None = None,
        finalize: Callable[[PreparationCoordinator], Any] | None = None,
        target_provider: Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        self.env = env
        self.owner = self if owner is None else owner
        self.host = self if host is None else host
        self.file_layer = file_layer or FileLayer({"schema_version": "1.0"})
        self.closure = DependencyClosure()
        self.state = PreparationState.DECLARING
        self.active_context: StageContext | None = None
        self._network_builder = network_builder
        self._graph_of = graph_of or (lambda result: result)
        self._node_resolver = node_resolver
        self._node_identity = node_identity
        self._validate = validate
        self._bind = bind
        self._finalize = finalize
        self._target_provider = target_provider
        self._stages: list[StageSpec] = []
        self._binders: list[StageSpec] = []
        self._code: list[tuple[str, Mapping[str, Any]]] = []
        self._targets: dict[str, str] = {}
        self._owners: dict[tuple[str, ...], str] = {}
        self._frozen: set[tuple[str, ...]] = set()
        self._stage = "declare"
        self._barrier = False
        self.network: Any = None
        self._result: Any = None
        self.values = self._resolve()
        self._network_values = self.values

    def stage_records(self) -> dict[str, Any]:
        """Copy registration-ordered author records for the saved layer bundle."""
        return {
            "stages": [spec.to_dict() for spec in self._stages],
            "binders": [spec.to_dict() for spec in self._binders],
        }

    def _resolve(self, *, final: bool = False) -> ResolvedValues:
        targets = self._target_provider() if final and self._target_provider else self._targets
        return resolve_layers(self._code, self.file_layer, targets=targets, final=final)

    def ref(self, *path: str) -> ValueRef:
        return ValueRef(self.owner, tuple(path))

    def graph(self) -> GraphRef:
        return GraphRef(self.owner)

    def _mutable(self, *, registration: bool = False) -> None:
        allowed = (
            (PreparationState.DECLARING,) if registration else (PreparationState.DECLARING, PreparationState.RESOLVING)
        )
        if self.state not in allowed:
            raise LayoutError(f"layout mutation prohibited while {self.state.value}")

    def _owner(self, ref: ValueRef | GraphRef | NodeRef | PortRef) -> None:
        if ref.owner is not self.owner:
            raise LayoutError("foreign layout reference")

    def stage(self, name: str, *, reads: Sequence[ValueRef | GraphRef], after: Sequence[str] = ()) -> Callable:
        self._mutable(registration=True)

        def register(callback: Callable[[StageContext], None]) -> Callable:
            self._mutable(registration=True)
            spec = StageSpec(name, callback, tuple(reads), tuple(after))
            if any(s.name == name for s in self._stages):
                raise LayoutError(f"duplicate stage id: {name}")
            self._stages.append(spec)
            return callback

        return register

    def on_bind(
        self, name: str, callback: Callable[[BindContext], None], *, reads: Sequence[ValueRef | GraphRef] = ()
    ) -> None:
        self._mutable(registration=True)
        spec = StageSpec(name, callback, tuple(reads))
        if any(s.name == name for s in self._binders):
            raise LayoutError(f"duplicate binder id: {name}")
        self._binders.append(spec)

    def attach_target(self, entity_id: str, kind: str) -> None:
        """Report attachment (or a recognized graph definition), before publication."""
        self._mutable()
        path = ("placements", entity_id)
        if entity_id in self._targets:
            raise LayoutError(f"target already attached: {entity_id}")
        for query in self.closure.queries:
            if (
                query.origin == "model"
                and tuple(query.descriptor["path"]) in ((), ("placements",))
                and query.descriptor["kind"] in ("value", "membership", "selection")
                and entity_id not in query.targets
            ):
                raise LayoutError("placement collection membership is frozen")
        old_targets = dict(self._targets)
        self._targets[entity_id] = kind
        try:
            candidate = self._resolve()
            self._check_changes(candidate)
        except BaseException:
            self._targets = old_targets
            raise
        self._owners.setdefault(path, self._stage)
        self.values = candidate

    def declare(self, patch: Mapping[str, Any]) -> None:
        """Copy a validated code patch and atomically check stage-owned freezes."""
        self._mutable()
        data = FileLayer({"schema_version": "1.0", **patch}).data
        paths: list[tuple[str, ...]] = [("placements", key) for key in data.get("placements", {})]
        paths.extend((key,) for key in ("grid", "network") if key in data)
        for path in paths:
            if path in self._frozen:
                raise LayoutError(f"target/specification is frozen: {path}")
        if self._barrier and any(key in data for key in ("network", "grid", "overrides")):
            raise LayoutError("late-network-edit after graph barrier")
        self._code.append((self._stage, data))
        try:
            candidate = self._resolve()
            self._check_changes(candidate)
        except BaseException:
            self._code.pop()
            raise
        for path in paths:
            self._owners.setdefault(path, self._stage)
        self.values = candidate

    def _network_view(self, values: ResolvedValues) -> ResolvedValues:
        """Compiler candidates are actual attachments/definitions, never pending code ids."""
        targets = self._target_provider() if self._target_provider else self._targets
        return resolve_layers(values.bundle.code_stages, values.bundle.file_layer, targets=targets, final=True)

    def _check_changes(self, candidate: ResolvedValues) -> None:
        network_values = self._network_view(candidate) if self._barrier else None
        if network_values is not None:
            for predicate in ("obstacle", "connector"):
                descriptor = {"kind": "selection", "path": ["placements"], "predicate": predicate}
                before = evaluate_query(self._network_values, descriptor)
                after = evaluate_query(network_values, descriptor)
                if after["selected"] != before["selected"]:
                    raise LayoutError("late-network-edit: new obstacle or connector")
        changed = self.closure.changed(candidate, network_values=network_values, allow_network_extensions=True)
        if changed:
            raise LayoutError(f"physical dependency is frozen: {changed}")
        if network_values is not None:
            self._network_values = network_values

    def _freeze_stage(self) -> None:
        self._frozen.update(path for path, owner in self._owners.items() if owner == self._stage)
        # File-only physical specifications belong to stage zero as well.
        self._frozen.update(
            (key,) for key in ("grid", "network") if self.values.at((key,), explicit=True).state == "present"
        )

    def check_grant(self, context: StageContext, ref: ValueRef) -> None:
        self._owner(ref)
        if not any(
            isinstance(grant, ValueRef) and ref.path[: len(grant.path)] == grant.path for grant in context.spec.reads
        ):
            raise LayoutError(f"undeclared physical read: {ref.path}")
        if len(ref.path) >= 2 and ref.path[0] == "placements":
            if ("placements", ref.path[1]) not in self._frozen or ref.path[1] not in self.values.targets:
                raise LayoutError(f"physical read requires a prior-stage target: {ref.path}")

    def read_physical(self, context: StageContext, ref: ValueRef, *, default: Any = MISSING) -> Any:
        self.check_grant(context, ref)
        return self.closure.read(self.values, ref.path, default=default)

    def physical(self, ref: ValueRef, *, default: Any = MISSING) -> Any:
        self._owner(ref)
        if self.active_context is not None:
            return self.active_context.physical(ref, default=default)
        if self.state is PreparationState.FINALIZED:
            return self.closure.read(self.values, ref.path, default=default)
        raise LayoutNotReady(f"physical read unavailable while {self.state.value}")

    def read_graph(self, context: StageContext, ref: GraphRef) -> Any:
        self._owner(ref)
        if ref not in context.spec.reads:
            raise LayoutError("undeclared graph read")
        if self.network is None:
            raise LayoutNotReady("no declared/resolved network")
        return self._graph_of(self.network)

    def read_node(self, context: BindContext, ref: NodeRef | PortRef) -> Any:
        self._owner(ref)
        if self.graph() not in context.spec.reads:
            raise LayoutError("node binding requires a declared graph read")
        if self._node_resolver is None or self._node_identity is None:
            raise LayoutNotReady("node resolver is not installed")
        value = self._node_resolver(ref)
        self.closure.bind(
            f"{context.spec.name}:{ref.address.encoded_id}",
            {"address": ref.to_dict(), "endpoint": self._node_identity(value)},
        )
        return value

    def _run(self, spec: StageSpec, *, binding: bool = False) -> None:
        context = BindContext(self, spec) if binding else StageContext(self, spec)
        self.active_context = context
        try:
            for ref in spec.reads:
                self._owner(ref)
                if isinstance(ref, ValueRef):
                    self.check_grant(context, ref)
                    if self.values.at(ref.path, explicit=True).state == "present":
                        self.closure.read(self.values, ref.path)
                    else:
                        self.closure.record(self.values, ref.path)
                else:
                    self.read_graph(context, ref)
            spec.callback(context)
            if self.state is PreparationState.FAILED:
                raise LayoutError("preparation failed during callback")
        finally:
            self.active_context = None

    def _check_failure(self) -> None:
        if self.state is PreparationState.FAILED:
            raise LayoutError("preparation failed during hook")

    def _build_network(self) -> None:
        self._barrier = True
        self._network_values = self._network_view(self.values)
        with self.closure.network_queries():
            self.closure.record(self._network_values, ("network",), kind="variant")
            self.closure.record(self._network_values, ("grid",), kind="presence")
            if self._network_builder is not None:
                self.network = self._network_builder(self._network_values, self.closure)
                self._check_failure()

    def prepare(self) -> Any:
        if self.state is PreparationState.FINALIZED:
            return self._result
        if self.state is PreparationState.FAILED:
            raise LayoutError("preparation failed; use a fresh environment")
        if self.state is not PreparationState.DECLARING:
            self.state = PreparationState.FAILED
            self.active_context = None
            raise LayoutError("reentrant preparation")
        self.state = PreparationState.RESOLVING
        try:
            stages = ordered_stages(self._stages)
            for spec in (*stages, *self._binders):
                for ref in spec.reads:
                    self._owner(ref)
            self._freeze_stage()
            for spec in stages:
                if not self._barrier and any(isinstance(ref, GraphRef) for ref in spec.reads):
                    self._build_network()
                self._stage = spec.name
                self._run(spec)
                self._freeze_stage()
            if not self._barrier:
                self._build_network()
            self.state = PreparationState.VALIDATING
            self.values = self._resolve(final=True)
            if self._validate is not None:
                self.values = self._validate(self) or self.values
                self._check_failure()
            self._check_changes(self.values)
            self.closure.extend_network(self.values)
            self.state = PreparationState.BINDING
            if self._bind is not None:
                self._bind(self)
                self._check_failure()
            for spec in self._binders:
                self._run(spec, binding=True)
            self.closure.seal()
            self._result = self._finalize(self) if self._finalize else self.values
            self._check_failure()
            self.state = PreparationState.FINALIZED
            return self._result
        except BaseException:
            self.state = PreparationState.FAILED
            raise
        finally:
            self.active_context = None
