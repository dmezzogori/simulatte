"""The random worlds of ``random_worlds.py``: a deterministic description, runs that reproduce the fingerprints pinned
while both modes existed, a monitor that detects a violation."""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest

from simulatte._wire import FrozenMap
from simulatte.intralogistics import events as logistics_events
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.parking import ParkingArea
from simulatte.intralogistics.reservation import ReservationController
from simulatte.intralogistics.traffic_holdings import HoldingsLedger
from tests.intralogistics import random_worlds
from tests.intralogistics.random_worlds import (
    FEATURES,
    KNOWN_LIMIT,
    KNOWN_LIMIT_WITHDRAWN,
    exercised,
    present,
    run_world,
    world_spec,
)

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
FINGERPRINTS = {
    26: "f1de1cc861081516b04b41647fee919ab85822bf8d603fb4de349fd0351592ec",
    88: "c539208e1d5b79e8530e657f637adc1eefad87a39ff7f949b749f10e1c076f99",
    182: "e36b053f438b2090df7cb1fe932b0d9dd2ed678619929b5c234804deabfa9024",
    236: "761629ac3bcb4ab91b8c6835cf4ef40de65ce15312eb1ad879a88a6001aa0bca",
    272: "b4f29be8b9f03f51449ca4b261f5e90c4b3f324ad0fbc6833096ec3227df05bf",
    619: "bf99183019763d143b258513bf38c4b1caaf0cc98fc393b513f2e5383d85c618",
}
"""``env.fingerprint()`` of the runs of the tests below (619 under ``FreeFlowController`` and the shortest-distance
router), pinned at the last commit with explicit mode (e4afae4), where the run built with concrete objects and the run
built through the layout gave equal run records."""


SAFETY = {
    (100017, "reservation", "shortest"): (
        {"a passing segment held by two", "a deadlock", "charging", "stranding"},
        "cc03562cc25fb8139d0f6c496065a1fad6a5a022730f66f4558205ae2f53236d",
    ),
    (100039, "reservation", "congestion"): (
        {"a zone held by two", "a deadlock", "charging", "stranding"},
        "c262325e549be79d37b1c951a42d1b7cbd6360eba1178fd3fe78f571d4326799",
    ),
    (100083, "reservation", "fastest"): (
        {"a zone held by two", "a deadlock", "charging", "stranding"},
        "7b2fc028d750341b43d6ef410c9a466f6794c884cbb7cc7f136be4369c59f565",
    ),
    (100040, "reservation", "fastest"): (
        {"a zone held by two", "a deadlock", "charging", "stranding"},
        "e223bd9582c603d3de521da0324212f95d095945f9adb26353041b902af96f5a",
    ),
    (100045, "free", "fastest"): (
        {"a passing segment traversed", "parking entered", "charging", "stranding", "the second fleet moves"},
        "4f54418663b6befcdd38c2c145726151bf6564b04c3ed0d2473bdf9695531841",
    ),
}
"""Five runs of the safety check (`benchmarks/layout_safety.py`, spec §8.4), by (seed, controller, router): what each
exercises and its ``env.fingerprint()`` at the commit of the check. Together they exercise charging, stranding, a zone
and a passing segment held by two, and a deadlock."""


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
def test_a_world_reproduces_the_run_both_modes_agreed_on(seed):
    spec = world_spec(seed)
    layout = run_world(spec, controller="reservation")
    assert layout.events and layout.quiescent and not layout.violations
    assert layout.fingerprint.digest == FINGERPRINTS[seed]
    assert EXERCISED[seed] <= exercised(spec, layout.events) <= present(spec) | NOT_IN_A_SPEC


def test_the_monitor_reports_an_admission_that_ignores_node_capacity(monkeypatch):
    admit = HoldingsLedger.blockers

    def blockers(self, agv, item, among=None):
        if among is None and isinstance(item.resource, Node):
            return []
        return admit(self, agv, item, among)

    monkeypatch.setattr(HoldingsLedger, "blockers", blockers)
    record = run_world(world_spec(88), controller="reservation")
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
    record = run_world(world_spec(88), controller="reservation")
    assert "built arcs differ from the spec" in {violation[1] for violation in record.violations}


def test_the_monitor_reports_a_move_on_an_arc_the_spec_lacks_and_goes_on(monkeypatch):
    """The reference graph loses the first arc a vehicle uses: the geometry violation is kept, then the move is
    reported, and the run is not cut short by an exception."""
    spec = world_spec(88)
    probe = run_world(spec, controller="reservation")
    moves = [dict(fields) for kind, fields in probe.events if kind is logistics_events.AgvMoveStarted]
    used = {moves[0]["from_node"], moves[0]["to_node"]}
    reference = random_worlds.reference_graph

    def without_the_used_arc(spec):
        graph = reference(spec)
        arcs = [
            Arc(a, b, False, speed_limit=arc.speed_limit, length=arc.length, passing=arc.passing)
            for a in graph.nodes
            for b in graph.neighbors(a)
            if {a.id, b.id} != used
            for arc in (graph.arc_between(a, b),)
        ]
        return LayoutGraph(list(graph.nodes), arcs)

    monkeypatch.setattr(random_worlds, "reference_graph", without_the_used_arc)
    record = run_world(spec, controller="reservation")
    found = {violation[1] for violation in record.violations}
    assert {"built arcs differ from the spec", "move on an arc the spec does not have"} <= found
    assert record.quiescent and len(record.events) == len(probe.events)


def _declared_with(monkeypatch, change):
    """Make every ``Explicit`` carry what `change` turns its capacities and zones into (a scratch layout bug)."""
    from simulatte.layout import types

    declare = types.Explicit.__init__

    def wrong(self, graph, **options):
        declare(self, graph, **options)
        change(self)

    monkeypatch.setattr(types.Explicit, "__init__", wrong)


def test_the_monitor_reports_a_node_capacity_the_layout_resolves_wrongly(monkeypatch):
    spec = world_spec(182)
    assert spec.capacities
    node_id = spec.capacities[0][0]

    def lose_one(explicit):
        nodes = tuple(
            dataclasses.replace(node, capacity=None) if node.id == node_id else node for node in explicit.nodes
        )
        object.__setattr__(explicit, "nodes", nodes)

    _declared_with(monkeypatch, lose_one)
    record = run_world(spec, controller="reservation")
    assert "built node capacities differ from the spec" in {violation[1] for violation in record.violations}


@pytest.mark.parametrize("what", ["capacity", "hold_back", "members", "missing"])
def test_the_monitor_reports_a_zone_the_layout_resolves_wrongly(monkeypatch, what):
    spec = world_spec(26)
    assert spec.zones and any(zone.hold_back for zone in spec.zones)

    def alter(explicit):
        zones = {zone_id: dict(zone) for zone_id, zone in explicit.zones.items()}
        first = sorted(zones, key=lambda zone_id: -len(zones[zone_id]["members"]))[0]  # the zone with most members
        assert len(zones[first]["members"]) > 1
        if what == "capacity":
            zones[first]["capacity"] = zones[first].get("capacity", 1) + 1
        elif what == "hold_back":
            zones[first]["hold_back"] = not zones[first].get("hold_back", False)
        elif what == "members":
            zones[first]["members"] = list(zones[first]["members"])[:-1]
        else:
            del zones[first]
        object.__setattr__(explicit, "zones", zones)

    _declared_with(monkeypatch, alter)
    record = run_world(spec, controller="reservation")
    assert "built zones differ from the spec" in {violation[1] for violation in record.violations}


def test_the_features_are_named_once():
    assert len(set(FEATURES)) == len(FEATURES) and NOT_IN_A_SPEC <= set(FEATURES)


def test_a_parking_slot_kept_while_charging_on_the_way_to_park_is_the_known_limit_not_a_violation():
    """Seed 619: agv-a takes a slot of P0 at 97.0 as it sets off to park, turns to the charger at 122.0, charges from
    148.6 to 220.1 and leaves P0 as the charge ends. Fleet behaviour, pinned by a fingerprint taken while both
    modes existed."""
    spec = world_spec(619)
    layout = run_world(spec, controller="free", router="shortest")
    assert not layout.violations
    assert layout.fingerprint.digest == FINGERPRINTS[619]
    (case,) = layout.known_limits
    assert case[1:4] == (KNOWN_LIMIT, "P0", "agv-a")
    assert case[4] == pytest.approx(97.003, abs=1e-3) and case[5] == pytest.approx(220.061, abs=1e-3)


def test_a_parking_slot_held_by_an_agv_resting_elsewhere_for_another_reason_is_a_violation(monkeypatch):
    monkeypatch.setattr(ParkingArea, "leave", lambda self, agv: None)  # slots are never given back
    record = run_world(world_spec(619), controller="free", router="shortest")
    assert not record.known_limits
    assert "parking slot of an AGV resting elsewhere" in {violation[1] for violation in record.violations}


def test_waits_left_open_after_a_member_of_an_unresolved_deadlock_was_withdrawn_are_a_known_limit():
    """Seed 2000228 (`ReservationController`, `FastestRouter`): the deadlock of agv-a, agv-c, agv-d, agv-e and agv-g is
    reported unresolved (last at 378.2); agv-a's wait is withdrawn at 433.1 by a breakdown; the controller forgets the
    set and never looks at the other four again, though their waits, blocked by each other only, still form a
    deadlock: the run ends with four open waits that no report explains. A missed detection of traffic control that
    predates this work (present at e993ca8)."""
    record = run_world(world_spec(2000228), controller="reservation", router="fastest")
    assert record.quiescent and not record.violations
    (case,) = record.known_limits
    assert case[1] == KNOWN_LIMIT_WITHDRAWN
    assert case[2] == ("agv-c", "agv-d", "agv-e", "agv-g")


def _forget_to_scan_after_a_release(self, agv, gone):
    """`ReservationController._drop` without the scan that grants the waits the release unblocks."""
    if not gone:
        return
    self._changes += 1
    self._revision += 1
    self.ledger.drop(agv, gone)


def _suppress_stuck_reports(monkeypatch):
    monkeypatch.setattr(ReservationController, "_report_one", lambda self, waiting, blockers: False)


def _no_scan_after_a_release(monkeypatch):
    monkeypatch.setattr(ReservationController, "_drop", _forget_to_scan_after_a_release)


@pytest.mark.parametrize(
    ("patch", "seed", "router"),
    [
        (_suppress_stuck_reports, 26, "shortest"),
        # These two runs reach the exemption: a deadlock was reported unresolved and a member's wait was withdrawn,
        # and the waits left open are blocked by an AGV that is stranded or idle (a stuck wait, now unreported).
        (_suppress_stuck_reports, 2000228, "shortest"),
        (_suppress_stuck_reports, 2000228, "congestion"),
        # And this one, where the waits left open are not blocked at all (a grant that never came).
        (_no_scan_after_a_release, 2000156, "fastest"),
    ],
)
def test_an_open_wait_with_no_report_for_another_reason_is_a_violation(monkeypatch, patch, seed, router):
    patch(monkeypatch)
    record = run_world(world_spec(seed), controller="reservation", router=router)
    assert not record.known_limits
    assert "open wait with no report" in {violation[1] for violation in record.violations}


def _monitor_after(*events):
    """A monitor that has seen only `events`, for what it tracks of the deadlock reports."""
    monitor = random_worlds.Monitor.__new__(random_worlds.Monitor)
    monitor._standing, monitor._orphans = [], set()
    for event in events:
        monitor._track_deadlocks(event)
    return monitor


def _unresolved(*agvs):
    return logistics_events.TrafficDeadlock(agvs=agvs, victim=None, action="unresolved")


def _ended(agv, reason):
    return logistics_events.TrafficWaitEnded(agv=agv, node="n", reason=reason)


def test_only_a_withdrawal_leaves_the_rest_of_an_unresolved_deadlock_unreported():
    assert _monitor_after(_unresolved("a", "b", "c"), _ended("a", "interrupted"))._orphans == {"b", "c"}
    for reason in ("granted", "rerouted", "stepped_aside", "gave_up"):
        monitor = _monitor_after(_unresolved("a", "b", "c"), _ended("a", reason))
        assert not monitor._orphans and not monitor._standing, reason
    assert not _monitor_after(_ended("a", "interrupted"))._orphans  # a wait of no reported set


def test_a_later_report_or_the_end_of_their_own_wait_covers_the_rest_of_the_deadlock():
    withdrawn = (_unresolved("a", "b", "c", "d"), _ended("a", "interrupted"))
    stuck = logistics_events.TrafficWaitStuck(agv="b", blockers=FrozenMap({"x": "IDLE"}))
    assert _monitor_after(*withdrawn, stuck)._orphans == {"c", "d"}
    assert _monitor_after(*withdrawn, _ended("c", "granted"))._orphans == {"b", "d"}
    assert _monitor_after(*withdrawn, _unresolved("b", "c", "d"))._orphans == set()
    resolved = logistics_events.TrafficDeadlock(agvs=("b", "c"), victim="b", action="reroute")
    assert _monitor_after(*withdrawn, resolved)._orphans == {"d"}


@pytest.mark.parametrize(("seed", "controller", "router"), sorted(SAFETY))
def test_a_run_of_the_safety_check_returns_with_no_violation(seed, controller, router):
    spec = world_spec(seed)
    record = run_world(spec, controller=controller, router=router)
    features, digest = SAFETY[seed, controller, router]
    assert record.quiescent and not record.violations and not record.known_limits
    assert record.fingerprint.digest == digest
    assert features <= exercised(spec, record.events) <= present(spec) | NOT_IN_A_SPEC
