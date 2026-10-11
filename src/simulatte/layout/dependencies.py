"""Serializable physical queries over immutable, explicit layer views."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
import json
from typing import Any

from .identity import normalize, normalized_query
from .errors import LayoutError
from .layers import FileLayer, ResolvedValues, _thaw, resolve_layers
from .types import _freeze

MISSING = object()
Path = tuple[str, ...]


def _data(value: Any) -> Any:
    """Copy only finite JSON data; executable/default object identities never escape."""
    try:
        copied = _thaw(_freeze(value))
        encoded = json.dumps(copied, allow_nan=False, ensure_ascii=False).encode("utf-8")
        if json.loads(encoded) != copied:
            raise ValueError("JSON mapping keys must be strings")
    except (TypeError, ValueError, UnicodeError) as error:
        raise LayoutError("dependency values must be finite serializable data") from error
    return copied


def _eligible(values: ResolvedValues, path: Path) -> None:
    for candidate, leaf in values.leaves.items():
        if candidate[: len(path)] == path and leaf.source is not None and leaf.source.kind == "auto":
            raise LayoutError(
                f"auto value cannot be physical: {candidate}",
                code="physical-auto-read",
                path=candidate,
                target=candidate[1] if len(candidate) > 1 and candidate[0] == "placements" else None,
            )


def _selection(values: ResolvedValues, path: Path, predicate: str) -> dict[str, Any]:
    if predicate not in ("obstacle", "connector"):
        raise LayoutError(f"unknown schema predicate: {predicate}")
    leaf = values.at(path, explicit=True)
    candidates = leaf.value if leaf.state == "present" else {}
    if not isinstance(candidates, Mapping):
        raise LayoutError("selection requires a mapping collection")
    port_members: dict[str, set[str]] = {}
    for candidate in values.explicit_leaves:
        if candidate[: len(path)] == path and len(candidate) == len(path) + 3 and candidate[-2] == "ports":
            port_members.setdefault(candidate[len(path)], set()).add(candidate[-1])
    flags, selected = {}, []
    for key in sorted(candidates):
        item_path = (*path, key)
        if predicate == "obstacle":
            flag = values.at((*item_path, "obstacle"), explicit=True)
            flags[key] = {"state": flag.state, "value": flag.value}
            match = flag.state == "present" and flag.value is True
        else:
            ports = values.at((*item_path, "ports"), explicit=True)
            port_flags = {
                name: values.at((*item_path, "ports", name, "connector"), explicit=True).state
                for name in sorted(port_members.get(key, ()))
            }
            flags[key] = {"state": ports.state, "ports": port_flags}
            match = any(state == "present" for state in port_flags.values())
        if match:
            selected.append(key)
    return {"state": leaf.state, "members": sorted(candidates), "flags": flags, "selected": selected}


def evaluate_query(values: ResolvedValues, descriptor: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate saved data without running model callbacks or inspecting source identity."""
    path = tuple(descriptor["path"])
    leaf = values.at(path, explicit=True)
    kind = descriptor["kind"]
    if kind == "selection":
        return _selection(values, path, descriptor["predicate"])
    if kind == "presence":
        return {"state": leaf.state}
    if kind == "variant":
        return {"state": leaf.state, "value": leaf.value.get("type") if leaf.state == "present" else None}
    if kind == "membership":
        members = sorted(leaf.value) if isinstance(leaf.value, Mapping) else list(range(len(leaf.value or ())))
        return {"state": leaf.state, "members": members}
    if kind != "value":
        raise LayoutError(f"unknown dependency query: {kind}")
    eligible = all(
        item.source is None or item.source.kind != "auto"
        for candidate, item in values.explicit_leaves.items()
        if candidate[: len(path)] == path
    )
    # Optional absence is evaluated before auto fill, just as the original read was.
    if leaf.state != "present" and "default" in descriptor:
        eligible = True
    unset = [
        list(candidate[len(path) :])
        for candidate, item in sorted(values.explicit_leaves.items())
        if candidate[: len(path)] == path and item.state == "unset"
    ]
    return {"state": leaf.state, "value": normalize(leaf.value, path), "eligible": eligible, "unset": unset}


@dataclass(frozen=True)
class DependencyQuery:
    descriptor: Mapping[str, Any]
    observed: Mapping[str, Any]
    code_count: int
    targets: Mapping[str, str]
    origin: str = "model"

    def __post_init__(self) -> None:
        for field in ("descriptor", "observed", "targets"):
            object.__setattr__(self, field, _freeze(getattr(self, field)))


class DependencyClosure:
    """An append-only preparation log, sealed before publishing physical identity.

    Descriptors and normalized observations are hash inputs; stage snapshot metadata
    is for pure-data replay and does not distinguish equal code and file values.
    """

    def __init__(self) -> None:
        self._queries: list[DependencyQuery] = []
        self._sealed = False
        self._bindings: dict[str, Any] = {}
        self._origin = "model"

    @property
    def queries(self) -> tuple[DependencyQuery, ...]:
        return tuple(self._queries)

    @property
    def sealed(self) -> bool:
        return self._sealed

    @contextmanager
    def network_queries(self):
        """Tag compiler-owned queries; model reads never receive extension rights."""
        previous = self._origin
        self._origin = "network"
        try:
            yield
        finally:
            self._origin = previous

    def record(self, values: ResolvedValues, path: Path, *, kind: str = "value", **options: Any) -> Any:
        if self._sealed:
            raise LayoutError("physical dependency closure is sealed", code="lifecycle-error")
        descriptor = _data({"kind": kind, "path": list(path), **options})
        observed = evaluate_query(values, descriptor)
        query = DependencyQuery(descriptor, observed, len(values.bundle.code_stages), values.targets, self._origin)
        if query not in self._queries:
            self._queries.append(query)
        return observed

    def read(self, values: ResolvedValues, path: Path, *, default: Any = MISSING) -> Any:
        path = tuple(path)
        leaf = values.at(path, explicit=True)
        if default is not MISSING:
            default = _freeze(normalize(_data(default), path))
        if self._sealed:
            if not self.covers(path):
                raise LayoutError(f"physical path is not covered: {path}", code="undeclared-physical-read", path=path)
        else:
            if leaf.state == "present" or default is MISSING:
                _eligible(values, path)
            if leaf.state != "present" and default is MISSING:
                return values.require(path, explicit=True)
            self.record(values, path, **({"default": _data(default)} if default is not MISSING else {}))
        if leaf.state == "present":
            return _freeze(normalize(leaf.value, path))
        if default is MISSING:
            return values.require(path, explicit=True)
        return _freeze(default)

    def covers(self, path: Path) -> bool:
        return any(
            q.descriptor["kind"] == "value" and tuple(path[: len(q.descriptor["path"])]) == tuple(q.descriptor["path"])
            for q in self._queries
        )

    def select(self, values: ResolvedValues, path: Path, predicate: str) -> tuple[str, ...]:
        return tuple(self.record(values, path, kind="selection", predicate=predicate)["selected"])

    def bind(self, name: str, value: Any) -> None:
        if self._sealed:
            raise LayoutError("physical dependency closure is sealed", code="lifecycle-error")
        frozen = _freeze(_data(value))
        if name in self._bindings and self._bindings[name] != frozen:
            raise LayoutError(f"conflicting physical binding: {name}")
        self._bindings[name] = frozen

    def seal(self) -> None:
        self._sealed = True

    @staticmethod
    def _extension(query: DependencyQuery, current: Mapping[str, Any]) -> bool:
        if query.origin != "network" or query.descriptor["kind"] != "selection":
            return False
        old = _thaw(query.observed)
        return (
            old["selected"] == current["selected"]
            and set(old["members"]) <= set(current["members"])
            and all(current["flags"][key] == flag for key, flag in old["flags"].items())
        )

    def extend_network(self, values: ResolvedValues) -> None:
        """At finalization include newly allowed nonmatching graph candidates."""
        if self._sealed:
            raise LayoutError("physical dependency closure is sealed", code="lifecycle-error")
        self._queries = [
            replace(
                q,
                observed=evaluate_query(values, q.descriptor),
                code_count=len(values.bundle.code_stages),
                targets=values.targets,
            )
            if self._extension(q, evaluate_query(values, q.descriptor))
            else q
            for q in self._queries
        ]

    def changed(
        self,
        values: ResolvedValues,
        *,
        network_values: ResolvedValues | None = None,
        allow_network_extensions: bool = False,
    ) -> tuple[Path, ...]:
        """Use a separately authoritative compiler view during staged preparation."""
        changed = set()
        for query in self._queries:
            view = network_values if query.origin == "network" and network_values is not None else values
            current = evaluate_query(view, query.descriptor)
            if current != _thaw(query.observed) and not (allow_network_extensions and self._extension(query, current)):
                changed.add(tuple(query.descriptor["path"]))
        return tuple(sorted(changed))

    def reevaluate(self, code_stages: Any, file_layer: FileLayer) -> tuple[Path, ...]:
        """Compare each dependency in its saved stage view, before presentation fill."""
        changed = set()
        for query in self._queries:
            values = resolve_layers(code_stages[: query.code_count], file_layer, targets=query.targets, final=True)
            if normalized_query(
                query.descriptor, evaluate_query(values, query.descriptor), origin=query.origin, targets=query.targets
            ) != normalized_query(query.descriptor, query.observed, origin=query.origin, targets=query.targets):
                changed.add(tuple(query.descriptor["path"]))
        return tuple(sorted(changed))

    def physical_data(self) -> dict[str, Any]:
        """Hash payload before Task 9 numeric normalization; omit replay/source metadata.

        Identical queries from different saved stage views have identical effective
        identity, while their separate snapshots remain available in ``to_dict``.
        """
        unique = {}
        for query in self._queries:
            item = {"descriptor": _thaw(query.descriptor), "observed": _thaw(query.observed)}
            key = json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            unique[key] = item
        return {"queries": [unique[key] for key in sorted(unique)], "bindings": _thaw(self._bindings)}

    def to_dict(self) -> dict[str, Any]:
        return {
            "queries": [
                {
                    "descriptor": _thaw(q.descriptor),
                    "observed": _thaw(q.observed),
                    "code_count": q.code_count,
                    "targets": dict(q.targets),
                    "origin": q.origin,
                }
                for q in self._queries
            ],
            "bindings": _thaw(self._bindings),
            "sealed": self._sealed,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DependencyClosure:
        closure = cls()
        closure._queries = [DependencyQuery(**query) for query in data["queries"]]
        closure._bindings = {key: _freeze(value) for key, value in data["bindings"].items()}
        closure._sealed = data["sealed"]
        return closure
