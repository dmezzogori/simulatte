# Layouts and transport networks

`Layout` separates static placement, physical network generation and simulation execution. Attach one layout
before activation, declare the model, then call `layout.prepare()` to inspect the result or let `env.run()`
prepare it automatically. Preparation never advances simulation time. A layout needs only the core package:

```sh
python -m pip install simulatte
```

For an unreleased source checkout, run `python -m pip install .` from its repository root.
The examples below run without plotting, NumPy, Gymnasium or a JSON Schema package. See the
[layout API](../api/layout.md) for types, limits and constructor unions.

## Production-only auto layout and saved presentation

A production model needs no transport network. Static entity names and topology determine auto positions;
`flow` adds topology without invoking routing callbacks or drawing random numbers. Auto coordinates are
presentation defaults and cannot be consumed as physical inputs.

```python { .run }
from simulatte import Environment, Layout
from simulatte.layout import reresolve_presentation
from simulatte.server import Server

with Environment(seed=7) as env:
    layout = Layout(env)
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
from simulatte import Environment, Layout
from simulatte.layout import Address, Connector, Footprint, Grid, Lattice, Point, Port
from simulatte.intralogistics.builders import build_simple_system
from simulatte.intralogistics.sku import SKU

with Environment(seed=7) as env:
    layout = Layout(env)
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
from simulatte import Environment, Layout
from simulatte.layout import Address, Lanes

with Environment(seed=7) as env:
    layout = Layout(env)
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
`after` supplies stage dependencies. The `declare` stage precedes user stages. A stage cannot read its own
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

## Migration: explicit graphs with a Layout

Wrapping an existing graph copies its node/arc definitions. All consumers in the environment then receive
handles from that one layout and resolve to one canonical graph. Mixing concrete graph/node objects with
layout handles, or handles from different layouts, fails early.

```python { .run }
from simulatte import Environment, Layout
from simulatte.layout import Explicit, LayoutNotReady
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.traffic import ResourceBasedTrafficManager

with Environment(seed=7) as env:
    a, b = Node("a", 0, 0), Node("b", 2, 0)
    layout = Layout(env)
    layout.set_network(Explicit(LayoutGraph([a, b], [Arc(a, b)])))
    traffic = ResourceBasedTrafficManager(env=env, graph=layout.graph())
    try:
        traffic.graph
    except LayoutNotReady:
        pass
    result = layout.prepare()
    assert traffic.graph is result.network.graph
    assert result.network.node(layout.node("a").address).id == "a"
```

Before binding, physical getters (`AGV.current_node`, warehouse bays, station/parking `node`, fleet/traffic
`graph`) raise `LayoutNotReady`. Declaration snapshots can still record null pending endpoints. Call
`prepare()` before inspecting physical fields, or let activation bind them. Graph coordinates remain
authoritative: file placement positions on node definitions, or on node-bound AGVs, do not move the effective
graph/node positions. Graphs and static endpoints become read-only in layout mode; an AGV may move among exact
canonical nodes. Late components may attach to the frozen network but do not modify its saved static bundle. Submit/cancel calls before activation keep their FIFO order.

`build_simple_system(..., layout=layout)` (or an attached `env.layout`) returns a `GraphRef` as its fifth value;
without a layout it returns `LayoutGraph`. With no declared network it supplies its usual five-node line.
With an existing network, pass the complete `nodes` mapping shown above, with role keys `WH_A_OUT`, `N1`, `N2`,
`N3`, `WH_B_IN`. Use distinct mappings into a declared union network to compose systems; `prefix` names their
entities but never implicitly merges or replaces a network. Layout-mode AGVs have stable `vehicle-N` names
without a prefix. Builder warehouses are not obstacles unless explicitly placed as such.

**Traffic reserves nodes only.** Geometry does not add edge/segment reservations, continuous collision avoidance
or deadlock-free guarantees at lane crossings. Share one `ResourceBasedTrafficManager` per layout graph.
Custom managers must register a common resource owner and a named declared-read binder with
`register_resource_owner`; `FreeTrafficManager` requires no resource registration.

Existing scripts remain valid without opting into layout mode:

```python { .run }
from simulatte import Environment
from simulatte.intralogistics.builders import build_simple_system
from simulatte.intralogistics.graph import LayoutGraph

with Environment(seed=7) as env:
    fleet, carts, origin, destination, graph = build_simple_system(env)
    assert isinstance(graph, LayoutGraph)
    assert fleet.graph is graph
    assert carts[0].current_node in graph.nodes
    env.run(until=1)
```

## Diagnostics, schema and traces

The packaged [layout JSON Schema](https://github.com/dmezzogori/simulatte/blob/main/src/simulatte/layout/schema/layout-v1.schema.json)
defines author format `1.0`. Load it offline with
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

Trace container 1.1 stores the immutable bundle at `INITIAL.layout` in both full and KPI traces. The activation
manifest carries schema and hashes; the earlier manifest reports unresolved layout status. Old 1.0/no-layout
traces expose `Trace.layout is None`. Python `Trace.check()` checks known metadata and recomputes layout hashes;
the TypeScript reader validates known shape and exposes recorded hashes, without cryptographic verification.
Unknown future layout schemas remain inspectable but cannot be reused. INITIAL defaults to 64 MiB encoded,
independently of queue backpressure; if raising `TraceRecorder(max_initial_bytes=...)`, also raise
`ReaderLimits(max_record=...)` when opening the trace. No viewer or interactive editor is included.


Native Python can record and verify a prepared layout with the core package (the browser examples above do
not start a recorder thread):

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from simulatte import Environment, Layout, Trace, TraceRecorder

with TemporaryDirectory() as directory:
    path = Path(directory) / "layout.simtrace"
    with Environment(seed=7) as env:
        layout = Layout(env)
        TraceRecorder(env, path)
        env.run()
    trace = Trace.open(path)
    trace.check()
    assert trace.layout == layout.prepare().bundle
```
