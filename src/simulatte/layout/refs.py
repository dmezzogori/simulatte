"""Owner-scoped logical handles; none are runtime graph objects."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .errors import LayoutNotReady


@dataclass(frozen=True, init=False)
class Address:
    """Immutable logical address with an injective generated entity id."""

    _json: str

    def __init__(self, data: Mapping[str, Any]) -> None:
        from .schema import validate_definition

        copied = validate_definition(dict(data), "address")

        def canonicalize(value: Any) -> None:
            if isinstance(value, dict):
                for child in value.values():
                    canonicalize(child)
                if value.get("kind") == "lattice":
                    value["i"], value["j"] = int(value["i"]), int(value["j"])
                if value.get("kind") == "intersection":
                    value["incidents"].sort(
                        key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                    )
            elif isinstance(value, list):
                for child in value:
                    canonicalize(child)

        canonicalize(copied)
        object.__setattr__(self, "_json", json.dumps(copied, sort_keys=True, separators=(",", ":"), ensure_ascii=False))

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._json)

    @property
    def encoded_id(self) -> str:
        return "layout:" + self._json.encode("utf-8").hex()


@dataclass(frozen=True, eq=False)
class _Ref:
    owner: object
    address: Address

    def __eq__(self, other: object) -> bool:
        return (
            type(self) is type(other)
            and isinstance(other, _Ref)
            and self.owner is other.owner
            and self.address == other.address
        )

    def __hash__(self) -> int:
        return hash((type(self), id(self.owner), self.address))

    def to_dict(self) -> dict[str, Any]:
        """Wire address only: owner identity is process-local."""
        return self.address.to_dict()

    def __getattr__(self, name: str) -> Any:
        if name in ("x", "y", "z", "nodes", "neighbors", "arc_between"):
            raise LayoutNotReady(f"{type(self).__name__} is a logical handle; resolve it before reading {name}")
        raise AttributeError(name)


@dataclass(frozen=True, eq=False, init=False)
class NodeRef(_Ref):
    """Explicit node or generated node address."""

    def __init__(self, owner: object, address: Address) -> None:
        from .schema import validate_definition

        validate_definition(address.to_dict(), "nodeAddress")
        super().__init__(owner, address)


@dataclass(frozen=True, eq=False, init=False)
class ValueRef(_Ref):
    """Path to a layout value."""

    def __init__(self, owner: object, path: tuple[str, ...]) -> None:
        super().__init__(owner, Address({"kind": "value", "path": list(path)}))

    @property
    def path(self) -> tuple[str, ...]:
        return tuple(self.address.to_dict()["path"])


@dataclass(frozen=True, eq=False, init=False)
class PortRef(_Ref):
    """A named entity port, resolving to its port node."""

    def __init__(self, owner: object, entity: str, port: str) -> None:
        super().__init__(owner, Address({"kind": "port", "entity": entity, "port": port}))


@dataclass(frozen=True, eq=False, init=False)
class GraphRef(_Ref):
    """The owner's sole network."""

    def __init__(self, owner: object) -> None:
        super().__init__(owner, Address({"kind": "graph"}))
