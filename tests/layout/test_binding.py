from simulatte import Environment, Layout
from simulatte.intralogistics.agv import AGV, AGVType
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.traffic import ResourceBasedTrafficManager
from simulatte.layout import Explicit, LayoutNotReady
from simulatte import Trace, TraceRecorder
from simulatte._wire import FrozenMap
from simulatte.entities import EntityCreated
from simulatte.intralogistics.charging import ChargingStation
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.parking import ParkingArea
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.traffic import FreeTrafficManager
from simulatte.intralogistics.warehouse import Warehouse
from simulatte.layout import Address, Grid, Lattice, LayoutError, NetworkOverride, Point
from tests.intralogistics.test_fleet_events import FleetReplay
import pytest


def vehicle(env, node, name=None):
    return AGV(
        env=env, agv_id=name, initial_node=node, agv_type=AGVType("cart", TrapezoidalProfile(2, 1, 1), 100, 100, 100)
    )


def line(env):
    layout = Layout(env)
    a, b = Node("A", 0, 0), Node("B", 2, 0)
    layout.set_network(Explicit(LayoutGraph([a, b], [Arc(a, b)])))
    return layout


def test_pending_nodes_bind_in_attachment_order_and_prepare_is_pure():
    env = Environment()
    layout = line(env)
    first, second = vehicle(env, layout.node("A"), "zed"), vehicle(env, layout.node("A"), "alpha")
    manager = ResourceBasedTrafficManager(env=env, graph=layout.graph(), node_capacity=2)
    assert first.snapshot()["node"] is None
    with pytest.raises(LayoutNotReady):
        _ = first.current_node
    with pytest.raises(LayoutNotReady):
        _ = manager.graph
    prepared = layout.prepare()
    assert layout.prepare() is prepared
    assert first.current_node is second.current_node is prepared.network.node(layout.node("A").address)
    assert manager.graph is prepared.network.graph
    binding = env.entities.node_binding(first.current_node)
    assert binding is not None
    assert binding.agvs == ["zed", "alpha"]
    assert binding.reserved_by == []
    assert env.now == 0 and not env._activated


SKU_A = SKU("stock", 1, 1)


def warehouse(env, name, node):
    return Warehouse(
        env=env,
        name=name,
        input_bays=[node],
        output_bays=[node],
        n_slots=1,
        products=[SKU_A],
        initial_inventory={SKU_A: 20},
        pick_time=0,
        put_time=0,
    )


def system(env, layout, *, name="fleet", manager=None, start="A"):
    a, b = layout.node("A"), layout.node("B")
    cart = vehicle(env, layout.node(start), "cart")
    origin, destination = warehouse(env, "origin", a), warehouse(env, "destination", b)
    station = ChargingStation(env=env, name="charger", node=b, n_slots=1)
    parking = ParkingArea(env=env, name="parking", node=a, capacity=1)
    fleet = FleetCoordinator(
        env=env,
        graph=layout.graph(),
        fleet=[cart],
        warehouses=[origin, destination],
        charging_stations=[station],
        parking_areas=[parking],
        traffic_manager=manager,
        name=name,
    )
    return fleet, cart, origin, destination, station, parking


@pytest.mark.parametrize("mode", ["off", "full", "debug"])
def test_deferred_all_components_snapshot_binding_and_recording(mode, tmp_path):
    env = Environment(debug=mode == "debug", log_level="DEBUG" if mode == "debug" else "ERROR")
    recorder = TraceRecorder(env, tmp_path / "run.trace") if mode == "full" else None
    if mode == "debug":
        FleetReplay(env)
    layout = line(env)
    manager = ResourceBasedTrafficManager(env=env, graph=layout.graph())
    fleet, cart, origin, destination, station, parking = system(env, layout, manager=manager)
    for component, field in [
        (fleet, "graph"),
        (origin, "input_bays"),
        (origin, "output_bays"),
        (station, "node"),
        (parking, "node"),
    ]:
        with pytest.raises(LayoutNotReady):
            getattr(component, field)
    assert origin.get_inventory_level(SKU_A) == 20
    result = layout.prepare()
    a, b = result.network.graph.nodes
    assert fleet.graph is manager.graph is result.network.graph
    assert cart.current_node is origin.input_bays[0] is origin.output_bays[0] is parking.node is a
    assert destination.input_bays[0] is station.node is b
    assert isinstance(origin.input_bays, tuple)
    env.activate()
    assert env.initial_state["A"]["agvs"] == ("cart",)
    assert env.initial_state["A"]["reserved_by"] == ("cart",)
    env.run()
    env.close()
    if recorder is not None:
        trace = Trace.open(tmp_path / "run.trace")
        assert trace.verify()
        trace.check()


@pytest.mark.parametrize("activated", [False, True])
def test_finalized_constructors_publish_resolved_snapshots_and_reuse_resources(activated):
    env = Environment(debug=True)
    layout = line(env)
    manager = ResourceBasedTrafficManager(env=env, graph=layout.graph(), node_capacity=3)
    original = layout.prepare()
    if activated:
        env.activate()
    events = []
    env.bus.subscribe(events.append, (EntityCreated,))
    fleet, cart, origin, destination, station, parking = system(env, layout, manager=manager)
    assert events[0].deltas.ops[0][3]["node"] == "A"
    assert fleet.graph is manager.graph is original.network.graph
    assert cart.current_node is origin.input_bays[0] is parking.node
    assert station.node is destination.output_bays[0]
    assert layout.prepare() is original
    assert "cart" not in original.values.targets
    binding = env.entities.node_binding(cart.current_node)
    assert binding is not None and binding.agvs == ["cart"]
    if not activated:
        assert manager._node_resources[cart.current_node].count == 0
        env.activate()
    assert manager._node_resources[cart.current_node].count == 1
    with pytest.raises(LayoutError):
        ResourceBasedTrafficManager(env=env, graph=layout.graph())
    with pytest.raises(LayoutError):
        layout.place(cart, position=Point(2, 3))


def test_first_resource_manager_after_finalize_rejected_and_free_allowed():
    env = Environment()
    layout = line(env)
    layout.prepare()
    with pytest.raises(LayoutError):
        ResourceBasedTrafficManager(env=env, graph=layout.graph())
    fleet, *_ = system(env, layout)
    assert isinstance(fleet._traffic_manager, FreeTrafficManager)


@pytest.mark.parametrize("endpoint", ["copy", "foreign"])
def test_noncanonical_movement_is_atomic_before_equality_or_motion_clear(endpoint):
    env = Environment(debug=True)
    layout = line(env)
    fleet, cart, *_ = system(env, layout)
    layout.prepare()
    current = cart.current_node
    bad = Node(current.id, current.x, current.y) if endpoint == "copy" else Node("foreign", 1, 3)
    events = []
    env.bus.subscribe(events.append, "**")
    before = env.entities.snapshot()
    cart.motion = FrozenMap({"test": "active"})
    for move in (
        lambda: setattr(cart, "current_node", bad),
        lambda: cart._relocate(bad),
        lambda: fleet._end_segment(cart, bad),
        lambda: env.entities.bind_node(bad),
    ):
        with pytest.raises(LayoutError, match="canonical"):
            move()
        assert cart.current_node is current and cart.motion == {"test": "active"}
        assert not events
    cart.motion = None
    assert env.entities.snapshot() == before
    cart.current_node = current
    cart.current_node = None
    cart.current_node = fleet.graph.nodes[1]
    fleet._end_segment(cart, current)
    binding = env.entities.node_binding(current)
    assert binding is not None and binding.agvs == [cart.id]


def test_static_bindings_reject_assignment_but_no_layout_keeps_mutable_api():
    env = Environment()
    layout = line(env)
    manager = ResourceBasedTrafficManager(env=env, graph=layout.graph())
    fleet, _, origin, _, station, parking = system(env, layout, manager=manager)
    layout.prepare()
    for component, field in [
        (fleet, "graph"),
        (manager, "graph"),
        (origin, "input_bays"),
        (origin, "output_bays"),
        (station, "node"),
        (parking, "node"),
    ]:
        with pytest.raises(LayoutError, match="read-only"):
            setattr(component, field, getattr(component, field))
    with pytest.raises(AttributeError):
        origin.input_bays.append(fleet.graph.nodes[0])
    plain = Environment()
    node = Node("x", 0, 0)
    wh = warehouse(plain, "wh", node)
    wh.input_bays.append(node)
    wh.input_bays = [node]
    wh.output_bays = [node]
    st = ChargingStation(env=plain, name="charger", node=node, n_slots=1)
    pa = ParkingArea(env=plain, name="parking", node=node, capacity=1)
    st.node = pa.node = node
    graph = LayoutGraph([node], [])
    tm = ResourceBasedTrafficManager(env=plain, graph=graph)
    fc = FleetCoordinator(env=plain, graph=graph, fleet=[], warehouses=[wh], charging_stations=[st])
    fc.graph = tm.graph = graph
    assert isinstance(wh.input_bays, list) and tm.graph is graph


@pytest.mark.parametrize("consumer", ["agv", "warehouse", "charger", "parking", "fleet", "traffic", "node_binding"])
def test_explicit_consumers_before_layout_and_direct_values_in_layout_rejected(consumer):
    def create(env, node, graph):
        return {
            "agv": lambda: vehicle(env, node),
            "warehouse": lambda: warehouse(env, "wh", node),
            "charger": lambda: ChargingStation(env=env, name="charger", node=node, n_slots=1),
            "parking": lambda: ParkingArea(env=env, name="parking", node=node, capacity=1),
            "fleet": lambda: FleetCoordinator(env=env, graph=graph, fleet=[], warehouses=[], charging_stations=[]),
            "traffic": lambda: ResourceBasedTrafficManager(env=env, graph=graph),
            "node_binding": lambda: env.entities.bind_node(node),
        }[consumer]()

    node = Node("a", 0, 0)
    graph = LayoutGraph([node], [])
    plain = Environment()
    create(plain, node, graph)
    with pytest.raises(LayoutError):
        Layout(plain)
    env = Environment()
    line(env)
    before = env.entities.live()
    with pytest.raises(LayoutError):
        create(env, node, graph)
    assert env.entities.live() == before


def test_foreign_refs_wrong_environment_members_and_duplicate_managers_preflight():
    env, other = Environment(), Environment()
    layout, foreign = line(env), line(other)
    for make in (
        lambda: vehicle(env, foreign.node("A")),
        lambda: warehouse(env, "bad", foreign.node("A")),
        lambda: ResourceBasedTrafficManager(env=env, graph=foreign.graph()),
        lambda: vehicle(Environment(), layout.node("A")),
        lambda: ResourceBasedTrafficManager(env=Environment(), graph=layout.graph()),
    ):
        with pytest.raises(LayoutError):
            make()
    assert env.entities.live() == ()
    manager = ResourceBasedTrafficManager(env=env, graph=layout.graph())
    with pytest.raises(LayoutError, match="shared resource owner"):
        ResourceBasedTrafficManager(env=env, graph=layout.graph())
    alien = vehicle(other, foreign.node("A"))
    cart = vehicle(env, layout.node("A"))
    for members, traffic in [
        ([alien], manager),
        ([cart, cart], manager),
        ([cart], ResourceBasedTrafficManager(env=other, graph=foreign.graph())),
    ]:
        before = env.entities.live()
        with pytest.raises(LayoutError):
            FleetCoordinator(
                env=env,
                graph=layout.graph(),
                fleet=members,
                warehouses=[],
                charging_stations=[],
                traffic_manager=traffic,
            )
        assert env.entities.live() == before and cart.fleet is None
    fleet = FleetCoordinator(
        env=env, graph=layout.graph(), fleet=[cart], warehouses=[], charging_stations=[], traffic_manager=manager
    )
    with pytest.raises(LayoutError, match="already belongs"):
        FleetCoordinator(env=env, graph=layout.graph(), fleet=[cart], warehouses=[], charging_stations=[])
    second = vehicle(env, layout.node("B"))
    shared = FleetCoordinator(
        env=env, graph=layout.graph(), fleet=[second], warehouses=[], charging_stations=[], traffic_manager=manager
    )
    env.activate()
    assert fleet.graph is shared.graph is manager.graph


def test_capacity_overrides_fallback_and_canonical_generated_aliases():
    env = Environment()
    layout = Layout(env)
    layout.set_grid(Grid(1, 1))
    layout.set_network(Lattice((0, 0, 2, 0), capacity=5))
    layout.override(NetworkOverride("unset", {"kind": "lattice", "i": 0, "j": 0}, {"capacity": {"$unset": True}}))
    manager = ResourceBasedTrafficManager(env=env, graph=layout.graph(), node_capacity=2)
    ref = layout.node_at(Address({"kind": "lattice", "i": 0, "j": 0}))
    cart = vehicle(env, ref)
    layout.prepare()
    assert manager._node_resources[cart.current_node].capacity == 2
    assert sorted(resource.capacity for resource in manager._node_resources.values()) == [2, 5, 5]


def test_custom_resource_owner_requires_named_binder_and_can_share_identity():
    class Manager(FreeTrafficManager):
        pass

    env = Environment()
    layout = line(env)
    manager, other, owner = Manager(), Manager(), object()
    with pytest.raises(LayoutError, match="custom manager"):
        FleetCoordinator(
            env=env, graph=layout.graph(), fleet=[], warehouses=[], charging_stations=[], traffic_manager=manager
        )
    layout.register_resource_owner(manager, owner=owner, graph=layout.graph(), binder="resources")
    layout.register_resource_owner(other, owner=owner, graph=layout.graph(), binder="resources")
    with pytest.raises(LayoutError, match="already registered"):
        layout.register_resource_owner(manager, owner=owner, graph=layout.graph(), binder="resources")
    with pytest.raises(LayoutError, match="shared resource owner"):
        layout.register_resource_owner(Manager(), owner=object(), graph=layout.graph(), binder="resources")
    with pytest.raises(LayoutError, match="shared resource owner"):
        layout.register_resource_owner(Manager(), owner=None, graph=layout.graph(), binder="resources")
    seen = []
    layout.on_bind(
        "resources",
        lambda ctx: seen.append((ctx.node(layout.node("A")), ctx.graph(layout.graph()))),
        reads=(layout.graph(),),
    )
    fleet = FleetCoordinator(
        env=env, graph=layout.graph(), fleet=[], warehouses=[], charging_stations=[], traffic_manager=manager
    )
    result = layout.prepare()
    assert seen == [(result.network.graph.nodes[0], fleet.graph)]
    assert "object at" not in str(result.closure.to_dict())


@pytest.mark.parametrize("mode", ["off", "full", "debug"])
def test_explicit_layout_semantic_parity_and_speed_limited_transport(mode, tmp_path):
    outcomes = []
    for use_layout in (False, True):
        env = Environment(seed=17, debug=mode == "debug")
        env.enable_digest()
        if mode == "full":
            TraceRecorder(env, tmp_path / f"{use_layout}.trace")
        if mode == "debug":
            FleetReplay(env)
        a, b = Node("A", 0, 0), Node("B", 2, 0)
        graph = LayoutGraph([a, b], [Arc(a, b, speed_limit=0.5)])
        if use_layout:
            layout = Layout(env)
            layout.set_network(Explicit(graph))
            supplied_graph, supplied_a, supplied_b = layout.graph(), layout.node("A"), layout.node("B")
        else:
            supplied_graph, supplied_a, supplied_b = graph, a, b
        manager = ResourceBasedTrafficManager(env=env, graph=supplied_graph)
        cart = vehicle(env, supplied_b, "cart")
        origin, destination = warehouse(env, "origin", supplied_a), warehouse(env, "destination", supplied_b)
        fleet = FleetCoordinator(
            env=env,
            graph=supplied_graph,
            fleet=[cart],
            warehouses=[origin, destination],
            charging_stations=[],
            traffic_manager=manager,
            name="fleet",
        )
        order = fleet.create_order(sku=SKU_A, quantity=1, origin=origin, destination=destination)
        fleet.submit(order)
        env.run()
        assert order.status.name == "COMPLETED"
        assert env.now >= 8  # four meters at a maximum .5 m/time, plus acceleration
        assert fleet.graph.distance(fleet.graph.nodes[0], fleet.graph.nodes[1]) == 2
        outcomes.append((env.initial_state, env.entities.snapshot(), env.fingerprint()))
        env.close()
    assert outcomes[0] == outcomes[1]


def test_disabled_or_unreachable_required_nodes_fail_before_any_node_publication():
    for disabled in (False, True):
        env = Environment()
        layout = line(env)
        # One-way override applies to the forward explicit edge only; disable reverse.
        layout.override(
            NetworkOverride("reverse", {"kind": "explicit_arc", "source": "B", "target": "A"}, {"enabled": False})
        )
        if disabled:
            layout.override(NetworkOverride("node", {"kind": "explicit", "id": "B"}, {"enabled": False}))
            vehicle(env, layout.node("B"))
        else:
            layout.require_path(layout.node("B"), layout.node("A"))
        with pytest.raises(LayoutError, match="disabled|unreachable"):
            layout.prepare()
        assert not any(entity.kind == "node" for entity in env.entities.live())


@pytest.mark.parametrize("consumer", ["agv", "input_bays", "output_bays"])
def test_preexisting_public_mutation_to_concrete_nodes_blocks_layout_attachment(consumer):
    env = Environment()
    concrete = Node("A", 0, 0)
    if consumer == "agv":
        cart = vehicle(env, None)
        cart.current_node = concrete
    else:
        wh = Warehouse(
            env=env, name="empty", input_bays=[], output_bays=[], n_slots=1, products=[], pick_time=0, put_time=0
        )
        getattr(wh, consumer).append(concrete)
    before = env.entities.snapshot()
    with pytest.raises(LayoutError, match="explicit graph consumers"):
        Layout(env)
    assert env.layout is None and env.entities.snapshot() == before


def test_preexisting_empty_bays_seal_and_unplaced_agv_remains_supported():
    env = Environment()
    cart = vehicle(env, None)
    wh = Warehouse(
        env=env, name="empty", input_bays=[], output_bays=[], n_slots=1, products=[], pick_time=0, put_time=0
    )
    layout = Layout(env)
    layout.prepare()
    assert cart.current_node is None
    assert wh.input_bays == wh.output_bays == ()
    for field in ("input_bays", "output_bays"):
        with pytest.raises(AttributeError):
            getattr(wh, field).append(Node("outside", 0, 0))
    plain = Environment()
    unchanged = Warehouse(
        env=plain, name="empty", input_bays=[], output_bays=[], n_slots=1, products=[], pick_time=0, put_time=0
    )
    assert isinstance(unchanged.input_bays, list)
    unchanged.input_bays.append(Node("outside", 0, 0))
    assert isinstance(unchanged.input_bays, list)


def test_preexisting_bay_alias_cannot_introduce_concrete_nodes_during_preparation():
    env = Environment()
    wh = Warehouse(
        env=env, name="empty", input_bays=[], output_bays=[], n_slots=1, products=[], pick_time=0, put_time=0
    )
    old_bays = wh.input_bays
    layout = line(env)
    assert isinstance(old_bays, list)
    old_bays.append(Node("outside", 0, 0))
    with pytest.raises(LayoutError, match="explicit graph consumers"):
        layout.prepare()
    assert not any(entity.kind == "node" for entity in env.entities.live())
