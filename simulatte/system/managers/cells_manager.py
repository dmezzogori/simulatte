from __future__ import annotations

from typing import TYPE_CHECKING, Type

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

    def get_best_picking_cell(self, *, cls: Type[PickingCell] | None = None) -> PickingCell:
        cells = self.cells
        if cls is not None:
            cells = (c for c in cells if isinstance(c, cls))

        cell = min(cells, key=lambda c: len(c.feeding_area))
        if cell is None:
            raise ValueError("No picking cell available")
        return cell
