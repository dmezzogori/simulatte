"""Layout schema 1.1: passing edges, block length and zones (spec §12)."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from simulatte import Environment, Layout
from simulatte.intralogistics.agv import AGV, AGVType
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.network import Zone
from simulatte.intralogistics.reservation import ReservationController
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.layout import (
    Connector,
    Explicit,
    Grid,
    Lanes,
    Lattice,
    LayoutBundle,
    LayoutError,
    NetworkOverride,
    Point,
    Port,
)
from simulatte.layout.dependencies import DependencyClosure
from simulatte.layout.io import load_layout, save_layout
from simulatte.layout.layers import FileLayer, resolve_layers
from simulatte.layout.network import resolve_network
from simulatte.layout.refs import Address
from simulatte.layout.schema import parse_patch, schema
from simulatte.layout.types import LayoutLimits
from simulatte.server import Server
from tests.intralogistics.traffic_support import StillFleet, declare

FIXTURES = Path(__file__).parents[1] / "fixtures" / "layout"


def vertex(name: str, x: float, y: float = 0) -> dict[str, Any]:
    return {"id": name, "position": [x, y, 0]}


def lane_patch(**network: Any) -> dict[str, Any]:
    lane = network.pop("lane", {})
    return {
        "network": {
            "type": "lanes",
            "lanes": {"L": {"vertices": [vertex("a", 0), vertex("b", 10)], **lane}},
            **network,
        }
    }


def explicit_patch(arcs: list[dict[str, Any]], nodes: list[tuple[str, float, float]] | None = None, **network: Any):
    points = nodes or [("A", 0, 0), ("B", 10, 0)]
    return {
        "network": {
            "type": "explicit",
            "nodes": [vertex(name, x, y) for name, x, y in points],
            "arcs": arcs,
            **network,
        }
    }


def resolve(patch: dict[str, Any], *, limits: LayoutLimits = LayoutLimits(), version: str = "1.1"):
    values = resolve_layers((("declare", {"schema_version": version, **patch}),), FileLayer({"schema_version": "1.0"}))
    return resolve_network(values, DependencyClosure(), limits=limits)


def arc_of(result, a: Address, b: Address):
    return result.graph.arc_between(result.node(a), result.node(b))


def lane_vertex(name: str, lane: str = "L") -> Address:
    return Address({"kind": "lane_vertex", "lane": lane, "vertex": name})


def explicit_node(name: str) -> Address:
    return Address({"kind": "explicit", "id": name})


def prepared(patch: dict[str, Any], *, version: str = "1.1", **extra: Any):
    env = Environment()
    layout = Layout.from_dict(env, {"schema_version": version, **patch})
    server = Server(env=env, capacity=1, name="machine")
    layout.place(server, position=Point(1, 0), rotation=0, footprint={"width": 2}, **extra)
    return layout.prepare()


# --- versions -----------------------------------------------------------------------------------------------------

PINNED_1_0 = {
    "valid-lanes": (
        "f6d8962a8d108b7a43c577da2f12de014201c0b20faaff49b4c63e211867da86",
        "fe209b541a0faa0b030ccb1929f7425508ffaa2c7369bdfaf3907a6fe2a518c0",
        "46ab00dcd4ad9bb02d6a72bf3208247d6a54ab5b3dc0cccc36219422c884ec77",
    ),
    "valid-explicit": (
        "477265409fe069000079399223e25adcc49e8fc650a3fddad9623c46467e3f9a",
        "5ec6b04a724bf91d1181c9b0a842950928071e35c9952d75d20c7357467e9710",
        "0484016f178fcf1f2c3d2bf492267b3571b708fe45d6ebfc8554ea6eb2480064",
    ),
    "valid-overrides": (
        "79fe2887bd52db4942ccae587f9bc6529431472377630ce14a605ff2ea87e5b2",
        "0388e444e73c86ea4d433ea9456af88c8eb5309a5161ae76673998de7f619b81",
        "8f852b27598399765c7baed9def5c3480cf1c0bcc4c8799c27839c9a75786575",
    ),
    "valid-grid": (
        "d032e705bbe2f34df1801e874e1a48279c86076d1be8f0648037004324a1b854",
        "ae3ce8d072648fef2040391b4e9323cb808faa41df9da6671004473b806d7760",
        "3273cba68ed60c9ad4d7e72a3251118160214ca92a718d4a850a77636425a920",
    ),
}


@pytest.mark.parametrize("name", sorted(PINNED_1_0))
def test_1_0_file_loads_unchanged_and_keeps_its_hashes(name, tmp_path):
    path = FIXTURES / f"{name}.json"
    result = Layout.from_dict(Environment(), json.loads(path.read_text())).prepare()
    assert result.bundle.schema_version == "1.0"
    assert (result.physical_hash, result.full_hash, result.layers_hash) == PINNED_1_0[name]
    assert set(result.bundle["network"] or ()) <= {
        "nodes", "edges", "clearance", "obstacles", "lineage", "adjacency",
        "weak_components", "strong_components", "graph",
    }  # fmt: skip
    for arc in (result.bundle["network"] or {"graph": {"arcs": []}})["graph"]["arcs"]:
        assert set(arc) == {"source", "target", "speed_limit"}
    assert "zones" not in (result.bundle["network"] or {"graph": {}})["graph"]
    result.bundle.check()
    # Saving a loaded 1.0 file writes the same bytes again, still 1.0.
    saved = tmp_path / "saved.json"
    save_layout(saved, load_layout(path))
    again = tmp_path / "again.json"
    save_layout(again, load_layout(saved))
    assert saved.read_bytes() == again.read_bytes()
    assert json.loads(saved.read_text())["schema_version"] == "1.0"


FIELD_FIXTURES = ("valid-traffic-lanes", "valid-traffic-explicit", "valid-traffic-lattice")


@pytest.mark.parametrize("name", FIELD_FIXTURES)
def test_1_1_file_round_trips_through_save_and_load(name, tmp_path):
    original = json.loads((FIXTURES / f"{name}.json").read_text())
    layer = load_layout(FIXTURES / f"{name}.json")
    path = tmp_path / "layout.json"
    save_layout(path, layer)
    saved = json.loads(path.read_text())
    assert saved == original and saved["schema_version"] == "1.1"
    assert load_layout(path).to_dict() == original


def test_each_new_field_round_trips_alone(tmp_path):
    point = [0, 0, 0]
    cases = {
        "lane passing": {"network": {"type": "lanes", "lanes": {"L": {"passing": True}}}},
        "arc passing": {"network": {"type": "explicit", "arcs": [{"source": "a", "target": "b", "passing": False}]}},
        "lattice passing": {"network": {"type": "lattice", "passing": True}},
        "override passing": {
            "overrides": [{"id": "o", "target": {"kind": "row", "j": 0}, "properties": {"passing": {"$unset": True}}}]
        },
        "connector passing": {
            "placements": {
                "m": {
                    "kind": "server",
                    "ports": {"p": {"position": point, "approach": [1, 0, 0], "connector": {"passing": True}}},
                }
            }
        },
        "block_length": {"network": {"type": "explicit", "block_length": 2.5}},
        "zones": {"network": {"type": "explicit", "zones": {"z": {"members": [{"kind": "row", "j": 0}]}}}},
    }
    for label, patch in cases.items():
        path = tmp_path / "one.json"
        save_layout(path, FileLayer({"schema_version": "1.1", **patch}))
        assert json.loads(path.read_text())["schema_version"] == "1.1", label
        assert load_layout(path).to_dict() == {"schema_version": "1.1", **patch}, label


def test_save_writes_1_1_only_when_a_1_1_field_is_present(tmp_path):
    path = tmp_path / "layout.json"
    save_layout(path, FileLayer({"schema_version": "1.1", "network": {"type": "explicit"}}))
    assert json.loads(path.read_text())["schema_version"] == "1.0"
    save_layout(path, FileLayer({"schema_version": "1.0", "network": {"type": "explicit"}}))
    assert json.loads(path.read_text())["schema_version"] == "1.0"
    save_layout(path, FileLayer({"schema_version": "1.1", "network": {"type": "explicit", "block_length": 1}}))
    assert json.loads(path.read_text())["schema_version"] == "1.1"
    layout = Layout.from_dict(Environment(), {"schema_version": "1.1", "network": {"type": "explicit", "zones": {}}})
    layout.save(path)
    assert json.loads(path.read_text())["schema_version"] == "1.1"


USES_1_1 = {
    "lane passing": ({"network": {"type": "lanes", "lanes": {"L": {"passing": True}}}}, "passing"),
    "arc passing": (
        {"network": {"type": "explicit", "arcs": [{"source": "a", "target": "b", "passing": True}]}},
        "passing",
    ),
    "lattice passing": ({"network": {"type": "lattice", "passing": True}}, "passing"),
    "unset passing": ({"network": {"type": "lattice", "passing": {"$unset": True}}}, "passing"),
    "override passing": (
        {"overrides": [{"id": "o", "target": {"kind": "row", "j": 0}, "properties": {"passing": True}}]},
        "passing",
    ),
    "connector passing": (
        {
            "placements": {
                "m": {
                    "kind": "server",
                    "ports": {"p": {"position": [0, 0, 0], "approach": [1, 0, 0], "connector": {"passing": True}}},
                }
            }
        },
        "passing",
    ),
    "arc length": (
        {"network": {"type": "explicit", "arcs": [{"source": "a", "target": "b", "length": 2.5}]}},
        "length",
    ),
    "block_length": ({"network": {"type": "explicit", "block_length": 2}}, "block_length"),
    "zones": ({"network": {"type": "explicit", "zones": {}}}, "zones"),
}  # fmt: skip


@pytest.mark.parametrize("label", sorted(USES_1_1))
def test_1_0_file_with_a_1_1_field_fails_at_load_naming_the_field(label, tmp_path):
    patch, field = USES_1_1[label]
    path = tmp_path / "layout.json"
    path.write_text(json.dumps({"schema_version": "1.0", **patch}))
    with pytest.raises(LayoutError, match=field) as caught:
        load_layout(path)
    assert "1.0" in str(caught.value) and "1.1" in str(caught.value)
    assert caught.value.diagnostics[0].code == "schema-error"
    # The shared JSON Schema admits both versions; the rule is a Python load check.
    assert Draft202012Validator(schema()).is_valid({"schema_version": "1.0", **patch})
    assert Draft202012Validator(schema()).is_valid({"schema_version": "1.1", **patch})
    parse_patch({"schema_version": "1.1", **patch})


def test_unknown_versions_stay_rejected():
    with pytest.raises(LayoutError, match="schema_version"):
        parse_patch({"schema_version": "1.2"})
    assert not Draft202012Validator(schema()).is_valid({"schema_version": "1.2"})


def test_code_declarations_use_1_1_by_themselves():
    env = Environment()
    layout = Layout(env)
    layout.set_network(Lanes({"L": {"vertices": [vertex("a", 0), vertex("b", 4)], "passing": True}}, block_length=2))
    assert layout.to_dict()["schema_version"] == "1.0"  # the file layer is untouched by code
    result = layout.prepare()
    assert result.bundle.schema_version == "1.1"
    assert Layout(Environment()).prepare().bundle.schema_version == "1.0"


def test_code_api_carries_the_new_fields():
    env = Environment()
    layout = Layout(env)
    layout.set_grid(Grid(1, 1))
    layout.set_network(
        Lattice(
            (0, 0, 2, 0),
            passing=True,
            block_length=0.5,
            zones={"z": {"members": [{"kind": "row", "j": 0}], "capacity": 2}},
        )
    )
    layout.override(NetworkOverride("slow", {"kind": "rectangle", "bounds": [0, 0, 1, 0]}, {"passing": False}))
    result = layout.prepare()
    assert result.bundle.schema_version == "1.1"
    assert [zone.capacity for zone in result.network.zones] == [2]
    assert len(result.network.zones[0].members) == 3 + 2 + 4  # the row's nodes, its generated nodes and pieces
    assert len(result.network.graph.nodes) == 3 + 2  # 0.5 m blocks cut each 1 m lattice edge in two
    network = layout.to_dict()
    assert "network" not in network  # code declarations stay out of the file layer
    graph = LayoutGraph(
        [a := Node("a", 0, 0), b := Node("b", 4, 0)], [Arc(a, b, passing=True), Arc(b, a, passing=True)]
    )
    plain = LayoutGraph([c := Node("c", 0, 0), d := Node("d", 4, 0)], [Arc(c, d)])
    assert [arc.passing for arc in Explicit(graph, block_length=2).arcs] == [True, True]
    assert [arc.passing for arc in Explicit(plain).arcs] == [None, None]  # an ordinary arc adds no 1.1 field
    port = Port(Point(0, 1), Point(0, 1), Connector(passing=True))
    layout = Layout(Environment())
    server = Server(env=layout.env, capacity=1, name="m")
    layout.place(server, position=Point(0, 0), ports={"out": port})
    assert layout._coordinator.values.explicit_data["placements"]["m"]["ports"]["out"]["connector"]["passing"] is True
    assert layout._coordinator._code[-1][1]["schema_version"] == "1.1"


@pytest.mark.parametrize(
    "build",
    [
        lambda: Connector(passing=1),
        lambda: Lattice((0, 0, 1, 1), passing="yes"),
        lambda: Lattice((0, 0, 1, 1), block_length=0),
        lambda: Lanes({}, block_length=True),
        lambda: Lanes({}, zones={"z": {"members": [{"kind": "row", "j": 0}], "capacity": 0}}),
        lambda: Lanes({}, zones={"z": {"members": {"$unset": True}}}),
        lambda: Lanes({}, zones={"z": {"capacity": 2}}),
    ],
)
def test_code_api_rejects_invalid_values(build):
    with pytest.raises(LayoutError):
        build()


# --- passing ------------------------------------------------------------------------------------------------------


def test_passing_on_lanes_reaches_both_arcs():
    result = resolve(lane_patch(lane={"passing": True}))
    a, b = lane_vertex("a"), lane_vertex("b")
    assert arc_of(result, a, b).passing is True and arc_of(result, b, a).passing is True
    plain = resolve(lane_patch())
    assert arc_of(plain, a, b).passing is False


def test_passing_on_explicit_arcs_follows_the_direction():
    result = resolve(explicit_patch([{"source": "A", "target": "B", "direction": "both", "passing": True}]))
    a, b = explicit_node("A"), explicit_node("B")
    assert arc_of(result, a, b).passing and arc_of(result, b, a).passing
    one = resolve(explicit_patch([{"source": "A", "target": "B", "passing": True}]))
    assert arc_of(one, a, b).passing and arc_of(one, b, a) is None


def test_passing_must_agree_between_the_two_directions_of_a_pair():
    patch = explicit_patch(
        [{"source": "A", "target": "B", "passing": True}, {"source": "B", "target": "A", "passing": False}]
    )
    with pytest.raises(LayoutError, match="passing"):
        resolve(patch)


def test_passing_on_connectors():
    values = resolve_layers(
        (
            (
                "declare",
                {
                    "schema_version": "1.1",
                    **explicit_patch(
                        [{"source": "A", "target": "B", "direction": "both"}], [("A", 0, 0), ("B", 10, 0)]
                    ),
                    "placements": {
                        "m": {
                            "kind": "server",
                            "position": [0, 3, 0],
                            "rotation": 0,
                            "footprint": {"width": 2, "depth": 2},
                            "ports": {
                                "out": {
                                    "position": [0, -1, 0],
                                    "approach": [0, -1, 0],
                                    "connector": {"passing": True, "target": {"kind": "explicit", "id": "A"}},
                                }
                            },
                        }
                    },
                },
            ),
        ),
        FileLayer({"schema_version": "1.0"}),
        targets={"m": "server"},
        final=True,
    )
    result = resolve_network(values, DependencyClosure(), limits=LayoutLimits())
    connector = [e for a, e in result.definitions.edges.items() if a.to_dict()["kind"] == "connector"]
    assert connector and all(edge.passing for edge in connector)
    for edge in connector:
        assert result.graph.arc_between(result.node(edge.source), result.node(edge.target)).passing
    assert not arc_of(result, explicit_node("A"), explicit_node("B")).passing


def test_passing_on_the_lattice_network_and_edge_overrides_with_unset():
    patch = {
        "grid": {"dx": 1, "dy": 1},
        "network": {"type": "lattice", "bounds": [0, 0, 2, 0], "passing": True},
    }
    result = resolve(patch)
    n = [result.node(Address({"kind": "lattice", "i": i, "j": 0})) for i in range(3)]
    assert result.graph.arc_between(n[0], n[1]).passing is True
    assert result.graph.arc_between(n[2], n[1]).passing is True
    patch["overrides"] = [
        {"id": "off", "target": {"kind": "rectangle", "bounds": [0, 0, 2, 0]}, "properties": {"passing": False}},
        {"id": "back", "target": {"kind": "rectangle", "bounds": [0, 0, 1, 0]}, "properties": {"passing": True}},
        {
            "id": "clear",
            "target": {"kind": "rectangle", "bounds": [1, 0, 2, 0]},
            "properties": {"passing": {"$unset": True}},
        },
    ]
    result = resolve(patch)
    n = [result.node(Address({"kind": "lattice", "i": i, "j": 0})) for i in range(3)]
    assert result.graph.arc_between(n[0], n[1]).passing is True
    assert result.graph.arc_between(n[1], n[2]).passing is False  # unset clears the override and the network value


def pass_overrides(*directions: tuple[str, str]) -> list[dict[str, Any]]:
    return [
        {"id": f"p{i}", "target": {"kind": "explicit_arc", "source": a, "target": b}, "properties": {"passing": True}}
        for i, (a, b) in enumerate(directions)
    ]


def test_override_of_exact_edge_addresses_sets_passing():
    patch = explicit_patch([{"source": "A", "target": "B", "direction": "both"}])
    patch["overrides"] = pass_overrides(("A", "B"), ("B", "A"))
    result = resolve(patch)
    assert arc_of(result, explicit_node("A"), explicit_node("B")).passing is True
    assert arc_of(result, explicit_node("B"), explicit_node("A")).passing is True
    patch["overrides"] = pass_overrides(("A", "B"))  # one direction only: the pair disagrees
    with pytest.raises(LayoutError, match="passing"):
        resolve(patch)


def test_passing_changes_the_physical_hash():
    plain = prepared(lane_patch())
    passing = prepared(lane_patch(lane={"passing": True}))
    assert plain.physical_hash != passing.physical_hash
    assert plain.full_hash != passing.full_hash
    assert plain.bundle.schema_version == "1.0" and passing.bundle.schema_version == "1.1"
    assert {a["passing"] for a in passing.bundle["network"]["graph"]["arcs"]} == {True}
    passing.bundle.check()
    plain_with_flag = prepared(lane_patch(lane={"passing": False}))
    assert plain_with_flag.physical_hash != plain.physical_hash  # authored input, even at its default


def test_passing_in_a_file_override_changes_the_physical_hash():
    base = explicit_patch([{"source": "A", "target": "B", "direction": "both"}])
    a = prepared(base)
    b = prepared({**base, "overrides": pass_overrides(("A", "B"), ("B", "A"))})
    c = prepared(
        {
            **base,
            "overrides": pass_overrides(("A", "B"), ("B", "A"))[:1]
            + [
                {
                    "id": "q",
                    "target": {"kind": "explicit_arc", "source": "B", "target": "A"},
                    "properties": {"passing": True},
                }
            ],
        }
    )
    assert len({a.physical_hash, b.physical_hash}) == 2
    assert c.physical_hash != a.physical_hash  # the override ids are authored input as well
    assert {x["passing"] for x in b.bundle["network"]["graph"]["arcs"]} == {True}
    assert all("passing" not in x for x in a.bundle["network"]["graph"]["arcs"])  # a 1.0 bundle has no passing


# --- block_length -------------------------------------------------------------------------------------------------


def lane_child(lane: str, a: str, b: str) -> Address:
    return Address(
        {
            "kind": "lane_child",
            "segment": {"kind": "lane_segment", "lane": lane, "source": a, "target": b},
            "source": lane_vertex(a, lane).to_dict(),
            "target": lane_vertex(b, lane).to_dict(),
        }
    )


def block_node(edge: Address, index: int) -> Address:
    return Address({"kind": "block_node", "edge": edge.to_dict(), "index": index})


def test_a_lane_segment_splits_into_equal_pieces_with_the_default_capacity():
    result = resolve(lane_patch(block_length=4, capacity=3))
    a, b = lane_vertex("a"), lane_vertex("b")
    parent = lane_child("L", "a", "b")
    first, second = block_node(parent, 1), block_node(parent, 2)
    assert len(result.graph.nodes) == 4 and sum(len(result.graph.neighbors(n)) for n in result.graph.nodes) == 6
    chain = [result.node(x) for x in (a, first, second, b)]
    assert [round(n.x, 9) for n in chain] == [0.0, round(10 / 3, 9), round(20 / 3, 9), 10.0]
    assert chain[1].id == f"{chain[0].id}~{chain[3].id}#1" and chain[2].id == f"{chain[0].id}~{chain[3].id}#2"
    for x, y in zip(chain, chain[1:]):
        forward, backward = result.graph.arc_between(x, y), result.graph.arc_between(y, x)
        assert forward is not None and backward is not None
        assert forward.length == pytest.approx(10 / 3)
    assert result.graph.arc_between(chain[0], chain[2]) is None
    assert [result.capacities[n] for n in chain] == [3, 3, 3, 3]
    # Generated nodes are canonical nodes of the layout, and the parent edge is gone.
    assert result.aliases[first] == first
    assert parent not in result.definitions.edges
    assert result.definitions.lineage[parent]


def test_generated_nodes_without_a_network_capacity_have_none():
    result = resolve(lane_patch(block_length=4))
    assert set(result.capacities.values()) == {None}


def test_splitting_is_deterministic_and_independent_of_declaration_order():
    first = resolve(lane_patch(block_length=3))
    second = resolve(lane_patch(block_length=3))
    assert [n.id for n in first.graph.nodes] == [n.id for n in second.graph.nodes]
    assert [a for a in first.definitions.nodes] == [a for a in second.definitions.nodes]


def test_overrides_on_the_parent_apply_to_every_piece():
    patch = lane_patch(block_length=4, lane={"passing": True})
    patch["overrides"] = [
        {"id": "slow", "target": {"kind": "lane_segment", "lane": "L", "source": "a", "target": "b"},
         "properties": {"speed_limit": 1.5, "direction": "forward"}},
    ]  # fmt: skip
    result = resolve(patch)
    a, b = lane_vertex("a"), lane_vertex("b")
    parent = lane_child("L", "a", "b")
    chain = [a, block_node(parent, 1), block_node(parent, 2), b]
    for x, y in zip(chain, chain[1:]):
        arc = arc_of(result, x, y)
        assert arc.speed_limit == 1.5 and arc.passing is True
        assert arc_of(result, y, x) is None
    assert len(result.graph.nodes) == 4


def test_a_disabled_edge_is_not_split_and_an_override_can_disable_the_parent():
    patch = lane_patch(block_length=4, lane={"enabled": False})
    result = resolve(patch)
    assert not result.graph.nodes or len(result.graph.nodes) <= 2
    assert not any(a.to_dict()["kind"] == "block_node" for a in result.definitions.nodes)
    patch = lane_patch(block_length=4)
    patch["overrides"] = [{"id": "off", "target": {"kind": "lane", "id": "L"}, "properties": {"enabled": False}}]
    assert not any(a.to_dict()["kind"] == "block_node" for a in resolve(patch).definitions.nodes)


@pytest.mark.parametrize(
    "target",
    [
        {"kind": "block_node", "edge": {"kind": "explicit_arc", "source": "A", "target": "B"}, "index": 1},
        {"kind": "block_edge", "edge": {"kind": "explicit_arc", "source": "A", "target": "B"}, "index": 1},
    ],
)
def test_an_override_cannot_target_a_piece_or_a_generated_node(target):
    patch = explicit_patch([{"source": "A", "target": "B", "direction": "both"}], block_length=4)
    patch["overrides"] = [{"id": "bad", "target": target, "properties": {"enabled": False}}]
    with pytest.raises(LayoutError, match="block_") as caught:
        resolve(patch)
    assert "generated" in str(caught.value)
    assert caught.value.diagnostics[0].path == ("overrides", "0", "target")


def test_generated_addresses_are_not_valid_connector_or_endpoint_targets():
    from simulatte.layout.schema import validate_definition

    address = {"kind": "block_node", "edge": {"kind": "explicit_arc", "source": "A", "target": "B"}, "index": 2}
    validate_definition(address, "address")
    for definition in ("nodeAddress",):
        with pytest.raises(LayoutError):
            validate_definition(address, definition)
    with pytest.raises(LayoutError):
        Layout(Environment()).node_at(Address(address))


def test_explicit_pairs_share_their_generated_nodes_and_match_subdivide():
    arcs = [{"source": "A", "target": "B", "direction": "both", "passing": True}]
    result = resolve(explicit_patch(arcs, block_length=4))
    ids = [n.id for n in result.graph.nodes]
    assert ids == ["A", "B", "A~B#1", "A~B#2"]
    a, b = Node("A", 0, 0), Node("B", 10, 0)
    twin = LayoutGraph([a, b], [Arc(a, b, passing=True), Arc(b, a, passing=True)]).subdivide(4)
    expected = {n.id: (n.x, n.y) for n in twin.nodes}
    assert {n.id: (n.x, n.y) for n in result.graph.nodes} == expected
    for node in twin.nodes:
        mine = next(n for n in result.graph.nodes if n.id == node.id)
        assert sorted(x.id for x in result.graph.neighbors(mine)) == sorted(x.id for x in twin.neighbors(node))


def test_separate_directed_edges_with_distinct_attributes_share_nodes_but_keep_theirs():
    arcs = [
        {"source": "A", "target": "B", "speed_limit": 1},
        {"source": "B", "target": "A", "speed_limit": 2},
    ]
    result = resolve(explicit_patch(arcs, block_length=5))
    nodes = {n.id: n for n in result.graph.nodes}
    assert sorted(nodes) == ["A", "A~B#1", "B"]
    assert result.graph.arc_between(nodes["A"], nodes["A~B#1"]).speed_limit == 1
    assert result.graph.arc_between(nodes["B"], nodes["A~B#1"]).speed_limit == 2


@pytest.mark.parametrize(
    ("length", "block", "pieces"),
    [(10, 10, 1), (10, 3, 4), (0.30000000000000004, 0.1, 3), (0.6000000000000001, 0.2, 3), (2.0, 0.5, 4), (9, 10, 1)],
)
def test_piece_count_is_the_ceiling_without_float_noise(length, block, pieces):
    result = resolve(
        explicit_patch([{"source": "A", "target": "B"}], [("A", 0, 0), ("B", length, 0)], block_length=block)
    )
    assert len(result.graph.nodes) == pieces + 1


PITCH = [(f"P{i}", 3.3 * i, 0.0) for i in range(8)]
"""Eight nodes 3.3 m apart along a row: in floats the chords are 3.299999999999997 to 3.3000000000000007."""


def pitch_graph() -> LayoutGraph:
    nodes = [Node(name, x, y) for name, x, y in PITCH]
    return LayoutGraph(nodes, [Arc(a, b) for a, b in zip(nodes, nodes[1:])])


def pitch_patch(block_length: float) -> dict[str, Any]:
    arcs = [{"source": f"P{i}", "target": f"P{i + 1}", "direction": "both"} for i in range(len(PITCH) - 1)]
    return explicit_patch(arcs, PITCH, block_length=block_length)


def arc_lengths(graph: LayoutGraph) -> dict[tuple[str, str], float]:
    return {(a.id, b.id): graph.length(a, b) for a in graph.nodes for b in graph.neighbors(a)}


@pytest.mark.parametrize(("block_length", "pieces"), [(3.3, 1), (1.1, 3)])
def test_subdivide_and_block_length_build_the_same_network_on_a_3_3_pitch(block_length, pieces):
    """spec §6.1, §12, §17: one rule for the number of pieces and one for their lengths. With 3.3 neither splits an arc
    whose chord float noise makes 3.3000000000000007 (a bare ceiling made two pieces of it); with 1.1 every arc becomes
    three pieces of the parent's length over three, to the float, in both twins (before, the layout's pieces had the
    chords of their generated points)."""
    twin = pitch_graph().subdivide(block_length)
    layout = resolve(pitch_patch(block_length)).graph
    assert len(twin.nodes) == len(PITCH) + (pieces - 1) * (len(PITCH) - 1)
    assert {n.id: (n.x, n.y) for n in layout.nodes} == {n.id: (n.x, n.y) for n in twin.nodes}
    assert arc_lengths(layout) == arc_lengths(twin)
    parents = pitch_graph()
    for a, b in zip(parents.nodes, parents.nodes[1:]):
        chain = [a.id, *(f"{a.id}~{b.id}#{k}" for k in range(1, pieces)), b.id]
        assert [twin.length(twin_node(twin, u), twin_node(twin, v)) for u, v in zip(chain, chain[1:])] == [
            parents.length(a, b) / pieces
        ] * pieces


@pytest.mark.parametrize("offset", [1e6, 5e6])
@pytest.mark.parametrize("block_length", [1.0, 0.5, 0.1])
def test_subdivide_and_block_length_build_the_same_network_at_georeferenced_coordinates(offset, block_length):
    """Far from the origin the generated points are rounded to a spacing larger than the check's relative 1e-9, so a
    piece length could fall under the chord of its points and the build failed; the twins still make the same network
    to the float, from the same piece counts and lengths as near the origin."""
    rnd = random.Random(1)
    for _ in range(40):
        length, angle = rnd.uniform(5.0, 60.0), rnd.uniform(0.0, math.pi)
        points = [("A", offset, offset), ("B", offset + length * math.cos(angle), offset + length * math.sin(angle))]
        layout = resolve(
            explicit_patch([{"source": "A", "target": "B", "direction": "both"}], points, block_length=block_length)
        ).graph
        a, b = (Node(name, x, y) for name, x, y in points)
        twin = LayoutGraph([a, b], [Arc(a, b)]).subdivide(block_length)
        assert {n.id: (n.x, n.y) for n in layout.nodes} == {n.id: (n.x, n.y) for n in twin.nodes}
        assert arc_lengths(layout) == arc_lengths(twin)


def twin_node(graph: LayoutGraph, name: str) -> Node:
    return next(node for node in graph.nodes if node.id == name)


def test_a_mission_runs_the_same_on_the_subdivided_graph_and_on_its_layout_twin():
    """spec §17 (Layout): two AGVs 2 m long carry orders both ways around a one-way ring of two rows on the 3.3 m
    pitch, split by 1.1, under ``ReservationController``; the twins give the same initial state and the same event
    stream."""
    ring = [*PITCH, *((f"Q{i}", x, 3.3) for i, (_, x, _) in enumerate(PITCH))]
    links = [(f"P{i}", f"P{i + 1}") for i in range(7)] + [(f"Q{i + 1}", f"Q{i}") for i in range(7)]
    links += [("P7", "Q7"), ("Q0", "P0")]
    from simulatte.intralogistics.fleet import FleetCoordinator
    from simulatte.intralogistics.order import OrderStatus
    from simulatte.intralogistics.sku import SKU
    from simulatte.intralogistics.warehouse import Warehouse

    def run(layout: bool) -> tuple[list[str], list[OrderStatus]]:
        env = Environment()
        if layout:
            arcs = [{"source": a, "target": b, "direction": "forward"} for a, b in links]
            built = Layout.from_dict(env, {"schema_version": "1.1", **explicit_patch(arcs, ring, block_length=1.1)})
            handle, at = built.graph(), built.node
        else:
            by_id = {name: Node(name, x, y) for name, x, y in ring}
            plain = LayoutGraph(by_id.values(), [Arc(by_id[a], by_id[b], bidirectional=False) for a, b in links])
            world = declare(env, plain.subdivide(1.1))  # the subdivided graph, declared as it is
            handle, at = world.graph, world.ref
        sku = SKU("stock", 1, 0.1)
        sites = [
            Warehouse(
                env=env,
                name=name,
                input_bays=[at(node)],
                output_bays=[at(node)],
                n_slots=2,
                products=[sku],
                initial_inventory={sku: 50},
                pick_time=1.0,
                put_time=1.0,
            )
            for name, node in (("west", "P0"), ("east", "P7"))
        ]
        kind = AGVType(
            name="t",
            speed_profile=TrapezoidalProfile(max_speed=1.5, acceleration=0.8, deceleration=0.8),
            battery_capacity=1e9,
            weight_capacity=10.0,
            volume_capacity=1.0,
            length=2.0,
        )
        fleet = FleetCoordinator(
            env=env,
            graph=handle,
            fleet=[
                AGV(env=env, agv_type=kind, agv_id=name, initial_node=at(node))
                for name, node in (("a", "P1"), ("b", "P6"))
            ],
            warehouses=sites,
            charging_stations=[],
            traffic_controller=ReservationController(env=env, graph=handle),
            default_metrics=False,
        )
        orders = [
            fleet.create_order(sku=sku, quantity=1, origin=sites[k % 2], destination=sites[1 - k % 2]) for k in range(6)
        ]
        for order in orders:
            fleet.submit(order)
        env.activate()
        events: list[Any] = []
        env.bus.subscribe(events.append, "*")
        env.run()
        initial = {entity: dict(state) for entity, state in sorted(env.initial_state.items())}
        return [repr(initial), *map(repr, events)], [order.status for order in orders]

    explicit, explicit_orders = run(layout=False)
    twin, twin_orders = run(layout=True)
    assert explicit_orders == twin_orders == [OrderStatus.COMPLETED] * 6
    assert len(explicit) > 100
    assert explicit == twin


def test_limits_apply_after_splitting():
    patch = lane_patch(block_length=4)
    resolve(patch, limits=LayoutLimits(max_nodes=4, max_directed_arcs=6))
    with pytest.raises(LayoutError, match="max_nodes"):
        resolve(patch, limits=LayoutLimits(max_nodes=3))
    with pytest.raises(LayoutError, match="max_directed_arcs"):
        resolve(patch, limits=LayoutLimits(max_directed_arcs=5))
    with pytest.raises(LayoutError, match="max_nodes"):
        resolve(lane_patch(block_length=1e-9), limits=LayoutLimits())


def test_presentation_only_edits_keep_the_physical_hash():
    patch = lane_patch(block_length=4)
    base = prepared(patch)
    moved = prepared(patch, visual={"color": "red"})
    placed = prepared({**patch, "placements": {"machine": {"kind": "server", "visual": {"color": "blue"}}}})
    assert base.physical_hash == moved.physical_hash == placed.physical_hash
    from simulatte.layout.bundle import reresolve_presentation

    outcome = reresolve_presentation(
        base.bundle,
        {**patch, "schema_version": "1.1", "placements": {"machine": {"kind": "server", "visual": {"color": "red"}}}},
    )
    assert not outcome.requires_rerun
    assert outcome.bundle is not None and outcome.bundle.physical_hash == base.physical_hash
    outcome.bundle.check()
    assert prepared(lane_patch(block_length=5)).physical_hash != base.physical_hash


@pytest.mark.parametrize("block_length", [4, 1])
def test_ports_and_bays_keep_their_nodes(block_length):
    patch = {
        **explicit_patch([{"source": "A", "target": "B", "direction": "both"}], block_length=block_length),
        "placements": {
            "m": {
                "kind": "server",
                "position": [10, 4, 0],
                "rotation": 0,
                "footprint": {"width": 2, "depth": 2},
                "ports": {
                    "out": {
                        "position": [0, -1, 0],
                        "approach": [0, -1, 0],
                        "connector": {"target": {"kind": "explicit", "id": "B"}},
                    }
                },
            }
        },
    }
    env = Environment()
    layout = Layout.from_dict(env, {"schema_version": "1.1", **patch})
    Server(env=env, capacity=1, name="m")
    network = layout.prepare().network
    port = network.node(Address({"kind": "port", "entity": "m", "port": "out"}))
    approach = network.node(Address({"kind": "approach", "entity": "m", "port": "out"}))
    b = network.node(explicit_node("B"))
    assert b.id == "B" and port.id.startswith("layout:") and approach not in (port, b)
    assert network.graph.arc_between(port, approach) is not None
    # The connector's own edges are split like any other (1 m blocks cut the 2.99 m outer edge); the port, the
    # approach and B are the nodes they were.
    ids = [n.id for n in network.graph.nodes]
    expected = {4: (2, 0), 1: (9, 2)}[block_length]
    assert sum(i.startswith("A~B#") for i in ids) == expected[0]
    assert sum(i.startswith("B~layout:") for i in ids) == expected[1]
    assert len(ids) == 6 + (expected[0] - 2) + expected[1]


def test_a_warehouse_bay_on_a_split_edge_keeps_its_node():
    from simulatte.intralogistics.sku import SKU
    from simulatte.intralogistics.warehouse import Warehouse

    sku = SKU("stock", 1, 1)
    env = Environment()
    layout = Layout.from_dict(
        env,
        {
            "schema_version": "1.1",
            **explicit_patch([{"source": "A", "target": "B", "direction": "both"}], block_length=4),
        },
    )
    house = Warehouse(
        env=env,
        name="house",
        input_bays=[layout.node("B")],
        output_bays=[layout.node("A"), layout.node("B")],
        n_slots=1,
        products=[sku],
        initial_inventory={sku: 1},
        pick_time=0,
        put_time=0,
    )
    network = layout.prepare().network
    assert [n.id for n in house.input_bays] == ["B"] and [n.id for n in house.output_bays] == ["A", "B"]
    assert house.input_bays[0] is network.node(explicit_node("B"))
    assert house.output_bays[0] is network.node(explicit_node("A"))
    assert sorted(n.id for n in network.graph.nodes) == ["A", "A~B#1", "A~B#2", "B"]


STALE = {"id": "o", "target": {"kind": "explicit", "id": "gone"}, "properties": {"speed_limit": 1}}


def test_re_resolution_requires_a_rerun_when_the_layers_change_the_bundle_version():
    from simulatte.layout.bundle import reresolve_presentation

    base = explicit_patch([{"source": "A", "target": "B", "direction": "both"}])
    stale_passing = {**STALE, "properties": {"passing": True}}
    old = prepared({**base, "overrides": [STALE]}, version="1.0")
    assert old.bundle.schema_version == "1.0"
    upgraded = {**base, "schema_version": "1.1", "overrides": [stale_passing]}
    outcome = reresolve_presentation(old.bundle, upgraded)
    assert outcome.requires_rerun and outcome.bundle is None
    assert outcome.original_physical_hash == old.physical_hash
    fresh = prepared({**base, "overrides": [stale_passing]}, version="1.1")
    assert fresh.bundle.schema_version == "1.1" and fresh.physical_hash != old.physical_hash
    # The reverse edit: a 1.1 bundle whose layers no longer use a 1.1 field.
    outcome = reresolve_presentation(fresh.bundle, {**base, "schema_version": "1.0", "overrides": [STALE]})
    assert outcome.requires_rerun and outcome.bundle is None
    # Presentation edits that keep the version still re-resolve.
    same = reresolve_presentation(
        fresh.bundle, {**base, "schema_version": "1.1", "overrides": [stale_passing], "placements": {}}
    )
    assert not same.requires_rerun and same.bundle is not None and same.bundle.schema_version == "1.1"


# --- zones --------------------------------------------------------------------------------------------------------


def members(zone: Zone) -> set[Any]:
    return {m.id if isinstance(m, Node) else (m[0].id, m[1].id) for m in zone.members}


def test_zones_resolve_addresses_and_selectors_to_nodes_and_node_pairs():
    patch = explicit_patch(
        [{"source": "A", "target": "B", "direction": "both"}, {"source": "B", "target": "C", "direction": "both"}],
        [("A", 0, 0), ("B", 10, 0), ("C", 20, 0)],
        zones={
            "node": {"members": [{"kind": "explicit", "id": "B"}]},
            "pair": {
                "capacity": 2,
                "hold_back": True,
                "members": [{"kind": "explicit_arc", "source": "B", "target": "A"}],
            },
            "box": {"members": [{"kind": "rectangle", "bounds": [5, -1, 25, 1]}]},
        },
    )
    result = resolve(patch)
    zones = {zone.id: zone for zone in result.zones}
    assert [zone.id for zone in result.zones] == ["box", "node", "pair"]
    assert members(zones["node"]) == {"B"}
    assert zones["node"].capacity == 1 and zones["node"].hold_back is False
    assert members(zones["pair"]) == {("A", "B")}
    assert zones["pair"].capacity == 2 and zones["pair"].hold_back is True
    assert members(zones["box"]) == {"B", "C", ("B", "C")}
    nodes = {n.id: n for n in result.graph.nodes}
    for zone in result.zones:
        for member in zone.members:
            if isinstance(member, tuple):
                assert member[0] is nodes[member[0].id] and member[0].id < member[1].id
            else:
                assert member is nodes[member.id]
    assert not resolve(explicit_patch([])).zones


def test_zone_members_follow_a_split_edge():
    patch = lane_patch(
        block_length=4,
        zones={
            "seg": {"members": [{"kind": "lane_segment", "lane": "L", "source": "a", "target": "b"}]},
            "lane": {"members": [{"kind": "lane", "id": "L"}]},
        },
    )
    result = resolve(patch)
    zones = {zone.id: zone for zone in result.zones}
    # A matched edge stands for its three pieces and the two generated nodes between them; its ends only when selected.
    assert len(zones["seg"].members) == 3 + 2
    assert sum(isinstance(m, tuple) for m in zones["seg"].members) == 3
    assert {m.id for m in zones["seg"].members if not isinstance(m, tuple)} == {
        n.id for n in result.graph.nodes if "~" in n.id
    }
    assert len(zones["lane"].members) == 2 + 2 + 3
    zones_after = resolve(lane_patch(block_length=100, zones=patch["network"]["zones"]))
    assert {z.id: len(z.members) for z in zones_after.zones} == {"lane": 3, "seg": 1}


def stretch(zone: Zone, graph: LayoutGraph) -> set[Any]:
    """The members of a zone without ``block_length`` expanded onto the split graph: the same nodes, and for each
    pair the pieces and the generated nodes strictly between its ends."""
    nodes = {n.id: n for n in graph.nodes}
    expected: set[Any] = set()
    for member in zone.members:
        if not isinstance(member, tuple):
            expected.add(member.id)
            continue
        a, b = (nodes[m.id] for m in member)
        on = [
            n
            for n in graph.nodes
            if abs((b.x - a.x) * (n.y - a.y) - (b.y - a.y) * (n.x - a.x)) < 1e-9
            and min(a.x, b.x) - 1e-9 <= n.x <= max(a.x, b.x) + 1e-9
            and min(a.y, b.y) - 1e-9 <= n.y <= max(a.y, b.y) + 1e-9
        ]
        chain = sorted(on, key=lambda n: math.hypot(n.x - a.x, n.y - a.y))
        assert chain[0] is a and chain[-1] is b
        expected.update(n.id for n in chain[1:-1])
        expected.update(tuple(sorted((x.id, y.id))) for x, y in zip(chain, chain[1:]))
    return expected


GRID = {"grid": {"dx": 1, "dy": 1}}
LATTICE = {"type": "lattice", "bounds": [0, 0, 2, 1]}
BENT = {"type": "lanes", "lanes": {"L": {"vertices": [vertex("a", 0), vertex("b", 10), vertex("c", 10, 7)]}}}
COVERS = {
    "row": (GRID, LATTICE, {"kind": "row", "j": 0}),
    "column": (GRID, LATTICE, {"kind": "column", "i": 1}),
    "rectangle": (GRID, LATTICE, {"kind": "rectangle", "bounds": [0, 0, 1, 1]}),
    "lane": ({}, BENT, {"kind": "lane", "id": "L"}),
    "lane_segment": ({}, BENT, {"kind": "lane_segment", "lane": "L", "source": "b", "target": "c"}),
    "explicit_arc": (
        {},
        explicit_patch([{"source": "A", "target": "B", "direction": "both"}], [("A", 0, 0), ("B", 7, 0)])["network"],
        {"kind": "explicit_arc", "source": "A", "target": "B"},
    ),
}


@pytest.mark.parametrize("kind", sorted(COVERS))
def test_a_zone_covers_the_same_stretch_with_and_without_block_length(kind):
    extra, network, member = COVERS[kind]

    def zone(**more: Any) -> Zone:
        patch = {**extra, "network": {**network, **more, "zones": {"z": {"members": [member]}}}}
        (found,) = resolve(patch).zones
        return found

    whole = zone()
    split = resolve({**extra, "network": {**network, "block_length": 0.4, "zones": {"z": {"members": [member]}}}})
    assert any(isinstance(m, tuple) for m in whole.members)
    assert members(split.zones[0]) == stretch(whole, split.graph)
    assert len(split.zones[0].members) > len(whole.members)


def test_a_row_zone_keeps_its_segments_when_the_network_is_split():
    plain = resolve({**GRID, "network": {**LATTICE, "zones": {"z": {"members": [{"kind": "row", "j": 0}]}}}})
    assert len(plain.zones[0].members) == 3 + 2
    split = resolve(
        {**GRID, "network": {**LATTICE, "block_length": 0.5, "zones": {"z": {"members": [{"kind": "row", "j": 0}]}}}}
    )
    (zone,) = split.zones
    assert sum(not isinstance(m, tuple) for m in zone.members) == 3 + 2  # the row's nodes and one per segment
    assert sum(isinstance(m, tuple) for m in zone.members) == 4  # two pieces per segment


def test_a_row_zone_holds_the_second_cart_with_and_without_block_length():
    from simulatte.intralogistics.agv import AGVState
    from simulatte.intralogistics.events import TrafficWaitStarted

    def run(block_length: float | None) -> tuple[str, bool]:
        network: dict[str, Any] = {
            "type": "lattice",
            "bounds": [0, 0, 20, 4],
            "zones": {"row": {"members": [{"kind": "row", "j": 0}]}},
        }
        if block_length is not None:
            network["block_length"] = block_length
        env = Environment(debug=True)
        layout = Layout.from_dict(env, {"schema_version": "1.1", "grid": {"dx": 10, "dy": 4}, "network": network})
        ReservationController(env=env, graph=layout.graph())
        at = lambda i, j: layout.node_at(Address({"kind": "lattice", "i": i, "j": j}))  # noqa: E731
        carts = {
            "c1": AGV(env=env, agv_type=vehicle_type(), agv_id="c1", initial_node=at(0, 0)),
            "c2": AGV(env=env, agv_type=vehicle_type(), agv_id="c2", initial_node=at(0, 1)),
        }
        fleet = StillFleet(
            env=env,
            graph=layout.graph(),
            fleet=list(carts.values()),
            warehouses=[],
            charging_stations=[],
            traffic_controller=env.layout._resource_owner,
            default_metrics=False,
        )
        result = layout.prepare()
        waits: list[Any] = []
        env.bus.subscribe(waits.append, (TrafficWaitStarted,))
        env.activate()

        def mission(agv: AGV, goal: Node):
            agv.transition_to(AGVState.TRAVELING_EMPTY)
            yield from fleet._executor.travel(agv, goal, loaded=False, purpose="travel")
            agv.transition_to(AGVState.IDLE)

        env.process(mission(carts["c1"], result.network.node(Address({"kind": "lattice", "i": 2, "j": 0}))))
        env.process(mission(carts["c2"], result.network.node(Address({"kind": "lattice", "i": 1, "j": 0}))))
        env.run(until=100)
        return carts["c2"].current_node.id, any(w.agv == "c2" for w in waits)

    for block_length in (None, 5):
        node, waited = run(block_length)
        # c1 ends on the zone's last node and keeps the zone, so c2 never enters it, with or without blocks.
        assert waited and node == run(None)[0]


def test_a_zone_member_that_matches_nothing_is_a_stale_diagnostic():
    patch = explicit_patch(
        [{"source": "A", "target": "B"}],
        zones={
            "z": {
                "members": [
                    {"kind": "explicit", "id": "A"},
                    {"kind": "explicit", "id": "gone"},
                    {"kind": "row", "j": 9},
                ]
            }
        },
    )
    result = resolve(patch)
    stale = [d for d in result.diagnostics if d.code == "stale-zone-member"]
    assert [(d.severity, d.path, d.target) for d in stale] == [
        ("warning", ("network", "zones", "z", "members", "1"), "z"),
        ("warning", ("network", "zones", "z", "members", "2"), "z"),
    ]
    assert members(result.zones[0]) == {"A"}


def test_zone_members_that_are_disabled_are_dropped_without_a_diagnostic():
    patch = explicit_patch(
        [{"source": "A", "target": "B", "enabled": False}],
        zones={
            "z": {"members": [{"kind": "explicit_arc", "source": "A", "target": "B"}, {"kind": "explicit", "id": "B"}]}
        },
    )
    patch["overrides"] = [{"id": "off", "target": {"kind": "explicit", "id": "B"}, "properties": {"enabled": False}}]
    result = resolve(patch)
    assert result.zones[0].members == () and not [d for d in result.diagnostics if d.code == "stale-zone-member"]


def test_zones_enter_the_physical_hash_and_the_bundle():
    zones = {"gate": {"capacity": 1, "members": [{"kind": "explicit", "id": "A"}]}}
    base = prepared(explicit_patch([{"source": "A", "target": "B"}]))
    with_zone = prepared(explicit_patch([{"source": "A", "target": "B"}], zones=zones))
    bigger = prepared(
        explicit_patch([{"source": "A", "target": "B"}], zones={"gate": {**zones["gate"], "capacity": 2}})
    )
    held = prepared(
        explicit_patch([{"source": "A", "target": "B"}], zones={"gate": {**zones["gate"], "hold_back": True}})
    )
    assert len({base.physical_hash, with_zone.physical_hash, bigger.physical_hash, held.physical_hash}) == 4
    bundle = with_zone.bundle
    assert bundle.schema_version == "1.1"
    assert bundle.to_dict()["network"]["graph"]["zones"] == [
        {"id": "gate", "capacity": 1, "hold_back": False, "members": ["A"]}
    ]
    assert bigger.bundle.to_dict()["network"]["graph"]["zones"][0]["capacity"] == 2
    assert held.bundle.to_dict()["network"]["graph"]["zones"][0]["hold_back"] is True
    restored = LayoutBundle.from_dict(bundle.to_dict())
    restored.check()
    assert restored.physical_hash == bundle.physical_hash
    both = prepared(
        explicit_patch(
            [{"source": "A", "target": "B", "direction": "both"}],
            zones={"gate": {"members": [{"kind": "explicit_arc", "source": "B", "target": "A"}]}},
        )
    )
    assert both.bundle.to_dict()["network"]["graph"]["zones"][0]["members"] == [["A", "B"]]


def test_the_bundle_carries_the_generated_nodes():
    result = prepared(explicit_patch([{"source": "A", "target": "B", "direction": "both"}], block_length=4))
    bundle = result.bundle
    ids = [n["id"] for n in bundle["network"]["nodes"]]
    assert {"A~B#1", "A~B#2"} <= set(ids)
    generated = [n for n in bundle["network"]["nodes"] if n["address"]["kind"] == "block_node"]
    assert {n["address"]["index"] for n in generated} == {1, 2}
    assert [n["id"] for n in bundle["network"]["graph"]["nodes"]] == ["A", "B", "A~B#1", "A~B#2"]
    assert len(bundle["network"]["graph"]["arcs"]) == 6
    LayoutBundle.from_dict(bundle.to_dict()).check()


@pytest.mark.parametrize(
    "name", ["invalid-zone-capacity", "invalid-zone-field", "invalid-block-length", "invalid-passing"]
)
def test_invalid_traffic_fields_are_schema_errors(name):
    value = json.loads((FIXTURES / f"{name}.json").read_text())
    assert not Draft202012Validator(schema()).is_valid(value)
    with pytest.raises(LayoutError) as caught:
        parse_patch(value)
    assert caught.value.diagnostics[0].code == "schema-error"


def test_a_zone_needs_members():
    # Patches are partial, so a zone without members is a valid layer; the resolved zone must have them.
    patch = {"schema_version": "1.1", **explicit_patch([], zones={"z": {"capacity": 2}})}
    parse_patch(patch)
    with pytest.raises(LayoutError, match="network/zones/z/members"):
        resolve(patch)
    patch["network"]["zones"]["z"] = {"members": []}
    with pytest.raises(LayoutError):
        parse_patch(patch)
    with pytest.raises(LayoutError, match="members"):
        Lanes({}, zones={"z": {"capacity": 2}})


def test_a_zone_can_be_unset_by_a_later_layer():
    code = {"schema_version": "1.1", **explicit_patch([], zones={"z": {"members": [{"kind": "explicit", "id": "A"}]}})}
    file = FileLayer({"schema_version": "1.1", "network": {"zones": {"z": {"$unset": True}}}})
    values = resolve_layers((("declare", code),), file)
    assert resolve_network(values, DependencyClosure(), limits=LayoutLimits()).zones == ()


# --- binding ------------------------------------------------------------------------------------------------------


def _line_with_zone(env, authored: tuple[Zone, ...] = ()):
    layout = Layout.from_dict(
        env,
        {
            "schema_version": "1.1",
            **explicit_patch(
                [{"source": "A", "target": "B", "direction": "both"}],
                zones={"gate": {"capacity": 2, "members": [{"kind": "explicit", "id": "B"}]}},
            ),
        },
    )
    # the controller's own zones (`authored`) only for the test of both kinds of zones, which Task 9 decides
    options = {"zones": authored} if authored else {}
    return layout, ReservationController(env=env, graph=layout.graph(), **options)


def test_the_controller_reads_the_layout_zones_at_bind():
    env = Environment()
    layout, controller = _line_with_zone(env)
    result = layout.prepare()
    assert controller._zones == result.network.zones
    env.activate()
    assert [(zone.id, zone.capacity) for zone in controller.network.zones] == [("gate", 2)]
    assert [entity for entity, state in env.initial_state.items() if state["$kind"] == "zone"] == ["gate"]


def test_authored_and_layout_zones_both_apply_and_a_repeated_id_is_an_error():
    env = Environment()
    layout, controller = _line_with_zone(env, (Zone("mine", ()),))  # an authored zone cannot name layout nodes
    layout.prepare()
    env.activate()
    assert [zone.id for zone in controller.network.zones] == ["mine", "gate"]
    env = Environment()
    layout, _ = _line_with_zone(env, (Zone("gate", ()),))
    with pytest.raises(ValueError, match="gate"):
        layout.prepare()


def test_a_corrupted_1_1_bundle_is_rejected():
    original = prepared(
        explicit_patch(
            [{"source": "A", "target": "B", "direction": "both"}],
            zones={"gate": {"members": [{"kind": "explicit_arc", "source": "A", "target": "B"}]}},
        )
    ).bundle.to_dict()
    assert original["schema_version"] == "1.1"

    def damaged(change):
        data = json.loads(json.dumps(original))
        change(data["network"])
        return data

    for change in (
        lambda network: network["edges"][0].__setitem__("passing", "yes"),
        lambda network: network["edges"][0].pop("passing"),
        lambda network: network["graph"]["arcs"][0].pop("passing"),
        lambda network: network["graph"].pop("zones"),
        lambda network: network["graph"]["zones"][0].__setitem__("hold_back", None),
        lambda network: network["graph"]["zones"][0].__setitem__("capacity", 0),
        lambda network: network["graph"]["zones"][0]["members"].append(["A", "B", "C"]),
        lambda network: network["graph"]["zones"][0]["members"].append([1, 2]),
    ):
        with pytest.raises(LayoutError):
            LayoutBundle.from_dict(damaged(change))
    # A 1.0 bundle has none of these fields.
    old = prepared(explicit_patch([{"source": "A", "target": "B"}]), version="1.0").bundle.to_dict()
    assert old["schema_version"] == "1.0"
    old["network"]["graph"]["zones"] = []
    with pytest.raises(LayoutError):
        LayoutBundle.from_dict(old)


# --- the layout twin ----------------------------------------------------------------------------------------------


def vehicle_type() -> AGVType:
    return AGVType(
        "cart",
        TrapezoidalProfile(1.5, 0.8, 0.8, battery_degradation_fn=lambda level: 1.0),
        1000,
        100,
        10,
        length=2.0,
    )


POINTS = (("P", 0, 0), ("M", 10, 0), ("Q", 20, 0), ("R", 30, 0))
LINKS = (("P", "M"), ("M", "Q"), ("Q", "R"))


def run_twin(env: Environment, handle: Any, start: Any, nodes: Any, controller: Any) -> list[Any]:
    """Two carts on the line: a1 from P to Q and a2 from M to R, leaving at 10, so a1 comes to rest before M and waits
    for it, and both pass through the zone."""
    from simulatte.intralogistics.agv import AGVState

    carts = {
        name: AGV(env=env, agv_type=vehicle_type(), agv_id=name, initial_node=start(at))
        for name, at in (("a1", "P"), ("a2", "M"))
    }
    fleet = StillFleet(
        env=env,
        graph=handle,
        fleet=list(carts.values()),
        warehouses=[],
        charging_stations=[],
        traffic_controller=controller,
        default_metrics=False,
    )
    goals = nodes()
    env.activate()
    events: list[Any] = []
    env.bus.subscribe(events.append, "*")

    def mission(agv: AGV, goal: Node, at: float = 0.0):
        yield env.timeout(at)
        agv.transition_to(AGVState.TRAVELING_EMPTY)
        yield from fleet._executor.travel(agv, goal, loaded=False, purpose="travel")
        agv.transition_to(AGVState.IDLE)

    env.process(mission(carts["a1"], goals["Q"]))
    env.process(mission(carts["a2"], goals["R"], at=10.0))
    env.run()
    return [repr({entity: dict(state) for entity, state in sorted(env.initial_state.items())}), *map(repr, events)]


def test_an_explicit_graph_and_its_layout_twin_produce_the_same_event_stream():
    # The explicit model: the line in 5 m blocks, Q a passing place, and a zone over the segment M~Q (both its
    # pieces and the node between them) and the node Q.
    plain = {name: Node(name, x, y) for name, x, y in POINTS}
    arcs = [Arc(plain[a], plain[b], passing=a == "Q") for a, b in LINKS]
    arcs += [Arc(plain[b], plain[a], passing=a == "Q") for a, b in LINKS]
    env = Environment(debug=True)
    world = declare(
        env,
        LayoutGraph(plain.values(), arcs).subdivide(5),  # declared as it is: the zone names a node of the split
        zones={"gate": {"members": ["M~Q#1", "Q", ("M", "M~Q#1"), ("M~Q#1", "Q")], "capacity": 1}},
    )
    by_id = {node.id: node for node in LayoutGraph(plain.values(), arcs).subdivide(5).nodes}
    controller = ReservationController(env=env, graph=world.graph)
    explicit = run_twin(
        env,
        world.graph,
        world.ref,
        lambda: {node.id: node for node in world.canonical_graph().nodes},
        controller,
    )

    lay_env = Environment(debug=True)
    layout = Layout.from_dict(
        lay_env,
        {
            "schema_version": "1.1",
            **explicit_patch(
                [{"source": a, "target": b, "direction": "both", "passing": a == "Q"} for a, b in LINKS],
                list(POINTS),
                block_length=5,
                zones={
                    "gate": {
                        "members": [
                            {"kind": "explicit_arc", "source": "M", "target": "Q"},
                            {"kind": "explicit", "id": "Q"},
                        ]
                    }
                },
            ),
        },
    )
    lay_controller = ReservationController(env=lay_env, graph=layout.graph())
    prepared_layout: dict[str, Any] = {}

    def resolved() -> dict[str, Node]:
        return {n.id: n for n in prepared_layout["result"].network.graph.nodes}

    lay_graph = layout.graph()
    twin = run_twin(
        lay_env,
        lay_graph,
        lambda at: layout.node(at),
        lambda: (prepared_layout.setdefault("result", layout.prepare()), resolved())[1],
        lay_controller,
    )
    result = prepared_layout["result"]
    assert [(z.id, z.capacity) for z in lay_controller.network.zones] == [("gate", 1)]
    assert {n.id for n in result.network.graph.nodes} == set(by_id)
    assert any(line.startswith("TrafficReserved(") for line in twin)
    assert any(line.startswith("TrafficWaitStarted(") for line in twin)
    assert any("'gate'" in line for line in twin)
    assert twin == explicit
    assert math.isclose(env.now, lay_env.now)


# --- explicit arc length (spec 5.2) -------------------------------------------------------------------------------


def chord_only_square() -> Layout:
    layout = Layout(Environment())
    nodes = [Node(n, x, y) for n, x, y in (("A", 0, 0), ("B", 1.1, 0), ("C", 1.1, 2.2), ("D", 0, 2.2))]
    a, b, c, d = nodes
    arcs = [Arc(a, b), Arc(b, c, bidirectional=False, passing=True), Arc(c, d), Arc(d, a, False, 1.5)]
    layout.set_network(Explicit(LayoutGraph(nodes, arcs), capacity=2))
    return layout


def chord_only_line() -> Layout:
    layout = Layout(Environment())
    a, b = Node("A", 0, 0), Node("B", 3, 4)
    layout.set_network(Explicit(LayoutGraph([a, b], [Arc(a, b, speed_limit=2.0)])))
    return layout


PINNED_ALL_CHORD = {
    "line": (
        chord_only_line,
        "1.0",
        "9742e125430faeebcab7ef498d3e12d62d3b5c843f242cb0d21224333ae1b76e",
        "4a412ebca280f526810db2f5f975bfccca3582f359864ce5e96713f04f3e019b",
        "3a4f0eadaf18bfeeb088a4c09a4a29a6f56ea73a532627425ed778103ee7e1cb",
    ),
    "square": (
        chord_only_square,
        "1.1",
        "287e88e0b63058cb660b5cd9ab5f3a028da8e8dd23799acd62c6156b6bf52c1e",
        "ac2b195f5656077c76d095bbcaa8cf93c6ba8f183526eeed1ede38c6d15731e2",
        "7d10ff7357c9f3440d3fbd125fc58284e39e30f99ef6f00ecbf07ed297cac4b2",
    ),
}  # fmt: skip


@pytest.mark.parametrize("name", sorted(PINNED_ALL_CHORD))
def test_an_explicit_network_whose_arcs_are_all_chords_keeps_its_hashes_and_records_no_length(name):
    """The values were read at the commit before the change: recording lengths adds nothing for chords."""
    build, version, physical, full, layers = PINNED_ALL_CHORD[name]
    result = build().prepare()
    assert result.bundle.schema_version == version
    assert (result.physical_hash, result.full_hash, result.layers_hash) == (physical, full, layers)
    assert all("length" not in arc for arc in result.bundle["network"]["graph"]["arcs"])


def test_explicit_records_a_length_only_where_it_differs_from_the_chord():
    a, b, c = Node("A", 0, 0), Node("B", 10, 0), Node("C", 10, 5)
    graph = LayoutGraph([a, b, c], [Arc(a, b, length=25.0), Arc(b, c)])
    arcs = {(arc.source, arc.target): arc.length for arc in Explicit(graph).arcs}
    assert arcs == {("A", "B"): 25.0, ("B", "A"): 25.0, ("B", "C"): None, ("C", "B"): None}
    # An explicit length equal to the chord is the chord: nothing is recorded, as for an arc without one.
    assert [arc.length for arc in Explicit(LayoutGraph([a, b], [Arc(a, b, length=10.0)])).arcs] == [None, None]


def test_a_recorded_explicit_length_is_the_resolved_length_and_the_chord_stays_the_distance():
    layout = Layout(Environment())
    a, b = Node("A", 0, 0), Node("B", 10, 0)
    layout.set_network(Explicit(LayoutGraph([a, b], [Arc(a, b, length=25.0)])))
    graph = layout.prepare().network.graph
    first, second = graph.nodes
    assert (graph.length(first, second), graph.length(second, first)) == (25.0, 25.0)
    assert graph.distance(first, second) == 10.0  # the chord is still the straight-line distance


@pytest.mark.parametrize("pitch", [3.3, 1.1])
def test_explicit_keeps_the_lengths_of_a_subdivided_graph_to_the_float(pitch):
    """spec 5.2: ``subdivide`` gives each piece the parent's length over the count, which is not its chord; declared
    through ``Explicit`` the layout resolves to the same lengths, float for float."""
    twin = pitch_graph().subdivide(pitch)
    layout = Layout(Environment())
    layout.set_network(Explicit(twin))
    result = layout.prepare()
    assert len(twin.nodes) == len(PITCH) + (2 if pitch == 1.1 else 0) * (len(PITCH) - 1)
    assert arc_lengths(result.network.graph) == arc_lengths(twin)
    recorded = {(arc["source"], arc["target"]): arc.get("length") for arc in result.bundle["network"]["graph"]["arcs"]}
    expected = {(a.id, b.id): twin.length(a, b) for a in twin.nodes for b in twin.neighbors(a)}
    for key, length in recorded.items():
        assert length in (None, expected[key])
    assert (pitch == 1.1) == any(length is not None for length in recorded.values())
    assert result.bundle.schema_version == ("1.1" if pitch == 1.1 else "1.0")


def test_block_length_splits_a_recorded_length_and_its_pieces_are_the_length_over_the_count():
    arcs = [{"source": "A", "target": "B", "direction": "both", "length": 45.0}]
    result = resolve(explicit_patch(arcs, [("A", 0, 0), ("B", 30, 0)], block_length=10))
    ids = [n.id for n in result.graph.nodes]
    assert ids == ["A", "B", "A~B#1", "A~B#2", "A~B#3", "A~B#4"]  # ceil(45 / 10) = 5 pieces, not the chord's 3
    assert {arc_length for arc_length in arc_lengths(result.graph).values()} == {45.0 / 5}
    a, b = Node("A", 0, 0), Node("B", 30, 0)
    twin = LayoutGraph([a, b], [Arc(a, b, length=45.0)]).subdivide(10)
    assert arc_lengths(result.graph) == arc_lengths(twin)
    assert {n.id: (n.x, n.y) for n in result.graph.nodes} == {n.id: (n.x, n.y) for n in twin.nodes}


def test_explicit_with_block_length_splits_a_recorded_length_like_subdivide():
    a, b = Node("A", 0, 0), Node("B", 30, 0)
    graph = LayoutGraph([a, b], [Arc(a, b, length=45.0)])
    layout = Layout(Environment())
    layout.set_network(Explicit(graph, block_length=10))
    twin = graph.subdivide(10)
    assert arc_lengths(layout.prepare().network.graph) == arc_lengths(twin)


def test_a_recorded_length_gives_a_1_1_bundle_that_round_trips_and_changes_the_physical_hash():
    arcs = [{"source": "A", "target": "B", "direction": "both"}]
    plain = prepared(explicit_patch(arcs))
    longer = prepared(explicit_patch([{**arcs[0], "length": 25.0}]))
    assert longer.bundle.schema_version == "1.1"
    assert {arc["length"] for arc in longer.bundle["network"]["graph"]["arcs"]} == {25.0}
    assert all("length" not in arc for arc in plain.bundle["network"]["graph"]["arcs"])
    assert longer.physical_hash != plain.physical_hash
    assert longer.full_hash != plain.full_hash
    longer.bundle.check()
    again = LayoutBundle.from_dict(longer.bundle.to_dict())
    assert again == longer.bundle
    assert again.physical_hash == longer.physical_hash
    other = prepared(explicit_patch([{**arcs[0], "length": 26.0}]))
    assert other.physical_hash != longer.physical_hash
    broken = longer.bundle.to_dict()
    broken["network"]["graph"]["arcs"][0]["length"] = -1.0
    with pytest.raises(LayoutError):
        LayoutBundle.from_dict(broken)
    broken["network"]["graph"]["arcs"][0].update(length=25.0, unknown=1)
    with pytest.raises(LayoutError):
        LayoutBundle.from_dict(broken)


def test_a_1_0_bundle_rejects_a_length_on_its_arcs():
    data = prepared(explicit_patch([{"source": "A", "target": "B"}]), version="1.0").bundle.to_dict()
    assert data["schema_version"] == "1.0"
    data["network"]["graph"]["arcs"][0]["length"] = 25.0
    with pytest.raises(LayoutError):
        LayoutBundle.from_dict(data)


def test_a_length_of_the_pieces_of_an_unrecorded_arc_stays_out_of_the_bundle():
    result = prepared(explicit_patch([{"source": "A", "target": "B", "direction": "both"}], block_length=4))
    assert all("length" not in arc for arc in result.bundle["network"]["graph"]["arcs"])


def test_a_recorded_length_can_be_unset():
    patch = explicit_patch([{"source": "A", "target": "B", "length": {"$unset": True}}])
    assert arc_of(resolve(patch), explicit_node("A"), explicit_node("B")).length == 10.0


@pytest.mark.parametrize("length", [0, -1, "long", True, float("nan")])
def test_a_recorded_length_must_be_positive_and_finite(length):
    with pytest.raises(LayoutError):
        parse_patch({"schema_version": "1.1", **explicit_patch([{"source": "A", "target": "B", "length": length}])})


def test_a_recorded_length_below_the_chord_is_a_layout_error():
    with pytest.raises(LayoutError, match=r"A.*B.*length"):
        resolve(explicit_patch([{"source": "A", "target": "B", "length": 5.0}]))


def test_the_two_directions_of_a_pair_must_agree_on_length():
    arcs = [{"source": "A", "target": "B", "length": 25.0}, {"source": "B", "target": "A", "length": 26.0}]
    with pytest.raises(LayoutError, match="length"):
        resolve(explicit_patch(arcs))
    with pytest.raises(LayoutError, match="length"):
        resolve(explicit_patch(arcs, block_length=10))
    mixed = [{"source": "A", "target": "B", "length": 25.0}, {"source": "B", "target": "A"}]
    with pytest.raises(LayoutError, match="length"):
        resolve(explicit_patch(mixed))
    with pytest.raises(LayoutError, match="length"):
        resolve(explicit_patch(mixed, block_length=10))
    for length, other in ((25.0, 25.0), (10.0, None)):  # equal lengths, and a recorded chord against none
        pair = [{"source": "A", "target": "B", "length": length}, {"source": "B", "target": "A"}]
        if other is not None:
            pair[1]["length"] = other
        graph = resolve(explicit_patch(pair)).graph
        assert graph.length(*graph.nodes) == graph.length(*reversed(graph.nodes)) == length


def test_a_repeated_directed_arc_with_another_length_conflicts():
    arcs = [{"source": "A", "target": "B", "length": 25.0}, {"source": "A", "target": "B", "length": 26.0}]
    with pytest.raises(LayoutError, match="conflicting"):
        resolve(explicit_patch(arcs))


def test_a_recorded_length_is_hashed_and_resolved_by_value_not_by_spelling():
    arcs = [{"source": "A", "target": "B", "direction": "both"}]
    integer = prepared(explicit_patch([{**arcs[0], "length": 25}]))
    real = prepared(explicit_patch([{**arcs[0], "length": 25.0}]))
    assert (integer.physical_hash, integer.full_hash) == (real.physical_hash, real.full_hash)
    for result in (integer, real):
        assert {type(arc["length"]) for arc in result.bundle["network"]["graph"]["arcs"]} == {float}
    graph = resolve(explicit_patch([{**arcs[0], "length": 25}])).graph
    assert type(graph.length(*graph.nodes)) is float


def test_explicit_hashes_a_recorded_length_by_value():
    def hashes(length: float) -> tuple[str, str]:
        layout = Layout(Environment())
        a, b = Node("A", 0, 0), Node("B", 10, 0)
        layout.set_network(Explicit(LayoutGraph([a, b], [Arc(a, b, length=length)])))
        result = layout.prepare()
        return result.physical_hash, result.full_hash

    assert hashes(25) == hashes(25.0)


def test_explicit_accepts_a_float_subclass_length_and_names_a_bad_one():
    class Metres(float):
        """What a numpy float64 is to Python: a float of another type."""

    a, b = Node("A", 0, 0), Node("B", 10, 0)
    graph = LayoutGraph([a, b], [Arc(a, b, length=Metres(25.0))])
    assert {type(arc.length) for arc in Explicit(graph).arcs} == {float}
    layout = Layout(Environment())
    layout.set_network(Explicit(graph))
    resolved = layout.prepare().network.graph
    assert resolved.length(*resolved.nodes) == 25.0
    broken = LayoutGraph([a, b], [Arc(a, b)])
    object.__setattr__(broken._adjacency[a][b], "length", "far")
    with pytest.raises(LayoutError, match="length"):
        Explicit(broken)


def test_block_length_on_a_recorded_length_may_not_generate_coincident_nodes():
    """A declared length far above the nodes' distance makes pieces whose points coincide: a chord of 0 (L16)."""
    arcs = [{"source": "A", "target": "B", "direction": "both", "length": 20.0}]
    nodes = [("A", 1000.0, 0.0), ("B", 1000.0 + 1e-12, 0.0)]
    with pytest.raises(LayoutError, match=r"block_length 0\.5 .*explicit_arc"):
        resolve(explicit_patch(arcs, nodes, block_length=0.5))
    # The last generated node against the end node.
    near_end = [("A", 1000.0 - math.ulp(1000.0), 0.0), ("B", 1000.0, 0.0)]  # the one generated node lands on B
    with pytest.raises(LayoutError, match="block_length"):
        resolve(explicit_patch(arcs, near_end, block_length=10))
    spread = resolve(explicit_patch(arcs, [("A", 0.0, 0.0), ("B", 10.0, 0.0)], block_length=5))
    assert len(spread.graph.nodes) == 2 + 3  # distinct points: four pieces of 5.0


# --- node capacities and zones declared with an explicit network (spec 5.2, L13) ------------------------------------


def declared_network(graph: LayoutGraph, *, controller: dict[str, Any] | None = None, facilities=(), **declaration):
    """The traffic network of a ``ReservationController`` on ``graph``, declared with ``Explicit(graph, ...)``."""
    from simulatte.intralogistics.parking import ParkingArea

    env = Environment()
    env.layout.set_network(Explicit(graph, **declaration))
    controller_ = ReservationController(env=env, graph=env.layout.graph(), **(controller or {}))
    for name, node_id, capacity in facilities:
        ParkingArea(env=env, name=name, node=env.layout.node(node_id), capacity=capacity)
    fleet_areas = [entity for entity in env.entities.live() if isinstance(entity, ParkingArea)]
    if fleet_areas:  # a registered fleet is what hands its facilities to the controller
        controller_.register_fleet(
            StillFleet(
                env=env,
                graph=env.layout.graph(),
                fleet=[],
                warehouses=[],
                charging_stations=[],
                parking_areas=fleet_areas,
                traffic_controller=controller_,
                default_metrics=False,
            )
        )
    env.activate()
    return controller_.network


def five_nodes() -> tuple[LayoutGraph, dict[str, Node]]:
    """The graph of ``traffic_support.line(env, 5)``, ``N0 ... N4`` 10 m apart, with its nodes by id: the tests
    declare it themselves."""
    nodes = {f"N{i}": Node(f"N{i}", 10 * i, 0) for i in range(5)}
    return LayoutGraph(nodes.values(), [Arc(nodes[f"N{i}"], nodes[f"N{i + 1}"]) for i in range(4)]), nodes


def test_explicit_capacities_give_the_declared_nodes_their_capacity_and_the_default_elsewhere():
    graph, _ = five_nodes()
    network = declared_network(graph, capacities={"N1": 2, "N3": 4})
    assert {node.id: network.capacity(node) for node in network.graph.nodes} == {
        "N0": 1,
        "N1": 2,
        "N2": 1,
        "N3": 4,
        "N4": 1,
    }
    # A node's own capacity wins over the network default; the default still fills the other nodes.
    network = declared_network(graph, capacity=3, capacities={"N1": 2})
    assert {node.id: network.capacity(node) for node in network.graph.nodes} == {
        "N0": 3,
        "N1": 2,
        "N2": 3,
        "N3": 3,
        "N4": 3,
    }


def test_a_declared_capacity_is_the_one_the_resolved_network_holds():
    graph, _ = five_nodes()
    layout = Layout(Environment())
    layout.set_network(Explicit(graph, capacities={"N2": 2}))
    result = layout.prepare()
    assert {node.id: capacity for node, capacity in result.network.capacities.items()} == {
        "N0": None,
        "N1": None,
        "N2": 2,
        "N3": None,
        "N4": None,
    }
    assert {n["id"]: n["capacity"] for n in result.bundle["network"]["graph"]["nodes"]}["N2"] == 2


def test_declaring_a_capacity_changes_the_hash_and_declaring_none_changes_nothing():
    graph, _ = five_nodes()

    def hashes(**declaration: Any) -> tuple[str, str]:
        layout = Layout(Environment())
        layout.set_network(Explicit(graph, **declaration))
        result = layout.prepare()
        return result.physical_hash, result.full_hash

    assert hashes(capacities={}) == hashes() == hashes(capacities=None)
    assert hashes(capacities={"N2": 2}) != hashes()
    assert hashes(capacities={"N2": 2}) == hashes(capacities={"N2": 2})


def test_a_capacity_override_still_applies_after_the_declaration():
    graph, _ = five_nodes()
    env = Environment()
    env.layout.set_network(Explicit(graph, capacities={"N1": 2}))
    env.layout.override(NetworkOverride("o", Address({"kind": "explicit", "id": "N1"}), {"capacity": 3}))
    result = env.layout.prepare()
    assert {node.id: capacity for node, capacity in result.network.capacities.items()}["N1"] == 3


@pytest.mark.parametrize("capacities, named", [({"N9": 2}, "N9"), ({"N1": 2, "Z": 2}, "Z")])
def test_a_capacity_for_an_unknown_node_names_it(capacities, named):
    graph, _ = five_nodes()
    with pytest.raises(LayoutError, match=named):
        Explicit(graph, capacities=capacities)


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "2", 2**53])
def test_a_node_capacity_must_be_a_positive_safe_integer_and_names_the_node(value):
    graph, _ = five_nodes()
    with pytest.raises(LayoutError, match="N2.*capacity|capacity.*N2"):
        Explicit(graph, capacities={"N2": value})


@pytest.mark.parametrize("capacities", [[("N1", 2)], {1: 2}, "N1"])
def test_capacities_are_a_mapping_by_node_id(capacities):
    graph, _ = five_nodes()
    with pytest.raises(LayoutError, match="capacities"):
        Explicit(graph, capacities=capacities)


def test_the_capacity_fallback_order_is_unchanged():
    """Declared capacity, then the facilities on the node, then ``node_capacity``."""
    graph, _ = five_nodes()
    network = declared_network(
        graph,
        controller={"node_capacity": 2},
        facilities=[("p1", "N1", 3), ("p2", "N2", 3)],
        capacities={"N2": 5},
    )
    assert {node.id: network.capacity(node) for node in network.graph.nodes} == {
        "N0": 2,
        "N1": 3,
        "N2": 5,
        "N3": 2,
        "N4": 2,
    }


ZONE_MEMBERS = {
    "addresses": [{"kind": "explicit", "id": "N3"}, {"kind": "explicit_arc", "source": "N1", "target": "N2"}],
    "ids": ["N3", ("N1", "N2")],
    "reversed pair": ["N3", ["N2", "N1"]],
    "mixed": ["N3", {"kind": "explicit_arc", "source": "N1", "target": "N2"}],
}


@pytest.mark.parametrize("form", sorted(ZONE_MEMBERS))
@pytest.mark.parametrize("hold_back", [False, True])
@pytest.mark.parametrize("capacity", [1, 2])
def test_a_zone_declared_on_the_network_derives_the_claims_of_the_same_zone_given_to_the_controller(
    hold_back, capacity, form
):
    """Node and pair members, in a five-node world: every leg claims the same resources either way."""
    graph, nodes = five_nodes()
    declared = {
        "z": {
            "members": ZONE_MEMBERS[form],
            "capacity": capacity,
            "hold_back": hold_back,
        }
    }
    from_layout = declared_network(graph, zones=declared)
    env = Environment()
    env.layout.set_network(Explicit(graph))
    controller = ReservationController(
        env=env,
        graph=env.layout.graph(),
        zones=[Zone("z", (nodes["N3"], (nodes["N1"], nodes["N2"])), capacity, hold_back)],
    )
    env.activate()
    from_controller = controller.network
    assert [zone.id for zone in from_layout.zones] == ["z"]

    def legs(network) -> dict[tuple[str, str], list[tuple[str, str | None]]]:
        found = {}
        for u in network.graph.nodes:
            for v in network.graph.neighbors(u):
                found[u.id, v.id] = [(item.resource.id, item.direction) for item in network.leg(u, v)]
        return found

    assert legs(from_layout) == legs(from_controller)
    assert len(legs(from_layout)) == 8  # four two-way arcs
    assert any("z" in {name for name, _ in claim} for claim in legs(from_layout).values())
    (zone,) = from_layout.zones
    assert (zone.capacity, zone.hold_back) == (capacity, hold_back)


def zone_hashes(graph: LayoutGraph, members: list[Any]) -> tuple[str, str, Any]:
    layout = Layout(Environment())
    layout.set_network(Explicit(graph, zones={"z": {"members": members, "capacity": 2}}))
    result = layout.prepare()
    return result.physical_hash, result.full_hash, result.bundle["network"]


def test_zone_members_by_id_give_the_bundle_and_hashes_of_the_same_zone_by_address():
    graph, _ = five_nodes()
    by_address = zone_hashes(graph, ZONE_MEMBERS["addresses"])
    assert zone_hashes(graph, ZONE_MEMBERS["ids"]) == by_address
    assert zone_hashes(graph, ZONE_MEMBERS["mixed"]) == by_address


def test_a_pair_member_of_a_one_way_arc_names_the_arc_that_exists():
    a, b, c = Node("A", 0, 0), Node("B", 10, 0), Node("C", 20, 0)
    graph = LayoutGraph([a, b, c], [Arc(a, b, bidirectional=False), Arc(c, b, bidirectional=False)])
    for pair in (("A", "B"), ("B", "A")):
        layout = Layout(Environment())
        layout.set_network(Explicit(graph, zones={"z": {"members": [pair, ("C", "B")]}}))
        (zone,) = layout.prepare().network.zones
        assert {(x.id, y.id) for x, y in (m for m in zone.members if isinstance(m, tuple))} == {("A", "B"), ("B", "C")}
    assert Explicit(graph, zones={"z": {"members": [("B", "A")]}}).zones["z"]["members"][0] == {
        "kind": "explicit_arc",
        "source": "A",
        "target": "B",
    }


@pytest.mark.parametrize(
    "member, named",
    [("N9", "N9"), (("N0", "N2"), "N0"), (("N1", "N9"), "N9"), (("N1", "N1"), "N1")],
)
def test_a_zone_member_that_is_not_in_the_graph_names_the_zone_and_the_member(member, named):
    graph, _ = five_nodes()
    with pytest.raises(LayoutError, match=rf"zones/z: member .*{named}"):
        Explicit(graph, zones={"z": {"members": ["N3", member]}})


def test_a_zone_member_of_another_shape_is_left_to_the_schema_and_the_other_networks_take_no_shorthand():
    graph, _ = five_nodes()
    with pytest.raises(LayoutError):
        Explicit(graph, zones={"z": {"members": [("N1", "N2", "N3")]}})
    with pytest.raises(LayoutError):
        Explicit(graph, zones={"z": {"members": [3]}})
    with pytest.raises(LayoutError):
        Explicit(graph, zones={"z": {}})
    with pytest.raises(LayoutError):
        Lanes({}, zones={"z": {"members": ["N1"]}})
    with pytest.raises(LayoutError):
        Lattice((0, 0, 1, 1), zones={"z": {"members": ["N1"]}})
