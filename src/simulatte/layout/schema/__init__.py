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
from typing import Any

from ..errors import LayoutError


@lru_cache(maxsize=1)
def _schema() -> dict[str, Any]:
    return json.loads(files("simulatte.layout.schema").joinpath("layout-v1.schema.json").read_text())


def schema() -> dict[str, Any]:
    """Return a fresh copy of the published JSON Schema."""
    return json.loads(json.dumps(_schema()))


def _copy(value: Any, path: str, depth: int, max_nesting: int) -> Any:
    if depth > max_nesting:
        raise LayoutError(f"{path}: max_nesting exceeded ({max_nesting})")
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise LayoutError(f"{path}: object keys must be strings")
        return {key: _copy(item, f"{path}/{key}", depth + 1, max_nesting) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_copy(item, f"{path}/{i}", depth + 1, max_nesting) for i, item in enumerate(value)]
    if isinstance(value, float) and not math.isfinite(value):
        raise LayoutError(f"{path}: numbers must be finite")
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise LayoutError(f"{path}: expected JSON data")


def _matches(value: Any, rule: dict[str, Any], path: str) -> bool:
    try:
        _validate(value, rule, path)
    except LayoutError:
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


def _validate(value: Any, rule: dict[str, Any], path: str) -> None:
    if not rule:
        return

    def fail(message: str) -> None:
        raise LayoutError(f"{path}: {message}")

    if "$ref" in rule:
        _validate(value, _schema()["$defs"][rule["$ref"].removeprefix("#/$defs/")], path)
    for keyword in ("oneOf", "anyOf"):
        if keyword in rule:
            matches = sum(_matches(value, branch, path) for branch in rule[keyword])
            if matches == 0 or (keyword == "oneOf" and matches != 1):
                fail(f"does not match {keyword} schema branches")
    kind = rule.get("type")
    checks = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": type(value) in (int, float),
        "integer": type(value) in (int, float) and value % 1 == 0,
        "boolean": type(value) is bool,
    }
    if kind is not None and not checks[kind]:
        fail(f"expected {kind}")
    if "const" in rule and (value != rule["const"] or (isinstance(value, bool) != isinstance(rule["const"], bool))):
        fail("unexpected constant")
    if "enum" in rule and value not in rule["enum"]:
        fail("unexpected value")
    if isinstance(value, dict):
        if any(key not in value for key in rule.get("required", ())):
            fail("missing required field")
        for key, item in value.items():
            if "propertyNames" in rule:
                _validate(key, rule["propertyNames"], f"{path}/{key}")
            child = rule.get("properties", {}).get(key, rule.get("additionalProperties", {}))
            if child is False:
                fail(f"unknown field {key}")
            _validate(item, child, f"{path}/{key}")
    if isinstance(value, list):
        if len(value) < rule.get("minItems", 0) or len(value) > rule.get("maxItems", math.inf):
            fail("invalid array length")
        if rule.get("uniqueItems") and len({_json_key(item) for item in value}) != len(value):
            fail("duplicate array item")
        for i, item in enumerate(value):
            prefix = rule.get("prefixItems", [])
            child = prefix[i] if i < len(prefix) else rule.get("items", {})
            if child is False:
                fail("extra array item")
            _validate(item, child, f"{path}/{i}")
    if isinstance(value, str):
        if len(value) < rule.get("minLength", 0) or ("pattern" in rule and re.search(rule["pattern"], value) is None):
            fail("invalid string")
    if type(value) in (int, float):
        if value < rule.get("minimum", -math.inf) or value > rule.get("maximum", math.inf):
            fail("number outside range")
        if value <= rule.get("exclusiveMinimum", -math.inf):
            fail("number must be positive")


def validate_definition(value: Any, definition: str, *, max_nesting: int = 64) -> Any:
    """Validate and copy one schema definition; do not fill defaults or snap."""
    result = _copy(value, "$", 0, max_nesting)
    _validate(result, _schema()["$defs"][definition], "$")
    return result


def parse_patch(value: Mapping[str, Any], *, max_nesting: int = 64) -> dict[str, Any]:
    """Validate and copy a file-layer patch, retaining authored leaf values."""
    result = _copy(value, "$", 0, max_nesting)
    if isinstance(result, dict) and result.get("schema_version", "1.0") != "1.0":
        raise LayoutError("unsupported layout schema_version; upgrade the layout reader")
    _validate(result, _schema(), "$")
    return result
