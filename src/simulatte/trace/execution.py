"""Validation for optional execution-v1 provenance; never part of the domain digest."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import math

from simulatte._wire import FrozenMap, freeze


def _map(value, label) -> FrozenMap:
    if not isinstance(value, Mapping) or type(value.get("schema")) is not int or value["schema"] != 1:
        raise ValueError(f"Invalid {label} execution metadata schema")
    result = freeze(dict(value))
    assert isinstance(result, FrozenMap)
    return result


def header(value) -> FrozenMap:
    from simulatte.execution.schema import ExecutionRequest, request_id

    data = _map(value, "requested")
    if not {"request", "request_id"} <= data.keys():
        raise ValueError("Missing execution request fields")
    raw = data["request"]
    if not isinstance(raw, Mapping):
        raise ValueError("Invalid execution request mapping")
    request = ExecutionRequest.from_dict(raw)
    if data["request_id"] != request_id(request):
        raise ValueError("Execution request identity mismatch")
    return data


def initial(resolved) -> FrozenMap:
    from simulatte.execution.schema import ResolvedManifest

    data = _map(resolved, "resolved")
    names = ("runtime", "dependencies", "imports", "layout", "capabilities", "warnings")
    if not set(names) <= data.keys() or not isinstance(data["warnings"], (tuple, list)):
        raise ValueError("Invalid resolved execution fields")
    values: Any = {name: data[name] for name in names}
    try:
        ResolvedManifest(**values)
    except (ValueError, TypeError) as error:
        raise ValueError("Invalid resolved execution metadata") from error
    value = freeze({"schema": 1, "resolved": data})
    assert isinstance(value, FrozenMap)
    return value


def footer(termination, actual_end) -> FrozenMap:
    if type(termination) is not str or not termination or len(termination.encode("utf-8")) > 4096:
        raise ValueError("Invalid execution termination reason")
    if type(actual_end) not in (int, float) or not math.isfinite(actual_end) or actual_end < 0:
        raise ValueError("Invalid execution actual end")
    return FrozenMap({"schema": 1, "termination": termination, "actual_end": float(actual_end)})


def read(header_record, initial_record, footer_record, *, has_initial):
    optional = header_record["features"].get("optional", ())
    if not isinstance(optional, (list, tuple)) or "execution-v1" not in optional:
        return None
    requested = header(header_record["execution"])
    resolved = None
    if has_initial:
        data = _map(initial_record, "initial")
        initial(data["resolved"])
        resolved = data
    ended = None
    if footer_record is not None:
        data = _map(footer_record["execution"], "final")
        footer(data["termination"], data["actual_end"])
        ended = data
        if ended["actual_end"] != footer_record["cursor"][0]:
            raise ValueError("Execution actual end differs from trace cursor")
    return FrozenMap({"header": requested, "initial": resolved, "footer": ended})
