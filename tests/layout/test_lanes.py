import pytest

from simulatte.layout.network import generate_base_network
from simulatte.layout.refs import Address
from simulatte.layout.types import LayoutLimits
from .test_lattice import values


def lane(*points, **attributes):
    return {"vertices": [{"id": str(i), "position": [x, y, 0]} for i, (x, y) in enumerate(points)], **attributes}


def test_crossing_and_endpoint_aliases():
    result = generate_base_network(
        values(
            {
                "type": "lanes",
                "lanes": {"a": lane((-1, 0), (1, 0)), "b": lane((0, -1), (0, 1)), "c": lane((0, 0), (1, 1))},
            }
        ),
        limits=LayoutLimits(),
    )
    assert len(result.nodes) == 6
    assert len(result.edges) == 5
    endpoint = Address({"kind": "lane_vertex", "lane": "c", "vertex": "0"})
    junction = result.aliases[endpoint]
    assert junction.to_dict()["kind"] == "intersection"
    assert len(junction.to_dict()["incidents"]) == 3
    assert len(result.adjacency[junction]) == 5


def test_overlap_and_pair_limit():
    data = values({"type": "lanes", "lanes": {"a": lane((0, 0), (2, 0)), "b": lane((1, 0), (3, 0))}})
    with pytest.raises(ValueError, match="overlap"):
        generate_base_network(data, limits=LayoutLimits())
    data = values({"type": "lanes", "lanes": {"a": lane((0, 0), (1, 0), (2, 1), (3, 0))}})
    with pytest.raises(ValueError, match="max_lane_segment_pairs"):
        generate_base_network(data, limits=LayoutLimits(max_lane_segment_pairs=2))


def test_t_endpoint_self_and_coincident_vertices():
    for lanes, nodes, edges in [
        ({"a": lane((-1, 0), (1, 0)), "b": lane((0, 0), (0, 1))}, 4, 3),
        ({"a": lane((0, 0), (1, 0)), "b": lane((1, 0), (1, 1))}, 3, 2),
        ({"a": lane((0, 0), (2, 2), (0, 2), (2, 0))}, 5, 5),
        ({"a": lane((0, 0), (1, 0), (1, 1), (0, 0))}, 3, 3),
    ]:
        result = generate_base_network(values({"type": "lanes", "lanes": lanes}), limits=LayoutLimits())
        assert len(result.nodes) == nodes and len(result.edges) == edges
        assert len(result.lineage) == sum(len(item["vertices"]) - 1 for item in lanes.values())
        assert all(e.lineage for e in result.edges.values())


def test_intersection_identity_movement_and_parent_staleness():
    def network(y):
        return generate_base_network(
            values(
                {
                    "type": "lanes",
                    "lanes": {
                        "a": lane((-2, y), (2, y), speed_limit=2),
                        "b": lane((0, -2), (0, 2), speed_limit=3, direction="forward"),
                    },
                }
            ),
            limits=LayoutLimits(),
        )

    original, moved = network(0), network(1)

    def junction(network):
        return next(a for a in network.nodes if a.to_dict()["kind"] == "intersection")

    assert junction(original) == junction(moved)
    assert original.nodes[junction(original)].position != moved.nodes[junction(moved)].position
    assert [e.speed_limit for e in original.edges.values()] == [2, 2, 3, 3]
    assert sum(map(len, original.adjacency.values())) == 6
    old = generate_base_network(values({"type": "lanes", "lanes": {"a": lane((0, 0), (2, 0))}}), limits=LayoutLimits())
    changed = generate_base_network(
        values(
            {
                "type": "lanes",
                "lanes": {
                    "a": {
                        "vertices": [
                            {"id": "0", "position": [0, 0, 0]},
                            {"id": "v", "position": [1, 0, 0]},
                            {"id": "1", "position": [2, 0, 0]},
                        ]
                    }
                },
            }
        ),
        limits=LayoutLimits(),
    )
    assert not set(old.lineage) & set(changed.lineage)
    assert {a for a in old.aliases if a.to_dict()["kind"] == "lane_vertex"} <= set(changed.aliases)


@pytest.mark.parametrize(
    "lanes,message",
    [
        ({"a": lane((0, 0), (0, 0))}, "zero-length"),
        (
            {"a": {"vertices": [{"id": "same", "position": [0, 0, 0]}, {"id": "same", "position": [1, 0, 0]}]}},
            "duplicate",
        ),
        ({"a": {}}, "required input"),
    ],
)
def test_invalid_lane_vertices(lanes, message):
    with pytest.raises(ValueError, match=message):
        generate_base_network(values({"type": "lanes", "lanes": lanes}), limits=LayoutLimits())


def test_distinct_exact_crossings_cannot_collapse():
    x = 1e16
    lanes = {"main": lane((x - 2, 0), (x + 2, 0)), "a": lane((x, -1), (x + 2, 5)), "b": lane((x, -2), (x + 2, 4))}
    with pytest.raises(ValueError, match="precision.*zero-length"):
        generate_base_network(values({"type": "lanes", "lanes": lanes}), limits=LayoutLimits())


def test_disabled_lane_stays_addressable_but_obstacles_still_fail():
    lanes = {"a": lane((-2, 0), (2, 0), enabled=False)}
    result = generate_base_network(values({"type": "lanes", "lanes": lanes}), limits=LayoutLimits())
    assert len(result.edges) == 1 and not any(result.adjacency.values())
    with pytest.raises(ValueError, match="obstacle"):
        generate_base_network(
            values(
                {
                    "type": "lanes",
                    "lanes": lanes,
                    "blocked": {"b": {"position": [0, 0, 0], "width": 1, "depth": 1, "rotation": 0}},
                }
            ),
            limits=LayoutLimits(),
        )


def test_lane_limits_before_and_after_splitting():
    lanes = {"a": lane((-1, 0), (1, 0)), "b": lane((0, -1), (0, 1))}
    for limits, message in [
        (LayoutLimits(max_nodes=3), "max_nodes"),
        (LayoutLimits(max_nodes=4), "max_nodes"),
        (LayoutLimits(max_directed_arcs=2), "max_directed_arcs"),
        (LayoutLimits(max_directed_arcs=5), "max_directed_arcs"),
    ]:
        with pytest.raises(ValueError, match=message):
            generate_base_network(values({"type": "lanes", "lanes": lanes}), limits=limits)
    backward = generate_base_network(
        values({"type": "lanes", "lanes": {"a": lane((0, 0), (1, 0), direction="backward")}}), limits=LayoutLimits()
    )
    assert sum(map(len, backward.adjacency.values())) == 1


def test_mapping_order_does_not_change_generated_network():
    a, b = lane((-1, 0), (1, 0)), lane((0, -1), (0, 1))

    def result(lanes):
        return generate_base_network(values({"type": "lanes", "lanes": lanes}), limits=LayoutLimits())

    assert result({"a": a, "b": b}) == result({"b": b, "a": a})


def test_rounded_split_segments_recheck_stored_obstacles():
    # Both original lines pass strictly above the rectangle's top-left corner.
    # Their exact crossing (1,1/3) rounds onto that corner, which is blocked.
    lanes = {"a": lane((0, 0), (3, 1)), "b": lane((0, -1), (3, 3))}
    blocked = {"b": {"position": [1.5, float(1 / 3) - 0.25, 0], "width": 1, "depth": 0.5, "rotation": 0}}
    with pytest.raises(ValueError, match="stored split segment"):
        generate_base_network(values({"type": "lanes", "lanes": lanes, "blocked": blocked}), limits=LayoutLimits())
