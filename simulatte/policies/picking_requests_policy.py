from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from simulatte.requests import Request


class PickingRequestSelectionPolicy(Protocol):
    def __call__(self, picking_requests: Sequence[Request]) -> list[Request]:
        ...
