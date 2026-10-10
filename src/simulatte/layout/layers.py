"""Immutable author layers and pure, provenance-preserving leaf resolution.

This module does not execute stages or decide which geometric inputs a graph needs.
Callers supply attached/definition targets and require inputs when they use them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from .errors import LayoutDiagnostic, LayoutError
from .schema import parse_patch, schema
from .types import _freeze, placement_defaults

Path = tuple[str, ...]


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, init=False)
class FileLayer:
    """Validated author data, including ineffective entries; never resolved data."""

    data: Mapping[str, Any]

    def __init__(self, data: Mapping[str, Any], *, max_nesting: int = 64) -> None:
        object.__setattr__(self, "data", _freeze(parse_patch(data, max_nesting=max_nesting)))

    def to_dict(self) -> dict[str, Any]:
        return _thaw(self.data)


@dataclass(frozen=True)
class LayerSource:
    kind: Literal["auto", "code", "file"]
    stage: str | None = None


@dataclass(frozen=True)
class LeafValue:
    value: Any = None
    state: Literal["present", "absent", "unset"] = "absent"
    source: LayerSource | None = None
    schema_default: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _freeze(self.value))


@dataclass(frozen=True)
class LayerBundle:
    code_stages: tuple[tuple[str, Mapping[str, Any]], ...]
    file_layer: FileLayer
    auto_layer: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "code_stages", tuple((stage, _freeze(parse_patch(data))) for stage, data in self.code_stages)
        )
        if self.auto_layer is not None:
            if set(self.auto_layer) - {"schema_version", "placements"}:
                raise LayoutError("auto layer may only fill presentation placements")
            object.__setattr__(self, "auto_layer", _freeze(parse_patch(self.auto_layer)))


@dataclass(frozen=True)
class ResolvedValues:
    data: Mapping[str, Any]
    explicit_data: Mapping[str, Any]
    leaves: Mapping[Path, LeafValue]
    explicit_leaves: Mapping[Path, LeafValue]
    bundle: LayerBundle
    targets: Mapping[str, str]
    diagnostics: tuple[LayoutDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        for name in ("data", "explicit_data", "leaves", "explicit_leaves", "targets"):
            object.__setattr__(self, name, _freeze(getattr(self, name)))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    def at(self, path: Sequence[str], *, explicit: bool = False) -> LeafValue:
        """Return immutable state; an unset ancestor keeps descendants unset."""
        path = tuple(path)
        if not path:
            return LeafValue(self.explicit_data if explicit else self.data, "present")
        leaves = self.explicit_leaves if explicit else self.leaves
        if path in leaves:
            return leaves[path]
        for size in range(len(path) - 1, -1, -1):
            ancestor = leaves.get(path[:size])
            if ancestor is not None and ancestor.state == "unset":
                return ancestor
        return LeafValue()

    def require(self, path: Sequence[str], *, explicit: bool = False) -> Any:
        """Require an input at actual use, rather than during a partial stage merge."""
        leaf = self.at(path, explicit=explicit)
        if leaf.state != "present":
            diagnostic = LayoutDiagnostic(
                "required-input",
                "error",
                tuple(path),
                leaf.source.stage if leaf.source else None,
                None,
                f"required input is {leaf.state}",
            )
            raise LayoutError(f"{'/'.join(path)}: {diagnostic.message}", diagnostics=(diagnostic,))
        return leaf.value


class _Merge:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.records: dict[Path, tuple[LayerSource, bool, bool]] = {}
        self.children: dict[Path, set[Path]] = {}

    def record(self, path: Path, source: LayerSource, default: bool, unset: bool) -> None:
        self.records[path] = (source, default, unset)
        self.children.setdefault(path[:-1], set()).add(path)

    def discard(self, path: Path) -> None:
        # Visit only the replaced subtree, rather than scanning every placement.
        self.children.get(path[:-1], set()).discard(path)
        pending = [path]
        while pending:
            child = pending.pop()
            self.records.pop(child, None)
            pending.extend(self.children.pop(child, ()))

    def stamp(self, value: Any, path: Path, source: LayerSource, default: bool = False) -> Any:
        self.record(path, source, default, False)
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                child = (*path, key)
                if isinstance(item, Mapping) and item == {"$unset": True}:
                    self.record(child, source, default, True)
                else:
                    result[key] = self.stamp(item, child, source, default)
            return result
        if isinstance(value, list):
            return [self.stamp(item, (*path, str(index)), source, default) for index, item in enumerate(value)]
        return value

    def merge(
        self, dest: dict[str, Any], patch: Mapping[str, Any], path: Path, source: LayerSource, *, fill: bool = False
    ) -> None:
        for key, value in patch.items():
            child = (*path, key)
            if fill and child in self.records and self.records[child][2]:
                continue
            if isinstance(value, Mapping) and value == {"$unset": True}:
                if not fill:
                    dest.pop(key, None)
                    self.discard(child)
                    self.record(child, source, False, True)
            elif isinstance(value, Mapping):
                if key not in dest or not isinstance(dest[key], dict):
                    if fill and key in dest:
                        continue
                    self.discard(child)
                    dest[key] = {}
                    self.record(child, source, False, False)
                if child == ("network",):
                    variant = value.get("type", dest[key].get("type"))
                    if variant is not None:
                        variants = schema()["$defs"]["network"]["oneOf"][:3]
                        allowed = next(
                            rule["properties"] for rule in variants if rule["properties"]["type"]["const"] == variant
                        )
                        incompatible = set(value) - allowed.keys()
                        if incompatible:
                            raise LayoutError(f"network: incompatible {variant} fields: {sorted(incompatible)}")
                        if "type" in value and variant != dest[key].get("type"):
                            old_fields = {record[1] for record in self.children.get(child, ())}
                            for old in old_fields - allowed.keys():
                                dest[key].pop(old, None)
                                self.discard((*child, old))
                self.merge(dest[key], value, child, source, fill=fill)
            elif not fill or key not in dest:
                self.discard(child)
                dest[key] = self.stamp(_thaw(value), child, source)

    def defaults(self, data: dict[str, Any], path: Path, defaults: Mapping[str, Any]) -> None:
        source = self.records[path][0]
        for key, value in defaults.items():
            child = (*path, key)
            if key not in data and child not in self.records:
                data[key] = _thaw(_freeze(value))
                self.stamp(data[key], child, source, True)

    def physical_defaults(self) -> None:
        if "grid" in self.data:
            self.defaults(self.data["grid"], ("grid",), {"origin": [0, 0, 0]})
        network = self.data.get("network")
        if network is not None:
            self.defaults(network, ("network",), {"clearance": 0, "blocked": {}})
            if network.get("type") == "lattice":
                self.defaults(network, ("network",), {"neighbors": 4, "enabled": True, "direction": "both"})
            for lane_id, lane in network.get("lanes", {}).items():
                self.defaults(lane, ("network", "lanes", lane_id), {"enabled": True, "direction": "both"})
            for collection in ("nodes", "arcs"):
                for index, item in enumerate(network.get(collection, ())):
                    self.defaults(
                        item,
                        ("network", collection, str(index)),
                        {"enabled": True, **({"direction": "forward"} if collection == "arcs" else {})},
                    )
        for entity_id, placement in self.data.get("placements", {}).items():
            for port_id, port in placement.get("ports", {}).items():
                if "connector" in port:
                    self.defaults(
                        port["connector"],
                        ("placements", entity_id, "ports", port_id, "connector"),
                        {"approach_margin": 0.01, "enabled": True, "direction": "both"},
                    )

    def leaves(self) -> Mapping[Path, LeafValue]:
        def value_at(path: Path) -> Any:
            value: Any = self.data
            for part in path:
                value = value[int(part)] if isinstance(value, list) else value[part]
            return value

        return MappingProxyType(
            {
                path: LeafValue(None if unset else value_at(path), "unset" if unset else "present", source, default)
                for path, (source, default, unset) in self.records.items()
            }
        )


def _known(bundle: LayerBundle, targets: Mapping[str, str]) -> dict[str, str]:
    known: dict[str, str] = {}
    for _, data in bundle.code_stages:
        for target, placement in data.get("placements", {}).items():
            known.setdefault(target, placement["kind"])
    known.update(targets)
    return known


def _applicable(data: Mapping[str, Any], targets: Mapping[str, str]) -> dict[str, Any]:
    copied = _thaw(data)
    if "placements" in copied:
        copied["placements"] = {
            target: placement
            for target, placement in copied["placements"].items()
            if targets.get(target) == placement["kind"]
            and placement_defaults(placement["kind"], explicitly_placed=True).placeable
        }
    return copied


def _diagnostics(bundle: LayerBundle, targets: Mapping[str, str], *, final: bool) -> tuple[LayoutDiagnostic, ...]:
    diagnostics = []
    for stage, data in (*bundle.code_stages, (None, bundle.file_layer.data)):
        for target, placement in data.get("placements", {}).items():
            kind = targets.get(target)
            if kind is None:
                if not final:
                    continue
                code, message = "orphan-placement", "placement target is absent"
            elif kind != placement["kind"]:
                code, message = "kind-mismatch", f"expected {placement['kind']}, target kind is {kind}"
            elif not placement_defaults(kind, explicitly_placed=True).placeable:
                code, message = "non-placeable-target", f"{kind} is not a static placement target"
            else:
                continue
            diagnostics.append(LayoutDiagnostic(code, "warning", ("placements", target), stage, target, message))
    order = {stage: index for index, (stage, _) in enumerate(bundle.code_stages)}
    return tuple(
        sorted(
            diagnostics, key=lambda item: (order.get(item.stage, len(order)), item.path, item.code, item.target or "")
        )
    )


def resolve_layers(
    code_stages: Sequence[tuple[str, Mapping[str, Any]]],
    file_layer: FileLayer,
    auto_layer: Mapping[str, Any] | None = None,
    *,
    targets: Mapping[str, str] | None = None,
    final: bool = False,
) -> ResolvedValues:
    """Resolve code in call order, then file; optionally fill presentation gaps.

    Code declarations define unbound targets. ``targets`` adds attached definitions
    or supplies authoritative kinds. Unknown file targets remain pending in the bundle.
    ``final=True`` requires authoritative targets and excludes unmatched code as well
    as file placements. Stage ownership/freezing and physical eligibility are enforced
    by the stage engine.
    """
    if final and targets is None:
        raise LayoutError("final layer resolution requires authoritative targets")
    bundle = LayerBundle(tuple(code_stages), file_layer, auto_layer)
    known = dict(targets or {}) if final else _known(bundle, targets or {})
    merged = _Merge()
    for stage, data in bundle.code_stages:
        merged.merge(merged.data, _applicable(data, known), (), LayerSource("code", stage))
    merged.merge(merged.data, _applicable(file_layer.data, known), (), LayerSource("file"))
    merged.physical_defaults()
    explicit_data, explicit_leaves = _freeze(merged.data), merged.leaves()
    if bundle.auto_layer is not None:
        merged.merge(merged.data, _applicable(bundle.auto_layer, known), (), LayerSource("auto"), fill=True)
    return ResolvedValues(
        merged.data,
        explicit_data,
        merged.leaves(),
        explicit_leaves,
        bundle,
        known,
        _diagnostics(bundle, known, final=final),
    )


def validate_targets(values: ResolvedValues, targets: Mapping[str, str]) -> tuple[LayoutDiagnostic, ...]:
    """Validate against final attached entities and caller-recognized definitions.

    ``targets`` is authoritative: a code placement alone does not establish a final
    target. This does not mutate values; use ``resolve_layers(..., final=True,
    targets=targets)`` to exclude orphans from effective data as well. Graph-address
    stale overrides are validated by the graph compiler.
    """
    return _diagnostics(values.bundle, targets, final=True)
