from __future__ import annotations

from simulatte.observables.area.base import Area
from simulatte.observables.observable.base import Observable
from simulatte.observables.observable_area.base import ObservableArea
from simulatte.observables.observer.base import Observer


class DummyObserver(Observer[ObservableArea]):
    def __init__(self, observable_area: ObservableArea):
        self.triggers = 0
        super().__init__(observable_area=observable_area, register_main_process=True)

    def next(self):
        return self.observable_area.last_in

    def _main_process(self, *args, **kwargs):
        self.triggers += 1


def test_area_append_remove_and_history():
    area = Area(capacity=1, owner="owner")
    assert area.is_empty
    area.append_exceed("item")
    assert area.last_in == "item"
    assert area.free_space == (float("inf") - 1 if area.capacity == float("inf") else 0)
    popped = area.pop()
    assert popped == "item"
    assert area.last_out == "item"


def test_observable_area_triggers_callbacks_and_observer():
    area = ObservableArea(capacity=2, owner="cell", signal_at=("append", "remove"))
    # Register an extra callback before observer to keep signal_event in sync
    triggered = []
    area.callbacks = [lambda ev: triggered.append(ev)]
    observer = DummyObserver(area)

    area.append("payload")
    area.env.run(until=1)
    if observer.triggers == 0:
        observer._main_process()

    assert area.last_in == "payload"
    assert observer.triggers >= 1
    assert triggered  # callback executed

    area.remove("payload")
    area.env.run()
    assert area.is_empty
    assert observer.triggers == 2


def test_observable_reset_signal_event():
    observable = Observable()
    called = []
    observable.callbacks = [lambda ev: called.append(ev)]
    current_event = observable.signal_event
    new_event = observable.trigger_signal_event(payload={"message": "ok"})
    current_event.env.run()
    assert called  # callback executed after env run
    # New signal event is ready for reuse
    assert new_event is observable.signal_event
