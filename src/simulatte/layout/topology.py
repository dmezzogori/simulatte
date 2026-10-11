"""Immutable snapshots of conservative, built-in static routing sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .errors import LayoutError

if TYPE_CHECKING:
    from simulatte.environment import Environment


def id_key(value: str) -> bytes:
    if type(value) is not str or not value:
        raise LayoutError("topology ids must be nonempty UTF-8 strings")
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise LayoutError("topology ids must be nonempty UTF-8 strings") from exc


def _sequences(value: Any) -> tuple[tuple[str, ...], ...]:
    if type(value) not in (tuple, list):
        raise LayoutError("static topology requires layout-owned list/tuple sequences")
    result = []
    for sequence in value:
        if type(sequence) not in (tuple, list):
            raise LayoutError("static topology requires layout-owned list/tuple sequences")
        for item in sequence:
            id_key(item)
        result.append(tuple(sequence))
    # Sequence collection order is immaterial, but each sequence's order is retained.
    return tuple(sorted(set(result), key=lambda seq: tuple(map(id_key, seq))))


@dataclass(frozen=True)
class StaticTopology:
    flows: tuple[tuple[str, ...], ...] = ()
    shopfloors: tuple[tuple[str, ...], ...] = ()
    routings: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        for name in ("flows", "shopfloors", "routings"):
            object.__setattr__(self, name, _sequences(getattr(self, name)))

    def to_dict(self) -> dict[str, Any]:
        return {
            name: [list(sequence) for sequence in getattr(self, name)] for name in ("flows", "shopfloors", "routings")
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StaticTopology:
        return cls(**data)


def snapshot_topology(env: Environment, flows: tuple[tuple[str, ...], ...] = ()) -> StaticTopology:
    """Read exact built-in containers only; callers normalize flow entity refs to ids.

    Custom mappings, sequence subclasses and sampled routing descriptions are opaque.
    Membership filtering preserves the original adjacency: a foreign/dead server breaks
    a sequence rather than inventing an edge between its neighbors.
    """
    from simulatte.distributions import FlowShopRouting
    from simulatte.router import Router
    from simulatte.server import Server
    from simulatte.shopfloor import ShopFloor

    def sequences(source: Any) -> list[tuple[str, ...]]:
        if type(source) not in (list, tuple):
            return []
        result, current = [], []
        for server in source:
            if type(server) is Server and server.env is env and env.entities.is_live(server):
                current.append(server.id)
            else:
                result.append(tuple(current))
                current = []
        result.append(tuple(current))
        return result

    shopfloors, routings = [], []
    for entity in env.entities.live():
        if type(entity) is ShopFloor and type(entity.servers) is list:
            shopfloors.extend(sequences(entity.servers))
        elif type(entity) is Router and type(entity.sku_routings) is dict:
            for source in entity.sku_routings.values():
                if type(source) is FlowShopRouting:
                    source = source.servers
                routings.extend(sequences(source))
    return StaticTopology(flows, tuple(shopfloors), tuple(routings))
