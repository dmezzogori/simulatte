from simulatte import Environment, Layout
from simulatte.intralogistics.agv import AGV, AGVType
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.reservation import ReservationController
from simulatte.layout import Explicit, LayoutNotReady
from simulatte import Trace, TraceRecorder
from simulatte._wire import FrozenMap
from simulatte.entities import EntityCreated
from simulatte.intralogistics.charging import ChargingStation
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.parking import ParkingArea
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.traffic import FreeFlowController
from simulatte.intralogistics.warehouse import Warehouse
from simulatte.layout import Address, Grid, Lattice, LayoutError, NetworkOverride, Point
from tests.intralogistics.test_fleet_events import FleetReplay
import pytest


def vehicle(env, node, name=None):
    return AGV(
        env=env,
        agv_id=name,
        initial_node=node,
        agv_type=AGVType("cart", TrapezoidalProfile(2, 1, 1), 100, 100, 100, length=0.5),
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
    manager = ReservationController(env=env, graph=layout.graph(), node_capacity=2)
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
        traffic_controller=manager,
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
    manager = ReservationController(env=env, graph=layout.graph())
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
    manager = ReservationController(env=env, graph=layout.graph(), node_capacity=3)
    original = layout.prepare()
    if activated:
        env.activate()
    events = []
    env.bus.subscribe(events.append, (EntityCreated,))
    # After activation the controller's network is built and no fleet can register with it: use free flow.
    fleet, cart, origin, destination, station, parking = system(env, layout, manager=None if activated else manager)
    assert events[0].deltas.ops[0][3]["node"] == "A"
    assert fleet.graph is manager.graph is original.network.graph
    assert cart.current_node is origin.input_bays[0] is parking.node
    assert station.node is destination.output_bays[0]
    assert layout.prepare() is original
    assert "cart" not in original.values.targets
    binding = env.entities.node_binding(cart.current_node)
    assert binding is not None and binding.agvs == ["cart"]
    if not activated:
        assert manager.view.holders(cart.current_node) == ()
        env.activate()
        assert manager.view.holders(cart.current_node) == (cart,)
        assert manager.network.capacity(cart.current_node) == 1  # the parking area's slot, before node_capacity
    with pytest.raises(LayoutError):
        ReservationController(env=env, graph=layout.graph())
    with pytest.raises(LayoutError):
        layout.place(cart, position=Point(2, 3))


def test_first_resource_manager_after_finalize_rejected_and_free_allowed():
    env = Environment()
    layout = line(env)
    layout.prepare()
    with pytest.raises(LayoutError):
        ReservationController(env=env, graph=layout.graph())
    fleet, *_ = system(env, layout)
    assert isinstance(fleet.traffic_controller, FreeFlowController)


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
        lambda: fleet._executor._end_segment(cart, bad),
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
    fleet._executor._end_segment(cart, current)
    binding = env.entities.node_binding(current)
    assert binding is not None and binding.agvs == [cart.id]


def test_static_bindings_reject_assignment():
    env = Environment()
    layout = line(env)
    manager = ReservationController(env=env, graph=layout.graph())
    fleet, _, origin, _, station, parking = system(env, layout, manager=manager)
    layout.prepare()
    with pytest.raises(AttributeError):
        manager.graph = manager.graph  # ty: ignore[invalid-assignment]  # the controller's graph never changes
    for component, field in [
        (fleet, "graph"),
        (origin, "input_bays"),
        (origin, "output_bays"),
        (station, "node"),
        (parking, "node"),
    ]:
        with pytest.raises(LayoutError, match="read-only"):
            setattr(component, field, getattr(component, field))
    with pytest.raises(AttributeError):
        origin.input_bays.append(fleet.graph.nodes[0])


@pytest.mark.parametrize("consumer", ["agv", "warehouse", "charger", "parking", "fleet", "traffic", "node_binding"])
def test_direct_values_in_layout_rejected(consumer):
    def create(env, node, graph):
        return {
            "agv": lambda: vehicle(env, node),
            "warehouse": lambda: warehouse(env, "wh", node),
            "charger": lambda: ChargingStation(env=env, name="charger", node=node, n_slots=1),
            "parking": lambda: ParkingArea(env=env, name="parking", node=node, capacity=1),
            "fleet": lambda: FleetCoordinator(env=env, graph=graph, fleet=[], warehouses=[], charging_stations=[]),
            "traffic": lambda: ReservationController(env=env, graph=graph),
            "node_binding": lambda: env.entities.bind_node(node),
        }[consumer]()

    node = Node("a", 0, 0)
    graph = LayoutGraph([node], [])
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
        lambda: ReservationController(env=env, graph=foreign.graph()),
        lambda: vehicle(Environment(), layout.node("A")),
        lambda: ReservationController(env=Environment(), graph=layout.graph()),
    ):
        with pytest.raises(LayoutError):
            make()
    assert env.entities.live() == ()
    manager = ReservationController(env=env, graph=layout.graph())
    with pytest.raises(LayoutError, match="shared resource owner"):
        ReservationController(env=env, graph=layout.graph())
    alien = vehicle(other, foreign.node("A"))
    cart = vehicle(env, layout.node("A"))
    for members, traffic in [
        ([alien], manager),
        ([cart, cart], manager),
        ([cart], ReservationController(env=other, graph=foreign.graph())),
    ]:
        before = env.entities.live()
        with pytest.raises(LayoutError):
            FleetCoordinator(
                env=env,
                graph=layout.graph(),
                fleet=members,
                warehouses=[],
                charging_stations=[],
                traffic_controller=traffic,
            )
        assert env.entities.live() == before and cart.fleet is None
    fleet = FleetCoordinator(
        env=env, graph=layout.graph(), fleet=[cart], warehouses=[], charging_stations=[], traffic_controller=manager
    )
    with pytest.raises(LayoutError, match="already belongs"):
        FleetCoordinator(env=env, graph=layout.graph(), fleet=[cart], warehouses=[], charging_stations=[])
    second = vehicle(env, layout.node("B"))
    shared = FleetCoordinator(
        env=env, graph=layout.graph(), fleet=[second], warehouses=[], charging_stations=[], traffic_controller=manager
    )
    env.activate()
    assert fleet.graph is shared.graph is manager.graph


def test_capacity_overrides_fallback_and_canonical_generated_aliases():
    env = Environment()
    layout = Layout(env)
    layout.set_grid(Grid(1, 1))
    layout.set_network(Lattice((0, 0, 2, 0), capacity=5))
    layout.override(NetworkOverride("unset", {"kind": "lattice", "i": 0, "j": 0}, {"capacity": {"$unset": True}}))
    manager = ReservationController(env=env, graph=layout.graph(), node_capacity=2)
    ref = layout.node_at(Address({"kind": "lattice", "i": 0, "j": 0}))
    cart = vehicle(env, ref)
    layout.prepare()
    assert manager.graph is layout.prepare().network.graph
    env.activate()  # the controller builds its network from the resolved graph and capacities
    network = manager.network
    assert network.graph is manager.graph
    assert network.capacity(cart.current_node) == 2
    assert sorted(network.capacity(node) for node in network.graph.nodes) == [2, 5, 5]


def test_custom_resource_owner_requires_named_binder_and_can_share_identity():
    class Manager(FreeFlowController):
        controls_occupancy = True

    env = Environment()
    layout = line(env)
    manager, other, owner = Manager(), Manager(), object()
    with pytest.raises(LayoutError, match="custom traffic controller requires") as raised:
        FleetCoordinator(
            env=env, graph=layout.graph(), fleet=[], warehouses=[], charging_stations=[], traffic_controller=manager
        )
    assert "layout.register_resource_owner(" in str(raised.value) and "layout.on_bind(" in str(raised.value)
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
        env=env, graph=layout.graph(), fleet=[], warehouses=[], charging_stations=[], traffic_controller=manager
    )
    result = layout.prepare()
    assert seen == [(result.network.graph.nodes[0], fleet.graph)]
    assert "object at" not in str(result.closure.to_dict())


@pytest.mark.parametrize("mode", ["off", "full", "debug"])
def test_speed_limited_transport_reproduces_the_fingerprint_both_modes_agreed_on(mode, tmp_path):
    """The run reproduces the fingerprint pinned at the last commit with explicit mode (e4afae4), where the same model
    built with concrete objects gave the same initial and final states and fingerprint; and it is deterministic."""
    outcomes = []
    for run in range(2):
        env = Environment(seed=17, debug=mode == "debug")
        env.enable_digest()
        if mode == "full":
            TraceRecorder(env, tmp_path / f"{run}.trace")
        if mode == "debug":
            FleetReplay(env)
        a, b = Node("A", 0, 0), Node("B", 2, 0)
        graph = LayoutGraph([a, b], [Arc(a, b, speed_limit=0.5)])
        layout = env.layout
        layout.set_network(Explicit(graph))
        supplied_graph, supplied_a, supplied_b = layout.graph(), layout.node("A"), layout.node("B")
        manager = ReservationController(env=env, graph=supplied_graph)
        cart = vehicle(env, supplied_b, "cart")
        origin, destination = warehouse(env, "origin", supplied_a), warehouse(env, "destination", supplied_b)
        fleet = FleetCoordinator(
            env=env,
            graph=supplied_graph,
            fleet=[cart],
            warehouses=[origin, destination],
            charging_stations=[],
            traffic_controller=manager,
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
    assert outcomes[1][2].digest == "489efea0a9cb922c03a4e0a87af29885cdaa2f4f6034f99e4fdbe560793349e3"


def test_a_controller_without_occupancy_needs_no_registration():
    """``_validate_fleet`` accepts any controller whose ``controls_occupancy`` is false, whatever its class."""

    class Unregistered(FreeFlowController):
        pass

    env = Environment()
    layout = line(env)
    cart = vehicle(env, layout.node("A"), "cart")
    controller = Unregistered()
    fleet = FleetCoordinator(
        env=env, graph=layout.graph(), fleet=[cart], warehouses=[], charging_stations=[], traffic_controller=controller
    )
    layout.prepare()
    assert fleet.traffic_controller is controller and controller.view.graph is fleet.graph


def test_a_reservation_controller_must_be_the_registered_owner_of_its_environment():
    env = Environment()
    layout = line(env)
    controller = ReservationController(env=env, graph=layout.graph())
    layout._resource_owner = object()  # another owner took the graph's resources
    with pytest.raises(LayoutError, match="reuse the registered traffic controller"):
        FleetCoordinator(
            env=env, graph=layout.graph(), fleet=[], warehouses=[], charging_stations=[], traffic_controller=controller
        )


def test_the_registered_resource_owner_is_accepted_whatever_its_class():
    """spec §12: ``_validate_fleet`` checks the layout's record of resource owners, not the controller's class."""

    class Owner(FreeFlowController):
        controls_occupancy = True

    env = Environment()
    layout = line(env)
    controller = Owner()
    layout._register_manager(controller)
    fleet = FleetCoordinator(
        env=env, graph=layout.graph(), fleet=[], warehouses=[], charging_stations=[], traffic_controller=controller
    )
    assert fleet.traffic_controller is controller
    other = Environment()
    stranger = ReservationController(env=other, graph=line(other).graph())  # the owner of another layout
    with pytest.raises(LayoutError, match="belongs to another environment") as raised:
        FleetCoordinator(
            env=env, graph=layout.graph(), fleet=[], warehouses=[], charging_stations=[], traffic_controller=stranger
        )
    assert "register_resource_owner" not in str(raised.value)  # its fix is not a registration


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
def test_public_mutation_to_concrete_nodes_is_rejected(consumer):
    """A component never holds a concrete node: an AGV refuses one and the bays are a tuple from construction."""
    env = Environment()
    concrete = Node("A", 0, 0)
    if consumer == "agv":
        cart = vehicle(env, None)
        before = env.entities.snapshot()
        with pytest.raises(LayoutError, match="canonical"):
            cart.current_node = concrete
    else:
        wh = Warehouse(
            env=env, name="empty", input_bays=[], output_bays=[], n_slots=1, products=[], pick_time=0, put_time=0
        )
        before = env.entities.snapshot()
        with pytest.raises(AttributeError):
            getattr(wh, consumer).append(concrete)
    assert env.entities.snapshot() == before
    env.activate()  # nothing concrete was taken: the environment's layout prepares
    assert env.initial_state == before


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
    fresh = Environment()  # bays are sealed at construction, before the environment's layout exists
    unchanged = Warehouse(
        env=fresh, name="empty", input_bays=[], output_bays=[], n_slots=1, products=[], pick_time=0, put_time=0
    )
    assert fresh._layout is None and unchanged.input_bays == ()
    with pytest.raises(AttributeError):
        unchanged.input_bays.append(Node("outside", 0, 0))


def test_preexisting_bay_alias_cannot_introduce_concrete_nodes_during_preparation():
    env = Environment()
    wh = Warehouse(
        env=env, name="empty", input_bays=[], output_bays=[], n_slots=1, products=[], pick_time=0, put_time=0
    )
    old_bays = wh.input_bays
    layout = line(env)
    assert old_bays == ()
    with pytest.raises(AttributeError):
        old_bays.append(Node("outside", 0, 0))
    layout.prepare()
    assert wh.input_bays == () and [entity.id for entity in env.entities.live() if entity.kind == "node"] == ["A", "B"]


class ConstantSpeed:
    """A speed profile with only ``travel_time``: 2 m/s, no limits."""

    def travel_time(
        self, distance: float, load_weight: float = 0.0, battery_level: float = 1.0, speed_limit: float | None = None
    ) -> float:
        return distance / 2.0


def test_an_agv_drives_a_recorded_explicit_length_and_not_the_chord():
    def run(length: float | None) -> float:
        env = Environment()
        layout = Layout(env)
        a, b = Node("A", 0, 0), Node("B", 10, 0)
        layout.set_network(Explicit(LayoutGraph([a, b], [Arc(a, b, length=length)])))
        origin, destination = (
            warehouse(env, "origin", layout.node("A")),
            warehouse(env, "destination", layout.node("B")),
        )
        kind = AGVType("cart", ConstantSpeed(), 100, 100, 100, length=0.5)
        cart = AGV(env=env, agv_id="cart", initial_node=layout.node("A"), agv_type=kind)
        fleet = FleetCoordinator(
            env=env, graph=layout.graph(), fleet=[cart], warehouses=[origin, destination], charging_stations=[]
        )
        fleet.submit(fleet.create_order(sku=SKU_A, quantity=1, origin=origin, destination=destination))
        env.run()
        env.close()
        return env.now

    assert run(None) == 5.0  # the chord, 10 m at 2 m/s
    assert run(25.0) == 12.5


def test_node_validation_accepts_the_canonical_node_and_nothing_else():
    env = Environment()
    layout = Layout(env)
    a, b, c = Node("A", 0, 0), Node("B", 2, 0), Node("C", 4, 0)
    layout.set_network(Explicit(LayoutGraph([a, b, c], [Arc(a, b), Arc(b, c)])))
    layout.override(NetworkOverride("off", {"kind": "explicit", "id": "C"}, {"enabled": False}))
    cart = vehicle(env, layout.node("A"))
    network = layout.prepare().network
    canonical = network.node(layout.node("A").address)
    other = Environment()
    elsewhere = Layout(other)
    elsewhere.set_network(Explicit(LayoutGraph([a, b], [Arc(a, b)])))
    foreign = elsewhere.prepare().network.node(elsewhere.node("A").address)
    assert foreign == canonical and foreign is not canonical
    equal = Node(canonical.id, canonical.x, canonical.y)
    assert equal == canonical and equal is not canonical
    layout._validate_node(None)
    layout._validate_node(canonical)
    cart._validate_node(None)
    cart._validate_node(canonical)
    cart.current_node = canonical
    for rejected in (equal, foreign, c, Node("unknown", 9, 9)):  # equal by value, another network, disabled, unknown
        with pytest.raises(LayoutError, match="exact canonical enabled graph node"):
            layout._validate_node(rejected)
        with pytest.raises(LayoutError, match="exact canonical enabled graph node"):
            cart._validate_node(rejected)
        with pytest.raises(LayoutError, match="exact canonical enabled graph node"):
            cart.current_node = rejected
        assert cart.current_node is canonical
    assert env.entities.node_binding(canonical).agvs == [cart.id]


def test_node_validation_names_a_missing_layout_as_before():
    env = Environment()
    cart = vehicle(env, None)
    with pytest.raises(LayoutError, match="exact canonical enabled graph node"):
        cart._validate_node(Node("A", 0, 0))


def test_node_validation_of_an_agv_whose_node_is_not_resolved_yet():
    env = Environment()
    layout = line(env)
    cart = vehicle(env, layout.node("A"))
    for action in (
        lambda: cart._validate_node(None),
        lambda: cart._validate_node(Node("A", 0, 0)),
        lambda: setattr(cart, "current_node", None),
        lambda: setattr(cart, "current_node", Node("A", 0, 0)),
    ):
        with pytest.raises(LayoutNotReady):
            action()
    layout.prepare()
    cart._validate_node(None)  # resolved: the validation passes
    assert cart.current_node is layout.prepare().network.node(layout.node("A").address)


def test_assigning_none_needs_no_layout():
    """`AGV._validate_node(None)` accepts without asking for the environment's layout, which it used to create: a model
    that only clears an AGV's node can still make its layout afterwards."""
    env = Environment()
    cart = vehicle(env, None)
    cart.current_node = None
    cart._validate_node(None)
    assert env._layout is None
    layout = Layout(env)
    assert env.layout is layout


class Unready:
    @property
    def id(self):
        raise LayoutNotReady("not ready")


def _outcome(call):
    try:
        call()
    except BaseException as error:
        return type(error), str(error)
    return None


@pytest.mark.parametrize("block", [None, 1.0])
def test_the_agv_and_the_layout_accept_the_same_nodes(block):
    """The AGV checks the canonical node on its own fast path and leaves the rest to the layout: whatever the inputs,
    the two give one outcome, so that a condition added to one and not to the other fails here."""
    import types
    from unittest import mock

    env = Environment()
    layout = Layout(env)
    a, b, c, d = Node("A", 0, 0), Node("B", 2, 0), Node("C", 4, 0), Node("D", 2, 3)
    kwargs = {} if block is None else {"block_length": block}
    layout.set_network(Explicit(LayoutGraph([a, b, c, d], [Arc(a, b), Arc(b, c), Arc(b, d)]), **kwargs))
    layout.override(NetworkOverride("off", {"kind": "explicit", "id": "C"}, {"enabled": False}))
    cart = vehicle(env, layout.node("A"))
    graph = layout.prepare().network.graph
    canonical = {node.id: node for node in graph.nodes}
    other = Environment()
    elsewhere = Layout(other)
    elsewhere.set_network(Explicit(LayoutGraph([a, b], [Arc(a, b)])))
    foreign = {node.id: node for node in elsewhere.prepare().network.graph.nodes}
    inputs = [
        canonical["A"],  # the current node
        canonical["B"],
        canonical["D"],
        Node("A", canonical["A"].x, canonical["A"].y),  # equal by value, not canonical
        Node("B", canonical["B"].x, canonical["B"].y),
        a,  # declared by the user: equal to the canonical node
        b,
        Node("A", 7.0, 7.0),  # the id of a node at another position
        c,  # disabled
        Node("C", 4.0, 0.0),
        foreign["A"],  # canonical in another environment
        foreign["B"],
        Node("unknown", 9, 9),
        None,
        "A",
        5,
        object(),
        types.SimpleNamespace(id="A"),
        types.SimpleNamespace(id=[]),
        mock.ANY,
        Node(None, 0, 0),
        layout.node("A"),  # a handle, which is for construction
        layout.node("B"),
        layout.graph(),
        Unready(),  # not a handle, its id is not ready
    ]
    generated = [node for node in graph.nodes if node.id not in ("A", "B", "C", "D")]
    assert bool(generated) == (block is not None)
    for node in generated[:2]:
        inputs += [node, Node(node.id, node.x, node.y)]
    outcomes = set()
    for node in inputs:
        by_layout = _outcome(lambda: layout._validate_node(node))
        assert _outcome(lambda: cart._validate_node(node)) == by_layout
        outcomes.add(by_layout)
        before = cart.current_node
        assert _outcome(lambda: setattr(cart, "current_node", node)) == by_layout
        if by_layout is not None:
            assert cart.current_node is before  # a refused node changes nothing
    assert None in outcomes and len(outcomes) >= 3  # accepted and refused for several reasons


CANONICAL_FIX = "fleet.graph, or env.layout.prepare().network.node(env.layout.node(id).address)"


def test_a_refused_node_names_where_the_canonical_node_comes_from():
    """A `Node` the script built, equal to the canonical node, and a handle, which is for construction, are refused by
    the AGV and by the node binding with a message that names the canonical node of the same id."""
    env = Environment()
    layout = line(env)
    cart = vehicle(env, layout.node("A"))
    canonical = layout.prepare().network.node(layout.node("B").address)
    copy = Node("B", canonical.x, canonical.y)
    for refuse in (lambda node: setattr(cart, "current_node", node), env.entities.bind_node, layout._validate_node):
        with pytest.raises(LayoutError, match="exact canonical enabled graph node") as raised:
            refuse(copy)
        assert CANONICAL_FIX in str(raised.value)
        for handle in (layout.node("B"), layout.graph()):
            with pytest.raises(LayoutError) as raised:
                refuse(handle)
            message = str(raised.value)
            assert "a layout handle" in message and "for constructing components" in message, message
            assert CANONICAL_FIX in message, message
    assert cart.current_node is layout.prepare().network.node(layout.node("A").address)
    cart.current_node = canonical
    assert cart.current_node is canonical


def test_assigning_the_node_of_an_agv_whose_node_is_pending_names_the_assignment():
    env = Environment()
    layout = line(env)
    cart = vehicle(env, layout.node("A"))
    with pytest.raises(LayoutNotReady, match="before reading it"):
        _ = cart.current_node
    for node in (None, Node("A", 0, 0)):
        with pytest.raises(LayoutNotReady) as raised:
            cart.current_node = node
        message = str(raised.value)
        assert "before assigning it" in message and "reading" not in message, message
