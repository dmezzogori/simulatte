"""layout-hash-v1: domain-separated, canonical immutable layout identities."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import math
from typing import Any

from simulatte._wire import canonical_pack, freeze
from .errors import LayoutError

ALGORITHMS = {"auto": "layered-v1", "network": "network-v1", "hash": "layout-hash-v1"}
_GEOMETRY = frozenset(
    {
        "position",
        "approach",
        "bounds",
        "origin",
        "graph_position",
        "vertices",
        "dx",
        "dy",
        "x",
        "y",
        "z",
        "width",
        "depth",
        "height",
        "clearance",
        "rotation",
        "speed_limit",
        "approach_margin",
        "max_distance",
    }
)


def normalize(value: Any, path: tuple[str, ...] = ()) -> Any:
    """Normalize schema geometry only; integer counts and logical indices stay integers."""
    if isinstance(value, Mapping):
        return {key: normalize(item, (*path, key)) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [normalize(item, path) for item in value]
    if type(value) in (int, float):
        if not math.isfinite(value):
            raise LayoutError("layout numbers must be finite")
        if path and path[-1] in {"capacity", "neighbors", "i", "j", "vertex", "row", "column", "code_count"}:
            if value != int(value):
                raise LayoutError("layout counts and indices must be integers")
            return int(value)
        if any(key in _GEOMETRY for key in path):
            value = float(value)
            if path and path[-1] == "rotation":
                value %= 360
        return 0.0 if type(value) is float and value == 0 else value
    return value


def normalized_query(
    descriptor: Mapping[str, Any],
    observed: Mapping[str, Any],
    *,
    origin: str = "model",
    targets: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    path = tuple(descriptor["path"])
    desc = normalize(descriptor)
    if "default" in desc:
        desc["default"] = normalize(desc["default"], path)
    obs = normalize(observed)
    if "value" in obs:
        obs["value"] = normalize(obs["value"], path)
    if origin == "network" and descriptor["kind"] == "selection" and tuple(descriptor["path"]) == ("placements",):
        # The compiler's candidate universe is the saved static target set. A first
        # purely visual declaration does not add a model target or physical flag.
        obs["state"] = "present"
        obs["members"] = sorted(targets or {})
        absent = (
            {"state": "absent", "value": None}
            if descriptor["predicate"] == "obstacle"
            else {"state": "absent", "ports": {}}
        )
        obs["flags"] = {key: obs["flags"].get(key, dict(absent)) for key in obs["members"]}
    return {"descriptor": desc, "observed": obs}


def layout_hash(domain: str, data: Any) -> str:
    return hashlib.blake2b(
        b"layout-hash-v1\0" + domain.encode("ascii") + b"\0" + canonical_pack(freeze(normalize(data))), digest_size=32
    ).hexdigest()


def hash_inputs(data: Mapping[str, Any]) -> dict[str, Any]:
    base = {"schema_version": data["schema_version"], "algorithms": data["algorithms"]}
    queries = {}
    for query in data["dependencies"]["queries"]:
        item = normalized_query(
            query["descriptor"], query["observed"], origin=query["origin"], targets=query["targets"]
        )
        queries[canonical_pack(freeze(item))] = item
    network = data["network"]
    return {
        "full": {
            **base,
            "resolved": data["resolved"],
            "targets": data["targets"],
            "network": network,
            "aliases": data["aliases"],
        },
        "physical": {
            **base,
            "queries": [queries[key] for key in sorted(queries)],
            "graph": None if network is None else network["graph"],
            "bindings": data["dependencies"]["bindings"],
        },
        "layers": {
            **base,
            "layers": data["layers"],
            "stages": data["stages"],
            "topology": data["topology"],
            "targets": data["targets"],
            "dependencies": data["dependencies"],
            "leaves": data["leaves"],
        },
    }


def hashes(data: Mapping[str, Any]) -> dict[str, str]:
    return {f"{name}_hash": layout_hash(name, payload) for name, payload in hash_inputs(data).items()}
