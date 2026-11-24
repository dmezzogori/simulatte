"""Pure structural patch validation against the bundled schema.

Only local definitions are interpreted; ``$schema`` in user input is informational.
File I/O, duplicate JSON keys, merging and resolved-model validation belong to the
layer loader. No optional JSON Schema library is needed at runtime.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from functools import lru_cache
from importlib.resources import files
from typing import Any, NoReturn

from ..errors import LayoutError


@lru_cache(maxsize=1)
def _schema() -> dict[str, Any]:
    return json.loads(files("simulatte.layout.schema").joinpath("layout-v1.schema.json").read_text())


def schema() -> dict[str, Any]:
    """Return a fresh copy of the published JSON Schema."""
    return json.loads(json.dumps(_schema()))


def _utf8(value: str, path: tuple[str, ...]) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise LayoutError(
            f"{'/'.join(('$', *path))}: strings must be valid UTF-8", code="schema-error", path=path
        ) from exc


def _copy(value: Any, path: tuple[str, ...], depth: int, max_nesting: int) -> Any:
    if depth > max_nesting:
        raise LayoutError(
            f"{'/'.join(('$', *path))}: max_nesting exceeded ({max_nesting})", code="schema-error", path=path
        )
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise LayoutError(f"{'/'.join(('$', *path))}: object keys must be strings", code="schema-error", path=path)
        for key in value:
            _utf8(key, path)
        return {key: _copy(item, (*path, key), depth + 1, max_nesting) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_copy(item, (*path, str(i)), depth + 1, max_nesting) for i, item in enumerate(value)]
    if isinstance(value, float) and not math.isfinite(value):
        raise LayoutError(f"{'/'.join(('$', *path))}: numbers must be finite", code="schema-error", path=path)
    if isinstance(value, str):
        _utf8(value, path)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise LayoutError(f"{'/'.join(('$', *path))}: expected JSON data", code="schema-error", path=path)


class _Mismatch(Exception):
    """A value does not match a rule, in a validation whose message nobody reads (a branch of ``oneOf``/``anyOf``)."""


def _matches(value: Any, rule: dict[str, Any], path: tuple[str, ...]) -> bool:
    try:
        _validate(value, rule, path, quiet=True)
    except _Mismatch:
        return False
    return True


def _json_key(value: Any) -> tuple[Any, ...]:
    """Hashable JSON equality key: numbers compare by value; bool stays distinct."""
    if isinstance(value, dict):
        return ("object", frozenset((key, _json_key(item)) for key, item in value.items()))
    if isinstance(value, list):
        return ("array", tuple(_json_key(item) for item in value))
    if isinstance(value, bool):
        return ("boolean", value)
    return ("scalar", value)


_KINDS = {
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    "number": lambda value: type(value) in (int, float),
    "integer": lambda value: type(value) in (int, float) and value % 1 == 0,
    "boolean": lambda value: type(value) is bool,
}


_DISCRIMINATORS: dict[int, tuple[list[dict[str, Any]], tuple[Any, ...] | None]] = {}


def _branches_of_kind(branches: list[dict[str, Any]], kind: Any) -> list[dict[str, Any]]:
    """The branches that a value whose ``kind`` is `kind` can match.

    When every branch fixes ``kind`` to a constant (the addresses and selectors), a branch with another constant rejects
    the value at its ``kind`` property whatever else the value holds, so it is not evaluated; the number of matching
    branches is the same. Branches of any other shape are all kept.
    """
    entry = _DISCRIMINATORS.get(id(branches))  # the entry keeps `branches` alive, so its id is not reused
    if entry is None:
        consts: list[Any] | None = []
        for branch in branches:
            fixed = branch.get("properties", {}).get("kind", {})
            if "const" not in fixed:
                consts = None
                break
            consts.append(fixed["const"])
        entry = _DISCRIMINATORS[id(branches)] = (branches, None if consts is None else tuple(consts))
    consts_of = entry[1]
    if consts_of is None:
        return branches
    return [
        branch
        for branch, const in zip(branches, consts_of)
        if kind == const and isinstance(kind, bool) == isinstance(const, bool)
    ]


def _fail(path: tuple[str, ...], message: str, quiet: bool) -> NoReturn:
    """Reject the value at `path`; when `quiet` the caller only needs to know that it does not match."""
    if quiet:
        raise _Mismatch
    raise LayoutError(f"{'/'.join(('$', *path))}: {message}", code="schema-error", path=path)


def _validate(value: Any, rule: dict[str, Any], path: tuple[str, ...], quiet: bool = False) -> None:
    if not rule:
        return
    if "$ref" in rule:
        _validate(value, _schema()["$defs"][rule["$ref"].removeprefix("#/$defs/")], path, quiet)
    for keyword in ("oneOf", "anyOf"):
        if keyword in rule:
            branches = rule[keyword]
            if isinstance(value, dict) and "kind" in value:
                branches = _branches_of_kind(branches, value["kind"])
            matches = sum(_matches(value, branch, path) for branch in branches)
            if matches == 0 or (keyword == "oneOf" and matches != 1):
                _fail(path, f"does not match {keyword} schema branches", quiet)
    kind = rule.get("type")
    if kind is not None and not _KINDS[kind](value):
        _fail(path, f"expected {kind}", quiet)
    if "const" in rule and (value != rule["const"] or (isinstance(value, bool) != isinstance(rule["const"], bool))):
        _fail(path, "unexpected constant", quiet)
    if "enum" in rule and value not in rule["enum"]:
        _fail(path, "unexpected value", quiet)
    if isinstance(value, dict):
        if any(key not in value for key in rule.get("required", ())):
            _fail(path, "missing required field", quiet)
        for key, item in value.items():
            if "propertyNames" in rule:
                _validate(key, rule["propertyNames"], (*path, key), quiet)
            child = rule.get("properties", {}).get(key, rule.get("additionalProperties", {}))
            if child is False:
                _fail(path, f"unknown field {key}", quiet)
            _validate(item, child, (*path, key), quiet)
    if isinstance(value, list):
        if len(value) < rule.get("minItems", 0) or len(value) > rule.get("maxItems", math.inf):
            _fail(path, "invalid array length", quiet)
        if rule.get("uniqueItems") and len({_json_key(item) for item in value}) != len(value):
            _fail(path, "duplicate array item", quiet)
        for i, item in enumerate(value):
            prefix = rule.get("prefixItems", [])
            child = prefix[i] if i < len(prefix) else rule.get("items", {})
            if child is False:
                _fail(path, "extra array item", quiet)
            _validate(item, child, (*path, str(i)), quiet)
    if isinstance(value, str):
        if len(value) < rule.get("minLength", 0) or ("pattern" in rule and re.search(rule["pattern"], value) is None):
            _fail(path, "invalid string", quiet)
    if type(value) in (int, float):
        if value < rule.get("minimum", -math.inf) or value > rule.get("maximum", math.inf):
            _fail(path, "number outside range", quiet)
        if value <= rule.get("exclusiveMinimum", -math.inf):
            _fail(path, "number must be positive", quiet)


def validate_definition(value: Any, definition: str, *, max_nesting: int = 64) -> Any:
    """Validate and copy one schema definition; do not fill defaults or snap."""
    result = _copy(value, (), 0, max_nesting)
    _validate(result, _schema()["$defs"][definition], ())
    return result


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> tuple[Any, ...] | list[Any]:
    return value if isinstance(value, (list, tuple)) else ()


def traffic_fields(value: Mapping[str, Any]) -> list[tuple[str, ...]]:
    """The paths of the schema 1.1 fields (spec §12) that a layout patch uses, in a fixed order.

    ``passing`` on lanes, explicit arcs, connectors, the lattice network and edge overrides; ``length`` on explicit
    arcs; ``network.block_length`` and ``network.zones``. An ``$unset`` marker counts as use, since it names the
    field. The patch must be valid against the schema."""
    network = _map(value.get("network"))
    found: list[tuple[str, ...]] = [
        ("network", name) for name in ("passing", "block_length", "zones") if name in network
    ]
    for lane_id, lane in _map(network.get("lanes")).items():
        if "passing" in _map(lane):
            found.append(("network", "lanes", lane_id, "passing"))
    for index, arc in enumerate(_list(network.get("arcs"))):
        for name in ("passing", "length"):
            if name in _map(arc):
                found.append(("network", "arcs", str(index), name))
    for index, record in enumerate(_list(value.get("overrides"))):
        if "passing" in _map(_map(record).get("properties")):
            found.append(("overrides", str(index), "properties", "passing"))
    for entity, placement in _map(value.get("placements")).items():
        for port, record in _map(_map(placement).get("ports")).items():
            if "passing" in _map(_map(record).get("connector")):
                found.append(("placements", entity, "ports", port, "connector", "passing"))
    return found


def layout_version(value: Mapping[str, Any]) -> str:
    """``"1.1"`` when the patch uses a 1.1 field, else ``"1.0"``."""
    return "1.1" if traffic_fields(value) else "1.0"


def parse_patch(value: Mapping[str, Any], *, max_nesting: int = 64) -> dict[str, Any]:
    """Validate and copy a file-layer patch, retaining authored leaf values.

    Versions 1.0 and 1.1 share one schema; a 1.0 patch that uses a 1.1 field is rejected here, naming the field."""
    result = _copy(value, (), 0, max_nesting)
    if isinstance(result, dict) and result.get("schema_version", "1.0") not in ("1.0", "1.1"):
        raise LayoutError(
            "unsupported layout schema_version; upgrade the layout reader",
            code="schema-error",
            path=("schema_version",),
        )
    _validate(result, _schema(), ())
    if result.get("schema_version", "1.0") == "1.0" and (fields := traffic_fields(result)):
        raise LayoutError(
            f"{'/'.join(fields[0])}: layout schema 1.0 does not allow this field; set schema_version to 1.1",
            code="schema-error",
            path=fields[0],
        )
    return result
