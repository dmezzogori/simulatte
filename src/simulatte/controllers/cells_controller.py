from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING
from collections.abc import Callable

if TYPE_CHECKING:
    from simulatte.controllers.system_controller import SystemController
    from simulatte.picking_cell.cell import PickingCell


def first_available(cells: set[PickingCell]) -> PickingCell | None:
    """Simple default cell selector."""

    for cell in cells:
        return cell
    return None


class CellsController:
    """
    Lightweight controller over registered picking cells.
    Selection is delegated to a small callable instead of a dedicated policy class.
    """

    def __init__(self, *, select_cell: Callable[[set[PickingCell]], PickingCell | None] = first_available):
        self.system_controller: SystemController | None = None
        self._select_cell = select_cell
        self._picking_cells: set[PickingCell] = set()

    @property
    def picking_cells(self) -> set[PickingCell]:
        return self._picking_cells

    @lru_cache(maxsize=128)
    def get_cells_by_type(self, type_: type[PickingCell]) -> tuple[PickingCell, ...]:
        return tuple(c for c in self._picking_cells if isinstance(c, type_))

    def register_system(self, system: SystemController):
        self.system_controller = system

    def register_cell(self, picking_cell: PickingCell):
        """
        Registers a new PickingCell instance with this CellsController.

        Parameters:
        picking_cell (PickingCell): The PickingCell instance to register.

        This adds the picking_cell to the internal _picking_cells set that
        tracks all registered cells.

        Allows the CellsController to be aware of a new PickingCell that
        should be considered when selecting the best cell based on the policy.
        """

        self._picking_cells.add(picking_cell)

    def get_best_picking_cell(self, *, cls: type[PickingCell] | None = None):
        """
        Get the best picking cell from the registered ones based on the cell
        selection policy.

        Parameters:
        cls: type[PickingCell] | None - Optional cell type to filter on. If given,
            only cells of that type are considered.

        Returns:
        The picking cell chosen as best by the cell selection policy.

        Steps:
        1. Start with all registered picking cells
        2. If cls is given, filter to only cells of that type
        3. Pass the cells set to the cell selection policy
        4. Return the cell it chooses as best
        """

        cells = self._picking_cells
        if cls is not None:
            cells = {c for c in cells if isinstance(c, cls)}

        return self._select_cell(cells)
