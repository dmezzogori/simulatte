"""Components take only handles of their environment's layout (spec §5.3 to §5.6 of the layout-only design, L1, L2).

A concrete `LayoutGraph` or `Node` raises with a message that names the fix; physical fields are read after the layout
is prepared; components constructed after preparation resolve their handles at once; one network per environment.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from simulatte import Environment
from simulatte.intralogistics.agv import AGV, AGVType
from simulatte.intralogistics.builders import build_simple_system
from simulatte.intralogistics.charging import ChargingStation
from simulatte.intralogistics.events import AgvPlaced
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.order import OrderStatus
from simulatte.intralogistics.parking import ParkingArea
from simulatte.intralogistics.reservation import ReservationController
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.traffic import FreeFlowController
from simulatte.intralogistics.warehouse import Warehouse
from simulatte.layout import Explicit, Layout, LayoutError, LayoutNotReady, Point

CART = AGVType("cart", TrapezoidalProfile(2, 1, 1), 100, 100, 100, length=0.5)
GRAPH_FIX = "takes layout.graph(); declare the graph with env.layout.set_network(Explicit(graph))"
NODE_FIX = "takes layout.node(id) or layout.port(...) handles, not Node objects"


def _graph() -> LayoutGraph:
    a, b = Node("A", 0, 0), Node("B", 2, 0)
    return LayoutGraph([a, b], [Arc(a, b)])


def _declared(env: Environment) -> Layout:
    env.layout.set_network(Explicit(_graph()))
    return env.layout


def _warehouse(env: Environment, name: str = "wh", *, input_bays=(), output_bays=()) -> Warehouse:
    return Warehouse(
        env=env,
        name=name,
        input_bays=list(input_bays),
        output_bays=list(output_bays),
        n_slots=1,
        products=[],
        pick_time=0,
        put_time=0,
    )


def _fleet(env: Environment, graph: Any, **options: Any) -> FleetCoordinator:
    return FleetCoordinator(env=env, graph=graph, fleet=[], warehouses=[], charging_stations=[], **options)


CASES = {
    "FleetCoordinator(graph=...)": lambda env, graph, node: _fleet(env, graph),
    "ReservationController(graph=...)": lambda env, graph, node: ReservationController(env=env, graph=graph),
    "Warehouse(input_bays=...)": lambda env, graph, node: _warehouse(env, input_bays=[node]),
    "Warehouse(output_bays=...)": lambda env, graph, node: _warehouse(env, output_bays=[node]),
    "AGV(initial_node=...)": lambda env, graph, node: AGV(env=env, agv_type=CART, initial_node=node),
    "ChargingStation(node=...)": lambda env, graph, node: ChargingStation(env=env, name="cs", node=node, n_slots=1),
    "ParkingArea(node=...)": lambda env, graph, node: ParkingArea(env=env, name="pa", node=node, capacity=1),
}


@pytest.mark.parametrize("declared", [False, True], ids=["no-network", "network"])
@pytest.mark.parametrize("case", sorted(CASES))
def test_a_concrete_graph_or_node_raises_with_the_fix(case: str, declared: bool) -> None:
    env = Environment()
    if declared:
        _declared(env)
    graph = _graph()
    fix = GRAPH_FIX if case.endswith("(graph=...)") else NODE_FIX
    before = env.entities.live()
    with pytest.raises(LayoutError, match=f"^{re.escape(f'{case} {fix}')}$") as raised:
        CASES[case](env, graph, graph.nodes[0])
    assert raised.value.diagnostics[0].code == "foreign-handle"
    assert env.entities.live() == before


@pytest.mark.parametrize("declared", [False, True], ids=["no-network", "network"])
def test_a_concrete_node_in_the_builders_mapping_raises_with_the_fix(declared: bool) -> None:
    env = Environment()
    if declared:
        _declared(env)
    roles = ("WH_A_OUT", "N1", "N2", "N3", "WH_B_IN")
    nodes = {role: Node(role, float(i), 0.0) for i, role in enumerate(roles)}
    with pytest.raises(LayoutError, match=f"^{re.escape(f'build_simple_system(nodes=...) {NODE_FIX}')}$") as raised:
        build_simple_system(env, nodes=nodes)  # ty: ignore[invalid-argument-type]
    assert raised.value.diagnostics[0].code == "foreign-handle"
    assert env.entities.live() == ()


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_handle_of_another_environments_layout_raises(case: str) -> None:
    env, other = Environment(), Environment()
    _declared(env)
    foreign = _declared(other)
    before = env.entities.live()
    with pytest.raises(LayoutError, match="same layout") as raised:
        CASES[case](env, foreign.graph(), foreign.node("A"))
    assert raised.value.diagnostics[0].code == "foreign-handle"
    assert env.entities.live() == before


def test_an_agv_without_a_node_and_a_warehouse_without_bays_are_accepted() -> None:
    env = Environment()
    cart = AGV(env=env, agv_type=CART, agv_id="cart", initial_node=None)
    empty = _warehouse(env, "empty")
    assert empty.input_bays == empty.output_bays == ()
    env.activate()
    assert cart.current_node is None
    assert empty.input_bays == empty.output_bays == ()
    assert env.initial_state["cart"]["node"] is None


def _system(env: Environment, layout: Layout) -> dict[str, tuple[Any, str]]:
    controller = ReservationController(env=env, graph=layout.graph())
    cart = AGV(env=env, agv_type=CART, agv_id="cart", initial_node=layout.node("A"))
    origin = _warehouse(env, "origin", input_bays=[layout.node("A")], output_bays=[layout.node("A")])
    station = ChargingStation(env=env, name="cs", node=layout.node("B"), n_slots=1)
    parking = ParkingArea(env=env, name="pa", node=layout.node("A"), capacity=1)
    fleet = FleetCoordinator(
        env=env,
        graph=layout.graph(),
        fleet=[cart],
        warehouses=[origin],
        charging_stations=[station],
        parking_areas=[parking],
        traffic_controller=controller,
    )
    return {
        "AGV.current_node": (cart, "current_node"),
        "Warehouse.input_bays": (origin, "input_bays"),
        "Warehouse.output_bays": (origin, "output_bays"),
        "ChargingStation.node": (station, "node"),
        "ParkingArea.node": (parking, "node"),
        "FleetCoordinator.graph": (fleet, "graph"),
        "ReservationController.graph": (controller, "graph"),
    }


def test_reading_before_preparation_names_the_fix() -> None:
    env = Environment()
    getters = _system(env, _declared(env))
    assert len(getters) == 7  # the physical getters of spec §2
    for name, (component, field) in getters.items():
        with pytest.raises(LayoutNotReady) as raised:
            getattr(component, field)
        message = str(raised.value)
        assert message.startswith(f"{name} is unresolved"), message
        assert "env.layout.prepare()" in message and "env.activate()" in message, message
    env.layout.prepare()
    for component, field in getters.values():
        getattr(component, field)


def test_a_component_repr_reads_no_physical_field_before_preparation() -> None:
    env = Environment()
    getters = _system(env, _declared(env))
    components = {name.split(".")[0]: component for name, (component, _) in getters.items()}
    before = {name: repr(component) for name, component in components.items()}
    assert before["ParkingArea"] == "ParkingArea(name='pa', node=unbound, capacity=1)"
    env.layout.prepare()
    after = {name: repr(component) for name, component in components.items()}
    assert after["ParkingArea"] == "ParkingArea(name='pa', node='A', capacity=1)"
    assert {name for name in before if before[name] != after[name]} == {"ParkingArea"}


def test_physical_fields_are_read_only() -> None:
    env = Environment()
    getters = _system(env, _declared(env))
    env.layout.prepare()
    for name, (component, field) in getters.items():
        if name in ("AGV.current_node", "ReservationController.graph"):
            continue  # an AGV moves; the controller's graph has no setter
        with pytest.raises(LayoutError, match=f"^{field} is read-only"):
            setattr(component, field, getattr(component, field))


def test_late_components_resolve_at_once() -> None:
    env = Environment()
    layout = _declared(env)
    early = AGV(env=env, agv_type=CART, agv_id="early", initial_node=layout.node("A"))
    _fleet(env, layout.graph(), name="early-fleet")  # a fleet before activation, on free flow
    env.activate()
    network = layout.prepare().network
    a, b = network.node(layout.node("A").address), network.node(layout.node("B").address)
    placed = []
    env.bus.subscribe(lambda event: placed.append((event.agv, event.node, event.previous)), (AgvPlaced,))
    late = AGV(env=env, agv_type=CART, agv_id="late", initial_node=layout.node("B"))
    parking = ParkingArea(env=env, name="late-pa", node=layout.node("A"), capacity=1)
    assert late.current_node is b and parking.node is a
    assert early.current_node is a
    assert env.entities.node_binding(b).agvs == ["late"]
    assert placed == [("late", "B", None)]
    sku = SKU("stock", 1, 1)
    origin, destination = (
        Warehouse(
            env=env,
            name=name,
            input_bays=[layout.node(at)],
            output_bays=[layout.node(at)],
            n_slots=1,
            products=[sku],
            initial_inventory={sku: stock},
            pick_time=1,
            put_time=1,
        )
        for name, at, stock in (("late-origin", "A", 5), ("late-destination", "B", 0))
    )
    fleet = FleetCoordinator(
        env=env,
        graph=layout.graph(),
        fleet=[late],
        warehouses=[origin, destination],
        charging_stations=[],
        parking_areas=[parking],
        name="late-fleet",
    )
    assert fleet.graph is network.graph and origin.output_bays == (a,) and destination.input_bays == (b,)
    order = fleet.create_order(sku=sku, quantity=2, origin=origin, destination=destination)
    fleet.submit(order)
    env.run()
    assert order.status is OrderStatus.COMPLETED  # the late AGV works: it fetched from A and delivered to B
    assert destination.get_inventory_level(sku) == 2 and late.current_node is b
    with pytest.raises(LayoutError, match="must be constructed before the layout is prepared"):
        ReservationController(env=env, graph=layout.graph())


@pytest.mark.parametrize("finalize", ["activate", "run", "prepare"])
def test_the_network_is_declared_before_the_layout_is_finalized(finalize: str) -> None:
    """Ruling T4-a: a network, or a first fleet on it, after activation raises, naming the cause and the fix."""
    env = Environment()
    {"activate": env.activate, "run": env.run, "prepare": env.layout.prepare}[finalize]()
    cause = "the layout is finalized when it is prepared, at env.activate() or the first env.run()"
    fix = "declare the network on env.layout before activating; components with handles may still be added later"
    with pytest.raises(LayoutError, match=re.escape(f"cannot declare the network: {cause}: {fix}")):
        env.layout.set_network(Explicit(_graph()))
    with pytest.raises(LayoutError, match=re.escape(f"graph reference requires a declared network: {cause}: {fix}")):
        _fleet(env, env.layout.graph())
    a = env.layout.node("A")
    for make in (
        lambda: AGV(env=env, agv_type=CART, initial_node=a),
        lambda: _warehouse(env, input_bays=[a]),
        lambda: ChargingStation(env=env, name="cs", node=a, n_slots=1),
        lambda: ParkingArea(env=env, name="pa", node=a, capacity=1),
    ):
        with pytest.raises(LayoutError, match=re.escape(f"node reference requires a declared network: {cause}: {fix}")):
            make()
    assert env.entities.live() == ()


def test_one_network_per_environment() -> None:
    """Spec §5.2: before preparation the last declaration wins; after it a declaration raises."""
    env = Environment()
    x, y = Node("X", 0, 0), Node("Y", 5, 0)
    env.layout.set_network(Explicit(LayoutGraph([x, y], [Arc(x, y)])))
    layout = _declared(env)  # replaces the first declaration
    first = _fleet(env, layout.graph(), name="first")
    second = _fleet(env, layout.graph(), name="second")
    result = layout.prepare()
    assert first.graph is second.graph is result.network.graph
    assert [node.id for node in result.network.graph.nodes] == ["A", "B"]
    with pytest.raises(LayoutError, match="cannot declare the network"):
        layout.set_network(Explicit(_graph()))


def test_two_fleets_of_one_environment_share_a_free_flow_controller_and_another_environment_is_refused() -> None:
    env, other = Environment(), Environment()
    controller = FreeFlowController()
    first = _fleet(env, _declared(env).graph(), traffic_controller=controller)
    _fleet(env, env.layout.graph(), traffic_controller=controller)
    with pytest.raises(ValueError, match="another environment's layout"):
        _fleet(other, _declared(other).graph(), traffic_controller=controller)
    env.layout.prepare()
    assert controller.view.graph is first.graph


def test_the_controller_takes_no_capacities_or_zones() -> None:
    """Node capacities and zones are declared on the network (L13); the controller has no such arguments."""
    env = Environment()
    layout = _declared(env)
    for argument in ("capacities", "zones"):
        with pytest.raises(TypeError, match=argument):
            ReservationController(env=env, graph=layout.graph(), **{argument: {}})


def test_explicit_rejects_an_arc_between_two_nodes_at_one_position() -> None:
    """L16: a lift or a transfer point is two nodes a small distance apart with an explicit ``length``."""
    a, b = Node("A", 1, 1), Node("B", 1, 1)
    with pytest.raises(LayoutError, match="zero-length"):
        Explicit(LayoutGraph([a, b], [Arc(a, b, length=3.0)]))
    apart = Node("B", 1, 1.001)
    explicit = Explicit(LayoutGraph([a, apart], [Arc(a, apart, length=3.0)]))
    env = Environment()
    env.layout.set_network(explicit)
    graph = env.layout.prepare().network.graph
    assert graph.length(*graph.nodes) == 3.0


def _handles(layout: Layout) -> tuple[Any, ...]:
    """One handle of each kind: the graph, a node, a port and a value."""
    return (
        layout.graph(),
        layout.node("A"),
        layout.port("machine", "out"),
        layout.ref("placements", "machine", "position"),
    )


PHYSICAL = (
    "id",
    "x",
    "y",
    "z",
    "nodes",
    "neighbors",
    "arc_between",
    "distance",
    "length",
    "path_length",
    "path_distance",
    "shortest_path",
    "subdivide",
)


def test_reading_a_physical_attribute_of_a_handle_names_the_resolved_objects() -> None:
    """A handle is not the resolved graph or node, before preparation and after it: reading what a `LayoutGraph` or a
    `Node` has raises `LayoutNotReady` with one message that names where the resolved objects are."""
    env = Environment()
    layout = _declared(env)
    handles = _handles(layout)
    for prepared in (False, True):
        if prepared:
            layout.prepare()
        for handle in handles:
            for name in PHYSICAL:
                with pytest.raises(LayoutNotReady) as raised:
                    getattr(handle, name)
                message = str(raised.value)
                assert message.startswith(f"{type(handle).__name__} is a layout handle"), message
                assert f"has no {name}" in message, message
                assert "fleet.graph" in message and "env.layout.prepare().network" in message, message


def test_a_probe_of_a_physical_attribute_of_a_handle_finds_nothing_and_a_read_still_names_the_resolved_objects() -> (
    None
):
    """The error of reading a physical attribute is a `LayoutNotReady` and also an `AttributeError`, so `hasattr` is
    False and `getattr` with a default returns it, while a direct read raises the same message as before."""
    env = Environment()
    layout = _declared(env)
    default = object()
    for prepared in (False, True):
        if prepared:
            layout.prepare()
        for handle in _handles(layout):
            for name in PHYSICAL:
                assert not hasattr(handle, name), name
                assert getattr(handle, name, default) is default, name
                for caught in (LayoutNotReady, LayoutError, ValueError, AttributeError):
                    with pytest.raises(caught, match=f"has no {name}: read the resolved objects from fleet.graph"):
                        getattr(handle, name)
            assert hasattr(handle, "owner") and getattr(handle, "address", default) is not default


def test_the_probes_of_the_library_treat_a_handle_as_no_entity() -> None:
    """Every place that reads `id` with a default (`is_live`, `retire`, `attach`, a collector's scope) sees a handle as
    an object with no id, and answers as it does for any object that is not an entity."""
    from simulatte.kpi import Collector

    env = Environment()
    layout = _declared(env)
    for handle in _handles(layout):
        assert env.entities.is_live(handle) is False  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="is not an entity kind"):
            env.entities.attach(handle)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="retire takes an entity"):
            env.entities.retire(handle)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="must be an attached entity"):
            Collector(handle)  # type: ignore[arg-type]
    assert env.entities.live() == ()


def test_other_attributes_and_the_protocols_of_a_handle_are_unchanged() -> None:
    import copy
    import pickle

    env = Environment()
    layout = _declared(env)
    for handle in _handles(layout):
        assert not hasattr(handle, "unknown") and not hasattr(handle, "__deepcopy__")
        with pytest.raises(AttributeError, match="unknown"):
            _ = handle.unknown  # type: ignore[union-attr]
        assert not hasattr(handle, "x")  # a physical attribute is absent to a probe, and a read raises (above)
        assert hasattr(handle, "owner") and hasattr(handle, "address")
        assert copy.copy(handle) == handle
        assert repr(handle).startswith(f"{type(handle).__name__}(owner=")
        # The owner holds the environment, which does not copy or pickle; the error depends on the interpreter
        # (TypeError on CPython 3.14, AttributeError on 3.11, PicklingError on PyPy) and is not the handle's own.
        unpicklable = (TypeError, AttributeError, pickle.PicklingError)
        with pytest.raises(unpicklable):
            copy.deepcopy(handle)
        with pytest.raises(unpicklable):
            pickle.dumps(handle)


def test_place_flow_port_and_retire_say_what_they_take_when_given_a_handle() -> None:
    """A handle is not an entity: each call says what it takes, and none advises reading `fleet.graph`."""
    env = Environment()
    layout = _declared(env)
    handles = _handles(layout)
    node = handles[1]
    calls = {
        "layout.place": lambda handle: layout.place(handle, position=Point(0, 0)),
        "layout.flow": lambda handle: layout.flow([handle]),
        "layout.port": lambda handle: layout.port(handle, "out"),
        "env.entities.retire": env.entities.retire,
    }
    for name, call in calls.items():
        for handle in handles:
            with pytest.raises((LayoutError, ValueError)) as raised:
                call(handle)
            message = str(raised.value)
            assert message.startswith(f"{name} takes ") and f"{type(handle).__name__} is a layout handle" in message
            assert "stands for a part of the layout" in message and "is not an entity" in message, message
            assert "fleet.graph" not in message, message
            assert ("layout.place('A', kind='node', ...)" in message) == (name == "layout.place" and handle is node), (
                message
            )
    assert "an entity of this environment, or the id of one with kind=..." in str(_raised(calls["layout.place"], node))
    assert "the entities of the flow, or their ids" in str(_raised(calls["layout.flow"], node))
    assert "an entity of this environment, or its id" in str(_raised(calls["layout.port"], node))
    assert "attached to this environment" in str(_raised(calls["env.entities.retire"], node))
    layout.place("A", kind="node", position=Point(0, 0))  # what the advice writes is accepted
    assert env.entities.live() == ()


def _raised(call: Any, handle: Any) -> Exception:
    try:
        call(handle)
    except (LayoutError, ValueError) as error:
        return error
    raise AssertionError("no error")


def test_an_empty_sequence_of_zones_declares_no_zones_as_an_empty_mapping_does() -> None:
    """`zones=[]` and `zones=()` are accepted, as they were before the zones were declared as a mapping: they declare
    the empty zone map of `zones={}` and give its bundle and hashes."""
    results = []
    for zones in ({}, [], ()):
        env = Environment()
        env.layout.set_network(Explicit(_graph(), zones=zones))  # type: ignore[arg-type]
        results.append(env.layout.prepare())
    first = results[0]
    assert first.bundle.schema_version == "1.1" and first.network.zones == ()
    for result in results[1:]:
        assert result.bundle.to_dict() == first.bundle.to_dict()
        assert (result.full_hash, result.physical_hash, result.layers_hash) == (
            first.full_hash,
            first.physical_hash,
            first.layers_hash,
        )
        assert result.network.zones == ()
    assert Explicit(_graph(), zones=[]).zones == Explicit(_graph(), zones=()).zones == {}  # type: ignore[arg-type]


def test_old_style_declarations_name_what_to_write() -> None:
    from simulatte.intralogistics.network import Zone

    graph = _graph()
    a, b = graph.nodes
    zone = Zone("z", (a, (a, b)), 1)
    shorthand = "zones={'z': {'members': ['A', ('A', 'B')]"
    for zones in ([zone], {"z": zone}):
        with pytest.raises(LayoutError, match="takes a mapping of zone id") as raised:
            Explicit(graph, zones=zones)  # type: ignore[arg-type]
        assert shorthand in str(raised.value)
    with pytest.raises(LayoutError, match=r"member Node\(id='A'.*: write its id 'A'"):
        Explicit(graph, zones={"z": {"members": [a]}})
    with pytest.raises(LayoutError, match=r"write the pair of ids \('A', 'B'\)"):
        Explicit(graph, zones={"z": {"members": [(a, b)]}})
    for zones in (["z"], ("z",), [{"members": ["A"]}]):  # a non-empty sequence is not a mapping either
        with pytest.raises(LayoutError, match="takes a mapping of zone id"):
            Explicit(graph, zones=zones)  # type: ignore[arg-type]
    env = Environment()
    with pytest.raises(LayoutError, match=re.escape("takes a node id: write layout.node(node.id)")):
        env.layout.node(a)  # type: ignore[arg-type]
    with pytest.raises(LayoutError, match="does not match"):
        env.layout.node(5)  # type: ignore[arg-type]  # not a node: the schema refuses it, as before
    with pytest.raises(LayoutError, match=re.escape("declare a LayoutGraph with layout.set_network(Explicit(graph))")):
        env.layout.set_network(graph)  # type: ignore[arg-type]
    with pytest.raises(LayoutError, match="network must be Lattice, Lanes or Explicit"):
        env.layout.set_network("graph")  # type: ignore[arg-type]
