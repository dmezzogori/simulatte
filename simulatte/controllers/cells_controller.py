from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from simulatte.controllers import SystemController
    from simulatte.picking_cell import PickingCell
    from simulatte.policies import CellSelectionPolicy
    from simulatte.requests import PalletRequest


class CellsController(Protocol):
    """
    CellsController manages a set of PickingCell instances and provides
    methods to select the best one based on a policy.

    Attributes:

    system_controller: SystemController - The parent system controller instance.

    cell_selection_policy: CellSelectionPolicy - The policy to use for selecting cells.

    _picking_cells: set[PickingCell] - The registered picking cells.


    Methods:

    register_cell: Registers a new PickingCell instance.

    picking_cells: Returns the set of registered picking cells.

    get_best_picking_cell: Selects the best picking cell from the registered ones
                          based on the cell_selection_policy. An optional cls
                          argument filters cells by type.
    """

    def __init__(self, *, cell_selection_policy: CellSelectionPolicy):
        self.system_controller = None
        self._cell_selection_policy = cell_selection_policy
        self._picking_cells: set[PickingCell] = set()

    @property
    def picking_cells(self) -> set[PickingCell]:
        return self._picking_cells

    @lru_cache(maxsize=128)
    def get_cells_by_type(self, type_: type[PickingCell]) -> tuple[PickingCell]:
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

    def filter_picking_cell_type_for_pallet_request(self, *, pallet_request: PalletRequest) -> type[PickingCell]:
        """
        Filters the registered picking cells to only those that can handle the
        given pallet request.

        Parameters:
        pallet_request (PalletRequest): The pallet request to filter cells for.

        Returns:
        A set of picking cells that can handle the pallet request.

        Steps:
        1. Start with all registered picking cells
        2. Filter to only cells that can handle the pallet request
        3. Return the filtered set
        """
        ...

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
            cells = (c for c in cells if isinstance(c, cls))

        return self._cell_selection_policy(cells)
