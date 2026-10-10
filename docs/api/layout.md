# Layout API

`Layout` is also exported from `simulatte`. All other declarations below are exported from
`simulatte.layout`. The [runnable layout guide](../guides/layout.md) covers author files, networks,
stages, migration, diagnostics and saved presentation reuse. APIs remain unstable during active development.

## Environment and resolution

::: simulatte.layout.Layout
    options:
      members: null
      filters: ["!^_"]

`prepare()` returns a cached `ResolvedLayout` after all component and custom binders finish. Its `values`,
`auto`, `network`, `closure`, `stage_records`, `required_paths` and `diagnostics` describe the prepared model;
`bundle`, `full_hash`, `physical_hash` and `layers_hash` are the final stored identity. `network.graph` is the
single canonical runtime graph. `bundle["network"]` is null when no network was declared.

::: simulatte.layout.ResolvedLayout

::: simulatte.layout.LayoutBundle
    options:
      members: [from_dict, to_dict, check, manifest, schema_version, reusable, full_hash, physical_hash, layers_hash]

::: simulatte.layout.reresolve_presentation

::: simulatte.layout.PresentationResult

## Geometry and generators

Lengths use meters; z must equal zero. Speeds use meters per simulation time unit. Grid snapping is explicit.
Default footprints are 1×1 m (nodes: 0.1×0.1 m), rotation zero, no ports and `obstacle=False`. Servers, pre-shop
pools, warehouses, charging stations and parking areas receive auto positions. Custom kinds require explicit
placement; graph nodes use graph positions and placed AGVs use their bound node. Shop floors, routers, fleets,
jobs and orders are not placement targets. Transient jobs/orders never become static obstacles.

::: simulatte.layout.Point

::: simulatte.layout.Footprint

::: simulatte.layout.Grid
    options:
      members: [snap]

::: simulatte.layout.Port

::: simulatte.layout.Connector

::: simulatte.layout.Lattice

::: simulatte.layout.Lanes

::: simulatte.layout.Explicit

::: simulatte.layout.NetworkOverride

`Lanes.lanes` maps lane ids to records with `vertices` (`id`, `position`), optional `direction`, `enabled`, and
`speed_limit`. `Lattice.bounds` is `(xmin, ymin, xmax, ymax)` in world meters; it uses the declared grid.
`blocked` maps stable ids to rectangle records; see the packaged schema for the complete nested grammar.
A network override uses a stable `id`, logical address `target` and patch `properties`; lists in file layers
replace complete inherited lists.

## Handles and constructor unions

Handles are immutable and owner-scoped; they are not runtime nodes/graphs. Resolve through a stage/bind
context or `prepare().network`. `node_at(Address(...))` supports generated addresses; `node(id)` addresses an
explicit node, and `port(entity, name)` addresses a named port.

| Consumer parameter | Accepted type |
|---|---|
| `AGV(initial_node=...)` | `Node \| NodeRef \| PortRef \| None` |
| `Warehouse(input_bays=..., output_bays=...)` | sequences of `Node \| NodeRef \| PortRef` |
| `ChargingStation(node=...)`, `ParkingArea(node=...)` | `Node \| NodeRef \| PortRef` |
| `FleetCoordinator(graph=...)`, `ResourceBasedTrafficManager(graph=...)` | `LayoutGraph \| GraphRef` |
| `build_simple_system(layout=..., nodes=...)` | optional layout; role mapping to `NodeRef \| PortRef` |

The union describes both modes: an environment with a layout requires its own handles; an environment without
one keeps concrete objects. It does not permit mixed mode. Node-only traffic semantics are unchanged.

::: simulatte.layout.Address
    options:
      members: [to_dict, encoded_id]

::: simulatte.layout.NodeRef

::: simulatte.layout.PortRef

::: simulatte.layout.GraphRef

::: simulatte.layout.ValueRef

## Bounds and errors

`LayoutLimits` limits author bytes, nesting, placements, nodes, directed arcs and lane segment pairs before
unbounded expansion. Geometry, generated addresses and dependencies are validated during preparation, outside
the simulation hot path. INITIAL's separate 64 MiB encoded limit belongs to `TraceRecorder`.

::: simulatte.layout.LayoutLimits

::: simulatte.layout.LayoutError

::: simulatte.layout.LayoutNotReady

::: simulatte.layout.LayoutDiagnostic
