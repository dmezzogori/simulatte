# Layouts and transport networks

Every model has a layout. `env.layout` returns the environment's layout and creates it on first read; a model that
never reads it gets one at activation. The layout separates static placement, physical network generation and
simulation execution: it positions the entities, holds the transport network when the model has one, and its
resolved form is written to every trace and to the run manifest. Declare the model on the layout before activation,
then call `layout.prepare()` to inspect the result or let `env.run()` prepare it. Preparation never advances
simulation time. A layout needs only the core package:

```sh
python -m pip install simulatte
```

For an unreleased source checkout, run `python -m pip install .` from its repository root.
The examples below run without plotting, NumPy, Gymnasium or a JSON Schema package. See the
[layout API](../api/layout.md) for types, limits and handles.

## Every model has a layout

A model of servers, a shop floor or a production builder needs no layout code. Its entities get automatic
positions at activation, and its trace and manifest carry the resolved layout:

```python { .run }
from simulatte import Environment
from simulatte.server import Server

with Environment(seed=7) as env:
    Server(env=env, capacity=1, name="cutting")
    Server(env=env, capacity=1, name="packing")
    env.run(until=1)
    result = env.layout.prepare()
    assert sorted(result.bundle["resolved"]["placements"]) == ["cutting", "packing"]
    assert result.bundle["network"] is None
```

`Layout(env, file=..., limits=...)` and `Layout.from_dict(env, ...)` create the layout with a file layer or with
limits. Call them before the first read of `env.layout` and before activation; afterwards they raise `LayoutError`.
A script that needs neither writes `env.layout` and never calls the constructor. In a managed run (`simulatte run`,
Studio, experiments) the driver creates the layout and passes it to `build(env, layout, params)`; inside `build`,
`env.layout is layout`.

Every entity created before activation is a target of the layout, with an automatic placement and an entry in the
bundle. This includes jobs and orders. At most `LayoutLimits.max_placements` targets are allowed (100,000 by
default); a model that creates more before activation raises `LayoutError` at preparation, naming the limit and
`Layout(env, limits=...)`. Entities created during the run are not targets and cost nothing. The INITIAL record of a
recorded run has a size limit, `TraceRecorder`'s `max_initial_bytes` (64 MiB by default). With 99,990 jobs of two
operations on ten servers it takes about 35 MiB, of which about half is the layout bundle; jobs with longer routes
take more. The trace file is then about 37 MB, or about 63 MB when the recorder is attached before the jobs are
created, because their creation is recorded too.

A model pays nothing per event for its layout. Activation of a production model takes about 0.5 ms at 10 servers and
about 50 ms at 1,600, measured on a development machine. The bundle and its hashes are built on first read: a
recorded run builds them once, at INITIAL, and a run that nobody records and whose manifest nobody reads never builds
them.

## Declaring a transport network

A transport network is declared once on the layout with `layout.set_network(...)`. Three declarations exist:

| Declaration | The network is |
|---|---|
| `Lattice(bounds, neighbors=4)` | generated on the declared `Grid` around obstacles, with ports and connectors for the entities placed on it ([ports on a lattice](#ports-on-a-lattice)) |
| `Lanes({...})` | built from named lanes of vertices; crossings and T-junctions become nodes ([lanes](#lanes-and-logical-addresses)) |
| `Explicit(graph)` | a `LayoutGraph` of `Node` and `Arc` objects that you write by hand |

An environment has one layout and so one network; several fleets share it and its traffic controller. A model with
several disconnected regions declares their union in one call. A second `set_network` before preparation replaces
the first: the last declaration wins. After preparation it raises `LayoutError`.

The layout is finalized when it is prepared, by `layout.prepare()`, `env.activate()` or the first `env.run()`.
Declare the network before that; afterwards `set_network` raises `LayoutError`, also through `build_simple_system`,
which declares one, and through a component given a node handle or `layout.graph()` (a first fleet, for one) when no
network was declared. On a network declared before, an `AGV`, `Warehouse`, `ParkingArea` or `ChargingStation` with
handles (see [Handles](#handles)) may still be built later and resolves at once, and so may a fleet on the default
`FreeFlowController`. A fleet on a `ReservationController` must be built before activation: the controller and its
fleets register before the network is built, and a later one raises `RuntimeError`.

`Explicit(graph, *, clearance=0, blocked=None, capacity=None, capacities=None, block_length=None, zones=None)` copies
each node's id and position and, for each arc, its direction, `speed_limit`, `passing` and `length`. An arc's length
is recorded when it differs from the chord between its nodes, so a graph from `LayoutGraph.subdivide` keeps the
lengths of its pieces. `capacities` maps node ids to capacities, `zones` names members by node id or by a pair of
ids, and `block_length` splits long arcs (see [traffic fields](#traffic-fields-schema-11)). Two nodes at the same
position cannot be joined by an arc: a lift or a transfer point is a small offset between the nodes and an explicit
`length` on the arc. The offset gives the arc a direction, so with `corner_speed` or `turn_time` on the vehicle type
put it along the direction of travel: the lift then adds no turn, where an offset across the lane or against it costs
one.

The model below has two nodes, a warehouse at each, one vehicle and a `ReservationController`. Its components take
`layout.node("a")` and `layout.graph()`, handles described under [Handles](#handles):

```python { .run }
from simulatte import Environment
from simulatte.intralogistics import (
    AGV,
    AGVType,
    Arc,
    FleetCoordinator,
    LayoutGraph,
    Node,
    ReservationController,
    SKU,
    TrapezoidalProfile,
    Warehouse,
)
from simulatte.layout import Explicit

with Environment(seed=7) as env:
    a, b = Node("a", 0, 0), Node("b", 20, 0)
    layout = env.layout
    layout.set_network(Explicit(LayoutGraph([a, b], [Arc(a, b, bidirectional=True)])))

    box = SKU("box", weight=1.0, volume=0.1)
    store = Warehouse(
        env=env,
        name="store",
        input_bays=[layout.node("a")],
        output_bays=[layout.node("a")],
        n_slots=1,
        products=[box],
        initial_inventory={box: 5},
        pick_time=2.0,
        put_time=2.0,
    )
    line = Warehouse(
        env=env,
        name="line",
        input_bays=[layout.node("b")],
        output_bays=[layout.node("b")],
        n_slots=1,
        products=[box],
        pick_time=2.0,
        put_time=2.0,
    )
    cart = AGVType(
        name="cart",
        speed_profile=TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0),
        battery_capacity=100.0,
        weight_capacity=10.0,
        volume_capacity=1.0,
        length=1.0,
    )
    agv = AGV(env=env, agv_type=cart, initial_node=layout.node("a"))
    traffic = ReservationController(env=env, graph=layout.graph())
    fleet = FleetCoordinator(
        env=env,
        graph=layout.graph(),
        fleet=[agv],
        warehouses=[store, line],
        charging_stations=[],
        parking_areas=[],
        traffic_controller=traffic,
    )

    layout.prepare()
    assert agv.current_node == a and agv.current_node is not a
    order = fleet.create_order(sku=box, quantity=1, origin=store, destination=line)
    fleet.submit(order)
    env.run()
    assert order.status.name == "COMPLETED" and agv.current_node.id == "b"
```

## Handles

A component that needs a graph or a node takes a handle of the environment's layout, never the `LayoutGraph` or
`Node` itself:

- `layout.graph()` is the network's graph, for `FleetCoordinator(graph=)` and `ReservationController(graph=)`.
- `layout.node(id)` is the node of an explicit graph with that id, and `layout.node_at(Address(...))` a node of a
  generated network (a lattice cell or a lane vertex), for `AGV(initial_node=)`, `Warehouse(input_bays=,
  output_bays=)`, `ChargingStation(node=)` and `ParkingArea(node=)`.
- `layout.port(entity, name)` is a named port of a placed entity, accepted wherever a node handle is.

The layout resolves the handles when it is prepared. `AGV(initial_node=None)` and a warehouse without bays stay
valid. A concrete `LayoutGraph` or `Node` raises `LayoutError` with the code `foreign-handle` and a message that
names what to write:

- `FleetCoordinator(graph=...) takes layout.graph(); declare the graph with env.layout.set_network(Explicit(graph))`
- `Warehouse(input_bays=...) takes layout.node(id) or layout.port(...) handles, not Node objects`

A handle of another environment's layout raises as well.

## Reading physical fields after preparation

The physical fields of a component are `agv.current_node`, `fleet.graph`, `controller.graph`, a warehouse's
`input_bays` and `output_bays`, `ChargingStation.node` and `ParkingArea.node`. They hold concrete objects only after
the layout is prepared. Before that, reading one raises `LayoutNotReady`, whose message names `env.layout.prepare()`
and `env.activate()`; a script that wants to inspect them before running calls either. `repr()` of a component never
raises.

The objects are the canonical nodes and graph of the resolved network. A canonical node is equal to the node you
declared and is a different object, so compare nodes with `==`, never with `is`, or look one up with
`result.network.node(layout.node("a").address)`. The resolved graph is not equal to the graph you declared, and its
arcs are one-way: an arc declared `bidirectional=True` (the default) is two arcs, `arc_between(u, v)` and
`arc_between(v, u)`, each with `bidirectional` false and not equal to the declared arc. Test for the way back with
`graph.arc_between(v, u) is not None`. Assigning `agv.current_node` takes a canonical node of the resolved network
and nothing else.

## What is frozen

After preparation the network and the static endpoints are read-only: bays are tuples, and `fleet.graph`,
`controller.graph`, `station.node` and `area.node` cannot be assigned. An AGV moves among canonical nodes. Declaring
the network after activation raises `LayoutError`. A component with handles constructed after
preparation resolves them at once and may attach to the frozen network, but cannot change it; a
`ReservationController` constructed then raises, because it takes the network's resources at binding, and so does
a fleet on a `ReservationController` (`RuntimeError`). Graph
coordinates stay authoritative: placement positions in a file or on node-bound AGVs do not move the nodes of the
network.

Manual `env.step()` needs `env.activate()` first, for every environment; `env.run()` activates on first use.

## Auto layout and saved presentation

Static entity names and topology determine auto positions;
`flow` adds topology without invoking routing callbacks or drawing random numbers. Auto coordinates are
presentation defaults and cannot be consumed as physical inputs.

```python { .run }
from simulatte import Environment
from simulatte.layout import reresolve_presentation
from simulatte.server import Server

with Environment(seed=7) as env:
    layout = env.layout
    cutting = Server(env=env, capacity=1, name="cutting")
    packing = Server(env=env, capacity=1, name="packing")
    layout.flow([cutting, packing])
    result = layout.prepare()
    assert env.now == 0 and result.bundle["network"] is None
    view = reresolve_presentation(
        result.bundle,
        {
            "schema_version": "1.0",
            "placements": {"cutting": {"kind": "server", "visual": {"color": "blue"}}},
        },
    )
    assert not view.requires_rerun
    assert view.bundle.physical_hash == result.physical_hash
    print(result.values.data["placements"]["cutting"]["position"])
```

Saved reuse executes no model callbacks and does not regenerate a physical graph. A physical edit returns
`requires_rerun=True`, `changed_paths`, and no derived bundle. A presentation edit returns a new immutable
bundle and `original_physical_hash`; the original trace and manifest stay unchanged. The replacement file
layer is complete: omitted prior file overrides disappear. `full_hash` identifies effective presentation and
network data, `physical_hash` identifies consumed physical inputs and bindings, and `layers_hash` also
identifies original author layers and dependency provenance. These are separate from the semantic run digest.

Caller-supplied `Provenance.source` should hash model source excluding `layout.json`, so presentation edits do
not change source identity. The caller must make that file selection: SP2 does not discover, exclude or
snapshot source files automatically. Interactive Studio source snapshots remain future work.

## Code and file precedence

Resolution is **auto < code < file, per leaf**. Lists replace entire lists. An optional inherited property can
be removed with `{"$unset": true}`; required properties cannot be unset. `load()` replaces the file layer and
`save()` writes that author layer, including ineffective orphan entries, rather than flattening the result.
Unknown keys, unsupported schema versions and non-finite numbers fail validation.

```python { .run }
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from simulatte import Environment, Layout
from simulatte.layout import Point
from simulatte.server import Server

with TemporaryDirectory() as directory:
    path = Path(directory) / "layout.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "placements": {"press": {"kind": "server", "position": [4, 2, 0], "visual": {"label": "File label"}}},
            }
        )
    )
    env = Environment(seed=7)
    layout = Layout(env, file=path)
    press = Server(env=env, capacity=1, name="press")
    layout.place(press, position=Point(0, 0), visual={"color": "blue", "label": "Code label"})
    result = layout.prepare()
    placed = result.values.data["placements"]["press"]
    assert tuple(placed["position"]) == (4, 2, 0)
    assert placed["visual"] == {"color": "blue", "label": "File label"}
    layout.save(Path(directory) / "saved.json")
    env.close()
```

Use explicit stable names for file-controlled physical inputs. Generated ids permit presentation edits with
a warning; a physical file override on an unnamed entity is rejected even when its value equals the code
value. Missing targets are preserved as `orphan-placement` diagnostics. Wrong-kind placements are quarantined
whole; stale network overrides remain saved but have no effect. No nearby entity is guessed as a replacement.

## Ports on a lattice

Coordinates and footprints use finite float64 meters, with z=0. Rotation is counter-clockwise degrees around
the placement center. Ports use local coordinates and a nonzero local approach vector. A connector travels
from the port through an approach point, then to its explicit target or the nearest visible network node.
Its positive approach margin defaults to **0.01 m** and participates in physical identity.
Declare position, rotation and footprint explicitly for an obstacle or connector owner; physical network
generation cannot consume auto defaults.

```python { .run }
from simulatte import Environment
from simulatte.layout import Address, Connector, Footprint, Grid, Lattice, Point, Port
from simulatte.intralogistics.builders import build_simple_system
from simulatte.intralogistics.sku import SKU

with Environment(seed=7) as env:
    layout = env.layout
    layout.set_grid(Grid(5, 5))
    layout.set_network(Lattice((0, 0, 20, 0), neighbors=4))
    node = lambda i: layout.node_at(Address({"kind": "lattice", "i": i, "j": 0}))
    sku = SKU("part", 1, 1)
    roles = dict(
        zip(("WH_A_OUT", "N1", "N2", "N3", "WH_B_IN"), (layout.port("WH-A", "out"), node(1), node(2), node(3), node(4)))
    )
    fleet, carts, origin, destination, handle = build_simple_system(
        env,
        layout=layout,
        nodes=roles,
        products=[sku],
        initial_inventory_a={sku: 10},
    )
    layout.place(
        origin,
        position=Point(-2, 0),
        rotation=0,
        footprint=Footprint(2, 2),
        obstacle=True,
        ports={"out": Port(Point(1, 0), Point(1, 0), Connector())},
    )
    layout.require_path(layout.port(origin, "out"), node(4))
    result = layout.prepare()
    assert fleet.graph is result.network.graph
    order = fleet.create_order(sku=sku, quantity=1, origin=origin, destination=destination)
    fleet.submit(order)
    env.run()
    assert order.status.name == "COMPLETED"
    print(order.status.name)
```

`neighbors=8` adds diagonals but forbids corner cutting. Obstacle boundaries count as contact; clearance expands
the exclusion. Only a connector's initial owner-exit segment is exempt from its owner's obstacle, never from
other obstacles. There is no implicit grid snapping: call `Grid.snap(Point(...))` explicitly. Half ties round
toward the positive grid index, including negative coordinates. Exact quadrantal rotations and stored-coordinate
rational predicates give portable topology; arbitrary rotations may materialize different last bits with a
different runtime's libm, and their hashes expose those differences.

## Lanes and logical addresses

Lane vertices have stable author ids. Crossings and T-junctions become connected graph nodes; positive-length
collinear overlaps fail. Moving an intersection preserves its address when its incident parent segments stay
the same. Removing a parent can make an override stale.

```python { .run }
from simulatte import Environment
from simulatte.layout import Address, Lanes

with Environment(seed=7) as env:
    layout = env.layout
    layout.set_network(
        Lanes(
            {
                "horizontal": {
                    "vertices": [{"id": "west", "position": [-2, 0, 0]}, {"id": "east", "position": [2, 0, 0]}]
                },
                "vertical": {
                    "vertices": [{"id": "south", "position": [0, -2, 0]}, {"id": "north", "position": [0, 2, 0]}]
                },
            }
        )
    )
    west = layout.node_at(Address({"kind": "lane_vertex", "lane": "horizontal", "vertex": "west"}))
    north = layout.node_at(Address({"kind": "lane_vertex", "lane": "vertical", "vertex": "north"}))
    layout.require_path(west, north)
    result = layout.prepare()
    assert len(result.network.graph.nodes) == 5
    print(len(result.network.graph.nodes))
```

Use `NetworkOverride(id, target, properties)` for enabled/capacity/direction/speed overrides addressed by logical
identity. Direction is `both`, `forward` or `backward`; speed is meters per environment time unit. Directed
reachability is checked for every `require_path` pair. A declared network is generated even without consumers.

## Stages and physical reads

Stages may create entity counts from explicit resolved inputs, including file edits. Declare every read;
`after` supplies stage dependencies. The `declare` stage precedes user stages, and `declare` is not a valid
stage name. A stage or binder name must be a nonempty string without NUL or a lone surrogate; any other name raises
`LayoutError` where it is declared. A stage cannot read its own
outputs, future targets or auto defaults. Each stage freezes its complete output placements, including absent
leaves; later stages cannot add a property to one of those targets.

```python { .run }
from simulatte import Environment, Layout
from simulatte.layout import Footprint, Point
from simulatte.server import Server

with Environment(seed=7) as env:
    layout = Layout.from_dict(
        env,
        {
            "schema_version": "1.0",
            "placements": {
                "hall": {"kind": "server", "footprint": {"width": 9}},
                "machine-02": {"kind": "server", "visual": {"color": "green"}},
            },
        },
    )
    hall = Server(env=env, capacity=1, name="hall")
    layout.place(hall, position=Point(0, 5), footprint=Footprint(6, 2))
    width = layout.ref("placements", "hall", "footprint", "width")
    machines = []

    @layout.stage("machines", reads=[width])
    def make_machines(ctx):
        for i in range(int(ctx.physical(width) // 3)):
            machine = Server(env=ctx.env, capacity=1, name=f"machine-{i:02}")
            layout.place(machine, position=Point(i * 3, 0))
            machines.append(machine)

    result = layout.prepare()
    assert len(machines) == 3
    assert layout.physical(width) == 9
    assert result.values.data["placements"]["machine-02"]["visual"]["color"] == "green"
    print(len(machines))
```

The first declared `GraphRef` read introduces the graph barrier. After it, stages may attach components to
existing node/port handles, but cannot alter graph-producing data: grid, network, obstacles, connectors or
overrides. Bind hooks registered with `on_bind(name, callback, reads=[...])` run after validation. Use
`ctx.physical`, `ctx.graph`, and `ctx.node`; undeclared reads fail, including custom resource binders. Declared
reads themselves are physical dependencies even if the callback does not consume them. Absence, membership and
negative obstacle/connector predicates are tracked too. After preparation, `layout.physical(ref)` only reads
already covered explicit dependencies. Preparation failure is latched and runs no simulation event; create a
new environment to retry.

Physical reads return immutable canonical values: geometry uses float64, rotations are modulo 360, and
signed zero becomes positive zero. Maps iterate in the wire codec's canonical escaped-key order, recursively;
list order is preserved. The same rules apply to leaves, parent containers and explicit defaults in stages,
binders and later covered reads, so equivalent saved inputs produce the same model-visible values. Author
layers retain source, unset and list-order intent and the original angle; geometry numbers are stored as
float64 even when supplied as large JSON integers. Counts and logical indices remain wire-safe integers.

## Builders, fleets and traffic

`build_simple_system(env)` builds on `env.layout` (or on the layout passed as `layout=`) and returns a `GraphRef` as
its fifth value. With no declared network it declares its usual five-node line. With an existing network, pass the
complete `nodes` mapping of handles, as in the lattice example above, with role keys `WH_A_OUT`, `N1`, `N2`, `N3`,
`WH_B_IN`. Use distinct mappings into a declared union network to compose systems; `prefix` names their entities but
never implicitly merges or replaces a network. Its AGVs are named `vehicle-<i>` without a prefix and
`<prefix>agv-<i>` with one. An `AGV` constructed directly keeps its generated `agv-<n>` id unless `agv_id` is given.
Builder warehouses are not obstacles unless explicitly placed as such.

**Traffic on the layout graph.** A `ReservationController` built with `graph=layout.graph()` registers itself as the
layout's resource owner and reads the resolved graph, the node capacities and the zones when the layout binds; it
then holds nodes, lane segments, zones and corridors (see the [intralogistics guide](intralogistics.md#traffic-control)).
Node capacities and zones belong to the network, so they are declared with it: the `capacity` and `zones` of
`Explicit`, `Lattice` and `Lanes`, the `capacities` of `Explicit` and the `capacity` of a network override.
`ReservationController(node_capacity=)` is the fallback capacity and names no node. Lanes and lattices are straight; an
explicit arc travels its recorded `length`. Share one `ReservationController` per environment: every fleet must use
that same controller. A controller that does not control occupancy, such as the default `FreeFlowController`, needs
no registration. Any other controller that does must register a common resource owner and a named declared-read
binder with `register_resource_owner`; the layout checks its own record of registered owners, not the controller's
class. The model has no continuous collision avoidance: vehicles are kept apart only by the resources they hold.
Submit and cancel calls before activation keep their FIFO order.

## Traffic fields (schema 1.1)

Author format `1.1` adds four fields for traffic control. They are physical inputs: they enter the dependency
closure and the physical hash. Per-node capacities are not among them: the `capacity` of an explicit node is a
schema `1.0` field, which `Explicit(capacities={"N1": 2})` writes by node id. It overrides the network's default
`capacity` for that node, and an override of the node's `capacity` still applies after it. An id that is not a node of
the graph, or a capacity below 1, raises `LayoutError`.

- `passing` (boolean, default false) on lanes, explicit arcs, connectors, the lattice network and edge overrides:
  opposite traffic may pass on the edge. It sets `Arc.passing` on the resolved arcs.
- `length` (positive number) on explicit arcs: the travelled length, when it is not the straight line between the
  nodes. Without it an arc is as long as its chord. `Explicit(graph)` records an arc's length exactly when it differs
  from its chord, so a graph from `LayoutGraph.subdivide` keeps the lengths of its pieces; the two directions of a
  pair must agree, and `block_length` splits an arc by its recorded length.
- `network.block_length` (positive number): after the overrides, every enabled edge longer than it is split into
  `ceil(length / block_length)` equal pieces, so that a vehicle holds a shorter stretch of lane at a time. A
  generated node has the address kind `block_node` (the parent edge's address and the piece index `k`, counted
  from the end with the smaller id), the id `"<a>~<b>#<k>"` (`a` and `b` the ids of the edge's ends in sorted
  order) and the network's default capacity; a piece has the address kind `block_edge`. Pieces inherit the parent
  edge's effective properties, and each piece's `Arc.length` is the parent's length divided by the number of
  pieces. The count leaves out the piece that float noise in the quotient would add (a 3.3 m edge with
  `block_length` 3.3 stays whole even when its chord is 3.3000000000000007), the same rule as
  `LayoutGraph.subdivide`, so a layout and the subdivided graph of the same nodes and arcs are the same network.
  Overrides cannot target a piece or a generated node. `max_nodes` and `max_directed_arcs` apply after splitting.
- `network.zones`: a map of zone id to `{capacity, hold_back, members}`, where `members` lists element addresses
  and selectors as overrides accept them. Members are matched against the network before splitting, and a matched
  edge stands for all its pieces and the generated nodes between them, so a zone covers the same stretch with and
  without `block_length`. A member that matches nothing is a `stale-zone-member` warning. The resolved zones are
  `ResolvedNetwork.zones` (concrete nodes and node pairs), and a `ReservationController` on the layout graph takes
  them as its `zones`, ordered by zone id.
  In `Explicit(zones=...)`, a member may also be a node id (`"N3"`) or a pair of ids (`("N1", "N2")`), which is the
  segment between the two nodes whichever way the arc runs; they are converted to the addresses above, may be mixed
  with them, and name an unknown id or a pair that is not an arc in a `LayoutError`. `Lattice` and `Lanes` take
  addresses only.

Readers accept `1.0` and `1.1`; a `1.0` file with a `1.1` field is an error that names the field, and `save()`
writes `1.1` only when a `1.1` field is present. Code declarations (`Lanes`, `Lattice`, `Explicit`, `Connector`,
`NetworkOverride`) accept the same fields, and a prepared bundle is `1.1` exactly when its layers use one.

```python { .run }
from simulatte import Environment
from simulatte.layout import Lanes
from simulatte.intralogistics.reservation import ReservationController

with Environment(seed=7) as env:
    layout = env.layout
    layout.set_network(
        Lanes(
            {
                "aisle": {
                    "vertices": [{"id": "west", "position": [0, 0, 0]}, {"id": "east", "position": [20, 0, 0]}],
                    "passing": True,
                }
            },
            block_length=5,
            zones={"aisle": {"capacity": 2, "members": [{"kind": "lane", "id": "aisle"}]}},
        )
    )
    traffic = ReservationController(env=env, graph=layout.graph())
    result = layout.prepare()
    assert result.bundle.schema_version == "1.1"
    kinds = [address.to_dict()["kind"] for address in result.network.nodes]
    assert kinds == ["lane_vertex", "lane_vertex", "block_node", "block_node", "block_node"]
    graph = result.network.graph
    assert all(graph.arc_between(u, v).passing for u in graph.nodes for v in graph.neighbors(u))
    (zone,) = result.network.zones
    assert (zone.id, zone.capacity, len(zone.members)) == ("aisle", 2, 2 + 3 + 4)
    env.activate()
    assert [zone.id for zone in traffic.network.zones] == ["aisle"]
    print(kinds)
```

## Diagnostics, schema and traces

The packaged [layout JSON Schema](https://github.com/dmezzogori/simulatte/blob/main/src/simulatte/layout/schema/layout-v1.schema.json)
defines author formats `1.0` and `1.1`. Load it offline with
`importlib.resources.files("simulatte.layout.schema").joinpath("layout-v1.schema.json")`.
A `LayoutError` is a `ValueError` with a nonempty tuple of structured `diagnostics`; each diagnostic provides `code`,
`severity`, `path`, `stage`, `target`, `message` and `related_paths`. Successful results expose warnings through
`result.diagnostics`. Diagnostics follow actual stage execution order, then path, code and target; file and
unstaged diagnostics follow the stages. Saved replay retains that order. Check these for orphans, kind
mismatches and stale overrides before trusting an edit.
Required missing bindings, blocked connectors, unreachable paths, unknown fields and late edits fail preparation.

Handle errors use `missing-handle` or `foreign-handle`; connector and reachability failures use
`blocked-connector` and `unreachable-required-path`. Physical read errors use `physical-auto-read`,
`undeclared-physical-read` or `non-prior-stage-read`. Frozen inputs use `frozen-layout-input`, graph edits
after the barrier use `late-network-edit`, and preparation/activation misuse uses `lifecycle-error`.
Invalid file data uses `schema-error`; other validation failures use `invalid-layout`.
Catch `LayoutError` and inspect these codes instead of parsing its human-readable message.
Paths are tuples of intact keys (including keys containing `/`) and string list indices; an empty path
means the whole layout. Required path diagnostics refer to `("required_paths", index)` in declaration order.
The target and stage are supplied when known; errors inside a stage or binder retain its name.

Trace container 1.1 stores the immutable bundle at `INITIAL.layout` in both full and KPI traces, for every model. The
activation manifest carries schema and hashes; `env.manifest()` before activation reports
`{"schema_version": "1.0", "status": "unresolved"}`. Traces written by versions before every model had a layout
expose `Trace.layout is None`. Python `Trace.check()` checks known metadata and recomputes layout hashes;
the TypeScript reader validates known shape and exposes recorded hashes, without cryptographic verification.
Unknown future layout schemas remain inspectable but cannot be reused. INITIAL defaults to 64 MiB encoded,
independently of queue backpressure; if raising `TraceRecorder(max_initial_bytes=...)`, also raise
`ReaderLimits(max_record=...)` when opening the trace.


Native Python can record and verify a prepared layout with the core package (the browser examples above do
not start a recorder thread):

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from simulatte import Environment, Trace, TraceRecorder

with TemporaryDirectory() as directory:
    path = Path(directory) / "layout.simtrace"
    with Environment(seed=7) as env:
        layout = env.layout
        TraceRecorder(env, path)
        env.run()
    trace = Trace.open(path)
    trace.check()
    assert trace.layout == layout.prepare().bundle
```
