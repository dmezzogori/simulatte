from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from simulatte.picking_cell.cell import PickingCell


class CellSelectionPolicy(Protocol):
    """
    CellSelectionPolicy defines the interface for implementing policies to
    select a PickingCell from a set of available ones.

    The __call__ method takes the following parameter:

    picking_cells: set[PickingCell] - The set of PickingCell objects to choose from.

    It returns either a PickingCell instance selected by the policy, or None if
    no suitable cell was found.

    Subclasses should implement the logic to select the best PickingCell based
    on the required policy, such as distributing workload evenly.
    """

    def __call__(self, picking_cells: set[PickingCell]) -> PickingCell | None: ...
