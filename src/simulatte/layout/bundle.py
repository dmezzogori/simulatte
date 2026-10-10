"""Immutable saved layouts and callback-free presentation re-resolution.

The bundle is data only. ResolvedLayout additionally retains preparation's runtime
network for model inspection, which is never serialized into a trace.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass
import re
from typing import Any

from simulatte._wire import freeze
from .auto import TargetMetadata, auto_layout
from .dependencies import DependencyClosure, evaluate_query
from .errors import LayoutDiagnostic, LayoutError
from .schema import validate_definition
from .identity import ALGORITHMS, hashes, normalize, normalized_query
from .layers import FileLayer, ResolvedValues, _thaw, resolve_layers
from .topology import StaticTopology

_FIELDS = frozenset(
    {
        "schema_version",
        "algorithms",
        "stages",
        "topology",
        "targets",
        "layers",
        "dependencies",
        "resolved",
        "network",
        "aliases",
        "bindings",
        "diagnostics",
        "auto",
        "leaves",
        "required_paths",
        "full_hash",
        "physical_hash",
        "layers_hash",
    }
)


def _validate(data: Mapping[str, Any]) -> None:
    """Validate known layout-v1 shape without instantiating runtime model objects."""
    if set(data) != _FIELDS:
        raise LayoutError("layout-v1 bundle has missing or unknown fields")
    if dict(data["algorithms"]) != ALGORITHMS:
        raise LayoutError("unsupported layout algorithm; upgrade the reader")
    for name in ("full_hash", "physical_hash", "layers_hash"):
        if not isinstance(data[name], str) or not re.fullmatch("[0-9a-f]{64}", data[name]):
            raise LayoutError(f"invalid layout {name}")
    for name in ("stages", "topology", "targets", "layers", "dependencies", "resolved", "auto", "bindings"):
        if not isinstance(data[name], Mapping):
            raise LayoutError(f"layout {name} must be a map")
    for name in ("aliases", "diagnostics", "leaves", "required_paths"):
        if not isinstance(data[name], (tuple, list)):
            raise LayoutError(f"layout {name} must be an array")
    FileLayer(data["layers"]["file"])
    for stage, patch in data["layers"]["code"]:
        if not isinstance(stage, str):
            raise LayoutError("layout stage id must be a string")
        FileLayer(patch)
    StaticTopology.from_dict(_thaw(data["topology"]))
    for target in data["targets"].values():
        if type(target["explicitly_named"]) is not bool:
            raise LayoutError("layout target name eligibility must be bool")
        TargetMetadata.from_dict({k: v for k, v in target.items() if k != "explicitly_named"})
    closure = data["dependencies"]
    if closure["sealed"] is not True or not isinstance(closure["bindings"], Mapping):
        raise LayoutError("layout dependency closure must be sealed")
    for query in closure["queries"]:
        descriptor = query["descriptor"]
        if descriptor["kind"] not in ("value", "presence", "variant", "membership", "selection"):
            raise LayoutError("unknown layout dependency query")
        if not isinstance(descriptor["path"], (tuple, list)) or not all(isinstance(p, str) for p in descriptor["path"]):
            raise LayoutError("invalid layout dependency path")
        if type(query["code_count"]) is not int or not 0 <= query["code_count"] <= len(data["layers"]["code"]):
            raise LayoutError("invalid layout dependency stage prefix")
        if not isinstance(query["observed"], Mapping) or not isinstance(query["targets"], Mapping):
            raise LayoutError("invalid layout dependency observation")
        if query["origin"] not in ("model", "network"):
            raise LayoutError("invalid layout dependency origin")
    if data["network"] is not None:
        network = data["network"]
        for field in ("nodes", "edges", "lineage", "adjacency", "weak_components", "strong_components"):
            if not isinstance(network[field], (list, tuple)):
                raise LayoutError(f"layout network {field} must be an array")
        if not isinstance(network["graph"], Mapping):
            raise LayoutError("layout graph must be a map")
        for node in network["nodes"]:
            if not isinstance(node["id"], str) or len(node["position"]) != 3 or type(node["enabled"]) is not bool:
                raise LayoutError("invalid layout node definition")
        for edge in network["edges"]:
            if edge["direction"] not in (None, "both", "forward", "backward"):
                raise LayoutError("invalid layout edge direction")
    _validate_details(data)
    normalize(data)  # Reject non-finite values anywhere, including ignored input.


def _keys(value: Any, required: str, optional: str = "") -> None:
    if (
        not isinstance(value, Mapping)
        or not set(required.split()) <= set(value)
        or set(value) - set((required + " " + optional).split())
    ):
        raise LayoutError("invalid layout record fields")


def _array(value: Any) -> Any:
    if not isinstance(value, (tuple, list)):
        raise LayoutError("layout record requires an array")
    return value


def _strings(value: Any) -> None:
    if not all(isinstance(item, str) for item in _array(value)):
        raise LayoutError("layout record requires string paths/ids")


def _validate_selection(descriptor: Mapping[str, Any], observed: Mapping[str, Any]) -> None:
    predicate = descriptor["predicate"]
    if predicate not in ("obstacle", "connector") or not isinstance(observed["flags"], Mapping):
        raise LayoutError("invalid selection predicate")
    _strings(observed["members"])
    _strings(observed["selected"])
    members, flags = observed["members"], observed["flags"]
    if len(set(members)) != len(members) or set(flags) != set(members):
        raise LayoutError("invalid selection candidate membership")
    selected = []
    for member in members:
        flag = flags[member]
        _keys(flag, "state value" if predicate == "obstacle" else "state ports")
        state = flag["state"]
        if state not in ("present", "absent", "unset"):
            raise LayoutError("invalid selection flag state")
        if predicate == "obstacle":
            value = flag["value"]
            if (state == "present" and type(value) is not bool) or (state != "present" and value is not None):
                raise LayoutError("invalid obstacle predicate value")
            match = value is True
        else:
            ports = flag["ports"]
            if not isinstance(ports, Mapping) or any(
                not isinstance(k, str) or v not in ("present", "absent", "unset") for k, v in ports.items()
            ):
                raise LayoutError("invalid connector predicate ports")
            if state != "present" and ports:
                raise LayoutError("absent ports have predicate members")
            match = "present" in ports.values()
        if match:
            selected.append(member)
    if list(observed["selected"]) != selected or (observed["state"] != "present" and members):
        raise LayoutError("selection result disagrees with predicate flags")


def _validate_details(data: Mapping[str, Any]) -> None:
    _keys(data["layers"], "code file")
    FileLayer(data["resolved"])
    _keys(data["stages"], "stages binders")
    for group in data["stages"].values():
        for stage in _array(group):
            _keys(stage, "name reads after")
            if not isinstance(stage["name"], str):
                raise LayoutError("invalid stage name")
            _strings(stage["after"])
            for read in _array(stage["reads"]):
                _keys(read, "kind", "path")
                if read["kind"] not in ("value", "graph"):
                    raise LayoutError("invalid saved read grant")
                if read["kind"] == "value":
                    _strings(read["path"])
                validate_definition(_thaw(read), "address")
    _keys(data["auto"], "algorithm data targets topology derived_positions")
    if data["auto"]["algorithm"] != "layered-v1":
        raise LayoutError("unsupported auto algorithm")
    FileLayer(data["auto"]["data"])
    StaticTopology.from_dict(_thaw(data["auto"]["topology"]))
    for target in data["auto"]["targets"].values():
        TargetMetadata.from_dict(target)
    for point in data["auto"]["derived_positions"].values():
        validate_definition(_thaw(point), "point")
    _keys(data["dependencies"], "queries bindings sealed")
    if data["bindings"] != data["dependencies"]["bindings"]:
        raise LayoutError("layout binding records disagree")
    for query in _array(data["dependencies"]["queries"]):
        _keys(query, "descriptor observed code_count targets origin")
        descriptor, observed = query["descriptor"], query["observed"]
        _keys(descriptor, "kind path", "default predicate")
        kind = descriptor["kind"]
        expected = {
            "value": "state value eligible unset",
            "presence": "state",
            "variant": "state value",
            "membership": "state members",
            "selection": "state members flags selected",
        }[kind]
        _keys(observed, expected)
        if observed["state"] not in ("present", "absent", "unset"):
            raise LayoutError("invalid dependency state")
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in query["targets"].items()):
            raise LayoutError("invalid dependency target table")
        if kind == "value":
            if type(observed["eligible"]) is not bool:
                raise LayoutError("invalid physical eligibility")
            for path in _array(observed["unset"]):
                _strings(path)
        if kind == "membership":
            members = _array(observed["members"])
            if not (all(isinstance(item, str) for item in members) or all(type(item) is int for item in members)):
                raise LayoutError("invalid dependency membership")
            if len(set(members)) != len(members) or (
                members and type(members[0]) is int and list(members) != list(range(len(members)))
            ):
                raise LayoutError("invalid dependency member order")
        if kind == "selection":
            _validate_selection(descriptor, observed)
    for diagnostic in data["diagnostics"]:
        _keys(diagnostic, "code severity path stage target message related_paths")
        LayoutDiagnostic(**diagnostic)
        if not all(isinstance(diagnostic[k], str) for k in ("code", "message")) or any(
            diagnostic[k] is not None and not isinstance(diagnostic[k], str) for k in ("stage", "target")
        ):
            raise LayoutError("invalid diagnostic text")
        _strings(diagnostic["path"])
        for path in _array(diagnostic["related_paths"]):
            _strings(path)
    for leaf in data["leaves"]:
        _keys(leaf, "path state source schema_default")
        _strings(leaf["path"])
        if leaf["state"] not in ("present", "absent", "unset") or type(leaf["schema_default"]) is not bool:
            raise LayoutError("invalid layout leaf provenance")
        if leaf["source"] is not None:
            _keys(leaf["source"], "kind stage")
            if leaf["source"]["kind"] not in ("auto", "code", "file") or (
                leaf["source"]["stage"] is not None and not isinstance(leaf["source"]["stage"], str)
            ):
                raise LayoutError("invalid layout leaf source")
    for alias in data["aliases"]:
        _keys(alias, "address canonical")
        for address in alias.values():
            validate_definition(_thaw(address), "address")
    for requirement in data["required_paths"]:
        _keys(requirement, "source target path")
        for address in (requirement["source"], requirement["target"], *_array(requirement["path"])):
            validate_definition(_thaw(address), "address")
    network = data["network"]
    if network is None:
        return
    _keys(network, "nodes edges clearance obstacles lineage adjacency weak_components strong_components graph")
    validate_definition(network["clearance"], "nonnegative")
    for obstacle in _array(network["obstacles"]):
        _keys(obstacle, "id owner vertices")
        validate_definition(obstacle["id"], "id")
        if obstacle["owner"] is not None:
            validate_definition(obstacle["owner"], "id")
        if len(_array(obstacle["vertices"])) != 4:
            raise LayoutError("obstacle requires four vertices")
        for point in obstacle["vertices"]:
            validate_definition(_thaw(point), "point")
    for node in network["nodes"]:
        _keys(node, "address id position enabled capacity blockers")
        validate_definition(_thaw(node["address"]), "address")
        validate_definition(_thaw(node["position"]), "point")
        _strings(node["blockers"])
        if node["capacity"] is not None:
            validate_definition(node["capacity"], "capacity")
    for edge in network["edges"]:
        _keys(edge, "address source target enabled direction speed_limit lineage blockers")
        for address in (edge["address"], edge["source"], edge["target"], *_array(edge["lineage"])):
            validate_definition(_thaw(address), "address")
        if edge["enabled"] is not None and type(edge["enabled"]) is not bool:
            raise LayoutError("invalid edge enabled state")
        if edge["speed_limit"] is not None:
            validate_definition(edge["speed_limit"], "positive")
        _strings(edge["blockers"])
    for lineage in network["lineage"]:
        _keys(lineage, "address children")
        for address in (lineage["address"], *_array(lineage["children"])):
            validate_definition(_thaw(address), "address")
    for adjacency in network["adjacency"]:
        _keys(adjacency, "address edges")
        validate_definition(_thaw(adjacency["address"]), "address")
        for target, edge in _array(adjacency["edges"]):
            validate_definition(_thaw(target), "address")
            validate_definition(_thaw(edge), "address")
    for group in (*network["weak_components"], *network["strong_components"]):
        for address in _array(group):
            validate_definition(_thaw(address), "address")
    graph = network["graph"]
    _keys(graph, "nodes arcs")
    for node in _array(graph["nodes"]):
        _keys(node, "id position capacity")
        validate_definition(_thaw({k: v for k, v in node.items() if v is not None}), "explicitNode")
    for arc in _array(graph["arcs"]):
        _keys(arc, "source target speed_limit")
        validate_definition(_thaw({k: v for k, v in arc.items() if v is not None}), "explicitArc")


@dataclass(frozen=True)
class LayoutBundle(Mapping[str, Any]):
    """Deeply immutable layout-v1 data; unknown versions remain opaque and inspectable."""

    _data: Mapping[str, Any]

    def __post_init__(self) -> None:
        data = _thaw(self._data)
        if not isinstance(data, Mapping) or not isinstance(data.get("schema_version"), str):
            raise LayoutError("layout bundle requires a schema_version string")
        if data["schema_version"] == "1.0":
            _validate(data)
        object.__setattr__(self, "_data", freeze(data))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LayoutBundle:
        return cls(data)

    def to_dict(self) -> dict[str, Any]:
        return _thaw(self._data)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    @property
    def schema_version(self) -> str:
        return self["schema_version"]

    @property
    def reusable(self) -> bool:
        return self.schema_version == "1.0"

    @property
    def full_hash(self) -> str:
        return self["full_hash"]

    @property
    def physical_hash(self) -> str:
        return self["physical_hash"]

    @property
    def layers_hash(self) -> str:
        return self["layers_hash"]

    def manifest(self) -> dict[str, Any]:
        return {name: self[name] for name in ("schema_version", "full_hash", "physical_hash", "layers_hash")}

    def check(self) -> None:
        if not self.reusable:
            raise LayoutError(f"unsupported layout schema {self.schema_version}; upgrade the reader")
        for name, value in hashes(self).items():
            if value != self[name]:
                raise LayoutError(f"layout {name} mismatch")


@dataclass(frozen=True)
class ResolvedLayout:
    bundle: LayoutBundle
    values: Any
    auto: Any
    network: Any
    closure: Any
    stage_records: Any
    required_paths: Any
    diagnostics: Any
    explicitly_named: frozenset[str]

    @property
    def full_hash(self) -> str:
        return self.bundle.full_hash

    @property
    def physical_hash(self) -> str:
        return self.bundle.physical_hash

    @property
    def layers_hash(self) -> str:
        return self.bundle.layers_hash


def _network(network: Any) -> dict[str, Any]:
    definitions = network.definitions
    nodes = []
    for address, node in sorted(definitions.nodes.items(), key=lambda pair: pair[0]._json):
        nodes.append(
            {
                "address": address.to_dict(),
                "id": node.id,
                "position": [node.position.x, node.position.y, node.position.z],
                "enabled": node.enabled,
                "capacity": node.capacity,
                "blockers": list(node.blockers),
            }
        )
    edges = []
    for address, edge in sorted(definitions.edges.items(), key=lambda pair: pair[0]._json):
        edges.append(
            {
                "address": address.to_dict(),
                "source": edge.source.to_dict(),
                "target": edge.target.to_dict(),
                "enabled": edge.enabled,
                "direction": edge.direction,
                "speed_limit": edge.speed_limit,
                "lineage": [a.to_dict() for a in edge.lineage],
                "blockers": list(edge.blockers),
            }
        )
    graph = network.graph
    return {
        "nodes": nodes,
        "edges": edges,
        "clearance": definitions.clearance,
        "obstacles": [
            {"id": obstacle.id, "owner": obstacle.owner, "vertices": [[p.x, p.y, p.z] for p in obstacle.vertices]}
            for obstacle in definitions.obstacles
        ],
        "lineage": [
            {"address": a.to_dict(), "children": [b.to_dict() for b in bs]}
            for a, bs in sorted(definitions.lineage.items(), key=lambda pair: pair[0]._json)
        ],
        "adjacency": [
            {"address": a.to_dict(), "edges": [[b.to_dict(), e.to_dict()] for b, e in bs]}
            for a, bs in sorted(definitions.adjacency.items(), key=lambda pair: pair[0]._json)
        ],
        "weak_components": [[a.to_dict() for a in group] for group in network.weak_components],
        "strong_components": [[a.to_dict() for a in group] for group in network.strong_components],
        "graph": {
            "nodes": [
                {"id": n.id, "position": [n.x, n.y, 0.0], "capacity": network.capacities[n]} for n in graph.nodes
            ],
            "arcs": [
                {"source": a.id, "target": b.id, "speed_limit": graph.arc_between(a, b).speed_limit}
                for a in graph.nodes
                for b in graph.neighbors(a)
            ],
        },
    }


def _effective(values: Any, auto: Any) -> dict[str, Any]:
    result = {key: _thaw(value) for key, value in values.data.items() if key not in ("network", "overrides")}
    for target, point in auto.derived_positions.items():
        result["placements"][target]["position"] = [point.x, point.y, point.z]
    return result


def _leaves(values: Any) -> list[dict[str, Any]]:
    return [
        {
            "path": list(path),
            "state": leaf.state,
            "source": None if leaf.source is None else asdict(leaf.source),
            "schema_default": leaf.schema_default,
        }
        for path, leaf in sorted(values.leaves.items())
    ]


def finalize_layout(prepared: Any) -> ResolvedLayout:
    values, auto, network = prepared.values, prepared.auto, prepared.network
    data = {
        "schema_version": "1.0",
        "algorithms": ALGORITHMS,
        "stages": _thaw(prepared.stage_records),
        "topology": auto.topology.to_dict(),
        "targets": {
            key: {**value.to_dict(), "explicitly_named": key in prepared.explicitly_named}
            for key, value in auto.targets.items()
        },
        "layers": {
            "code": [[stage, _thaw(patch)] for stage, patch in values.bundle.code_stages],
            "file": values.bundle.file_layer.to_dict(),
        },
        "dependencies": prepared.closure.to_dict(),
        "resolved": _effective(values, auto),
        "network": _network(network) if "network" in values.explicit_data else None,
        "aliases": [
            {"address": a.to_dict(), "canonical": b.to_dict()}
            for a, b in sorted(network.aliases.items(), key=lambda pair: pair[0]._json)
        ],
        "bindings": prepared.closure.to_dict()["bindings"],
        "diagnostics": [asdict(d) for d in prepared.diagnostics],
        "auto": auto.to_dict(),
        "leaves": _leaves(values),
        "required_paths": [
            {"source": p.source.to_dict(), "target": p.target.to_dict(), "path": [a.to_dict() for a in p.path]}
            for p in prepared.required_paths
        ],
    }
    for query in data["dependencies"]["queries"]:
        query.update(normalized_query(query["descriptor"], query["observed"]))
    data.update(hashes(data))
    bundle = LayoutBundle.from_dict(data)
    return ResolvedLayout(
        bundle,
        values,
        auto,
        network,
        prepared.closure,
        prepared.stage_records,
        prepared.required_paths,
        prepared.diagnostics,
        prepared.explicitly_named,
    )


def validate_file_eligibility(
    values: ResolvedValues,
    closure: DependencyClosure,
    *,
    explicitly_named: frozenset[str],
    definitions: set[str],
    replay: bool = False,
    resolver: Any = resolve_layers,
) -> tuple[LayoutDiagnostic, ...]:
    """D30 source eligibility shared by fresh preparation and saved-file replacement.

    Values and names are static data. Replay refreshes only target-local selection
    views; no graph construction, model callback or live entity inspection occurs.
    """
    diagnostics = []
    code_placements: dict[str, list[tuple[int, str, Mapping[str, Any]]]] = {}
    for index, (stage, patch) in enumerate(values.bundle.code_stages):
        for target, placement in patch.get("placements", {}).items():
            code_placements.setdefault(target, []).append((index, stage, placement))
    file_paths: dict[str, list[tuple[str, ...]]] = {}
    for path, leaf in values.explicit_leaves.items():
        if (
            len(path) >= 2
            and path[0] == "placements"
            and leaf.source is not None
            and leaf.source.kind == "file"
            and not isinstance(leaf.value, (Mapping, tuple))
        ):
            file_paths.setdefault(path[1], []).append(path)
    empty_file = FileLayer({"schema_version": "1.0"})
    for entity_id in values.bundle.file_layer.data.get("placements", {}):
        if entity_id in definitions or entity_id not in values.targets or entity_id in explicitly_named:
            continue
        prefix = ("placements", entity_id)
        physical_paths = []
        predicate_paths = []
        structural_override = False
        baseline_views: dict[int, ResolvedValues] = {}
        current_views: dict[int, ResolvedValues] = {}
        for query in closure.queries:
            path = tuple(query.descriptor["path"])
            kind = query.descriptor["kind"]
            if kind == "value":
                physical_paths.append(path)
            elif kind == "selection" and path == ("placements",):
                observation = query.observed
                if replay:
                    if query.code_count not in current_views:
                        patches = tuple(
                            (stage, {"schema_version": "1.0", "placements": {entity_id: placement}})
                            for index, stage, placement in code_placements.get(entity_id, ())
                            if index < query.code_count
                        )
                        target_kind = query.targets.get(entity_id)
                        current_views[query.code_count] = resolver(
                            patches,
                            FileLayer(
                                {
                                    "schema_version": "1.0",
                                    "placements": {entity_id: values.bundle.file_layer.data["placements"][entity_id]},
                                }
                            ),
                            targets={} if target_kind is None else {entity_id: target_kind},
                            final=True,
                        )
                    observation = evaluate_query(current_views[query.code_count], query.descriptor)
                flags = observation["flags"].get(entity_id)
                if flags is not None:
                    if query.descriptor["predicate"] == "obstacle":
                        predicate_paths.append((*prefix, "obstacle"))
                    else:
                        predicate_paths.append((*prefix, "ports"))
                        for port in flags["ports"]:
                            predicate_paths.extend(((*prefix, "ports", port), (*prefix, "ports", port, "connector")))
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
                    baseline_views[query.code_count] = resolver(
                        patches,
                        empty_file,
                        targets={} if target_kind is None else {entity_id: target_kind},
                        final=True,
                    )
                baseline = normalized_query(
                    query.descriptor,
                    evaluate_query(baseline_views[query.code_count], query.descriptor),
                    origin=query.origin,
                    targets=query.targets,
                )["observed"]
                observed = normalized_query(query.descriptor, observation, origin=query.origin, targets=query.targets)[
                    "observed"
                ]
                if baseline["flags"].get(entity_id) != observed["flags"].get(entity_id):
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
    return tuple(diagnostics)


@dataclass(frozen=True)
class PresentationResult:
    requires_rerun: bool
    changed_paths: tuple[tuple[str, ...], ...]
    bundle: LayoutBundle | None
    original_physical_hash: str


def reresolve_presentation(bundle: LayoutBundle, file_patch: Mapping[str, Any]) -> PresentationResult:
    """Replace the saved file layer; never execute model code or regenerate a graph."""
    bundle.check()
    file_layer = FileLayer(file_patch)
    data = bundle.to_dict()
    stages = data["layers"]["code"]
    closure = DependencyClosure.from_dict(data["dependencies"])
    targets = {
        key: TargetMetadata.from_dict({k: v for k, v in value.items() if k != "explicitly_named"})
        for key, value in data["targets"].items()
    }
    kinds = {key: target.kind for key, target in targets.items()}
    explicit = resolve_layers(stages, file_layer, targets=kinds, final=True)
    generated_diagnostics = validate_file_eligibility(
        explicit,
        closure,
        explicitly_named=frozenset(key for key, target in data["targets"].items() if target["explicitly_named"]),
        definitions=set() if data["network"] is None else {node["id"] for node in data["network"]["nodes"]},
        replay=True,
    )
    changed = closure.reevaluate(stages, file_layer)
    if changed:
        return PresentationResult(True, changed, None, bundle.physical_hash)
    auto = auto_layout(targets, StaticTopology.from_dict(data["topology"]), explicit)
    values = resolve_layers(stages, file_layer, targets=kinds, final=True, auto_layer=auto.data)
    data["layers"]["file"] = file_layer.to_dict()
    data["resolved"] = _effective(values, auto)
    data["auto"] = auto.to_dict()
    data["leaves"] = _leaves(values)
    # Graph diagnostics stay valid; refresh layer and presentation diagnostics.
    replaced = {
        "orphan-placement",
        "kind-mismatch",
        "non-placeable-target",
        "placement-overlap",
        "unknown-flow-target",
        "node-position-ignored",
        "generated-id-presentation",
    }
    data["diagnostics"] = [d for d in data["diagnostics"] if d["code"] not in replaced] + [
        asdict(d) for d in (*values.diagnostics, *auto.diagnostics, *generated_diagnostics)
    ]
    data["diagnostics"].sort(key=lambda d: (d["stage"] or "", tuple(d["path"]), d["code"], d["target"] or ""))
    data.update(hashes(data))
    return PresentationResult(False, (), LayoutBundle.from_dict(data), bundle.physical_hash)
