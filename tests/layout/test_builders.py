from __future__ import annotations

import json
from dataclasses import fields

import pytest

from simulatte import Environment, Layout
from simulatte.intralogistics import Arc, FleetKPIs, LayoutGraph, Node, OrderStatus, build_simple_system
from simulatte.intralogistics.events import AgvMoveEnded, AgvMoveStarted, OrderStatusChanged
from simulatte.intralogistics.traffic import FreeTrafficManager
from simulatte.layout import Connector, Explicit, Footprint, GraphRef, LayoutError, LayoutNotReady, Point, Port

ROLES = ("WH_A_OUT", "N1", "N2", "N3", "WH_B_IN")


def line(prefix="", y=0):
    nodes = [Node(f"{prefix}{name}", i * 5, y) for i, name in enumerate(ROLES)]
    return nodes, [Arc(a, b, bidirectional=True) for a, b in zip(nodes, nodes[1:])]


def roles(layout, prefix=""):
    return {name: layout.node(f"{prefix}{name}") for name in ROLES}


@pytest.mark.parametrize("explicit", [False, True])
def test_layout_builder_defers_binding_and_preserves_five_tuple(explicit):
    env = Environment()
    layout = Layout(env)
    result = build_simple_system(env, **({"layout": layout} if explicit else {}))
    fleet, agvs, origin, destination, graph = result
    assert len(result) == 5 and isinstance(graph, GraphRef) and graph == layout.graph()
    assert [a.id for a in agvs] == ["vehicle-0", "vehicle-1"]
    assert all(env.entities.is_named(a.id) for a in agvs)
    for obj, field in ((fleet, "graph"), (agvs[0], "current_node"), (origin, "input_bays")):
        with pytest.raises(LayoutNotReady):
            getattr(obj, field)
    assert env.now == 0 and not env._activated
    for warehouse in (origin, destination):
        assert layout._coordinator.values.require(("placements", warehouse.id, "obstacle"), explicit=True) is False
    layout.prepare()
    resolved = {n.id: n for n in fleet.graph.nodes}
    assert set(resolved) == set(ROLES)
    assert [(resolved[name].x, resolved[name].y) for name in ROLES] == [(i * 5, 0) for i in range(5)]
    assert origin.output_bays == (resolved["WH_A_OUT"],)
    assert destination.input_bays == (resolved["WH_B_IN"],)
    assert agvs[0].current_node is fleet.parking_areas[0].node is resolved["N1"]
    assert fleet.charging_stations[0].node is resolved["N2"]
    assert isinstance(fleet._traffic_manager, FreeTrafficManager)


@pytest.mark.parametrize("union", [False, True])
def test_two_prefixed_systems_compose_on_caller_network(union):
    env = Environment()
    layout = Layout(env)
    nodes_a, arcs_a = line("a." if union else "")
    nodes_b, arcs_b = line("b.", 10) if union else ([], [])
    layout.set_network(Explicit(LayoutGraph(nodes_a + nodes_b, arcs_a + arcs_b)))
    before = layout._coordinator.values.explicit_data["network"]
    a = build_simple_system(env, prefix="a.", nodes=roles(layout, "a." if union else ""))
    b = build_simple_system(env, prefix="b.", nodes=roles(layout, "b." if union else ""))
    assert layout._coordinator.values.explicit_data["network"] == before
    assert [v.id for v in a[1] + b[1]] == ["a.agv-0", "a.agv-1", "b.agv-0", "b.agv-1"]
    orders = []
    for fleet, _, origin, destination, _ in (a, b):
        order = fleet.create_order(sku=next(iter(origin.inventory)), quantity=2, origin=origin, destination=destination)
        fleet.submit(order)
        orders.append(order)
    env.run(until=120)
    assert all(order.status is OrderStatus.COMPLETED for order in orders)
    assert a[0].graph is b[0].graph
    assert len(a[0].graph.nodes) == (10 if union else 5)
    assert not layout._resource_managers


@pytest.mark.parametrize("case", ["missing", "extra", "foreign", "concrete", "absent_mapping"])
def test_existing_network_requires_exact_same_layout_role_mapping(case):
    env = Environment()
    layout = Layout(env)
    nodes, arcs = line()
    layout.set_network(Explicit(LayoutGraph(nodes, arcs)))
    mapping = roles(layout)
    if case == "missing":
        del mapping["N3"]
    elif case == "extra":
        mapping["extra"] = layout.node("N3")
    elif case == "foreign":
        mapping["N3"] = Layout(Environment()).node("N3")
    elif case == "concrete":
        mapping["N3"] = nodes[3]
    before = layout._coordinator.values.explicit_data
    with pytest.raises(LayoutError, match="nodes|same layout"):
        build_simple_system(env, nodes=None if case == "absent_mapping" else mapping)
    assert layout._coordinator.values.explicit_data == before
    assert not list(env.entities.live())


def test_mapping_without_network_and_foreign_layout_fail_before_attachment():
    env = Environment()
    layout = Layout(env)
    with pytest.raises(LayoutError, match="network"):
        build_simple_system(env, nodes=roles(layout))
    with pytest.raises(LayoutError, match="environment"):
        build_simple_system(Environment(), layout=layout)
    with pytest.raises(LayoutError, match="layout"):
        build_simple_system(Environment(), nodes=roles(layout))
    assert not list(env.entities.live())


def test_complete_file_network_is_not_overwritten():
    env = Environment()
    layout = Layout.from_dict(
        env,
        {
            "schema_version": "1.0",
            "network": {
                "type": "explicit",
                "nodes": [{"id": role, "position": [i * 8, 0, 0]} for i, role in enumerate(ROLES)],
                "arcs": [{"source": a, "target": b, "direction": "both"} for a, b in zip(ROLES, ROLES[1:])],
            },
        },
    )
    with pytest.raises(LayoutError, match="nodes"):
        build_simple_system(env)
    fleet, *_ = build_simple_system(env, nodes=roles(layout))
    layout.prepare()
    assert {n.id: n.x for n in fleet.graph.nodes}["WH_B_IN"] == 32


def test_file_network_patch_moves_named_endpoint(tmp_path):
    path = tmp_path / "layout.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "network": {
                    "nodes": [
                        {"id": role, "position": [25 if role == "WH_B_IN" else i * 5, 0, 0]}
                        for i, role in enumerate(ROLES)
                    ],
                },
            }
        )
    )
    env = Environment()
    layout = Layout(env, file=path)
    fleet, _, _, destination, _ = build_simple_system(env)
    layout.prepare()
    assert destination.input_bays[0].id == "WH_B_IN"
    assert destination.input_bays[0].x == 25
    n3 = next(node for node in fleet.graph.nodes if node.id == "N3")
    assert fleet.graph.distance(n3, destination.input_bays[0]) == 10


def test_port_mapping_supports_named_file_endpoint_override():
    env = Environment()
    layout = Layout.from_dict(
        env,
        {
            "schema_version": "1.0",
            "placements": {
                "WH-A": {"kind": "warehouse", "position": [-4, 0, 0]},
            },
        },
    )
    nodes, arcs = line()
    layout.set_network(Explicit(LayoutGraph(nodes, arcs)))
    mapping = roles(layout)
    mapping["WH_A_OUT"] = layout.port("WH-A", "out")
    fleet, _, origin, _, _ = build_simple_system(env, nodes=mapping)
    layout.place(
        origin,
        position=Point(-2, 0),
        rotation=0,
        footprint=Footprint(2, 2),
        obstacle=True,
        ports={"out": Port(Point(1, 0), Point(1, 0), Connector(target=layout.node("WH_A_OUT")))},
    )
    layout.prepare()
    assert origin.output_bays[0].x == -3
    assert origin.output_bays[0] in fleet.graph.nodes


def test_plain_bay_inside_warehouse_obstacle_has_no_owner_exemption():
    env = Environment()
    layout = Layout(env)
    _, _, origin, _, _ = build_simple_system(env)
    layout.place(origin, position=Point(0, 0), rotation=0, footprint=Footprint(2, 2), obstacle=True)
    with pytest.raises(LayoutError, match="block|obstacle"):
        layout.prepare()
    assert "connector" in (build_simple_system.__doc__ or "")


def transport(layout_mode):
    with Environment(seed=17, debug=True) as env:
        env.enable_digest()
        if layout_mode:
            Layout(env)
        fleet, _, origin, destination, _ = build_simple_system(env, prefix="line.")
        kpis = FleetKPIs(fleet).attach(env)
        trajectory = []
        env.bus.subscribe(trajectory.append, (AgvMoveStarted, AgvMoveEnded, OrderStatusChanged))
        orders = []
        for quantity in (5, 8, 12, 6):
            order = fleet.create_order(
                sku=next(iter(origin.inventory)), quantity=quantity, origin=origin, destination=destination
            )
            fleet.submit(order)
            orders.append(order)
        env.run(until=120)
        assert all(order.status is OrderStatus.COMPLETED for order in orders)
        # Prelude attachment counts differ because node binding is deferred; event
        # sequence numbers are envelopes, not transport trajectories.
        observed = [
            (
                type(event),
                {
                    field.name: getattr(event, field.name)
                    for field in fields(event)
                    if field.name not in {"seq", "ordinal"}
                },
            )
            for event in trajectory
        ]
        return env.fingerprint(), kpis.scalars(), observed, env.entities.snapshot(), env.initial_state


def test_prefixed_explicit_and_layout_trajectories_and_kpis_match():
    assert transport(False) == transport(True)


@pytest.mark.parametrize("path", ["type", "network"])
def test_file_unset_does_not_replace_existing_code_network(tmp_path, path):
    env = Environment()
    layout = Layout(env)
    nodes, arcs = line()
    layout.set_network(Explicit(LayoutGraph(nodes, arcs)))
    file = tmp_path / "layout.json"
    network = {"type": {"$unset": True}} if path == "type" else {"$unset": True}
    file.write_text(json.dumps({"schema_version": "1.0", "network": network}))
    original = tuple(layout._coordinator._code)
    with pytest.raises(LayoutError, match="network"):
        layout.load(file)
    with pytest.raises(LayoutError, match="nodes"):
        build_simple_system(env)
    assert tuple(layout._coordinator._code) == original
    build_simple_system(env, nodes=roles(layout))
    layout.prepare()
