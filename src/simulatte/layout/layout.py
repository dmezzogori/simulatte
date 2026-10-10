"""Environment-owned layout declarations and preparation facade."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .auto import AutoLayer, TargetMetadata, auto_layout
from .binding import bind_components, check_existing_endpoints
from .dependencies import MISSING, DependencyClosure, evaluate_query
from .errors import LayoutDiagnostic, LayoutError
from .io import load_layout, save_layout
from .layers import FileLayer, ResolvedValues, _thaw, resolve_layers
from .lifecycle import PreparationCoordinator, PreparationState
from .network import ResolvedNetwork, resolve_network
from .refs import Address, GraphRef, NodeRef, PortRef, ValueRef
from .topology import snapshot_topology
from .types import Explicit, Grid, Lanes, Lattice, LayoutLimits, NetworkOverride, Point, _freeze

if TYPE_CHECKING:
    from simulatte.entities import Entity
    from simulatte.environment import Environment
    from simulatte.intralogistics.graph import Node

_BUILTINS = {
    "server",
    "psp",
    "warehouse",
    "charging_station",
    "parking_area",
    "agv",
    "node",
    "shopfloor",
    "router",
    "fleet",
    "job",
    "order",
}


def _data(value: Any, owner: object) -> Any:
    if isinstance(value, (NodeRef, PortRef, GraphRef, ValueRef)):
        if value.owner is not owner:
            raise LayoutError("foreign layout reference")
        return value.to_dict()
    if isinstance(value, Point):
        return [value.x, value.y, value.z]
    if isinstance(value, Mapping):
        return {key: _data(item, owner) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_data(item, owner) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _data(getattr(value, field.name), owner)
            for field in fields(value)
            if getattr(value, field.name) is not None
        }
    return value


@dataclass(frozen=True)
class _PreparedLayout:
    """Internal Task 9 finalizer input; no provisional hashes or bundle format."""

    values: ResolvedValues
    auto: AutoLayer
    network: ResolvedNetwork
    closure: DependencyClosure
    stage_records: Mapping[str, Any]
    required_paths: tuple[Any, ...]
    diagnostics: tuple[LayoutDiagnostic, ...]
    explicitly_named: frozenset[str]


class Layout:
    def __init__(
        self, env: Environment, *, file: str | Path | None = None, limits: LayoutLimits = LayoutLimits()
    ) -> None:
        if env.layout is not None or env._activation_started or env._explicit_graph_use or env.now != 0:
            raise LayoutError("Layout requires an unactivated environment without explicit graph consumers")
        check_existing_endpoints(env)
        self.env, self.limits = env, limits
        layer = FileLayer({"schema_version": "1.0"}) if file is None else load_layout(file, limits=limits)
        self._placed: set[str] = set()
        self._flows: list[tuple[str, ...]] = []
        self._requirements: list[tuple[NodeRef | PortRef, NodeRef | PortRef]] = []
        self._resource_managers: list[Any] = []
        self._resource_owner: object | None = None
        self._custom_managers: list[tuple[Any, object, str]] = []
        self._binding_node: Node | None = None
        self._attaching_target: tuple[str, str] | None = None
        self._canonical_nodes: dict[str, Node] = {}
        self._auto: AutoLayer | None = None
        self._required_paths: tuple[Any, ...] = ()
        self._coordinator = PreparationCoordinator(
            env,
            owner=self,
            host=self,
            file_layer=layer,
            network_builder=self._build_network,
            graph_of=self._graph_of,
            node_resolver=self._resolve_node,
            node_identity=lambda node: node.id,
            target_provider=self._targets,
            validate=self._validate,
            bind=lambda _: bind_components(self),
            finalize=self._finalize,
            binding_guard=env._scheduling_blocked,
        )
        for entity in env.entities.live():
            if entity.kind in _BUILTINS:
                self._coordinator.attach_target(entity.id, entity.kind)
        env._layout = self

    @classmethod
    def from_dict(
        cls, env: Environment, file_layer: Mapping[str, Any], *, limits: LayoutLimits = LayoutLimits()
    ) -> Layout:
        layer = FileLayer(file_layer, max_nesting=limits.max_nesting)
        if len(layer.data.get("placements", {})) > limits.max_placements:
            raise LayoutError("max_placements exceeded")
        result = cls(env, limits=limits)
        result._coordinator.file_layer = layer
        result._coordinator.values = result._coordinator._resolve()
        return result

    def _targets(self) -> dict[str, str]:
        result = {
            entity.id: entity.kind
            for entity in self.env.entities.live()
            if entity.kind in _BUILTINS or entity.id in self._placed
        }
        coordinator = getattr(self, "_coordinator", None)
        if coordinator is not None:
            if self._attaching_target is not None:
                entity_id, kind = self._attaching_target
                result[entity_id] = kind
            if coordinator.network is not None:
                result.update({definition.id: "node" for definition in coordinator.network.definitions.nodes.values()})
            else:
                raw = resolve_layers(coordinator._code, coordinator.file_layer).explicit_data.get("network", {})
                if raw.get("type") == "explicit":
                    result.update({node["id"]: "node" for node in raw.get("nodes", ())})
        return result

    def _entity_id(self, entity: Entity | str) -> str:
        if isinstance(entity, str):
            return entity
        if not self.env.entities.is_live(entity):
            raise LayoutError("placement/flow entity belongs to another environment or is not live")
        return entity.id

    def place(self, entity: Entity | str, *, kind: str | None = None, **placement_patch: Any) -> None:
        self._coordinator._mutable()
        entity_id = self._entity_id(entity)
        if isinstance(entity, str):
            if kind is None:
                raise LayoutError("string placement target requires kind")
        else:
            if kind is not None and kind != entity.kind:
                raise LayoutError("placement kind differs from entity kind")
            kind = entity.kind
        if set(placement_patch) - {"position", "rotation", "footprint", "ports", "obstacle", "visual"}:
            raise LayoutError("unknown placement property")
        data = {"kind": kind, **_data(placement_patch, self)}
        self._coordinator.declare({"placements": {entity_id: data}})
        self._placed.add(entity_id)
        if self.env.entities.kind_of(entity_id) is not None and entity_id not in self._coordinator._targets:
            self._coordinator.attach_target(entity_id, kind)

    def flow(self, entities: Sequence[Entity | str]) -> None:
        self._coordinator._mutable()
        self._flows.append(tuple(self._entity_id(entity) for entity in entities))

    def set_grid(self, grid: Grid) -> None:
        if not isinstance(grid, Grid):
            raise LayoutError("grid must be a Grid")
        self._coordinator.declare({"grid": _data(grid, self)})

    def set_network(self, spec: Lattice | Lanes | Explicit) -> None:
        if not isinstance(spec, (Lattice, Lanes, Explicit)):
            raise LayoutError("network must be Lattice, Lanes or Explicit")
        self._coordinator.declare({"network": {"type": type(spec).__name__.lower(), **_data(spec, self)}})

    def override(self, override: NetworkOverride) -> None:
        if not isinstance(override, NetworkOverride):
            raise LayoutError("override must be a NetworkOverride")
        # Lists replace across layers; aggregate only the code declarations here.
        records = []
        for _, patch in self._coordinator._code:
            if "overrides" in patch:
                records = _thaw(patch["overrides"])
        self._coordinator.declare({"overrides": [*records, _data(override, self)]})

    def ref(self, *path: str) -> ValueRef:
        return self._coordinator.ref(*path)

    def node(self, node_id: str) -> NodeRef:
        return self.node_at(Address({"kind": "explicit", "id": node_id}))

    def node_at(self, address: Address) -> NodeRef:
        return NodeRef(self, address)

    def port(self, entity: Entity | str, port_name: str) -> PortRef:
        return PortRef(self, self._entity_id(entity), port_name)

    def graph(self) -> GraphRef:
        return self._coordinator.graph()

    def require_path(self, a: NodeRef | PortRef, b: NodeRef | PortRef) -> None:
        self._coordinator._mutable()
        for ref in (a, b):
            self._accept_reference(ref, (NodeRef, PortRef))
        self._requirements.append((a, b))

    def stage(self, name: str, *, reads: Sequence[ValueRef | GraphRef], after: Sequence[str] = ()) -> Callable:
        return self._coordinator.stage(name, reads=reads, after=after)

    def on_bind(self, name: str, callback: Callable, *, reads: Sequence[ValueRef | GraphRef] = ()) -> None:
        self._coordinator.on_bind(name, callback, reads=reads)

    def load(self, path: str | Path) -> None:
        self._coordinator._mutable(registration=True)
        layer = load_layout(path, limits=self.limits)
        self._coordinator.file_layer = layer
        self._coordinator.values = self._coordinator._resolve()

    def save(self, path: str | Path) -> None:
        save_layout(path, self._coordinator.file_layer)

    def to_dict(self) -> dict[str, Any]:
        return self._coordinator.file_layer.to_dict()

    def physical(self, ref: ValueRef, *, default: Any = MISSING) -> Any:
        return self._coordinator.physical(ref, default=default)

    def prepare(self) -> Any:
        return self._coordinator.prepare()

    def _accept_reference(self, value: Any, classes: tuple[type, ...]) -> None:
        state = self._coordinator.state
        if state not in (PreparationState.DECLARING, PreparationState.RESOLVING, PreparationState.FINALIZED):
            raise LayoutError(f"cannot construct layout consumer while {state.value}")
        if not isinstance(value, classes) or value.owner is not self:
            raise LayoutError(
                "layout consumers require a reference from the same layout; direct nodes/graphs are invalid"
            )

    def _resolve_node(self, ref: NodeRef | PortRef) -> Node:
        self._coordinator._owner(ref)
        return self._coordinator.network.node(ref.address)

    def _bind_node(self, ref: NodeRef | PortRef, entity_id: str, field: str) -> Node:
        node = self._resolve_node(ref)
        self._coordinator.closure.bind(f"{entity_id}:{field}", {"address": ref.to_dict(), "endpoint": node.id})
        return node

    def _build_network(self, values: ResolvedValues, closure: DependencyClosure) -> ResolvedNetwork:
        network = resolve_network(values, closure, limits=self.limits)
        self._canonical_nodes = {node.id: node for node in network.graph.nodes}
        return network

    def _graph_of(self, network: ResolvedNetwork):
        if self._coordinator.values.at(("network",), explicit=True).state != "present":
            raise LayoutError("graph reference requires a declared network")
        return network.graph

    def _validate_node(self, node: Node | None) -> None:
        if node is None:
            return
        if self._canonical_nodes.get(node.id) is not node:
            raise LayoutError("AGV movement requires an exact canonical enabled graph node")

    def _before_attach(self, obj: Entity, entity_id: str) -> None:
        from simulatte.intralogistics.graph import NodeBinding

        coordinator = self._coordinator
        if type(obj) is NodeBinding and obj.node is self._binding_node:
            return
        if coordinator.state is PreparationState.FINALIZED:
            return  # constructor preflight already resolved frozen graph refs
        coordinator._mutable()
        if obj.kind in _BUILTINS or entity_id in self._placed:
            self._attaching_target = (entity_id, obj.kind)
            try:
                coordinator.attach_target(entity_id, obj.kind)
            finally:
                self._attaching_target = None

    def _register_manager(self, manager: Any) -> None:
        self._coordinator._mutable()
        if self._resource_owner is not None and self._resource_owner is not manager:
            raise LayoutError("one shared resource owner is allowed per layout graph")
        self._resource_owner = manager
        self._resource_managers.append(manager)

    def register_resource_owner(self, manager: Any, *, owner: object, graph: GraphRef, binder: str) -> None:
        """Register a custom manager and its named on_bind hook before prepare.

        The hook owns resource setup and declares every physical read. Managers sharing
        resources must provide the same identity token; it is never serialized.
        """
        self._coordinator._mutable(registration=True)
        self._accept_reference(graph, (GraphRef,))
        if owner is None or (self._resource_owner is not None and self._resource_owner is not owner):
            raise LayoutError("one shared resource owner is allowed per layout graph")
        if any(item[0] is manager for item in self._custom_managers):
            raise LayoutError("custom resource manager already registered")
        self._resource_owner = owner
        self._custom_managers.append((manager, owner, binder))

    def _validate_fleet(self, members: Sequence[Any], manager: Any) -> None:
        from simulatte.intralogistics.traffic import FreeTrafficManager, ResourceBasedTrafficManager

        for member in members:
            if getattr(member, "env", None) is not self.env or not self.env.entities.is_live(member):
                raise LayoutError("fleet member must be live in the same environment")
        if type(manager) is FreeTrafficManager:
            return
        if type(manager) is ResourceBasedTrafficManager:
            if manager._env is not self.env or manager is not self._resource_owner:
                raise LayoutError("fleet must reuse the registered resource manager in its environment")
        elif not any(item[0] is manager for item in self._custom_managers):
            raise LayoutError("custom manager requires explicit resource owner and binder registration")

    def _validate(self, coordinator: PreparationCoordinator) -> ResolvedValues:
        check_existing_endpoints(self.env)
        if (
            self._resource_managers
            or self._custom_managers
            or any(getattr(entity, "_pending_graph", None) is not None for entity in self.env.entities.live())
        ):
            self._graph_of(coordinator.network)
        names = {spec.name for spec in coordinator._binders}
        if any(binder not in names for _, _, binder in self._custom_managers):
            raise LayoutError("custom resource owner requires its registered on_bind hook")
        required = []
        metadata = {}
        for entity in self.env.entities.live():
            if entity.id not in coordinator.values.targets:
                continue
            node_id = None
            for field in ("node", "input_bays", "output_bays"):
                refs = getattr(entity, "_pending_" + field, None)
                for ref in refs if isinstance(refs, tuple) else (() if refs is None else (refs,)):
                    coordinator._owner(ref)
                    required.append(ref.address)
                    if entity.kind == "agv":
                        node_id = self._resolve_node(ref).id
            metadata[entity.id] = TargetMetadata(entity.kind, entity.id in self._placed, node_id=node_id)
        for definition in coordinator.network.definitions.nodes.values():
            metadata[definition.id] = TargetMetadata("node", definition.id in self._placed, definition.position)
        self._required_paths = coordinator.network.validate(
            required_nodes=required,
            required_paths=((a.address, b.address) for a, b in self._requirements),
            existing_entity_ids=(entity.id for entity in self.env.entities.live()),
        )
        # Registry names remain reserved after retirement. Preflight all names before
        # the first built-in NodeBinding publishes a creation event.
        for node in coordinator.network.graph.nodes:
            try:
                self.env.entities._check_name(node.id)
            except ValueError as error:
                raise LayoutError(str(error)) from error
        for index, result in enumerate(self._required_paths):
            coordinator.closure.bind(
                f"required-path:{index}",
                {
                    "source": result.source.to_dict(),
                    "target": result.target.to_dict(),
                    "path": [address.to_dict() for address in result.path],
                },
            )
        self._auto = auto_layout(
            metadata, snapshot_topology(self.env, tuple(self._flows)), coordinator.values, limits=self.limits
        )
        return resolve_layers(
            coordinator.values.bundle.code_stages,
            coordinator.file_layer,
            self._auto.data,
            targets=self._targets(),
            final=True,
        )

    def _finalize(self, coordinator: PreparationCoordinator) -> _PreparedLayout:
        assert self._auto is not None
        layer_codes = {"orphan-placement", "kind-mismatch", "non-placeable-target"}
        diagnostics = [
            *coordinator.values.diagnostics,
            *(item for item in coordinator.network.diagnostics if item.code not in layer_codes),
            *self._auto.diagnostics,
        ]
        definitions = {definition.id for definition in coordinator.network.definitions.nodes.values()}
        code_placements: dict[str, list[tuple[int, str, Mapping[str, Any]]]] = {}
        for index, (stage, patch) in enumerate(coordinator.values.bundle.code_stages):
            for target, placement in patch.get("placements", {}).items():
                code_placements.setdefault(target, []).append((index, stage, placement))
        file_paths: dict[str, list[tuple[str, ...]]] = {}
        for path, leaf in coordinator.values.explicit_leaves.items():
            if (
                len(path) >= 2
                and path[0] == "placements"
                and leaf.source is not None
                and leaf.source.kind == "file"
                and not isinstance(leaf.value, (Mapping, tuple))
            ):
                file_paths.setdefault(path[1], []).append(path)
        empty_file = FileLayer({"schema_version": "1.0"})
        for entity_id in coordinator.file_layer.data.get("placements", {}):
            if (
                entity_id in definitions
                or entity_id not in coordinator.values.targets
                or self.env.entities.is_named(entity_id)
            ):
                continue
            prefix = ("placements", entity_id)
            physical_paths = []
            predicate_paths = []
            structural_override = False
            baseline_views: dict[int, ResolvedValues] = {}
            for query in coordinator.closure.queries:
                path = tuple(query.descriptor["path"])
                kind = query.descriptor["kind"]
                if kind == "value":
                    physical_paths.append(path)
                elif kind == "selection" and path == ("placements",):
                    flags = query.observed["flags"].get(entity_id)
                    if flags is not None:
                        if query.descriptor["predicate"] == "obstacle":
                            predicate_paths.append((*prefix, "obstacle"))
                        else:
                            predicate_paths.append((*prefix, "ports"))
                            for port in flags["ports"]:
                                predicate_paths.extend(
                                    ((*prefix, "ports", port), (*prefix, "ports", port, "connector"))
                                )
                    # A selection consumes this target's membership/predicate flags,
                    # not visual descendants. Replay only its code placement prefix;
                    # the once-built index avoids remerging the whole model per target.
                    if query.code_count not in baseline_views:
                        patches = tuple(
                            (stage, {"schema_version": "1.0", "placements": {entity_id: placement}})
                            for index, stage, placement in code_placements.get(entity_id, ())
                            if index < query.code_count
                        )
                        target_kind = query.targets.get(entity_id)
                        baseline_views[query.code_count] = resolve_layers(
                            patches,
                            empty_file,
                            targets={} if target_kind is None else {entity_id: target_kind},
                            final=True,
                        )
                    baseline = evaluate_query(baseline_views[query.code_count], query.descriptor)
                    if baseline["flags"].get(entity_id) != _thaw(query.observed["flags"].get(entity_id)):
                        structural_override = True
                elif path[:2] == prefix:
                    physical_paths.append(path)
            # A file-origin predicate leaf (or removal of its ancestor container)
            # remains physical even when its value equals code. Descendants of a
            # presence/membership read are not implicitly consumed.
            predicate_override = any(
                used[: len(path)] == path for path in file_paths.get(entity_id, ()) for used in predicate_paths
            )
            physical = (
                structural_override
                or predicate_override
                or any(
                    path[: len(used)] == used or used[: len(path)] == path
                    for path in file_paths.get(entity_id, ())
                    for used in physical_paths
                )
            )
            if physical:
                raise LayoutError(f"physical file override requires an explicitly named entity: {entity_id}")
            diagnostics.append(
                LayoutDiagnostic(
                    "generated-id-presentation",
                    "warning",
                    prefix,
                    None,
                    entity_id,
                    "presentation override targets an auto-named entity",
                )
            )
        assert self._auto is not None
        return _PreparedLayout(
            coordinator.values,
            self._auto,
            coordinator.network,
            coordinator.closure,
            _freeze(coordinator.stage_records()),
            self._required_paths,
            tuple(sorted(set(diagnostics), key=lambda d: (d.stage or "", d.path, d.code, d.target or ""))),
            frozenset(entity.id for entity in self.env.entities.live() if self.env.entities.is_named(entity.id)),
        )
