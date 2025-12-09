from __future__ import annotations

import random

import numpy as np

from simulatte.simulation import Simulation


class DummySimulation(Simulation[dict]):
    def build(self) -> None:
        # schedule a simple timeout so run() advances time
        self.env.timeout(5)

    @property
    def results(self):
        return [1, 2, 3]

    def summary(self) -> None:  # pragma: no cover - side-effect only
        print("summary")


def test_simulation_seeds_and_run_advance_time(capsys):
    sim = DummySimulation(config={}, seed=42)

    # Seeds are applied globally
    assert random.random() == 0.6394267984578837
    assert np.random.rand() == 0.3745401188473625

    sim.run(until=5, debug=True)
    assert sim.env.now == 5

    captured = capsys.readouterr()
    assert "Simulation took:" in captured.out
