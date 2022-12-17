from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulatte import System
    from simulatte.picking_cell import PickingCell


class CellsManager:
    def __init__(self, system: System, *cells: PickingCell) -> None:
        self.system = system
        self._cells = list(cells)

    def __call__(self, cell: PickingCell) -> CellsManager:
        self._cells.append(cell)
        return self

    @property
    def cells(self):
        return self._cells
