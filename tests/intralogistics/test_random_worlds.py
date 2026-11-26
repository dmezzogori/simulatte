"""The random worlds of ``random_worlds.py``: a deterministic description, runs that reproduce the fingerprints pinned
while both modes existed, a monitor that detects a violation."""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest

from simulatte.environment import Environment
from simulatte.events import Deltas
from simulatte.intralogistics import events as logistics_events
from simulatte.intralogistics import travel
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.network import Segment
from simulatte.intralogistics.parking import ParkingArea
from simulatte.intralogistics.reservation import ReservationController
from simulatte.intralogistics.traffic import Disposition
from simulatte.intralogistics.traffic_holdings import HoldingsLedger
from simulatte.intralogistics.travel import TravelOutcome
from tests.intralogistics import random_worlds
from tests.intralogistics.random_worlds import (
    FEATURES,
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
    619: "d48e693d3dc0de65a73b199c1939765d11f3e5b5607e77294f6aaf3d49b00284",
}
"""``env.fingerprint()`` of the runs of the tests below (619 under ``FreeFlowController`` and the shortest-distance
router), pinned at the last commit with explicit mode (e4afae4), where the run built with concrete objects and the run
built through the layout gave equal run records. 619 is pinned again since a parking slot is given back when the trip to
park turns to charge: its ``parking.left`` comes at 122.0 instead of 220.1, and no other event changes."""


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


def test_a_trip_to_park_that_turns_to_charge_gives_its_parking_slot_back_as_it_turns():
    """Seed 619: agv-a takes a slot of P0 at 97.0 as it sets off to park, turns to the charger at 122.0 and gives the
    slot back then; it charges from 148.6 to 220.1. Until the fix it kept the slot until the charge ended."""
    record = run_world(world_spec(619), controller="free", router="shortest")
    assert record.quiescent and not record.violations
    assert record.fingerprint.digest == FINGERPRINTS[619]
    events = [(kind, dict(fields)) for kind, fields in record.events if dict(fields).get("agv") == "agv-a"]
    (entered, *_), (left, *_) = (
        [fields for kind, fields in events if kind is cls and fields["area"] == "P0"]
        for cls in (logistics_events.ParkingEntered, logistics_events.ParkingLeft)
    )
    turned = next(
        index
        for index, (kind, fields) in enumerate(events)
        if kind is logistics_events.AgvRouted and fields["destination"] == "c08"
    )
    (started, *_) = [fields for kind, fields in events if kind is logistics_events.ChargingStarted]
    assert entered["t"] == pytest.approx(97.003, abs=1e-3)
    assert events[turned - 2] == (logistics_events.ParkingLeft, left)  # before the turn's state change and route
    assert left["t"] == events[turned][1]["t"] == pytest.approx(122.048, abs=1e-3)
    assert started["t"] == pytest.approx(148.587, abs=1e-3)


def test_a_parking_slot_held_by_an_agv_resting_elsewhere_is_a_violation(monkeypatch):
    monkeypatch.setattr(ParkingArea, "leave", lambda self, agv: None)  # slots are never given back
    record = run_world(world_spec(619), controller="free", router="shortest")
    assert "parking slot of an AGV resting elsewhere" in {violation[1] for violation in record.violations}


@pytest.mark.parametrize(
    ("seed", "router", "withdrawn", "detected"),
    [
        (2000228, "fastest", "agv-a", (("agv-c", "agv-d", "agv-e", "agv-g"), "agv-e", "step_aside")),
        (52118, "shortest", "agv-f", (("agv-g", "bot-y"), None, "unresolved")),
    ],
)
def test_waits_left_after_a_member_of_an_unresolved_deadlock_was_withdrawn_are_resolved_or_reported(
    seed, router, withdrawn, detected
):
    """Seed 2000228 (`FastestRouter`): the deadlock of agv-a, agv-c, agv-d, agv-e and agv-g is reported unresolved; at
    433.1 a breakdown withdraws agv-a's wait, and the other four still block each other only. Seed 52118
    (`ShortestDistanceRouter`): the deadlock of agv-d, agv-f, agv-g, bot-y and bot-z is reported unresolved; at 293.7 a
    cancellation withdraws agv-f's wait, and agv-g and bot-y still block each other (and wait on a stranded AGV), with
    reviews that sleep. The controller forgot the set and never looked at these waits again, which were left open with
    no report. Now the withdrawal runs the detection again, at the same instant, and the run ends with every wait
    granted, resolved or reported."""
    record = run_world(world_spec(seed), controller="reservation", router=router)
    assert record.quiescent and not record.violations
    events = [(kind, dict(fields)) for kind, fields in record.events]
    (at,) = [
        index
        for index, (kind, fields) in enumerate(events)
        if kind is logistics_events.TrafficWaitEnded
        and fields["agv"] == withdrawn
        and fields["reason"] == "interrupted"
        and any(
            kind is logistics_events.TrafficDeadlock and withdrawn in fields["agvs"] for kind, fields in events[:index]
        )
        and not any(
            kind is logistics_events.TrafficWaitEnded and fields["agv"] == withdrawn
            for kind, fields in events[index + 1 :]
        )
    ]
    ended = events[at][1]
    kind, fields = next((kind, fields) for kind, fields in events[at + 1 :] if kind is logistics_events.TrafficDeadlock)
    assert fields["t"] == ended["t"]
    assert (fields["agvs"], fields["victim"], fields["action"]) == detected


def test_a_member_left_outside_a_deadlock_when_its_set_is_forgotten_gets_its_review():
    """Seed 8103801 (`FastestRouter`): agv-b's wait opens at 844.5 into the unresolved deadlock {agv-a, agv-b}, so it
    gets no on_blocked call and no review; it is also blocked by agv-e, idle. At 873.8 agv-a's own review reroutes it:
    the set is forgotten and agv-b alone is no deadlock. agv-b now gets the on_blocked call and the review it did not
    have, from that instant: the review 30 later reports it stuck. Before, nothing looked at agv-b again and the run
    ended with its wait open and unreported."""
    record = run_world(world_spec(8103801), controller="reservation", router="fastest")
    assert record.quiescent and not record.violations
    events = [(kind, dict(fields)) for kind, fields in record.events]
    formed = max(  # the last report of the set: at 844.5, when agv-b's wait opens into it
        index
        for index, (kind, fields) in enumerate(events)
        if kind is logistics_events.TrafficDeadlock and fields["agvs"] == ("agv-a", "agv-b")
    )
    assert round(events[formed][1]["t"], 1) == 844.5
    assert events[formed][1]["action"] == "unresolved"
    forgotten = next(
        fields
        for kind, fields in events[formed:]
        if kind is logistics_events.TrafficWaitEnded and fields["agv"] == "agv-a"
    )
    assert forgotten["reason"] == "rerouted"
    stuck = next(
        fields
        for kind, fields in events[formed:]
        if kind is logistics_events.TrafficWaitStuck and fields["agv"] == "agv-b"
    )
    assert stuck["t"] == forgotten["t"] + 30.0
    assert dict(stuck["blockers"]) == {"agv-e": "IDLE"}


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


def _blind_after_a_members_withdrawal(monkeypatch):
    """Once a member of an unresolved deadlock has its wait withdrawn, the controller neither detects nor reports a
    wait stuck: a real fault that follows an interruption."""
    cls = ReservationController
    withdraw, detect, report = cls.withdraw, cls._detect, cls._report_one

    def blinding_withdraw(self, request):
        waiting = self._pending.get(request.agv)
        if (
            waiting is not None
            and waiting.request is request
            and any(request.agv.id in ids for ids in self._unresolved)
        ):
            self._blind = True
        return withdraw(self, request)

    monkeypatch.setattr(cls, "withdraw", blinding_withdraw)
    monkeypatch.setattr(cls, "_detect", lambda self: [] if getattr(self, "_blind", False) else detect(self))
    monkeypatch.setattr(
        cls,
        "_report_one",
        lambda self, waiting, blockers: False if getattr(self, "_blind", False) else report(self, waiting, blockers),
    )


@pytest.mark.parametrize(
    ("patch", "seed", "router"),
    [
        (_suppress_stuck_reports, 26, "shortest"),
        # A deadlock was reported unresolved and a member's wait was withdrawn, and the waits left open are blocked by
        # an AGV that is stranded or idle (a stuck wait, now unreported).
        (_suppress_stuck_reports, 2000228, "shortest"),
        (_suppress_stuck_reports, 2000228, "congestion"),
        # A grant never came: releases do not scan.
        (_no_scan_after_a_release, 2000255, "shortest"),
        # After a member's withdrawal the controller is blind to detection and stuck reports, a real fault that follows
        # an interruption, and some waits are left open with a deadlock that nothing reports.
        (_blind_after_a_members_withdrawal, 2000173, "fastest"),
        (_blind_after_a_members_withdrawal, 2000245, "shortest"),
        (_blind_after_a_members_withdrawal, 2000254, "congestion"),
    ],
)
def test_an_open_wait_with_no_report_is_a_violation(monkeypatch, patch, seed, router):
    patch(monkeypatch)
    record = run_world(world_spec(seed), controller="reservation", router=router)
    assert "open wait with no report" in {violation[1] for violation in record.violations}


def _kinds(record):
    return {violation[1] for violation in record.violations}


def _report_unresolved_instead_of_stuck(monkeypatch):
    """A stuck wait is reported as an unresolved deadlock of its AGV alone, and remembered as one: the report would
    explain the wait at the end of the run, though nothing is deadlocked."""
    report = ReservationController._report_one

    def report_one(self, waiting, blockers):
        if waiting.stuck or self._unresolved_member(waiting.agv) or self._stuck(waiting, blockers) is None:
            return report(self, waiting, blockers)
        ids = (waiting.agv.id,)
        self._unresolved.append(ids)
        self._env.emit(logistics_events.TrafficDeadlock(agvs=ids, victim=None, action="unresolved"))
        return False

    monkeypatch.setattr(ReservationController, "_report_one", report_one)


def _detect_without_the_reduction(monkeypatch):
    """Deadlock detection takes every stopped wait for deadlocked: it reports deadlocks it should not."""
    reduce = ReservationController._reduce
    monkeypatch.setattr(
        ReservationController,
        "_reduce",
        lambda self, waiters, *, gates: list(waiters) if not gates else reduce(self, waiters, gates=gates),
    )


def _forget_unresolved_deadlocks(monkeypatch):
    """A deadlock that no reaction resolves is neither reported nor remembered: it misses a report. (The controller
    reports a set only when it does not stand already; the mutant makes it look standing, then forgets it.)"""
    resolve = ReservationController._resolve

    def silent(self, deadlocked):
        ids = tuple(sorted(waiting.agv.id for waiting in deadlocked))
        if ids in self._unresolved:
            return resolve(self, deadlocked)
        self._unresolved.append(ids)
        try:
            return resolve(self, deadlocked)
        finally:
            if ids in self._unresolved:
                self._unresolved.remove(ids)

    monkeypatch.setattr(ReservationController, "_resolve", silent)


def _stuck_reported_with_no_blocker(monkeypatch):
    stuck = ReservationController._stuck

    def empty(self, waiting, blockers):
        return None if stuck(self, waiting, blockers) is None else {}

    monkeypatch.setattr(ReservationController, "_stuck", empty)


def _stuck_reported_with_dispositions_swapped(monkeypatch):
    """Each blocker of a stuck report is given another disposition: idle and stranded swapped, the others idle."""
    stuck = ReservationController._stuck
    swapped = {"IDLE": "STRANDED", "STRANDED": "IDLE", "WAITING": "IDLE", "STALLED": "IDLE"}

    def swap(self, waiting, blockers):
        found = stuck(self, waiting, blockers)
        return None if found is None else {agv: swapped[state] for agv, state in found.items()}

    monkeypatch.setattr(ReservationController, "_stuck", swap)


def _stuck_reported_twice(monkeypatch):
    report = ReservationController._report_one

    def twice(self, waiting, blockers):
        reported = report(self, waiting, blockers)
        if reported:
            waiting.stuck = False
            report(self, waiting, blockers)
        return reported

    monkeypatch.setattr(ReservationController, "_report_one", twice)


def _stuck_on_direct_blockers_only(monkeypatch):
    """The stuck rule of spec §7.9 applied to the wait's own blockers only: a waiting blocker counts whatever its own
    wait is blocked by, so a wait is reported while a blocker of a blocker moves or serves."""

    def stuck(self, waiting, blockers):
        found = {}
        for blocker in blockers:
            motion = blocker.agv.motion
            if motion is not None and motion.get("stalled"):
                found[blocker.agv.id] = "STALLED"
            elif blocker.disposition in (Disposition.IDLE, Disposition.STRANDED, Disposition.WAITING):
                found[blocker.agv.id] = blocker.disposition.name
            else:
                return None
        return found

    monkeypatch.setattr(ReservationController, "_stuck", stuck)


def _stuck_with_a_moving_blocker_left_out(monkeypatch):
    """A blocker that moves or serves is left out of the stuck rule and of the report, instead of making the wait not
    stuck."""

    def stuck(self, waiting, blockers):
        found = {}
        reached = {waiting}
        todo = [(waiting, blockers)]
        while todo:
            current, current_blockers = todo.pop()
            for blocker in current_blockers:
                if blocker.disposition not in (Disposition.IDLE, Disposition.STRANDED, Disposition.WAITING):
                    continue
                if current is waiting:
                    found[blocker.agv.id] = blocker.disposition.name
                if blocker.disposition is Disposition.WAITING and self._pending[blocker.agv] not in reached:
                    behind = self._pending[blocker.agv]
                    reached.add(behind)
                    todo.append((behind, self._blocking(behind)))
        return found

    monkeypatch.setattr(ReservationController, "_stuck", stuck)


def _deadlock_reports_changed(monkeypatch, change):
    """Every `traffic.deadlock` is replaced by `change(event)`, or dropped when it returns None."""
    emit = Environment.emit

    def changed(self, event):
        if isinstance(event, logistics_events.TrafficDeadlock):
            event = change(event)
            if event is None:
                return None
        return emit(self, event)

    monkeypatch.setattr(Environment, "emit", changed)


def _resolved_deadlocks_not_reported(monkeypatch):
    _deadlock_reports_changed(monkeypatch, lambda event: None if event.victim is not None else event)


def _resolved_deadlocks_reported_one_member_short(monkeypatch):
    """A resolved report of three AGVs or more leaves out its last member that is not the victim: what is listed is
    still deadlocked, but not the whole deadlocked set."""

    def short(event):
        if event.victim is None or len(event.agvs) < 3:
            return event
        drop = [agv for agv in event.agvs if agv != event.victim][-1]
        agvs = tuple(agv for agv in event.agvs if agv != drop)
        return logistics_events.TrafficDeadlock(agvs=agvs, victim=event.victim, action=event.action)

    _deadlock_reports_changed(monkeypatch, short)


def _deadlocks_resolved_in_name_only(monkeypatch):
    """A deadlock is reported once as resolved by its first member giving up, though nothing is staged: every member
    keeps waiting and the set is not remembered."""

    def resolve(self, deadlocked):
        ids = tuple(sorted(waiting.agv.id for waiting in deadlocked))
        faked = self.__dict__.setdefault("_faked", set())
        if ids not in faked:
            faked.add(ids)
            self._env.emit(logistics_events.TrafficDeadlock(agvs=ids, victim=ids[0], action="give_up"))
        return False

    monkeypatch.setattr(ReservationController, "_resolve", resolve)


def _stuck_reported_with_an_unknown_blocker(monkeypatch):
    stuck = ReservationController._stuck

    def ghost(self, waiting, blockers):
        found = stuck(self, waiting, blockers)
        return None if found is None else {**found, "ghost-1": "IDLE"}

    monkeypatch.setattr(ReservationController, "_stuck", ghost)


def _slot_given_back_at_the_decision(monkeypatch):
    """The slot of a trip to park is given back as soon as it is taken: the AGV travels to the node without it."""
    take = FleetCoordinator._take_slot

    def take_and_give_back(self, agv, target):
        taken = take(self, agv, target)
        if taken and any(area.node == target for area in self._slots.get(agv, ())):
            self._release_slots(agv, target)
        return taken

    monkeypatch.setattr(FleetCoordinator, "_take_slot", take_and_give_back)


def _every_slot_given_back_twice(monkeypatch):
    """Every give-back emits `parking.left` twice, the second for a slot that is no longer held."""
    leave = ParkingArea.leave

    def twice(self, agv):
        leave(self, agv)
        self.env.emit(logistics_events.ParkingLeft(area=self.id, agv=agv.id, deltas=Deltas.build().done()))

    monkeypatch.setattr(ParkingArea, "leave", twice)


@pytest.mark.parametrize(
    ("patch", "seed", "router", "kind"),
    [
        (_report_unresolved_instead_of_stuck, 26, "shortest", "deadlock report differs from the deadlocked set"),
        (_detect_without_the_reduction, 26, "shortest", "deadlock report differs from the deadlocked set"),
        (_forget_unresolved_deadlocks, 2000228, "fastest", "deadlock not reported"),
        (_stuck_reported_with_no_blocker, 26, "shortest", "stuck report with no blocker"),
        (_stuck_reported_with_dispositions_swapped, 26, "shortest", "stuck report gives a blocker another disposition"),
        (_stuck_reported_twice, 26, "shortest", "stuck report repeated for one wait"),
        (_stuck_on_direct_blockers_only, 26, "shortest", "stuck report of a wait that reaches a blocker not at rest"),
        (
            _stuck_with_a_moving_blocker_left_out,
            26,
            "shortest",
            "stuck report leaves out a holder that blocks the wait",
        ),
        (_resolved_deadlocks_not_reported, 26, "shortest", "deadlock not reported"),
        (
            _resolved_deadlocks_reported_one_member_short,
            26,
            "shortest",
            "deadlock report differs from the deadlocked set",
        ),
        (_deadlocks_resolved_in_name_only, 26, "shortest", "deadlock report names a victim whose wait did not end"),
        (_deadlocks_resolved_in_name_only, 26, "shortest", "deadlock left at the end of the instant"),
        (_stuck_reported_with_an_unknown_blocker, 26, "shortest", "stuck report names an AGV the world does not have"),
        (
            _slot_given_back_at_the_decision,
            88,
            "shortest",
            "parking slot given back during a travel to the area's node",
        ),
        (_every_slot_given_back_twice, 88, "shortest", "parking slot given back that was not held"),
    ],
)
def test_a_wrong_or_missing_traffic_report_is_a_violation(monkeypatch, patch, seed, router, kind):
    """What a `traffic.wait_stuck` or a `traffic.deadlock` says is checked at its event, and every deadlock is
    reported, by the monitor's own reading of the events and of the replayed holdings."""
    patch(monkeypatch)
    assert kind in _kinds(run_world(world_spec(seed), controller="reservation", router=router))


def test_a_slot_kept_while_its_agv_travels_to_a_charger_elsewhere_is_a_violation_as_the_travel_starts(monkeypatch):
    """Seed 619 with the fix of the parking slots undone: agv-a keeps P0's slot when it turns to the charger c08 at
    122.0, and the monitor reports it then, during the travel, before the charge starts at 148.6."""
    monkeypatch.setattr(FleetCoordinator, "_release_slots_elsewhere", lambda self, agv, charger: None)
    record = run_world(world_spec(619), controller="free", router="shortest")
    (found, *_) = [v for v in record.violations if v[1] == "parking slot held during a travel elsewhere"]
    assert found[0] == pytest.approx(122.048, abs=1e-3) and found[2:] == ("P0", "agv-a", "c08")


def _slot_given_back_after_arrival(monkeypatch, delay):
    """A trip to park that arrives gives its slot back `delay` later, while the AGV rests on the area's node (0: as it
    arrives), instead of when it next departs."""
    travel = FleetCoordinator._idle_travel

    def idle_travel(self, agv, target, purpose="travel"):
        outcome = yield from travel(self, agv, target, purpose)
        if outcome is TravelOutcome.ARRIVED and agv in self._slots:

            def later():
                yield self.env.timeout(delay)
                if agv.current_node == target and agv.state.name == "IDLE":
                    self._release_slots(agv, target)

            self.env.process(later())
        return outcome

    monkeypatch.setattr(FleetCoordinator, "_idle_travel", idle_travel)


@pytest.mark.parametrize(("delay", "at"), [(0.0, 163.078), (5.0, 168.078)])
def test_a_slot_given_back_while_its_agv_rests_parked_is_a_violation(monkeypatch, delay, at):
    """Seed 619 (`FreeFlowController`): agv-b arrives on P1's node at 163.1 and rests there, parked; a slot given back
    then, or 5 later, is reported at that instant (spec §7.10: it goes back when the AGV next departs)."""
    _slot_given_back_after_arrival(monkeypatch, delay)
    record = run_world(world_spec(619), controller="free", router="shortest")
    (found, *_) = [
        v for v in record.violations if v[1] == "parking slot given back while the AGV rests on the area's node"
    ]
    assert found[0] == pytest.approx(at, abs=1e-3) and found[2:] == ("P1", "agv-b")


def test_a_segment_granted_against_a_body_is_a_violation(monkeypatch):
    """Seed 58: a ledger that ignores agv-a's holdings of single-lane segments admits a vehicle head-on against its
    body."""
    admit = HoldingsLedger.blockers

    def blockers(self, agv, item, among=None):
        found = admit(self, agv, item, among)
        if isinstance(item.resource, Segment) and not item.resource.passing:
            return [holder for holder in found if holder.id != "agv-a"]
        return found

    monkeypatch.setattr(HoldingsLedger, "blockers", blockers)
    assert "ADMITTED against a body on a segment" in _kinds(run_world(world_spec(58), controller="reservation"))


REVERSAL = (8101904, "fastest")
"""The run in which agv-c claims ``g0_2~g1_0`` again in its new direction while its body still lies on it as it arrived,
and agv-g asks for the segment the other way at the same instant (471.171): before the both-directions state of spec
§7.2, agv-g was granted it and the two bodies lay head-on on it."""


def _undone(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rule undone: a grant never leaves a holder in the both-directions state."""
    take = HoldingsLedger.take

    def never(self, agv, items):
        take(self, agv, items)
        self._both.clear()

    monkeypatch.setattr(HoldingsLedger, "take", never)


def test_with_the_rule_undone_the_monitor_reports_the_admission_before_the_physical_conflict(monkeypatch):
    """The admission is reported at agv-g's grant (its `traffic.reserved`), three events before agv-g's move starts and
    the bodies meet; no older kind comes before it. The check of recorded directions against bodies fires earlier, at
    every re-claim of the run (the first at 30.6)."""
    _undone(monkeypatch)
    record = run_world(world_spec(REVERSAL[0]), controller="reservation", router=REVERSAL[1])
    kinds = [violation[1] for violation in record.violations]
    older = [kind for kind in kinds if kind != "LEDGER recorded direction against the body"]
    assert older == ["ADMITTED against a body on a segment", "PHYSICAL opposite directions on a segment"]
    admitted = next(v for v in record.violations if v[1] == "ADMITTED against a body on a segment")
    assert admitted[2:] == ("g0_2~g1_0", "agv-c", "forward", "agv-g", "backward")
    physical = next(v for v in record.violations if v[1] == "PHYSICAL opposite directions on a segment")
    assert admitted[0] == physical[0] == pytest.approx(471.171, abs=1e-3)
    assert record.violations[0][1] == "LEDGER recorded direction against the body"
    assert record.violations[0][0] == pytest.approx(30.619, abs=1e-3)


def test_a_run_where_a_vehicle_reverses_on_a_segment_admits_no_vehicle_against_its_body():
    record = run_world(world_spec(REVERSAL[0]), controller="reservation", router=REVERSAL[1])
    assert record.quiescent and not record.violations


def _state_ends_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The executor reports every segment a grant lifts out of the trail as cleared at the grant."""
    granted = travel._Trip._granted

    def at_once(self, request):
        granted(self, request)
        segments = [resource for resource in self.lifted if isinstance(resource, Segment)]
        if segments:
            self.controller.cleared(self.agv, segments)

    monkeypatch.setattr(travel._Trip, "_granted", at_once)


def _no_end_at_the_old_clear_point(monkeypatch: pytest.MonkeyPatch) -> None:
    """The executor releases at clear points as before the rule and never reports the old clear point of a segment
    claimed again: the state lasts until the holder's own leg, a rest with room, or a release."""

    def release(self, trail, resources):
        resources = [resource for resource in resources if resource in trail.entries]
        if resources:
            for resource in resources:
                del trail.entries[resource]
            self.controller.release(self.agv, resources)

    monkeypatch.setattr(travel._Trip, "_release", release)


def _cleared_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """The controller ignores ``cleared``: the state lasts until a release."""
    monkeypatch.setattr(ReservationController, "cleared", lambda self, agv, resources: None)


def _state_unread(monkeypatch: pytest.MonkeyPatch) -> None:
    """The state is recorded, but admission does not read it."""
    blockers = HoldingsLedger.blockers

    def unread(self, agv, item, among=None):
        both, self._both = self._both, {}
        try:
            return blockers(self, agv, item, among)
        finally:
            self._both = both

    monkeypatch.setattr(HoldingsLedger, "blockers", unread)


@pytest.mark.parametrize(
    ("mutant", "first"),
    [
        (_state_ends_at_once, "LEDGER recorded direction against the body"),
        (_no_end_at_the_old_clear_point, "LEDGER both directions outlived the body"),
        (_cleared_ignored, "LEDGER both directions outlived the body"),
        (_state_unread, "ADMITTED against a body on a segment"),
    ],
)
def test_a_both_directions_state_that_is_wrong_is_a_violation(monkeypatch, mutant, first):
    """A state that ends at once fails the check of recorded directions against bodies; one that never ends at the old
    clear point, or that the controller never ends, outlives the body; one that admission does not read lets agv-g in
    against agv-c's body, a second holder of a segment in the state."""
    mutant(monkeypatch)
    record = run_world(world_spec(REVERSAL[0]), controller="reservation", router=REVERSAL[1])
    kinds = [violation[1] for violation in record.violations]
    assert kinds and kinds[0] == first
    if mutant is _state_unread:
        assert "LEDGER both directions with another holder" in kinds


GATE_WAIVER = [(7702388, "shortest")]
"""The run that ends with a wait left stuck and unreported after a gate waiver (hole A of audit (i))."""
BEHIND_A_REPORTED_WAIT = [
    (8102359, "congestion"),
    (8103231, "shortest"),
    (100281, "shortest"),
    (100281, "fastest"),
    (7700058, "shortest"),
    (7700058, "fastest"),
    (7700058, "congestion"),
    (7701490, "fastest"),
    (4100399, "congestion"),
    (4102230, "shortest"),
    (4103675, "fastest"),
    (4106591, "shortest"),
    (4106711, "congestion"),
    (4109344, "shortest"),
    (4109857, "fastest"),
    (4109857, "congestion"),
]
"""The runs that end with a wait behind a reported wait, stuck and unreported (hole R of audit (i))."""


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="a gate waiver leaves a sleeping wait stuck, unreported (hole A); T4 fixes it",
)
@pytest.mark.parametrize(("seed", "router"), GATE_WAIVER)
def test_a_run_with_a_gate_waiver_ends_with_every_open_wait_reported(seed, router):
    record = run_world(world_spec(seed), controller="reservation", router=router)
    assert record.quiescent and not record.violations


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="the stuck chain stops at a wait already reported (hole R, audit (i)); T4 fixes it",
)
@pytest.mark.parametrize(("seed", "router"), BEHIND_A_REPORTED_WAIT)
def test_a_run_with_a_wait_behind_a_reported_wait_ends_with_every_open_wait_reported(seed, router):
    record = run_world(world_spec(seed), controller="reservation", router=router)
    assert record.quiescent and not record.violations


@pytest.mark.parametrize(("seed", "router"), [*GATE_WAIVER, *BEHIND_A_REPORTED_WAIT])
def test_a_controller_that_reports_waits_stuck_too_eagerly_does_not_clear_the_hole_runs(monkeypatch, seed, router):
    """A wrong fix of the holes: the stuck rule applied to a wait's own blockers only reports the waits of these runs,
    but at instants when a wait behind a blocker is blocked by an AGV that moves or serves. The monitor reports that,
    so none of these runs comes out clean."""
    _stuck_on_direct_blockers_only(monkeypatch)
    record = run_world(world_spec(seed), controller="reservation", router=router)
    assert "stuck report of a wait that reaches a blocker not at rest" in _kinds(record)


@pytest.mark.parametrize(("seed", "controller", "router"), sorted(SAFETY))
def test_a_run_of_the_safety_check_returns_with_no_violation(seed, controller, router):
    spec = world_spec(seed)
    record = run_world(spec, controller=controller, router=router)
    features, digest = SAFETY[seed, controller, router]
    assert record.quiescent and not record.violations
    assert record.fingerprint.digest == digest
    assert features <= exercised(spec, record.events) <= present(spec) | NOT_IN_A_SPEC
