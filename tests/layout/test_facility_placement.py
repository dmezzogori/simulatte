"""Facilities placed at the node they serve (studio map spec §6.3, §6.5)."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import runpy
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from simulatte import Environment
from simulatte.intralogistics.builders import build_simple_system
from simulatte.intralogistics.charging import ChargingStation
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.parking import ParkingArea
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.warehouse import Warehouse
from simulatte.layout import Explicit, LayoutBundle, LayoutError
from simulatte.layout.auto import NetworkGeometry, TargetMetadata, auto_layout
from simulatte.layout.bundle import reresolve_presentation
from simulatte.layout.identity import hashes
from simulatte.layout.layers import FileLayer, resolve_layers
from simulatte.layout.topology import StaticTopology
from simulatte.layout.types import Point

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def values(placements=None):
    return resolve_layers(
        (),
        FileLayer({"schema_version": "1.0", "placements": placements or {}}),
        targets={key: value["kind"] for key, value in (placements or {}).items()},
    )


def network(nodes, arcs):
    """`nodes` maps ids to (x, y); the node targets and the geometry of the network."""
    targets = {key: TargetMetadata("node", graph_position=Point(*xy)) for key, xy in nodes.items()}
    return targets, NetworkGeometry.from_arcs({key: Point(*xy) for key, xy in nodes.items()}, arcs)


def place(nodes, arcs, facilities, placements=None):
    targets, geometry = network(nodes, arcs)
    result = auto_layout({**targets, **facilities}, StaticTopology(), values(placements), network=geometry)
    return result


def position(result, key):
    return result.data["placements"][key].get("position")


LANE = ({"A": (0.0, 0.0), "B": (10.0, 0.0)}, [("A", "B")])
"""A lane from A to B, one way: B is its end."""


def bound(kind, *node_ids):
    return TargetMetadata(kind, node_ids=node_ids)


# --- the rule on hand cases ---------------------------------------------------------------------------------------


def test_a_warehouse_at_the_end_of_a_lane_is_beyond_its_node_by_half_its_extent_and_1_m():
    result = place(*LANE, {"w": bound("warehouse", "B")})
    # B's only neighbour is A, to the west: the direction is +x; half of 1 m plus 1 m
    assert position(result, "w") == (11.5, 0.0, 0.0)
    assert result.algorithm == "layered-v2"
    footprint = {"kind": "warehouse", "footprint": {"width": 4, "depth": 2}}
    assert position(place(*LANE, {"w": bound("warehouse", "B")}, {"w": footprint}), "w") == (13.0, 0.0, 0.0)
    rotated = {**footprint, "rotation": 90}  # its depth now lies along x
    assert position(place(*LANE, {"w": bound("warehouse", "B")}, {"w": rotated}), "w") == (12.0, 0.0, 0.0)
    oblique = {**footprint, "rotation": 45}  # the support width of the rotated rectangle along x
    x, y, _ = position(place(*LANE, {"w": bound("warehouse", "B")}, {"w": oblique}), "w")
    assert math.isclose(x, 10 + 6 * math.sqrt(0.5) / 2 + 1, rel_tol=1e-15) and y == 0.0


def test_a_warehouse_with_several_bays_is_beside_its_first_input_bay():
    nodes = {"I": (0.0, 0.0), "X": (10.0, 0.0), "O": (0.0, 10.0)}
    result = place(nodes, [("I", "X"), ("X", "O")], {"w": bound("warehouse", "I", "O")})
    assert position(result, "w") == (-1.5, 0.0, 0.0)  # not the mean of its bays (M28)


def test_neighbours_that_cancel_and_no_neighbour_give_plus_y():
    nodes = {"L": (-5.0, 0.0), "C": (0.0, 0.0), "R": (5.0, 0.0), "N": (3.0, 4.0)}
    result = place(nodes, [("L", "C"), ("C", "R")], {"w": bound("warehouse", "C"), "v": bound("warehouse", "N")})
    assert position(result, "w") == (0.0, 1.5, 0.0)
    assert position(result, "v") == (3.0, 5.5, 0.0)


def test_both_arc_directions_count_once_per_neighbour():
    nodes = {"A": (0.0, 0.0), "B": (10.0, 0.0), "C": (10.0, 10.0)}
    result = place(nodes, [("A", "B"), ("B", "A"), ("B", "C")], {"w": bound("warehouse", "B")})
    # unit vectors (-1, 0) and (0, 1): the direction is (1, -1) / sqrt 2, the extent of 1 x 1 along it sqrt 2
    reach = math.sqrt(2) / 2 + 1
    x, y, _ = position(result, "w")
    assert math.isclose(x, 10 + reach / math.sqrt(2), rel_tol=1e-15)
    assert math.isclose(y, -reach / math.sqrt(2), rel_tol=1e-15)


def test_a_neighbour_on_the_node_itself_gives_no_direction():
    nodes = {"A": (0.0, 0.0), "B": (10.0, 0.0), "C": (10.0, 0.0)}
    result = place(nodes, [("A", "B"), ("B", "C")], {"w": bound("warehouse", "B"), "v": bound("warehouse", "C")})
    assert position(result, "w") == (11.5, 0.0, 0.0)  # A alone gives the direction
    assert position(result, "v") == (10.0, 1.5, 0.0)  # C's only neighbour is on it: +y


def test_a_warehouse_overlapping_an_explicit_placement_moves_by_its_own_extent_until_clear():
    explicit = {
        "s": {"kind": "server", "position": [12, 0, 0], "footprint": {"width": 3, "depth": 1}},
        "p": {"kind": "parking_area", "position": [16.5, 0, 0]},  # parking areas are no obstacle
    }
    facilities = {"w": bound("warehouse", "B"), "s": TargetMetadata("server"), "p": TargetMetadata("parking_area")}
    result = place(*LANE, facilities, explicit)
    # s covers x in [10.5, 13.5]: the 1 m warehouse at 11.5, 12.5 and 13.5 overlaps it, at 14.5 it touches it
    assert position(result, "w") == (14.5, 0.0, 0.0)
    wide = {"w": {"kind": "warehouse", "footprint": {"width": 2, "depth": 1}}, **explicit}
    # its extent along x is 2 m: at 12 and 14 it overlaps s, at 16 it is clear (p at 16.5 is no obstacle)
    assert position(place(*LANE, facilities, wide), "w") == (16.0, 0.0, 0.0)


def test_a_warehouse_moving_towards_negative_x_steps_the_same_way():
    explicit = {"s": {"kind": "server", "position": [-2, 0, 0], "footprint": {"width": 3, "depth": 1}}}
    result = place(*LANE, {"w": bound("warehouse", "A"), "s": TargetMetadata("server")}, explicit)
    # A's neighbour B is east: the direction is -x; s covers x in [-3.5, -0.5]
    assert position(result, "w") == (-4.5, 0.0, 0.0)


def test_an_overlap_on_one_axis_only_is_no_overlap():
    explicit = {"s": {"kind": "server", "position": [11.5, 2, 0]}}
    result = place(*LANE, {"w": bound("warehouse", "B"), "s": TargetMetadata("server")}, explicit)
    assert position(result, "w") == (11.5, 0.0, 0.0)


def test_two_warehouses_on_one_node_are_placed_in_id_order():
    result = place(*LANE, {"b": bound("warehouse", "B"), "a": bound("warehouse", "B")})
    assert position(result, "a") == (11.5, 0.0, 0.0)
    assert position(result, "b") == (12.5, 0.0, 0.0)


def test_parking_areas_and_charging_stations_are_centred_on_their_node_with_3_by_3():
    facilities = {
        "w": bound("warehouse", "B"),
        "p": bound("parking_area", "B"),
        "c": bound("charging_station", "B"),
        "q": bound("parking_area", "A"),
    }
    result = place(*LANE, facilities, {"q": {"kind": "parking_area", "footprint": {"width": 5}}})
    for key in ("p", "c"):  # two on one node coincide
        assert position(result, key) == (10.0, 0.0, 0.0)
        assert dict(result.data["placements"][key]["footprint"]) == {"width": 3.0, "depth": 3.0, "height": 0.0}
    assert position(result, "q") == (0.0, 0.0, 0.0)
    merged = resolve_layers((), values().bundle.file_layer, result.data, targets={"q": "parking_area"})
    assert merged.data["placements"]["q"]["footprint"]["width"] == 3.0  # the auto layer alone
    explicit = values({"q": {"kind": "parking_area", "footprint": {"width": 5}}})
    merged = resolve_layers((), explicit.bundle.file_layer, result.data, targets={"q": "parking_area"})
    assert dict(merged.data["placements"]["q"]["footprint"]) == {"width": 5, "depth": 3.0, "height": 0.0}
    assert position(result, "w") == (11.5, 0.0, 0.0)  # the stations on its node are no obstacle


def test_a_facility_without_binding_and_a_server_keep_the_grid():
    facilities = {"u": TargetMetadata("warehouse"), "s": TargetMetadata("server"), "x": TargetMetadata("parking_area")}
    without = place(*LANE, {**facilities, "w": TargetMetadata("warehouse")})
    result = place(*LANE, {**facilities, "w": bound("warehouse", "B")})
    for key in facilities:
        assert position(result, key) == position(without, key) is not None
    assert dict(result.data["placements"]["x"]["footprint"]) == {"width": 1, "depth": 1, "height": 0}
    assert without.algorithm == "layered-v1"


def test_an_oblique_direction_is_the_same_on_every_platform():
    # from N the neighbour M is at (-3, -4): the direction is (0.6, 0.8); a 1 m square extends 1.4 m along it, so the
    # centre is 0.7 + 1 m from N. The length is the square root of an exact sum of squares, not libm's hypot.
    result = place({"N": (0.0, 0.0), "M": (-3.0, -4.0)}, [("M", "N")], {"w": bound("warehouse", "N")})
    assert position(result, "w") == (1.02, 1.36, 0.0)


def test_the_grid_avoids_the_facilities_placed_at_their_node():
    # A warehouse beside B reaches into the grid near the origin; the servers packed there must keep clear of it.
    nodes = {"A": (-20.0, 0.3), "B": (1.0, 0.3)}
    servers = {f"s{i}": TargetMetadata("server") for i in range(6)}
    explicit = {"w": {"kind": "warehouse", "footprint": {"width": 4, "depth": 2}}}
    result = place(nodes, [("A", "B")], {"w": bound("warehouse", "B"), **servers}, explicit)
    assert position(result, "w") == (4.0, 0.3, 0.0)
    wx, wy, _ = position(result, "w")
    for key in servers:
        x, y, _ = position(result, key)
        apart = abs(x - wx) >= (4 + 1) / 2 or abs(y - wy) >= (2 + 1) / 2  # touching is no overlap
        assert apart, (key, (x, y))


def test_a_station_with_an_explicit_position_keeps_the_1_by_1_default():
    explicit = {"p": {"kind": "parking_area", "position": [11.2, -1.2, 0]}}
    targets, geometry = network({"A": (0.0, 0.0), "B": (10.0, 0.0)}, [("A", "B")])
    result = auto_layout(
        {**targets, "p": bound("parking_area", "B")}, StaticTopology(), values(explicit), network=geometry
    )
    assert dict(result.data["placements"]["p"]["footprint"]) == {"width": 1, "depth": 1, "height": 0}
    assert not [item for item in result.diagnostics if item.code == "placement-overlap"]


def test_an_explicit_position_wins():
    explicit = {
        "w": {"kind": "warehouse", "position": [50, 50, 0]},
        "p": {"kind": "parking_area", "position": [60, 60, 0]},
    }
    result = place(*LANE, {"w": bound("warehouse", "B"), "p": bound("parking_area", "B")}, explicit)
    assert position(result, "w") is None and position(result, "p") is None


def test_an_explicitly_unset_position_stays_unset():
    targets, geometry = network(*LANE)
    unset = resolve_layers(
        (),
        FileLayer({"schema_version": "1.0", "placements": {"w": {"kind": "warehouse", "position": {"$unset": True}}}}),
        targets={"w": "warehouse"},
    )
    result = auto_layout({**targets, "w": bound("warehouse", "B")}, StaticTopology(), unset, network=geometry)
    assert position(result, "w") is None


def test_node_ids_need_their_node_in_the_network():
    targets, geometry = network(*LANE)
    with pytest.raises(LayoutError, match="node_ids"):
        auto_layout({**targets, "w": bound("warehouse", "Z")}, StaticTopology(), values(), network=geometry)
    with pytest.raises(LayoutError, match="node_ids"):
        auto_layout({"w": bound("warehouse", "B")}, StaticTopology(), values())


def test_a_placement_beyond_float_range_is_refused():
    huge = {"A": (-1.7e308, 0.0), "B": (1.7e308, 0.0)}
    with pytest.raises(LayoutError, match="float64"):
        place(
            huge,
            [("A", "B")],
            {"w": bound("warehouse", "B")},
            {"w": {"kind": "warehouse", "footprint": {"width": 1e308}}},
        )


def test_a_step_below_float_precision_is_refused():
    far = {"A": (0.0, 0.0), "B": (1e17, 0.0)}  # a float there moves by 16 m: a 1 m step cannot move it
    explicit = {"s": {"kind": "server", "position": [1e17, 0, 0], "footprint": {"width": 64, "depth": 1}}}
    with pytest.raises(LayoutError, match="precision"):
        place(far, [("A", "B")], {"w": bound("warehouse", "B"), "s": TargetMetadata("server")}, explicit)


def test_a_graph_record_naming_a_missing_node_is_refused():
    record = {"nodes": [{"id": "A", "position": [0.0, 0.0, 0.0]}], "arcs": [{"source": "A", "target": "Z"}]}
    with pytest.raises(LayoutError, match="Z"):
        NetworkGeometry.from_record(record)
    env = Environment(seed=1)
    build_simple_system(env)
    data = env.layout.prepare().bundle.to_dict()
    data["network"]["graph"]["arcs"][0]["target"] = "nowhere"
    data.update(hashes(data))
    with pytest.raises(LayoutError, match="nowhere"):
        reresolve_presentation(LayoutBundle.from_dict(data), {"schema_version": "1.0"})


def test_the_geometry_of_a_bundle_record():
    record = {
        "nodes": [{"id": "A", "position": [0.0, 0.0, 0.0]}, {"id": "B", "position": [10.0, 0.0, 0.0]}],
        "arcs": [{"source": "A", "target": "B"}, {"source": "B", "target": "A"}, {"source": "B", "target": "B"}],
    }
    geometry = NetworkGeometry.from_record(record)
    assert geometry.positions == {"A": Point(0, 0), "B": Point(10, 0)}
    assert geometry.neighbours == {"A": ("B",), "B": ("A",)}


# --- models -------------------------------------------------------------------------------------------------------


def sha(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def placed(bundle, key):
    return tuple(bundle["resolved"]["placements"][key]["position"][:2])


def test_a_model_records_the_bound_nodes_in_binding_order_and_reresolves_them():
    env = Environment(seed=1)
    layout = env.layout
    x, y, z = Node("X", 0, 0), Node("Y", 10, 0), Node("Z", 20, 0)
    layout.set_network(
        Explicit(LayoutGraph([x, y, z], [Arc(x, y, bidirectional=False), Arc(y, z, bidirectional=False)]))
    )
    sku = SKU("s", 1.0, 1.0)
    Warehouse(
        env=env,
        name="Store",
        input_bays=[layout.node("Z"), layout.node("Y")],
        output_bays=[layout.node("Y"), layout.node("X")],
        n_slots=1,
        products=[sku],
        pick_time=1.0,
        put_time=1.0,
    )
    Warehouse(env=env, name="Loose", input_bays=[], output_bays=[], n_slots=1, products=[sku], pick_time=1, put_time=1)
    ParkingArea(env=env, name="Bay", node=layout.node("X"), capacity=1)
    ChargingStation(env=env, name="Plug", node=layout.node("X"), n_slots=1)
    bundle = layout.prepare().bundle
    assert bundle["targets"]["Store"]["node_ids"] == ("Z", "Y", "X")
    assert bundle["targets"]["Bay"]["node_ids"] == bundle["targets"]["Plug"]["node_ids"] == ("X",)
    assert "node_ids" not in bundle["targets"]["Loose"]
    assert bundle["algorithms"]["auto"] == "layered-v2"
    assert placed(bundle, "Store") == (21.5, 0.0)  # beside Z, its first input bay, away from Y
    assert placed(bundle, "Bay") == placed(bundle, "Plug") == (0.0, 0.0)
    bundle.check()
    saved = LayoutBundle.from_dict(json.loads(json.dumps(bundle.to_dict())))
    patch = {"schema_version": "1.0", "placements": {"Store": {"kind": "warehouse", "visual": {"label": "S"}}}}
    replay = reresolve_presentation(saved, patch)
    assert replay.bundle is not None
    assert replay.bundle["resolved"]["placements"] == {
        key: value if key != "Store" else {**value, "visual": {"label": "S"}}
        for key, value in bundle["resolved"]["placements"].items()
    }
    assert replay.bundle.physical_hash == bundle.physical_hash


_SCRIPT = """
import hashlib, io, json, runpy, sys
from contextlib import redirect_stdout
from simulatte import Environment
from simulatte.intralogistics.builders import build_simple_system

def digest(bundle):
    return hashlib.sha256(json.dumps(bundle.to_dict(), sort_keys=True).encode()).hexdigest()

env = Environment(seed=1)
build_simple_system(env)
print(digest(env.layout.prepare().bundle))

class Prepared(Exception):
    pass

class Captured(Environment):  # the advanced example: five facilities, warehouses with two bays
    def run(self, *args, **kwargs):
        print(digest(self.layout.prepare().bundle))
        raise Prepared

main = runpy.run_path(sys.argv[1], run_name="__headless__")["main"]
main.__globals__["Environment"] = Captured
try:
    with redirect_stdout(io.StringIO()) as quiet:
        main(plot=False)
except Prepared:
    print(quiet.getvalue().splitlines()[-1])
"""


def test_the_same_model_gives_the_same_bundle_under_any_hash_seed():
    output = [
        subprocess.check_output(
            [sys.executable, "-c", _SCRIPT, str(EXAMPLES / "intralogistics_advanced.py")],
            env={**os.environ, "PYTHONHASHSEED": seed},
            text=True,
        )
        for seed in ("0", "1")
    ]
    assert output[0] == output[1]
    assert len(output[0].split()) == 2


class _Prepared(Exception):
    pass


def example_bundle(example, **kwargs):
    """The layout bundle of ``examples/<example>.py``, prepared where the script would run its environment."""
    captured = {}

    class Captured(Environment):
        def run(self, *args, **kwargs):
            captured["bundle"] = self.layout.prepare().bundle
            raise _Prepared

    main = runpy.run_path(str(EXAMPLES / f"{example}.py"), run_name="__headless__")["main"]
    main.__globals__["Environment"] = Captured
    with pytest.raises(_Prepared), redirect_stdout(io.StringIO()):
        main(**kwargs)
    return captured["bundle"]


def test_the_corridor_and_the_advanced_example_give_the_positions_of_the_map_fixtures():
    corridor = example_bundle("intralogistics_traffic")
    assert placed(corridor, "West") == (-6.5, 0.0)
    assert placed(corridor, "East") == (96.5, 0.0)
    assert placed(corridor, "Siding") == (45.0, 6.0)
    assert dict(corridor["resolved"]["placements"]["Siding"]["footprint"])["width"] == 3.0
    advanced = example_bundle("intralogistics_advanced", plot=False)
    expected = {
        "Receiving": (-1.5, 30.0),
        "Bulk Storage": (111.5, 30.0),
        "Dispatch": (140.0, 1.5),
        "Charger": (80.0, 15.0),
        "Parking": (100.0, 15.0),
    }
    assert {key: placed(advanced, key) for key in expected} == expected


MOVED: dict[str, set[str]] = {}
"""Per example, the warehouses the overlap rule moved further along the direction: none."""


@pytest.mark.parametrize(
    ("example", "kwargs"),
    [
        ("intralogistics_simple", {}),
        ("intralogistics_intermediate", {"plot": False}),
        ("intralogistics_advanced", {"plot": False}),
        ("intralogistics_traffic", {}),
    ],
)
def test_each_warehouse_of_the_examples_is_beside_its_first_input_bay(example, kwargs):
    bundle = example_bundle(example, **kwargs)
    graph = bundle["network"]["graph"]
    nodes = {node["id"]: node["position"] for node in graph["nodes"]}
    warehouses = [key for key, target in bundle["targets"].items() if target["kind"] == "warehouse"]
    assert warehouses
    for key in warehouses:
        anchor = bundle["targets"][key]["node_ids"][0]
        ax, ay = nodes[anchor][:2]
        near = {arc["target"] for arc in graph["arcs"] if arc["source"] == anchor}
        near |= {arc["source"] for arc in graph["arcs"] if arc["target"] == anchor}
        units = [(nodes[n][0] - ax, nodes[n][1] - ay) for n in sorted(near)]
        sx = sum(dx / math.hypot(dx, dy) for dx, dy in units) / len(units)
        sy = sum(dy / math.hypot(dx, dy) for dx, dy in units) / len(units)
        ux, uy = (0.0, 1.0) if math.hypot(sx, sy) < 1e-9 else (-sx / math.hypot(sx, sy), -sy / math.hypot(sx, sy))
        placement = bundle["resolved"]["placements"][key]
        footprint, angle = placement["footprint"], math.radians(placement["rotation"])
        extent = (
            abs(ux * math.cos(angle) + uy * math.sin(angle)) * footprint["width"]
            + abs(-ux * math.sin(angle) + uy * math.cos(angle)) * footprint["depth"]
        )
        x, y = placement["position"][:2]
        along, across = (x - ax) * ux + (y - ay) * uy, (x - ax) * uy - (y - ay) * ux
        assert abs(across) <= 1e-9, (example, key)
        if key in MOVED.get(example, set()):
            assert along > extent / 2 + 1, (example, key)
        else:
            assert abs(along - (extent / 2 + 1)) <= 1e-9, (example, key)


def test_a_simple_system_places_its_warehouses_and_its_station():
    env = Environment(seed=1)
    build_simple_system(env)
    bundle = env.layout.prepare().bundle
    assert placed(bundle, "WH-A") == (-1.5, 0.0)
    assert placed(bundle, "WH-B") == (21.5, 0.0)
    assert placed(bundle, "CS-CENTER") == (10.0, 0.0)
