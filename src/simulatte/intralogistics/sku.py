from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SKU:
    id: str
    weight: float
    volume: float
    attributes: tuple[tuple[str, Any], ...] = ()

    def get_attribute(self, key: str, default: Any = None) -> Any:
        for k, v in self.attributes:
            if k == key:
                return v
        return default
