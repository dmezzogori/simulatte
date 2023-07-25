from __future__ import annotations

from typing import TYPE_CHECKING, Generic, TypeVar

from simulatte.picking_cell.areas import ObservableArea

if TYPE_CHECKING:
    from simulatte.controllers import SystemController
    from simulatte.picking_cell import PickingCell


T = TypeVar("T", bound=ObservableArea)


class Observer(Generic[T]):
    """
    An observer is in charge of observing a specific logical area of a picking cell.

    It implements a signal event, which is triggered by other objects when there is
    the need to notify the observer about the availability of the observed area.
    """

    def __init__(self, *, system: SystemController, observable_area: T, register_main_process: bool = True) -> None:
        self.system = system
        self.observable_area = observable_area
        if register_main_process:
            self._main = self.system.env.process(self.run())

    @property
    def cell(self) -> PickingCell:
        return self.observable_area.cell

    def run(self):
        """
        Run the observer main process.
        Each observer waits for the signal event to be triggered from the assigned observable area.
        Once triggered, the observer will execute its main process.
        """
        while True:
            yield self.observable_area.signal_event
            self._main_process()

    def next(self):
        """
        Used to get the next item from the observable area.
        """
        raise NotImplementedError

    def _main_process(self):
        """
        The main process of the observer.
        """
        raise NotImplementedError
