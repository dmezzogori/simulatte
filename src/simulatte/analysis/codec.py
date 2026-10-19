"""Collision-free tagged JSON for analytical values (not the legacy trace wire)."""

from __future__ import annotations
from collections.abc import Mapping
import hashlib
import json
import math
from typing import Any
from .types import copied


def to_json(value: Any) -> Any:
    value = copied(value)
    if isinstance(value, Mapping):
        return {"$record": {key: to_json(item) for key, item in value.items()}}
    if isinstance(value, tuple):
        return [to_json(item) for item in value]
    if type(value) is int and abs(value) > 2**53 - 1:
        return {"$int64": str(value)}
    if type(value) is float and not math.isfinite(value):
        return {"$float64": "nan" if math.isnan(value) else "inf" if value > 0 else "-inf"}
    return value


def from_json(value: Any) -> Any:
    if type(value) is dict:
        if set(value) == {"$record"} and type(value["$record"]) is dict:
            return {key: from_json(item) for key, item in value["$record"].items()}
        if set(value) == {"$int64"} and type(value["$int64"]) is str:
            result = int(value["$int64"])
            if str(result) == value["$int64"] and 2**53 <= abs(result) and -(2**63) <= result < 2**63:
                return result
        if set(value) == {"$float64"} and value["$float64"] in ("nan", "inf", "-inf"):
            return float(value["$float64"])
        raise ValueError("Invalid analytical JSON tag")
    if type(value) is list:
        return [from_json(item) for item in value]
    if type(value) is int and abs(value) > 2**53 - 1 or type(value) is float and not math.isfinite(value):
        raise ValueError("Analytical JSON requires an exact numeric tag")
    return copied(value)


def dumps(value: Any) -> bytes:
    return json.dumps(to_json(value), sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=True).encode(
        "ascii"
    )


def loads(data: bytes | str) -> Any:
    return from_json(json.loads(data))


def identity(value: Any) -> str:
    return hashlib.sha256(dumps(value)).hexdigest()
