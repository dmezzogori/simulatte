"""Static topology and callback-free deterministic presentation placement."""

import json
import os
import subprocess
import sys

import pytest

from simulatte import Environment
from simulatte.layout.auto import TargetMetadata, auto_layout
from simulatte.layout.layers import FileLayer, LayerSource, resolve_layers
from simulatte.layout.topology import StaticTopology, snapshot_topology
from simulatte.layout.types import Point


def values(placements=None, **extra):
    return resolve_layers(
        (),
        FileLayer({"schema_version": "1.0", "placements": placements or {}, **extra}),
        targets={key: value["kind"] for key, value in (placements or {}).items()},
    )


def targets(*ids):
    return {key: TargetMetadata("server") for key in ids}


def positions(layer):
    return {key: value["position"] for key, value in layer.data["placements"].items() if "position" in value}


@pytest.mark.parametrize(
    ("flows", "expected"),
    [
        ((("a", "b", "c"),), {"a": (0, 0, 0), "b": (3, 0, 0), "c": (6, 0, 0)}),
        ((("a", "b", "d"), ("a", "c", "d")), {"a": (0, 0, 0), "b": (3, 0, 0), "c": (3, 3, 0), "d": (6, 0, 0)}),
        ((("a", "b", "a"), ("b", "c")), {"a": (0, 0, 0), "b": (0, 3, 0), "c": (3, 0, 0)}),
        ((("c", "d"), ("a", "b")), {"a": (0, 0, 0), "c": (0, 3, 0), "b": (3, 0, 0), "d": (3, 3, 0)}),
    ],
)
def test_layered_components(flows, expected):
    result = auto_layout(targets(*reversed(expected)), StaticTopology(flows=flows), values())
    assert positions(result) == expected
    assert list(positions(result)) == list(expected)


def test_square_fallback_ignores_grid_and_shuffled_maps():
    expected = {"a": (0, 0, 0), "b": (3, 0, 0), "c": (0, 3, 0), "d": (3, 3, 0)}
    for ids in ("abcd", "dcba"):
        assert positions(auto_layout(targets(*ids), StaticTopology(), values(grid={"dx": 100, "dy": 99}))) == expected


def test_pinned_bounds_do_not_enlarge_cells_and_rotated_auto_bounds_do():
    data = values(
        {
            "p": {"kind": "warehouse", "position": [0, 0, 0], "footprint": {"width": 20, "depth": 20}},
            "a": {"kind": "server", "footprint": {"width": 2, "depth": 4}, "rotation": 90},
        }
    )
    result = auto_layout(
        {**targets("a", "b"), "p": TargetMetadata("warehouse")}, StaticTopology(flows=(("a", "b"),)), data
    )
    assert positions(result) == {"a": (0, 16, 0), "b": (6, 16, 0)}
    assert "position" not in result.data["placements"]["p"]


def test_custom_optin_and_graph_derived_positions_are_separate():
    metadata = {
        **targets("a"),
        "custom": TargetMetadata("zone", explicitly_placed=True),
        "ignored": TargetMetadata("zone"),
        "node": TargetMetadata("node", graph_position=Point(20, 10)),
        "agv": TargetMetadata("agv", node_id="node"),
        "free-agv": TargetMetadata("agv"),
        **{kind: TargetMetadata(kind) for kind in ("fleet", "job", "order", "router", "shopfloor")},
    }
    result = auto_layout(metadata, StaticTopology(), values())
    assert set(positions(result)) == {"a", "custom"}
    assert result.derived_positions == {"node": Point(20, 10), "agv": Point(20, 10)}
    assert set(result.data["placements"]) == {"a", "custom", "node", "agv", "free-agv"}
    assert result.data["placements"]["node"]["footprint"]["width"] == 0.1


def test_fresh_process_hash_seed_parity():
    script = """
import json
from simulatte.layout.auto import TargetMetadata, auto_layout
from simulatte.layout.topology import StaticTopology
from simulatte.layout.layers import FileLayer, resolve_layers
ids = set("abcdef")
result = auto_layout(
    {i: TargetMetadata("server") for i in ids},
    StaticTopology(flows=(("b","c","b"),("a","c","d"))),
    resolve_layers((), FileLayer({"schema_version":"1.0"})),
)
print(json.dumps(result.to_dict(), sort_keys=True))
"""
    output = [
        subprocess.check_output([sys.executable, "-c", script], env={**os.environ, "PYTHONHASHSEED": seed}, text=True)
        for seed in ("1", "17")
    ]
    assert output[0] == output[1]
    assert json.loads(output[0])["algorithm"] == "layered-v1"


def test_snapshot_empty_and_replay():
    result = snapshot_topology(Environment(), (("a", "b"),))
    assert result.flows == (("a", "b"),)
    assert StaticTopology.from_dict(json.loads(json.dumps(result.to_dict()))) == result


def test_snapshot_static_sources_are_conservative_and_rng_is_untouched():
    from simulatte.distributions import FlowShopRouting, GeneralFlowShopRouting, PureJobShopRouting
    from simulatte.router import Router
    from simulatte.server import Server
    from simulatte.shopfloor import ShopFloor

    class OpaqueList(list):
        def __iter__(self):
            raise AssertionError("user iteration")

    class OpaqueMapping(dict):
        def values(self):
            raise AssertionError("user mapping")

    def callback():
        raise AssertionError("routing callback")

    env = Environment(seed=42)
    floor = ShopFloor(env=env)
    a, b, c = [Server(env=env, shopfloor=floor, name=key, capacity=1) for key in "abc"]
    router = Router(
        env=env,
        shopfloor=floor,
        servers=[a, b, c],
        psp=None,
        inter_arrival_distribution=1,
        sku_distributions={"A": 1},
        sku_routings={"A": [a, b]},
        sku_service_times={"A": {a: 1, b: 1}},
        due_date_offset_distribution={"A": 1},
    )
    # Mutate only raw descriptions after constructor binding: construction itself
    # may intentionally normalize user iterables, but preparation must never do so.
    router.sku_routings = {
        "list": [c, a],
        "tuple": (b, c),
        "flow": FlowShopRouting((a, c)),
        "pure": PureJobShopRouting((b, a)),
        "general": GeneralFlowShopRouting((b, a)),
        "callback": callback,
        "iterable": OpaqueList([b, a]),
    }
    stream = env.rng("test/static")
    before = stream.getstate()
    result = snapshot_topology(env, (("c", "b"),))
    assert result.shopfloors == (("a", "b", "c"),)
    assert result.routings == (("a", "c"), ("b", "c"), ("c", "a"))
    assert stream.getstate() == before
    floor.servers = OpaqueList([a, b])
    router.sku_routings = OpaqueMapping()
    assert snapshot_topology(env) == StaticTopology()


def test_foreign_invalid_and_retired_servers_break_static_sequences():
    from simulatte.server import Server
    from simulatte.shopfloor import ShopFloor

    env, other = Environment(), Environment()
    floor = ShopFloor(env=env)
    a, b, c = [Server(env=env, shopfloor=floor, name=key, capacity=1) for key in "abc"]
    foreign = Server(env=other, shopfloor=ShopFloor(env=other), capacity=1)
    env.entities.retire(b)
    vars(floor)["servers"] = [a, b, c, foreign, a, object(), c]
    result = snapshot_topology(env)
    assert result.shopfloors == (("a",), ("c",))


def test_unknown_flow_diagnostics_self_duplicate_edges_and_unplaced_nodes():
    topology = StaticTopology(flows=(("a", "missing", "b"), ("a", "a"), ("a", "missing", "b")))
    result = auto_layout({**targets("a", "b"), "node": TargetMetadata("node")}, topology, values())
    assert positions(result) == {"a": (0, 0, 0), "b": (3, 0, 0)}
    assert [item.code for item in result.diagnostics] == ["unknown-flow-target"]
    assert result.diagnostics[0].target == "missing"
    assert result.derived_positions == {}


def test_graph_position_wins_pinned_overlaps_and_agvs_do_not_occupy_cells():
    metadata = {
        "a": TargetMetadata("node", graph_position=Point(0, 0)),
        "b": TargetMetadata("node", graph_position=Point(0, 0)),
        "robot": TargetMetadata("agv", node_id="missing"),
        **targets("c"),
    }
    explicit = values(
        {
            "a": {"kind": "node", "position": [100, 100, 0]},
            "robot": {"kind": "agv", "position": [0, 3, 0], "footprint": {"width": 1000, "depth": 1000}},
        }
    )
    result = auto_layout(metadata, StaticTopology(), explicit)
    assert positions(result) == {"c": (0, 3, 0)}
    assert result.derived_positions == {"a": Point(0, 0), "b": Point(0, 0)}
    assert [(item.code, item.target) for item in result.diagnostics] == [
        ("node-position-ignored", "a"),
        ("placement-overlap", "a"),
        ("placement-overlap", "b"),
    ]


def test_replay_immutable_targets_and_auto_provenance():
    metadata = {
        "a": TargetMetadata("node", graph_position=Point(9, 9)),
        "b": TargetMetadata("agv", node_id="a"),
        "c": TargetMetadata("server"),
    }
    explicit = values({"c": {"kind": "server", "rotation": {"$unset": True}, "visual": {"label": "C"}}})
    topology = StaticTopology(flows=(("c",),))
    result = auto_layout(metadata, topology, explicit)
    serialized = json.loads(json.dumps(result.to_dict()))
    replay = auto_layout(
        {key: TargetMetadata.from_dict(value) for key, value in serialized["targets"].items()},
        StaticTopology.from_dict(serialized["topology"]),
        explicit,
    )
    assert replay == result
    metadata.clear()
    with pytest.raises(TypeError):
        result.data["placements"]["c"]["footprint"]["width"] = 9
    merged = resolve_layers(
        (),
        explicit.bundle.file_layer,
        result.data,
        targets={key: target.kind for key, target in result.targets.items()},
    )
    assert merged.at(("placements", "c", "position")).source == LayerSource("auto")
    assert merged.at(("placements", "c", "position"), explicit=True).state == "absent"
    assert merged.at(("placements", "c", "rotation")).state == "unset"
    assert merged.at(("placements", "c", "visual", "label")).value == "C"
    assert merged.at(("placements", "a", "position")).state == "absent"


def test_longest_predecessor_path_and_large_nonrecursive_components():
    from simulatte.layout.auto import _cells

    ids = [f"s{i:05}" for i in range(100_000)]
    edges = set(zip(ids, ids[1:]))
    cells = _cells(ids, edges)
    assert cells[-1] == (ids[-1], len(ids) - 1, 0)
    edges.add((ids[-1], ids[0]))
    cells = _cells(ids, edges)
    assert cells[-1] == (ids[-1], 0, len(ids) - 1)
    result = auto_layout(
        targets("a", "b", "c", "d"), StaticTopology(flows=(("a", "b", "c", "d"), ("a", "d"))), values()
    )
    assert positions(result)["d"] == (9, 0, 0)


def test_arbitrary_rotations_and_exact_two_meter_packing_gaps():
    from fractions import Fraction

    explicit = values({key: {"kind": "server", "footprint": {"width": 0.3, "depth": 0.3}} for key in "abcd"})
    result = auto_layout(targets(*"abcd"), StaticTopology(), explicit)
    points = list(positions(result).values())
    for i, a in enumerate(points):
        for b in points[i + 1 :]:
            assert any(abs(Fraction(a[axis]) - Fraction(b[axis])) >= Fraction(0.3) + 2 for axis in (0, 1))
    rotated = values({"a": {"kind": "server", "rotation": 45, "footprint": {"width": 2, "depth": 4}}})
    result = auto_layout(targets("a", "b"), StaticTopology(flows=(("a", "b"),)), rotated)
    assert positions(result)["b"][0] == pytest.approx(6 / 2**0.5 + 2)


def test_small_target_can_reuse_a_row_skipped_by_large_target():
    explicit = values(
        {
            "a": {"kind": "server", "footprint": {"width": 1, "depth": 20}},
            "pin": {"kind": "warehouse", "position": [0, 10, 0]},
        }
    )
    result = auto_layout(
        {**targets("a", "b"), "pin": TargetMetadata("warehouse")}, StaticTopology(flows=(("a", "b", "a"),)), explicit
    )
    assert positions(result) == {"a": (0, 44, 0), "b": (0, 22, 0)}


@pytest.mark.parametrize(
    "metadata",
    [
        {"kind": "server", "explicitly_placed": 1},
        {"kind": "server", "graph_position": Point(0, 0)},
        {"kind": "node", "graph_position": (0, 0)},
        {"kind": "node", "node_id": "n"},
        {"kind": ""},
        {"kind": "\ud800"},
    ],
)
def test_invalid_metadata(metadata):
    from simulatte.layout.errors import LayoutError

    with pytest.raises(LayoutError):
        TargetMetadata(**metadata)


@pytest.mark.parametrize("flows", ["abc", [iter("abc")], [(1,)], [("",)], [("\ud800",)]])
def test_invalid_static_sequences_never_invoke_iterators(flows):
    from simulatte.layout.errors import LayoutError

    with pytest.raises(LayoutError):
        StaticTopology(flows=flows)


def test_limits_empty_and_all_pinned():
    from simulatte.layout.errors import LayoutError
    from simulatte.layout.types import LayoutLimits

    assert auto_layout({}, StaticTopology(), values()).data["placements"] == {}
    with pytest.raises(LayoutError, match="max_placements"):
        auto_layout(targets("a", "b"), StaticTopology(), values(), limits=LayoutLimits(max_placements=1))
    result = auto_layout(targets("a"), StaticTopology(), values({"a": {"kind": "server", "position": [0, 0, 0]}}))
    assert positions(result) == {}


def test_nonfinite_rotated_sizes_position_overflow_and_precision_fail():
    from simulatte.layout.errors import LayoutError

    rotated = values({"a": {"kind": "server", "rotation": 45, "footprint": {"width": 1.7e308, "depth": 1.7e308}}})
    with pytest.raises(LayoutError, match="rotated width"):
        auto_layout(targets("a"), StaticTopology(), rotated)
    huge = values({"a": {"kind": "server", "footprint": {"width": 1e308, "depth": 1}}})
    with pytest.raises(LayoutError, match="finite float64"):
        auto_layout(targets(*"abc"), StaticTopology(flows=(("a", "b", "c"),)), huge)
    pinned = values(
        {"pin": {"kind": "warehouse", "position": [0, 1e100, 0], "footprint": {"width": 1, "depth": 2e100}}}
    )
    with pytest.raises(LayoutError, match="precision"):
        auto_layout({**targets("a"), "pin": TargetMetadata("warehouse")}, StaticTopology(), pinned)


def test_successive_rows_skip_prior_occupants_with_static_routing_edges():
    explicit = values({"pin": {"kind": "warehouse", "position": [0, 0, 0], "footprint": {"width": 10, "depth": 10}}})
    result = auto_layout(
        {**targets(*"abc"), "pin": TargetMetadata("warehouse")},
        StaticTopology(shopfloors=(("a", "b"),), routings=(("b", "c", "a"),)),
        explicit,
    )
    assert positions(result) == {"a": (0, 9, 0), "b": (0, 12, 0), "c": (0, 15, 0)}


def test_unset_position_has_no_phantom_occupancy_and_near_pins_do_not_warn():
    explicit = values(
        {
            "a": {"kind": "server", "position": {"$unset": True}, "footprint": {"width": 100, "depth": 100}},
            "c": {"kind": "server", "position": [20, 20, 0]},
            "d": {"kind": "server", "position": [22, 20, 0]},
        }
    )
    result = auto_layout(targets(*"abcd"), StaticTopology(), explicit)
    assert positions(result) == {"b": (3, 0, 0)}
    assert not result.diagnostics


def test_flow_through_known_graph_node_sets_layers_without_auto_node_position():
    metadata = {**targets("a", "b"), "node": TargetMetadata("node", graph_position=Point(100, 100))}
    result = auto_layout(metadata, StaticTopology(flows=(("a", "node", "b"),)), values())
    assert positions(result) == {"a": (0, 0, 0), "b": (6, 0, 0)}
    assert result.derived_positions == {"node": Point(100, 100)}
    assert not result.diagnostics
