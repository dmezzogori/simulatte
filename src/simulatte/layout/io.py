"""Bounded JSON loading and atomic author-layer persistence."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .errors import LayoutError
from .layers import FileLayer
from .types import LayoutLimits


def _check_depth(text: str, limit: int) -> None:
    # Check before json.loads, which can exhaust its recursion limit first.
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > limit + 1:  # root is depth zero, as in parse_patch
                raise LayoutError(f"max_nesting exceeded ({limit})")
        elif char in "]}":
            depth -= 1


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise LayoutError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise LayoutError(f"JSON numbers must be finite: {value}")


def load_layout(path: str | Path, *, limits: LayoutLimits = LayoutLimits()) -> FileLayer:
    with Path(path).open("rb") as stream:
        raw = stream.read(limits.max_file_bytes + 1)
    if len(raw) > limits.max_file_bytes:
        raise LayoutError(f"max_file_bytes exceeded ({limits.max_file_bytes})")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LayoutError("layout JSON must be UTF-8") from exc
    _check_depth(text, limits.max_nesting)
    try:
        data = json.loads(text, object_pairs_hook=_object, parse_constant=_constant)
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, LayoutError):
            raise
        raise LayoutError(f"invalid layout JSON: {exc}") from exc
    layer = FileLayer(data, max_nesting=limits.max_nesting)
    if len(layer.data.get("placements", {})) > limits.max_placements:
        raise LayoutError(f"max_placements exceeded ({limits.max_placements})")
    return layer


def save_layout(path: str | Path, layer: FileLayer) -> None:
    """Save only author data using shortest round-trip floats and atomic replacement."""
    path = Path(path)
    text = json.dumps(layer.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
