from __future__ import annotations

import pytest

from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.network import (
    Branch,
    Claim,
    Corridor,
    TrafficNetwork,
    Zone,
)
from tests.intralogistics.traffic_support import build, line, loop_with_side_node, t_layout, two_junction_corridor


def describe(claim: Claim) -> list[tuple[str, str | None]]:
    """The items of a claim as ``(resource id, direction)`` pairs."""
    out: list[tuple[str, str | None]] = []
    for item in claim.items:
        resource = item.resource
        out.append((resource.id, item.direction))
    return out


def route_of(nodes: dict[str, Node], *names: str) -> list[Node]:
    return [nodes[name] for name in names]


class TestCapacity:
    def test_default_is_node_capacity(self) -> None:
        graph, nodes = line(3)
        network = TrafficNetwork(graph)
        assert [network.capacity(node) for node in graph.nodes] == [1, 1, 1]
        assert network.warnings == ()

    def test_node_capacity_override(self) -> None:
        graph, nodes = line(3)
        assert TrafficNetwork(graph, node_capacity=3).capacity(nodes["N1"]) == 3

    def test_facility_sum_beats_node_capacity(self) -> None:
        graph, nodes = line(3)
        network = TrafficNetwork(graph, node_capacity=2, facility_capacities={nodes["N1"]: 4})
        assert network.capacity(nodes["N1"]) == 4
        assert network.capacity(nodes["N0"]) == 2

    def test_explicit_beats_facility_sum(self) -> None:
        graph, nodes = line(3)
        network = TrafficNetwork(
            graph,
            node_capacity=2,
            capacities={nodes["N1"]: 1},
            facility_capacities={nodes["N1"]: 4},
        )
        assert network.capacity(nodes["N1"]) == 1

    def test_warning_when_explicit_is_below_facility_sum(self) -> None:
        graph, nodes = line(3)
        network = TrafficNetwork(
            graph,
            capacities={nodes["N1"]: 1, nodes["N2"]: 5},
            facility_capacities={nodes["N1"]: 4, nodes["N2"]: 5},
        )
        assert len(network.warnings) == 1
        assert "N1" in network.warnings[0]

    @pytest.mark.parametrize("which", ["node_capacity", "capacities", "facility_capacities"])
    def test_capacity_below_one_raises(self, which: str) -> None:
        graph, nodes = line(3)
        kwargs: dict[str, object] = {
            "node_capacity": 0,
            "capacities": {nodes["N1"]: 0},
            "facility_capacities": {nodes["N1"]: 0},
        }
        with pytest.raises(ValueError, match="capacity"):
            TrafficNetwork(graph, **{which: kwargs[which]})  # ty: ignore[invalid-argument-type]


class TestSegments:
    def test_two_way_arc_gives_one_segment(self) -> None:
        graph, nodes = line(2)
        network = TrafficNetwork(graph)
        assert len(network.segments) == 1
        segment = network.segments[0]
        assert (segment.a, segment.b) == (nodes["N0"], nodes["N1"])
        assert segment.length == 10.0
        assert segment.passing is False

    def test_opposite_one_way_arcs_give_one_segment(self) -> None:
        a, b = Node("A", 0, 0), Node("B", 10, 0)
        graph = LayoutGraph([a, b], [Arc(b, a, bidirectional=False), Arc(a, b, bidirectional=False)])
        network = TrafficNetwork(graph)
        assert len(network.segments) == 1
        assert network.segment(a, b) is network.segment(b, a)

    def test_length_and_passing_come_from_the_arc(self) -> None:
        a, b = Node("A", 0, 0), Node("B", 10, 0)
        graph = LayoutGraph([a, b], [Arc(a, b, length=15.0, passing=True)])
        segment = TrafficNetwork(graph).segment(a, b)
        assert segment.length == 15.0
        assert segment.passing is True

    def test_direction_follows_id_order(self) -> None:
        graph, nodes = line(2)
        network = TrafficNetwork(graph)
        assert network.direction(nodes["N0"], nodes["N1"]) == "forward"
        assert network.direction(nodes["N1"], nodes["N0"]) == "backward"

    def test_segments_in_id_order_whatever_the_graph_order(self) -> None:
        graph, nodes = t_layout()
        links = [("C1", "PARK"), ("C2", "LINE_IN"), ("C1", "C2"), ("STORE_OUT", "C1")]
        reordered = LayoutGraph(reversed(graph.nodes), [Arc(nodes[a], nodes[b]) for a, b in links])
        ids = [f"{s.a.id}~{s.b.id}" for s in TrafficNetwork(reordered).segments]
        assert ids == ["C1~C2", "C1~PARK", "C1~STORE_OUT", "C2~LINE_IN"]

    def test_segment_of_non_adjacent_nodes_raises(self) -> None:
        graph, nodes = line(3)
        with pytest.raises(ValueError, match="N0"):
            TrafficNetwork(graph).segment(nodes["N0"], nodes["N2"])


class TestCorridors:
    def test_two_junction_corridor(self) -> None:
        graph, nodes = two_junction_corridor()
        network = TrafficNetwork(graph)
        assert network.corridors == (Corridor("corridor:a", tuple(route_of(nodes, "J1", "a", "b", "J2"))),)
        assert network.warnings == ()

    def test_passing_segment_removes_the_corridor(self) -> None:
        graph, nodes = two_junction_corridor()
        arcs = [
            Arc(nodes["J1"], nodes["a"]),
            Arc(nodes["a"], nodes["b"], passing=True),
            Arc(nodes["b"], nodes["J2"]),
            *[Arc(nodes[p], nodes[q]) for p, q in [("J1", "x1"), ("J1", "y1"), ("J2", "x2"), ("J2", "y2")]],
        ]
        assert TrafficNetwork(LayoutGraph(graph.nodes, arcs)).corridors == ()

    def test_one_way_arc_removes_the_corridor(self) -> None:
        graph, nodes = two_junction_corridor()
        arcs = [
            Arc(nodes["J1"], nodes["a"]),
            Arc(nodes["a"], nodes["b"], bidirectional=False),
            Arc(nodes["b"], nodes["J2"]),
        ]
        arcs += [Arc(nodes[p], nodes[q]) for p, q in [("J1", "x1"), ("J1", "y1"), ("J2", "x2"), ("J2", "y2")]]
        assert TrafficNetwork(LayoutGraph(graph.nodes, arcs)).corridors == ()

    def test_interior_capacity_two_makes_the_node_a_port(self) -> None:
        graph, nodes = two_junction_corridor()
        network = TrafficNetwork(graph, capacities={nodes["b"]: 2})
        assert network.corridors == (Corridor("corridor:a", tuple(route_of(nodes, "J1", "a", "b"))),)

    def test_single_segment_between_two_junctions_gives_none(self) -> None:
        graph, _ = build(
            [("J1", 0, 0), ("J2", 10, 0), ("p", -10, 5), ("q", -10, -5), ("r", 20, 5), ("s", 20, -5)],
            [("J1", "J2"), ("J1", "p"), ("J1", "q"), ("J2", "r"), ("J2", "s")],
        )
        assert TrafficNetwork(graph).corridors == ()

    def test_ring_without_a_port_gives_none_and_one_warning(self) -> None:
        graph, _ = build(
            [("R0", 0, 0), ("R1", 10, 0), ("R2", 10, 10), ("R3", 0, 10)],
            [("R0", "R1"), ("R1", "R2"), ("R2", "R3"), ("R3", "R0")],
        )
        network = TrafficNetwork(graph)
        assert network.corridors == ()
        assert len(network.warnings) == 1
        assert "R0" in network.warnings[0]

    def test_ring_is_not_reported_when_derivation_is_off(self) -> None:
        graph, _ = build([("R0", 0, 0), ("R1", 10, 0), ("R2", 10, 10)], [("R0", "R1"), ("R1", "R2"), ("R2", "R0")])
        network = TrafficNetwork(graph, derive_corridors=False)
        assert network.corridors == ()
        assert network.warnings == ()

    def test_derivation_off(self) -> None:
        graph, _ = two_junction_corridor()
        assert TrafficNetwork(graph, derive_corridors=False).corridors == ()

    def test_two_parallel_chains_between_the_same_ports(self) -> None:
        graph, nodes = loop_with_side_node()
        network = TrafficNetwork(graph)
        assert [c.id for c in network.corridors] == ["corridor:L", "corridor:U"]
        assert network.corridors[0].nodes == tuple(route_of(nodes, "J1", "L", "J2"))
        assert network.corridors[1].nodes == tuple(route_of(nodes, "J1", "U", "J2"))

    def test_chain_returning_to_its_port(self) -> None:
        graph, nodes = build(
            [("P", 0, 0), ("A", 10, 10), ("B", 10, -10), ("Q", -10, 0), ("R", -20, 5), ("S", -20, -5)],
            [("P", "A"), ("A", "B"), ("B", "P"), ("P", "Q"), ("Q", "R"), ("Q", "S")],
        )
        network = TrafficNetwork(graph)
        assert network.corridors == (Corridor("corridor:A", tuple(route_of(nodes, "P", "A", "B", "P"))),)

    def test_ordered_by_smallest_interior_id_not_graph_order(self) -> None:
        graph, nodes = loop_with_side_node()
        arcs = [
            Arc(nodes[a], nodes[b])
            for a, b in [("S2", "J2"), ("J2", "L"), ("U", "J1"), ("S1", "J1"), ("L", "J1"), ("J2", "U")]
        ]
        reversed_graph = LayoutGraph(reversed(graph.nodes), arcs)
        network = TrafficNetwork(reversed_graph)
        assert [c.id for c in network.corridors] == ["corridor:L", "corridor:U"]
        assert all(c.nodes[0].id == "J1" for c in network.corridors)


class TestBranches:
    def test_t_layout(self) -> None:
        graph, nodes = t_layout()
        network = TrafficNetwork(graph)
        assert network.branches == (
            Branch(nodes["C1"], (nodes["STORE_OUT"],)),
            Branch(nodes["C1"], (nodes["C2"], nodes["LINE_IN"])),
            Branch(nodes["C1"], (nodes["PARK"],)),
        )

    def test_plain_line_has_none(self) -> None:
        graph, _ = line(3)
        assert TrafficNetwork(graph).branches == ()

    def test_capacity_two_nodes_are_roots(self) -> None:
        graph, nodes = build(
            [("WH_A_OUT", 0, 0), ("N1", 5, 0), ("N2", 10, 0), ("N3", 15, 0), ("WH_B_IN", 20, 0)],
            [("WH_A_OUT", "N1"), ("N1", "N2"), ("N2", "N3"), ("N3", "WH_B_IN")],
        )
        network = TrafficNetwork(graph, capacities={nodes["N1"]: 2, nodes["N2"]: 2})
        assert network.branches == (
            Branch(nodes["N1"], (nodes["WH_A_OUT"],)),
            Branch(nodes["N2"], (nodes["N3"], nodes["WH_B_IN"])),
        )

    def test_branches_stay_listed_when_the_rule_is_off(self) -> None:
        graph, _ = t_layout()
        assert len(TrafficNetwork(graph, hold_back_dead_ends=False).branches) == 3


class TestLeg:
    def test_leg_items(self) -> None:
        graph, nodes = t_layout()
        network = TrafficNetwork(graph)
        items = network.leg(nodes["PARK"], nodes["C1"])
        assert [(i.resource, i.direction) for i in items] == [
            (network.segment(nodes["PARK"], nodes["C1"]), "backward"),
            (nodes["C1"], None),
        ]

    def test_leg_without_an_arc_raises(self) -> None:
        a, b = Node("A", 0, 0), Node("B", 10, 0)
        network = TrafficNetwork(LayoutGraph([a, b], [Arc(a, b, bidirectional=False)]))
        assert [i.resource for i in network.leg(a, b)] == [network.segment(a, b), b]
        with pytest.raises(ValueError, match="B"):
            network.leg(b, a)

    def test_leg_lists_zones_and_corridors_first_by_id(self) -> None:
        graph, nodes = two_junction_corridor()
        zones = [Zone("zz", (nodes["a"],)), Zone("aa", ((nodes["a"], nodes["b"]),)), Zone("other", (nodes["J2"],))]
        network = TrafficNetwork(graph, zones=zones)
        assert describe(Claim(network.leg(nodes["a"], nodes["b"]), 1)) == [
            ("aa", None),
            ("corridor:a", "forward"),
            ("a~b", "forward"),
            ("b", None),
        ]
        assert describe(Claim(network.leg(nodes["J1"], nodes["a"]), 1)) == [
            ("corridor:a", "forward"),
            ("zz", None),
            ("J1~a", "forward"),
            ("a", None),
        ]


class TestClaims:
    def test_leaving_a_dead_end_is_one_leg(self) -> None:
        graph, nodes = t_layout()
        network = TrafficNetwork(graph)
        claim = network.claim(route_of(nodes, "LINE_IN", "C2", "C1", "PARK"), 0)
        assert claim.legs == 1
        assert describe(claim) == [("C2~LINE_IN", "backward"), ("C2", None)]

    def test_root_followed_by_a_branch_entry_covers_the_rest(self) -> None:
        graph, nodes = t_layout()
        network = TrafficNetwork(graph)
        route = route_of(nodes, "LINE_IN", "C2", "C1", "PARK")
        claim = network.claim(route, 1)
        assert claim.legs == 2
        assert describe(claim) == [("C1~C2", "backward"), ("C1", None), ("C1~PARK", "forward"), ("PARK", None)]

    def test_into_a_branch_from_the_side(self) -> None:
        graph, nodes = t_layout()
        network = TrafficNetwork(graph)
        claim = network.claim(route_of(nodes, "PARK", "C1", "STORE_OUT"), 0)
        assert claim.legs == 2
        assert describe(claim) == [
            ("C1~PARK", "backward"),
            ("C1", None),
            ("C1~STORE_OUT", "forward"),
            ("STORE_OUT", None),
        ]

    def test_through_the_root_into_a_branch(self) -> None:
        graph, nodes = t_layout()
        network = TrafficNetwork(graph)
        claim = network.claim(route_of(nodes, "STORE_OUT", "C1", "C2", "LINE_IN"), 0)
        assert claim.legs == 3
        assert [name for name, _ in describe(claim) if "~" not in name] == ["C1", "C2", "LINE_IN"]

    def test_starting_at_the_root(self) -> None:
        graph, nodes = t_layout()
        network = TrafficNetwork(graph)
        claim = network.claim(route_of(nodes, "C1", "STORE_OUT"), 0)
        assert claim.legs == 1
        assert describe(claim) == [("C1~STORE_OUT", "forward"), ("STORE_OUT", None)]

    def test_starting_inside_a_branch_and_going_deeper(self) -> None:
        graph, nodes = t_layout()
        network = TrafficNetwork(graph)
        claim = network.claim(route_of(nodes, "C2", "LINE_IN"), 0)
        assert claim.legs == 1
        # a branch with two walked nodes, entered at its first node and going deeper to the leaf
        graph3, nodes3 = build(
            [("R", 0, 0), ("B1", 10, 0), ("B2", 20, 0), ("L", 30, 0), ("p", 0, 10), ("q", 0, -10)],
            [("R", "B1"), ("B1", "B2"), ("B2", "L"), ("R", "p"), ("R", "q")],
        )
        deep = TrafficNetwork(graph3).claim(route_of(nodes3, "B1", "B2", "L"), 0)
        assert deep.legs == 2

    def test_rule_off_gives_single_legs(self) -> None:
        graph, nodes = t_layout()
        network = TrafficNetwork(graph, hold_back_dead_ends=False)
        for route in (
            route_of(nodes, "PARK", "C1", "STORE_OUT"),
            route_of(nodes, "STORE_OUT", "C1", "C2", "LINE_IN"),
            route_of(nodes, "C1", "STORE_OUT"),
            route_of(nodes, "LINE_IN", "C2", "C1", "PARK"),
        ):
            for index in range(len(route) - 1):
                assert network.claim(route, index).legs == 1

    def test_index_out_of_range_raises(self) -> None:
        graph, nodes = t_layout()
        network = TrafficNetwork(graph)
        route = route_of(nodes, "C1", "C2")
        with pytest.raises(ValueError, match="index"):
            network.claim(route, 1)
        with pytest.raises(ValueError, match="index"):
            network.claim(route, -1)
        with pytest.raises(ValueError, match="index"):
            network.claim(route[:1], 0)

    def test_corridor_is_claimed_with_the_leg_into_its_port(self) -> None:
        graph, nodes = two_junction_corridor()
        network = TrafficNetwork(graph)
        claim = network.claim(route_of(nodes, "x1", "J1", "a", "b", "J2", "y2"), 0)
        assert claim.legs == 1
        assert describe(claim) == [("corridor:a", "forward"), ("J1~x1", "backward"), ("J1", None)]

    def test_corridor_orientation_backward(self) -> None:
        graph, nodes = two_junction_corridor()
        network = TrafficNetwork(graph)
        claim = network.claim(route_of(nodes, "y2", "J2", "b", "a", "J1", "x1"), 0)
        assert describe(claim)[0] == ("corridor:a", "backward")

    def test_route_starting_at_the_port_carries_the_corridor(self) -> None:
        graph, nodes = two_junction_corridor()
        network = TrafficNetwork(graph)
        claim = network.claim(route_of(nodes, "J1", "a", "b", "J2"), 0)
        assert describe(claim) == [("corridor:a", "forward"), ("J1~a", "forward"), ("a", None)]

    def test_leg_inside_the_corridor_carries_it_again(self) -> None:
        graph, nodes = two_junction_corridor()
        network = TrafficNetwork(graph)
        route = route_of(nodes, "J1", "a", "b", "J2")
        assert describe(network.claim(route, 1)) == [("corridor:a", "forward"), ("a~b", "forward"), ("b", None)]
        assert describe(network.claim(route, 2))[0] == ("corridor:a", "forward")

    def test_corridor_not_claimed_for_a_leg_that_does_not_enter_it(self) -> None:
        graph, nodes = two_junction_corridor()
        network = TrafficNetwork(graph)
        claim = network.claim(route_of(nodes, "x1", "J1", "y1"), 0)
        assert all(not name.startswith("corridor") for name, _ in describe(claim))

    def test_route_ending_at_the_port_has_no_lookahead(self) -> None:
        graph, nodes = two_junction_corridor()
        network = TrafficNetwork(graph)
        claim = network.claim(route_of(nodes, "a", "b", "J2"), 1)
        assert describe(claim) == [("corridor:a", "forward"), ("J2~b", "backward"), ("J2", None)]

    def test_same_port_corridor_orientations(self) -> None:
        graph, nodes = build(
            [("P", 0, 0), ("A", 10, 10), ("B", 10, -10), ("Q", -10, 0), ("R", -20, 5), ("S", -20, -5)],
            [("P", "A"), ("A", "B"), ("B", "P"), ("P", "Q"), ("Q", "R"), ("Q", "S")],
        )
        network = TrafficNetwork(graph)
        forward = network.claim(route_of(nodes, "Q", "P", "A", "B", "P"), 0)
        assert describe(forward)[0] == ("corridor:A", "forward")
        backward = network.claim(route_of(nodes, "Q", "P", "B", "A", "P"), 0)
        assert describe(backward)[0] == ("corridor:A", "backward")
        assert describe(network.claim(route_of(nodes, "A", "B", "P"), 1))[0] == ("corridor:A", "forward")

    def test_hold_back_zone_is_claimed_one_leg_early(self) -> None:
        graph, nodes = line(5)
        network = TrafficNetwork(graph, zones=[Zone("z", (nodes["N3"],), hold_back=True)])
        route = route_of(nodes, "N0", "N1", "N2", "N3", "N4")
        names = [{name for name, _ in describe(network.claim(route, i))} for i in range(4)]
        assert [("z" in n) for n in names] == [False, True, True, False]

    def test_plain_zone_only_with_the_leg_that_takes_a_member(self) -> None:
        graph, nodes = line(5)
        network = TrafficNetwork(graph, zones=[Zone("z", (nodes["N3"],))])
        route = route_of(nodes, "N0", "N1", "N2", "N3", "N4")
        names = [{name for name, _ in describe(network.claim(route, i))} for i in range(4)]
        assert [("z" in n) for n in names] == [False, False, True, False]

    def test_zone_segment_member(self) -> None:
        graph, nodes = line(5)
        network = TrafficNetwork(graph, zones=[Zone("z", ((nodes["N3"], nodes["N2"]),), hold_back=True)])
        route = route_of(nodes, "N0", "N1", "N2", "N3", "N4")
        names = [{name for name, _ in describe(network.claim(route, i))} for i in range(4)]
        assert [("z" in n) for n in names] == [False, True, True, False]

    def test_hold_back_zone_touched_by_the_first_leg(self) -> None:
        graph, nodes = line(3)
        network = TrafficNetwork(graph, zones=[Zone("z", (nodes["N1"],), hold_back=True)])
        route = route_of(nodes, "N0", "N1", "N2")
        assert ("z", None) in describe(network.claim(route, 0))

    def test_hold_back_zone_is_claimed_only_before_its_first_member_leg(self) -> None:
        graph, nodes = line(5)
        network = TrafficNetwork(graph, zones=[Zone("z", (nodes["N2"], nodes["N4"]), hold_back=True)])
        route = route_of(nodes, "N0", "N1", "N2", "N3", "N4")
        names = [{name for name, _ in describe(network.claim(route, i))} for i in range(4)]
        assert [("z" in n) for n in names] == [True, True, False, True]

    def test_zone_spanning_a_dead_end_claim_appears_once(self) -> None:
        graph, nodes = t_layout()
        zone = Zone("z", (nodes["C1"], nodes["C2"]))
        network = TrafficNetwork(graph, zones=[zone])
        claim = network.claim(route_of(nodes, "STORE_OUT", "C1", "C2", "LINE_IN"), 0)
        assert [name for name, _ in describe(claim)].count("z") == 1
        assert len(set(claim.items)) == len(claim.items)


class TestZoneValidation:
    def test_member_outside_the_graph_raises(self) -> None:
        graph, _ = line(3)
        with pytest.raises(ValueError, match="Z"):
            TrafficNetwork(graph, zones=[Zone("z", (Node("Z", 0, 0),))])

    def test_pair_member_outside_the_graph_raises(self) -> None:
        graph, nodes = line(3)
        with pytest.raises(ValueError, match="Z"):
            TrafficNetwork(graph, zones=[Zone("z", ((nodes["N0"], Node("Z", 0, 0)),))])

    def test_pair_must_be_adjacent(self) -> None:
        graph, nodes = line(3)
        with pytest.raises(ValueError, match="adjacent"):
            TrafficNetwork(graph, zones=[Zone("z", ((nodes["N0"], nodes["N2"]),))])

    def test_duplicate_zone_ids_raise(self) -> None:
        graph, nodes = line(3)
        with pytest.raises(ValueError, match="duplicate"):
            TrafficNetwork(graph, zones=[Zone("z", (nodes["N0"],)), Zone("z", (nodes["N1"],))])

    def test_zone_id_equal_to_a_corridor_id_raises(self) -> None:
        graph, nodes = two_junction_corridor()
        with pytest.raises(ValueError, match="corridor:a"):
            TrafficNetwork(graph, zones=[Zone("corridor:a", (nodes["x1"],))])

    def test_corridor_id_is_free_when_derivation_is_off(self) -> None:
        graph, nodes = two_junction_corridor()
        network = TrafficNetwork(graph, zones=[Zone("corridor:a", (nodes["x1"],))], derive_corridors=False)
        assert [z.id for z in network.zones] == ["corridor:a"]


class TestImports:
    def test_only_graph_is_imported_from_the_package(self) -> None:
        import ast
        from pathlib import Path

        import simulatte.intralogistics.network as module

        tree = ast.parse(Path(module.__file__).read_text())
        imported = {
            node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module is not None
        } | {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        assert imported <= {"__future__", "collections.abc", "dataclasses", "typing", "simulatte.intralogistics.graph"}
