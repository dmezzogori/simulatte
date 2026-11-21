"""Support shared by the traffic tests: graph builders, a full replay checker, the traffic invariants, fake hooks.

Each builder returns ``(graph, nodes)`` with ``nodes`` the dict of the graph's nodes by id. All arcs are two-way and
keep the default chord length.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from simulatte.environment import Environment
from simulatte.events import DomainEvent, apply_deltas
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node, NodeBinding
from simulatte.intralogistics.reservation import ReservationController, SegmentBinding, ZoneBinding
from simulatte.intralogistics.traffic import WaitHooks


def build(
    points: Sequence[tuple[str, float, float]], links: Sequence[tuple[str, str]]
) -> tuple[LayoutGraph, dict[str, Node]]:
    """A graph of the given ``(id, x, y)`` nodes in order, joined by two-way arcs."""
    nodes = {name: Node(name, x, y) for name, x, y in points}
    graph = LayoutGraph(nodes.values(), [Arc(nodes[a], nodes[b]) for a, b in links])
    return graph, nodes


def t_layout() -> tuple[LayoutGraph, dict[str, Node]]:
    """The layout of spec Appendix A: a line ``STORE_OUT, C1, C2, LINE_IN`` with ``PARK`` hanging off ``C1``."""
    return build(
        [("STORE_OUT", 0, 0), ("C1", 10, 0), ("C2", 20, 0), ("LINE_IN", 30, 0), ("PARK", 10, 10)],
        [("STORE_OUT", "C1"), ("C1", "C2"), ("C2", "LINE_IN"), ("C1", "PARK")],
    )


def line(n: int) -> tuple[LayoutGraph, dict[str, Node]]:
    """Nodes ``N0 ... N{n-1}`` at ``(10 * i, 0)`` joined in a row."""
    return build([(f"N{i}", 10 * i, 0) for i in range(n)], [(f"N{i}", f"N{i + 1}") for i in range(n - 1)])


def two_junction_corridor() -> tuple[LayoutGraph, dict[str, Node]]:
    """Junctions ``J1`` and ``J2`` with three neighbours each, joined by the chain ``J1, a, b, J2``."""
    return build(
        [
            ("J1", 0, 0),
            ("a", 10, 0),
            ("b", 20, 0),
            ("J2", 30, 0),
            ("x1", -10, 10),
            ("y1", -10, -10),
            ("x2", 40, 10),
            ("y2", 40, -10),
        ],
        [("J1", "a"), ("a", "b"), ("b", "J2"), ("J1", "x1"), ("J1", "y1"), ("J2", "x2"), ("J2", "y2")],
    )


def loop_with_side_node() -> tuple[LayoutGraph, dict[str, Node]]:
    """Two parallel one-node chains ``J1, U, J2`` and ``J1, L, J2``, with a leaf on each junction."""
    return build(
        [("J1", 0, 0), ("U", 10, 10), ("L", 10, -10), ("J2", 20, 0), ("S1", -10, 0), ("S2", 30, 0)],
        [("J1", "U"), ("U", "J2"), ("J1", "L"), ("L", "J2"), ("J1", "S1"), ("J2", "S2")],
    )


class FullReplay:
    """Applies the deltas of every domain event and compares EVERY live entity, of every kind, after each event.

    Two readers are checked (spec §17, U3): ``state`` replays from the first event seen (from ``{}`` when created
    before any entity, from the live snapshot otherwise), and ``seeded`` replays from ``env.initial_state`` as a
    reader that seeks from the start of the run does. Comparing after every event is comparing at every cursor.
    """

    def __init__(self, env: Environment, *, from_snapshot: bool = False) -> None:
        self.env = env
        self.state: dict[str, dict[str, Any]] = (
            {entity: dict(fields) for entity, fields in env.entities.snapshot().items()} if from_snapshot else {}
        )
        self.seeded: dict[str, dict[str, Any]] | None = None
        self.events: list[DomainEvent] = []
        env.bus.subscribe(self, "*")

    def __call__(self, event: DomainEvent) -> None:
        live = self.env.entities.snapshot()
        apply_deltas(self.state, event.deltas)
        assert self.state == live, event
        initial = self.env._initial_state
        if self.seeded is None and initial is not None:
            self.seeded = {entity: dict(fields) for entity, fields in initial.items()}
        if self.seeded is not None:
            apply_deltas(self.seeded, event.deltas)
            assert self.seeded == live, event
        self.events.append(event)

    def of(self, *types: type[DomainEvent]) -> list[Any]:
        return [event for event in self.events if isinstance(event, types)]


class TrafficInvariants:
    """Asserts the invariants of spec §17 after every domain event, from the live entities and the controller.

    Node holders stay within the network capacity; a segment without ``passing`` never holds both directions; zone
    holders stay within capacity; a corridor's holders never show both orientations; every AGV placed with the
    controller that has no active motion holds its current node; an AGV holding a member of a zone or corridor holds
    that zone or corridor. ``checked`` counts the events checked (those after
    the network is built).
    """

    def __init__(self, env: Environment, controller: ReservationController) -> None:
        self.env = env
        self.controller = controller
        self.checked = 0
        env.bus.subscribe(self, "*")

    def __call__(self, event: DomainEvent) -> None:
        controller = self.controller
        network = controller._network
        if network is None:
            return
        for entity in self.env.entities.live():
            if isinstance(entity, NodeBinding):
                assert len(entity.reserved_by) <= network.capacity(entity.node), (event, entity)
            elif isinstance(entity, SegmentBinding):
                assert entity.segment.passing or not (entity.forward and entity.backward), (event, entity.id)
            elif isinstance(entity, ZoneBinding):
                if entity.rule == "exclusive":
                    capacity = entity.capacity
                    assert capacity is not None and len(entity.reserved_by) <= capacity, (event, entity.id)
                else:
                    holders = controller._holders.get(entity.container, {})
                    orientations = {o for o in holders.values() if o is not None}
                    assert len(orientations) <= 1, (event, entity.id)
        for agv in controller._placed:
            if agv.motion is None:
                assert agv.current_node in controller._held.get(agv, {}), (event, agv.id)
        for agv, held in controller._held.items():
            for container, members in controller._members.items():
                assert container in held or not any(member in held for member in members), (event, agv.id, container)
        self.checked += 1


@dataclass
class FakeHooks(WaitHooks):
    """Wait hooks that record their calls in ``calls`` as ``(name, args)``."""

    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)


def fake_hooks(route: Sequence[Node] = (), loaded: bool = False, resolutions: int = 0) -> FakeHooks:
    """Hooks returning `route`, `loaded` and `resolutions`; ``try_reroute``, ``try_step_aside`` and ``give_up`` stage
    nothing and return false."""
    calls: list[tuple[str, tuple[Any, ...]]] = []

    def recording(name: str, value: Any) -> Any:
        def call(*args: Any) -> Any:
            calls.append((name, args))
            return value

        return call

    return FakeHooks(
        route=recording("route", tuple(route)),
        loaded=recording("loaded", loaded),
        resolutions=recording("resolutions", resolutions),
        try_reroute=recording("try_reroute", False),
        try_step_aside=recording("try_step_aside", False),
        give_up=recording("give_up", False),
        calls=calls,
    )
