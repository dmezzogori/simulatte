from __future__ import annotations

from typing import Generic, TypeVar

import simpy

from simulatte.observables.observable_area.base import ObservableArea
from simulatte.utils import EnvMixin

T = TypeVar("T", bound=ObservableArea)


class Observer(EnvMixin, Generic[T]):
    """
    An observer is in charge of observing an observable area of an element of the simulation.
    When the observable area signal event is triggered, the observer will execute its main process.
    """

    def __init__(self, *, observable_area: T, register_main_process: bool = True) -> None:
        EnvMixin.__init__(self)

        self.observable_area = observable_area
        self._main = None
        if register_main_process:
            self._main = self.env.process(self.run())

    def run(self):
        """
        Run the observer main process.
        Each observer waits for the signal event to be triggered from the assigned observable area.
        Once triggered, the observer will execute its main process.
        """
        while True:
            try:
                yield self.observable_area.signal_event
                self._main_process()
            except simpy.Interrupt:
                break

    def next(self):
        """
        Used to get the next item from the observable area.
        """
        raise NotImplementedError

    def _main_process(self, *args, **kwargs):
        """
        The main process of the observer.
        """
        raise NotImplementedError
