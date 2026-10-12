"""Domain-separated identity for execution data, independent of hash randomization."""

from __future__ import annotations

import hashlib
from typing import Any

from simulatte._wire import canonical_pack, freeze


def canonical_hash(domain: str, value: Any) -> str:
    return hashlib.sha256(b"simulatte\0" + domain.encode("ascii") + b"\0" + canonical_pack(freeze(value))).hexdigest()
