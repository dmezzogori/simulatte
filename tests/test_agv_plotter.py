from __future__ import annotations

from typing import cast

import matplotlib.pyplot as plt

from simulatte.agv.agv_plotter import AGVPlotter
from simulatte.agv import AGV


class DummyTrip:
    def __init__(self, duration: float):
        self.duration = duration


class DummyAGV:
    def __init__(self):
        self.id = 7
        self.trips = [DummyTrip(60), DummyTrip(120)]


def test_agv_plotter_uses_trips(monkeypatch):
    monkeypatch.setattr(plt, "show", lambda *_, **__: None)

    plotter = AGVPlotter(agv=cast(AGV, DummyAGV()))
    plotter.plot_travel_time()

    assert True  # no exception raised
