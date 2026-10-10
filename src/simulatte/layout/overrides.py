"""Ordered exact-address and geometric property overrides on immutable definitions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, TypeVar

from .errors import LayoutDiagnostic, LayoutError
from .layers import ResolvedValues
from .network import EdgeDefinition, NetworkDefinitions, NodeDefinition
from .refs import Address


Definition = TypeVar("Definition", NodeDefinition, EdgeDefinition)


def _replace(mapping: dict[Address, Definition], address: Address, name: str, value: Any) -> None:
    mapping[address] = replace(mapping[address], **{name: value})


def _matches(definitions: NetworkDefinitions, target: Mapping[str, Any]) -> tuple[set[Address], set[Address]]:
    kind = target["kind"]
    if kind not in ("row", "column", "rectangle", "lane"):
        address = Address(target)
        canonical = definitions.aliases.get(address)
        return (
            {canonical} if canonical is not None else set(),
            {address} if address in definitions.edges else set(definitions.lineage.get(address, ())),
        )
    if kind == "lane":
        edges = {
            a for a, e in definitions.edges.items() if any(p.to_dict().get("lane") == target["id"] for p in e.lineage)
        }
        nodes = {n for a in edges for n in (definitions.edges[a].source, definitions.edges[a].target)}
        return nodes, edges
    if kind == "rectangle":
        xmin, ymin, xmax, ymax = target["bounds"]
        if xmin > xmax or ymin > ymax:
            raise LayoutError("override rectangle bounds must be ordered")
        nodes = {
            a for a, n in definitions.nodes.items() if xmin <= n.position.x <= xmax and ymin <= n.position.y <= ymax
        }
    else:
        index = "i" if kind == "column" else "j"
        nodes = {
            a for a in definitions.nodes if a.to_dict()["kind"] == "lattice" and a.to_dict()[index] == target[index]
        }
    edges = {a for a, e in definitions.edges.items() if e.source in nodes and e.target in nodes}
    return nodes, edges


def apply_overrides(
    values: ResolvedValues, definitions: NetworkDefinitions
) -> tuple[NetworkDefinitions, tuple[LayoutDiagnostic, ...]]:
    nodes, edges = dict(definitions.nodes), dict(definitions.edges)
    diagnostics = []
    required_unsets: dict[tuple[Address, str], tuple[str, ...]] = {}
    explicit_paths = {}
    for i, arc in enumerate(values.explicit_data.get("network", {}).get("arcs", ())):
        pairs = []
        if arc.get("direction") in ("both", "forward"):
            pairs.append((arc["source"], arc["target"]))
        if arc.get("direction") in ("both", "backward"):
            pairs.append((arc["target"], arc["source"]))
        for pair in pairs:
            explicit_paths[pair] = ("network", "arcs", str(i))
    for address, edge in edges.items():
        data = address.to_dict()
        kind = data["kind"]
        if kind == "connector":
            source_path = ("placements", data["entity"], "ports", data["port"], "connector")
        elif kind == "lane_child":
            source_path = ("network", "lanes", data["segment"]["lane"])
        elif kind == "explicit_arc":
            source_path = explicit_paths[data["source"], data["target"]]
        else:
            source_path = ("network",)
        for name in ("enabled", "direction"):
            if getattr(edge, name) is None:
                required_unsets[address, name] = (*source_path, name)
    for i, record in enumerate(values.explicit_data.get("overrides", ())):
        selected_nodes, selected_edges = _matches(definitions, record["target"])
        path = ("overrides", str(i))
        if not selected_nodes and not selected_edges:
            source = values.at(path).source
            diagnostics.append(
                LayoutDiagnostic(
                    "stale-override",
                    "warning",
                    path,
                    source.stage if source else None,
                    record["id"],
                    "override matches no network element",
                )
            )
            continue
        for selected, mapping, properties in (
            (selected_nodes, nodes, ("enabled", "capacity")),
            (selected_edges, edges, ("enabled", "direction", "speed_limit")),
        ):
            for name in properties:
                leaf = values.at((*path, "properties", name), explicit=True)
                if leaf.state == "absent":
                    continue
                for address in selected:
                    key = (address, name)
                    if leaf.state == "unset" and name in ("enabled", "direction"):
                        required_unsets[key] = (*path, "properties", name)
                    else:
                        required_unsets.pop(key, None)
                    _replace(mapping, address, name, leaf.value)
    for (address, name), path in required_unsets.items():
        edge = edges.get(address)
        if name == "enabled" or (
            edge is not None and edge.enabled and nodes[edge.source].enabled and nodes[edge.target].enabled
        ):
            raise LayoutError(f"required input is unset: {'/'.join(path)}")
    return NetworkDefinitions(
        nodes,
        edges,
        definitions.aliases,
        definitions.lineage,
        obstacles=definitions.obstacles,
        clearance=definitions.clearance,
    ), tuple(diagnostics)
