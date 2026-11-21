"""Graph builders shared by the traffic tests.

Each builder returns ``(graph, nodes)`` with ``nodes`` the dict of the graph's nodes by id. All arcs are two-way and
keep the default chord length.
"""

from __future__ import annotations

from collections.abc import Sequence

from simulatte.intralogistics.graph import Arc, LayoutGraph, Node


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
