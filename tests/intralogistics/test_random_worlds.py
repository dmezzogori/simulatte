"""The random worlds of ``random_worlds.py``: a deterministic description, equal runs in both modes, a monitor that
detects a violation."""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest

from simulatte.intralogistics.graph import Node
from simulatte.intralogistics.parking import ParkingArea
from simulatte.intralogistics.traffic_holdings import HoldingsLedger
from tests.intralogistics.random_worlds import KNOWN_LIMIT, FEATURES, exercised, present, run_world, world_spec

ROOT = Path(__file__).resolve().parents[2]
NOT_IN_A_SPEC = {"a traffic wait", "a stuck wait", "a deadlock", "a make-way move"}
EXERCISED = {
    26: {"charging", "stranding", "a zone held by two", "a hold-back zone reserved", "the second fleet moves"},
    88: {"a zone held by two", "a recorded length traversed", "a block piece traversed", "an order cancelled"},
    182: {"charging", "stranding", "the second fleet moves", "a node of capacity 2 held by two", "parking entered"},
    236: {"charging", "stranding", "a block piece traversed", "a one-way arc traversed", "a make-way move"},
    272: {"a passing segment held by two", "stranding", "a recorded length traversed", "a deadlock"},
}
"""Five worlds and some of what their run under ``ReservationController`` and the default router exercises."""


def test_world_spec_is_deterministic_per_seed():
    assert world_spec(45) == world_spec(45)
    assert world_spec(45) != world_spec(46)
    code = "from tests.intralogistics.random_worlds import world_spec; print(repr(world_spec(45)))"
    for hash_seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        out = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True
        )
        assert out.stdout.strip() == repr(world_spec(45))


def test_no_world_has_two_nodes_at_one_position():
    for seed in range(200):
        positions = [(x, y) for _, x, y in world_spec(seed).nodes]
        assert len(set(positions)) == len(positions), seed


def test_every_world_has_vehicles_in_every_fleet():
    for seed in range(1000):
        fleets = world_spec(seed).fleets
        assert len(fleets[0].agvs) >= 2 and all(fleet.agvs for fleet in fleets), seed


@pytest.mark.parametrize("seed", sorted(EXERCISED))
def test_a_world_runs_alike_built_explicitly_and_through_the_layout(seed):
    spec = world_spec(seed)
    explicit = run_world(spec, mode="explicit", controller="reservation")
    layout = run_world(spec, mode="layout", controller="reservation")
    assert layout.events and layout.quiescent and not layout.violations
    assert explicit == layout
    assert EXERCISED[seed] <= exercised(spec, layout.events) <= present(spec) | NOT_IN_A_SPEC


def test_the_monitor_reports_an_admission_that_ignores_node_capacity(monkeypatch):
    admit = HoldingsLedger.blockers

    def blockers(self, agv, item, among=None):
        if among is None and isinstance(item.resource, Node):
            return []
        return admit(self, agv, item, among)

    monkeypatch.setattr(HoldingsLedger, "blockers", blockers)
    record = run_world(world_spec(88), mode="layout", controller="reservation")
    found = {violation[1] for violation in record.violations}
    assert {"PHYSICAL node over capacity", "LEDGER node over capacity"} <= found


def test_the_monitor_reports_a_built_network_that_differs_from_the_spec(monkeypatch):
    """The monitor's geometry comes from the spec, so a layout that loses a recorded length is noticed."""
    from simulatte.layout import types

    declare = types.Explicit.__init__

    def without_lengths(self, graph, **options):
        declare(self, graph, **options)
        object.__setattr__(self, "arcs", tuple(dataclasses.replace(arc, length=None) for arc in self.arcs))

    monkeypatch.setattr(types.Explicit, "__init__", without_lengths)
    record = run_world(world_spec(88), mode="layout", controller="reservation")
    assert "built arcs differ from the spec" in {violation[1] for violation in record.violations}


def test_the_features_are_named_once():
    assert len(set(FEATURES)) == len(FEATURES) and NOT_IN_A_SPEC <= set(FEATURES)


def test_a_parking_slot_kept_while_charging_on_the_way_to_park_is_the_known_limit_not_a_violation():
    """Seed 619: agv-a takes a slot of P0 at 97.0 as it sets off to park, turns to the charger at 122.0, charges from
    148.6 to 220.1 and leaves P0 as the charge ends. Fleet behaviour, the same in both modes."""
    spec = world_spec(619)
    explicit = run_world(spec, mode="explicit", controller="free", router="shortest")
    layout = run_world(spec, mode="layout", controller="free", router="shortest")
    assert explicit == layout and not layout.violations
    (case,) = layout.known_limits
    assert case[1:4] == (KNOWN_LIMIT, "P0", "agv-a")
    assert case[4] == pytest.approx(97.003, abs=1e-3) and case[5] == pytest.approx(220.061, abs=1e-3)


def test_a_parking_slot_held_by_an_agv_resting_elsewhere_for_another_reason_is_a_violation(monkeypatch):
    monkeypatch.setattr(ParkingArea, "leave", lambda self, agv: None)  # slots are never given back
    record = run_world(world_spec(619), mode="layout", controller="free", router="shortest")
    assert not record.known_limits
    assert "parking slot of an AGV resting elsewhere" in {violation[1] for violation in record.violations}
