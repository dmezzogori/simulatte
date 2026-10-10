# SP2 Layout design

- **Status:** revision 3, after [Fable review 1](reviews/2026-10-10-sp2-review-1.md) and
  [Codex review 2](reviews/2026-10-10-sp2-review-2.md); A1–A5 approved by Davide on 2026-10-10.
  Implementation authorized with subagents on 2026-10-10.
- **Target:** Simulatte 0.14; based on released 0.13.0, commit `b1467709e88cf640155b83c037f06573fcddd875`.
- **Branch:** `feature/sp2-layout`.
- **Parent:** [approved global design](2026-10-08-studio-global-design.md), especially §4, C2 and §11;
  [decisions D4–D7, D27, D30, D33, D38, D41, D43, D46](studio-decisions.md).
- **Plan:** [SP2 implementation plan](../plans/2026-10-10-sp2-layout.md).
- **Handoff:** Cortex `capture-ce68d761-ea30-4325-995d-c5bf679ae7c4`, read with `projects/simulatte.md` on 2026-10-10.

## 1. Outcome and boundaries

Add a headless layout subsystem that gives scripts reproducible placements and an optional physical transport
network. Python declarations and a versioned file resolve together; layout stages can use physical values to
construct entities. Every layout-backed intralogistics component binds to one resolved graph before activation.
The result, its layers and its dependency closure travel in the trace for SP3 and SP4.

SP2 includes no renderer, viewer CLI, web server, editor, worker, file watcher or model decorator. SP3 owns the
viewer; SP4 owns the interactive Studio and source snapshots. Production transfers remain instantaneous unless
model code explicitly supplies transport. The accepted PyPy recording limitation in #59 is nonblocking and
does not authorize changing its budgets. Branding and packaging follow-ups are outside this change.

Python remains >=3.11, with the existing CPython 3.11–3.15 and PyPy 3.11 CI lanes. Add no runtime dependency:
geometry, ordering, JSON and hashing use the standard library. Publish a JSON Schema; a development-only
schema validator may be added to check fixtures against it. Maintain the existing full-suite 99% branch-coverage
gate and observer-invariance requirements.

## 2. Evidence from the current code

| Existing implementation | SP2 consequence |
|---|---|
| `entities.py`: `EntityRegistry.attach` emits creation using `snapshot`; `bind_node` attaches a `NodeBinding`, deduplicating by node equality | Pending handles must never leak into wire state; keep attachment immediate, record explicit-name metadata, bind nodes only after validation |
| `environment.py`: `activate` runs initializers, captures initial state, starts projection, then drains the one command queue | Add preparation before the initializer loop, not as an ordinary initializer; do not create a second command queue |
| `intralogistics/graph.py`: frozen 2D `Node`, `Arc(bidirectional, speed_limit)`, mutable internal adjacency and no validation of duplicate endpoint definitions | Keep public explicit definitions compatible; compile layout networks to a validated, frozen instance with deterministic adjacency |
| `fleet.py`: constructor binds every graph node; `_initial_placement` is already an activation initializer | Defer graph work for handles, keep initial placement after binding and finalization |
| `traffic.py`: constructor builds one resource per concrete graph node; traffic protocol reserves nodes | Defer resources until bind; segment exclusion is a separate behavioral decision (§7) |
| `warehouse.py`, `agv.py`, `charging.py`, `parking.py`: store concrete bay/start/station nodes | Store private handles during declaration and resolve them before public physical reads are valid |
| `fleet.py`, `policies.py`, `pathfinding.py`: all route with the coordinator graph; speed limits already reach motion and energy calculations | Preserve distance-based route costs and actual speed-limited traversal; bind custom policies through the same graph boundary |
| `shopfloor.py`: ordered server list; `router.py`: original `sku_routings` retained alongside bound samplers | Snapshot only approved static data; never call a routing factory, sampler or callback for auto-layout |
| `trace/writer.py`: HEADER is written on recorder construction, INITIAL at activation; only parameters/time unit/warmup are delayed today | Add layout metadata to INITIAL, after stages and binding; extend both Python and TS readers |
| `provenance.py`: caller hashes and runtime fields, no layout fields | Layout identity must be populated by core preparation, separately from caller source/input hashes |
| `intralogistics/builders.py`: `build_simple_system` returns a graph as its fifth result | Preserve that no-layout return; provide a handle as the fifth result in layout mode (§10) |

The initial draft contained no product code changes. These observations are from the fetched local source, not inferred
from the older design. Relevant regression homes include `tests/core/test_activation.py`, `test_entities.py`,
`test_provenance.py`, `test_trace_writer.py`, `test_trace_reader.py`, and intralogistics activation, graph, traffic,
motion, builders and invariance tests.

## 3. Design decisions

The defaults below resolve the five choices deferred by global §11. Davide authorized implementation of the
reviewed design on 2026-10-10, after approving A1–A5.

| Choice | Decision | Alternatives and reason |
|---|---|---|
| Auto-layout | Versioned SCC/layer layout with stable id ordering and a grid fallback (§5) | Force-directed placement adds convergence and reproducibility problems; a grid alone discards flow information |
| Arc traffic attributes | Keep node-reservation semantics in 0.14; enabled/direction/speed limits on edges, capacities on nodes (§7) | Segment reservations would change fleet traversal, cancellation and deadlock semantics; require a separate design if selected |
| Lane intersections | Every geometric crossing, endpoint touch and T-junction connects; reject positive-length collinear overlaps (§6) | Opt-in crossings would contradict C2.3; silently merging overlaps leaves conflicting direction/speed overrides ambiguous |
| Handle API | Immutable, layout-owned `NodeRef`, `PortRef`, `GraphRef`; existing constructor keywords accept either definitions or handles (§10) | String duck typing loses ownership checks; proxy Nodes expose misleading coordinates before resolution |
| Stage API | Named decorator, path-reference reads, explicit predecessors, frozen prior-stage view (§9) | Implicit callback discovery hides dependencies; a general reactive graph is unnecessary for ordered preparation |

### 3.1 Approved amendments and clarifications

Davide approved A1–A5 on 2026-10-10 (“All approved”). Recorded as D61–D65 in the
[decision log](studio-decisions.md), with the corresponding global/SP1 contracts updated.

**A1 — traffic scope (D61, amendment to C2.3/D6).** Replace “and traffic attributes (deferred to SP2)”
with “node capacity configures the existing resource-based traffic manager; SP2 introduces no segment
reservation attributes.” Disabled/directed/speed-limited arcs still ship. The existing head-on passing
limitation remains. Do not silently accept unenforced arc attributes.

**A2 — segment addresses (clarification to C2.3).** Inserting vertex V between A and B preserves the ids of
A, B and every unaffected segment; it replaces segment `(A,B)` with `(A,V)` and `(V,B)`. An override for the
removed segment becomes stale, never copied to both children. The existing phrase “changes no existing address”
must not be read as preserving an element that no longer exists.

**A3 — shipped trace extension (amendment to SP1 trace schema).** Use trace container **1.1** for all 0.14 traces,
with optional feature `layout-v1`, an optional `layout` member in INITIAL, and `layout` added to the manifest's
activation-time fields (§12). No-layout manifests store null. The major remains 1; old readers can replay state
while ignoring layout. Conformance fixtures are updated during implementation.

**A4 — layout-mode lifecycle guards (explicit refinement to C1.10/C2.7).** Manual `env.step()` requires completed
activation when a Layout is attached. No-layout stepping remains unchanged. Multiple resource-based fleets over
one layout graph must share one traffic-manager instance; no-layout scripts retain their current freedom to
create separate managers. This guards graph preparation and shared reservations; it does not add lane locks.

**Recording clarification, not a new restriction.** A recorder captures only prelude events emitted after its
attachment, as in SP1. Attach it before construction/preparation to capture the whole prelude. Attachment after
`prepare()` but before activation remains valid: INITIAL contains the complete bound state and layout; earlier
prelude events are absent, not reconstructed. Attachment after activation remains prohibited.

**A5 — layout-bound mutation rules (explicit refinement to C2.7).** Once bound, static bays/station nodes,
component graph references and graph traffic attributes are read-only. AGV movement accepts only the canonical
enabled node objects (or None), never an equal copy. No-layout mutation behavior is unchanged (§10).

No other contract change is intended. Two reviews are complete and triaged in the linked reports; revision 3
addresses the second review's three findings. Targeted verification confirmed those corrections, and its two
minor documentation findings are corrected. Davide subsequently approved all five amendments; technical review
and amendment approval do not claim implemented behavior.

## 4. Data model and public surface

The package is `simulatte.layout`; export `Layout` lazily at the top level, following the existing `Runner`
`__getattr__` pattern. `import simulatte` must not import the layout package or intralogistics graph solely for
that export. Definitions are immutable dataclasses;
patches are copied on declaration. `Layout(env, *, file=None, limits=LayoutLimits())` attaches exactly once to an
unactivated environment. The environment exposes `env.layout`, initially `None`. Reusing a Layout across
environments or attaching a second one is an error. Supplying `file` loads data now, not on each preparation.

Core types and declaration methods:

```text
Point(x: float, y: float, z: float = 0)
Footprint(width: float, depth: float, height: float = 0)
Port(position: Point, approach: Point, connector: Connector | None = None)
Connector(target: NodeRef | None = None, *, max_distance: float | None = None,
          approach_margin: float = 0.01, enabled: bool = True,
          direction: Literal["both", "forward", "backward"] = "both", speed_limit: float | None = None)
Grid(dx: float, dy: float, origin: Point = Point(0, 0, 0))
Layout.place(entity: Entity | str, *, kind: str | None = None, **placement_patch) -> None
Layout.flow(entities: Sequence[Entity | str]) -> None
Layout.set_grid(grid: Grid) -> None
Layout.set_network(spec: Lattice | Lanes | Explicit) -> None
Layout.override(override: NetworkOverride) -> None
Layout.ref(*path: str) -> ValueRef
Layout.node(node_id: str) -> NodeRef
Layout.node_at(address: Address) -> NodeRef
Layout.port(entity: Entity | str, port_name: str) -> PortRef
Layout.graph() -> GraphRef
Layout.require_path(a: NodeRef | PortRef, b: NodeRef | PortRef) -> None
Layout.stage(name: str, *, reads: Sequence[ValueRef | GraphRef], after: Sequence[str] = ()) -> decorator
Layout.on_bind(name: str, callback: Callable[[BindContext], None],
               *, reads: Sequence[ValueRef | GraphRef] = ()) -> None
Layout.load(path: str | Path) -> None
Layout.save(path: str | Path) -> None
Layout.to_dict() -> dict  # copied file layer only
Layout.from_dict(env: Environment, file_layer: Mapping, *, limits=LayoutLimits()) -> Layout  # classmethod
Layout.prepare() -> ResolvedLayout
Layout.physical(ref: ValueRef, *, default=MISSING) -> immutable value
```

`place` accepts only `position`, `rotation`, `footprint`, `ports`, `obstacle`, `visual`. A string target requires
`kind`; an object must already belong to the same environment. A definition target can be declared before its
entity is attached, but must be matched during validation. References create no placements, nodes or connectors.
`graph()` returns a handle and does not resolve, freeze or generate anything.

Positions are in meters, right-handed x east/y north, z=0 in SP2. Nonzero z is rejected, not discarded. Rotation
is counterclockwise degrees about the footprint center, normalized modulo 360. Width/depth must be positive;
height nonnegative. Coordinates, dimensions, angles, clearance and speeds must be finite; bool is not a number.
An entity's position is its rectangle center at floor level. A local point transforms by rotation then translation.
Visuals are optional `shape`, `icon`, `color`, `label` strings; no executable values or HTML interpretation.
The following table fixes static placement eligibility and presentation footprint defaults (width, depth,
height in meters). They are drawing defaults, never implicit physical dimensions.

| Entity kind | Default footprint | Static position source |
|---|---|---|
| `server`, `psp`, `warehouse`, `charging_station`, `parking_area` | (1, 1, 0) | Explicit placement or auto-layout |
| `agv` | (1, 1, 0) | Bound node/motion; no independent auto position; unplaced AGVs have no world position |
| `node` | (0.1, 0.1, 0) | Resolved graph coordinates; never auto-positioned |
| `shopfloor`, `router`, `fleet`, `job`, `order` | None | Not static placeables; excluded from auto-layout |
| Other registered custom kind | (1, 1, 0) | Opt in with `Layout.place`; otherwise excluded |

Non-placeable built-in placement targets produce a diagnostic and are not applied. Default rotation is 0,
ports an empty map, obstacle false and visuals empty; these are auto-origin values for physical-read purposes.
Built-in placeables enter the target set when attached; a custom target enters when first explicitly placed.
A named custom floor-area entity must therefore be placed in the stage that owns it before a later stage reads it.

Ports form a map of stable names to local points plus a nonzero XY approach vector (z=0). Its magnitude has no
meaning: predicates normalize it by its largest absolute component using rational arithmetic, not `hypot`.
Ports used for transport are on the footprint boundary; the approach must point outward. Only ports declaring
a `connector` participate in generation. A purely visual port has no connector and is presentation-only unless
read physically. Connectors declare `target` (a NodeRef or automatic nearest visible node), optional positive
`max_distance`, positive `approach_margin` (default 0.01 m), and edge attributes. Their two segments are described
in §6.4. Approach margin is a schema default of a declared connector and a physical dependency.

Grid spacing is positive; origin has z=0. `Grid.snap(point)` snaps x/y to the nearest index; exact half ties go
toward positive infinity, including negative coordinates. Compute q=(coordinate-origin)/spacing as an exact
rational from the stored float64 inputs, f=floor(q), then index=f+int(q-f>=1/2); do not round q+0.5 in float64.
Compute the snapped coordinate rationally and round once to float64. Snapping is opt-in at edit time.
No load, save, merge, hash or resolve operation snaps coordinates. Lane creation helpers use this snap function;
stored coordinates are authoritative even if the grid later changes.

`LayoutLimits` defaults: 16 MiB input file, nesting 64, 100,000 placements/nodes, 400,000 directed arcs and
1,000,000 lane-segment candidate pairs. Check estimates before expensive allocation; errors name the exceeded
limit. Trusted scripts can raise limits explicitly. These protect preparation, independently of trace limits.

## 5. Deterministic auto-layout

Algorithm id is `layered-v1`; no RNG or runtime callback participates. Compute exactly once after the last stage,
over all static placeable entities and declared node definitions. Jobs and transfer orders are excluded; AGVs
derive initial position from their bound node and do not receive an independent physical auto-position.
Static membership and kinds are saved in the layer bundle.

1. Snapshot `layout.flow` sequences, shop-floor server attachment order and fixed tuple/list routings from
   Router. Only built-in fixed-routing descriptions explicitly exposing static sequences qualify. Random
   job-shop descriptions and opaque/custom iterables are not executed or inspected by invoking user methods.
2. Add edges between adjacent ids, remove duplicate edges and self-edges. Keep sequence order; canonicalize
   unordered sets by UTF-8 id bytes. Unknown explicit flow targets are diagnostics and excluded from auto-layout.
3. Find strongly connected components using id-sorted adjacency; order members by id. Condense to a DAG and
   assign each component the longest predecessor-path layer. Within a layer, order components by their least id,
   and members by id. Disconnected components are ordered by their least member id. No iterative crossing optimizer.
4. Use rectangular cells sized to the maximum rotated XY footprint bounds among auto-positioned placeables plus
   a 2 m gap (width and depth maxima separately). If there are none, skip cell packing. Explicitly positioned
   entities and graph nodes do not set cell size, but their static padded bounds constrain packing. AGVs are
   dynamic and excluded from packing occupancy.
   Layers run west to east; members run south to north. Without any flow edges, id-sorted entities use a square
   grid with `ceil(sqrt(n))` columns. Start at (0,0); auto-layout does not read the physical snapping grid.
5. Explicit code/file positions stay fixed. For an unpositioned entity, try its assigned cell, then successive
   rows in that column until its padded bounds avoid already occupied rectangles. Process by the total order
   above. Explicit overlaps are reported as presentation warnings unless physical geometry makes them errors.

Defaults and auto-computed values have origin `auto`, even if numerically identical to explicit values. A stage
or binder physically reading them fails. Network generation never uses auto-layout. Explicit node coordinates
and connector-derived graph positions are physical graph data, not auto positions. An override of their visual
appearance must not move a graph node away from the coordinates used by simulation.

## 6. Network resolution

`network` is optional: a production-only layout need not generate a graph. Requesting a GraphRef without a
network is an error. Otherwise resolve exactly one network at the barrier (§9), with algorithm id `network-v1`,
even when it currently has no graph consumer. Record the presence/variant including `none` before branching:
adding a network to a production-only layout is always physical and requires a rerun.
All graph nodes/arcs, including connectors, are materialized once, not regenerated by each component.

### 6.1 Geometry, obstacles and clearance

Only explicit `obstacle=True` placements and explicit blocked rectangles block paths. AGVs cannot be obstacles.
An obstacle requires explicit position, rotation and footprint values after precedence (rotation=0 must be
provided as a code value, not taken from auto-layout). This strict rule prevents invisible auto dependencies.
Blocked rectangles have stable ids, center, width, depth and rotation. Clearance is one nonnegative radius,
default 0 in the network specification; no vehicle-specific envelope or turning radius.

The inflated obstacle is the Minkowski sum of the rotated rectangle and a radius-clearance disk. Check point
and segment distance against the rectangle directly; do not approximate inflation by increasing width/depth,
which would overblock corners. Touching the inflated boundary counts as blocked. Geometry predicates operate
on normalized finite float64 inputs. Represent stored coordinates as exact rationals for intersection/orientation,
point-in-polygon and squared point/segment-to-polygon distances, boundary classification, candidate ordering and
distance-limit comparisons. Compare squared distances, not `hypot` results. Use exact signed permutations for
rotations that are multiples of 90°. For other rotations compute the transformed vertices/port/vector once with
Python `math.sin/cos`, store the resulting float64 geometry, then run exact predicates on that geometry. The
stored convex quadrilateral is authoritative; do not repeatedly re-transform it in each predicate. Convert exact
intersection points to float64 once, retain exact intersection keys for junction coalescing, and recheck emitted
segments against the stored obstacle geometry. Distinct exact nodes that collapse to the same float64 location
with a zero-length required edge are a precision error, not a silent merge.

No global coordinate rounding, epsilon-based node merging or random jitter. Generation is deterministic for the
same materialized geometry. Arbitrary-angle transforms can differ across libm/runtime versions: emitted geometry
and therefore physical hashes expose those differences; they must not be hidden by approximate hash equality.
Cross-runtime exact-hash fixtures use quadrantal rotations or explicitly equal materialized coordinates. Exercise
arbitrary angles separately for geometric validity and repeatability within a runtime, not an unproven portable
bit-identity promise. This retains the global manifest/fingerprint mismatch policy.

Explicit networks opt out of automatic obstacle pruning: supplied nodes/arcs are checked against geometry and
invalid enabled edges fail. The sole owner-clearance exception is the explicit connector interior segment.
Plain explicit bay nodes inside an obstacle are invalid; there is no inferred owner exemption. Model an interior
bay as a PortRef connector, or leave its warehouse non-obstructing. The simple builder defaults obstacle=false.
Disabled edges remain definitions available to overrides but are not traversable. Enabling an edge never
bypasses clearance checks. A removed generator candidate is stale, not resurrected by an `enabled=True` override.

### 6.2 Lattice

`Lattice(bounds=(xmin, ymin, xmax, ymax), neighbors=4|8, clearance=0, ...)` uses the one grid. Bounds are closed;
enumerate integer indices i then j whose points lie in bounds. Remove blocked points; keep candidate segments
only when obstacle-free. For diagonals both adjacent orthogonal grid points and the diagonal segment must be
free, so a diagonal cannot cut a blocked corner. Four-neighbor is the default.

Node addresses are `{"kind":"lattice","i":i,"j":j}`. Undirected candidate edge orientation is from the smaller
index tuple to the larger; `forward` and `backward` refer to that orientation. Stable generated entity ids are
derived from tagged addresses, never enumeration order. Addresses remain separate from display labels.

### 6.3 Lanes and intersections

`Lanes` contains a map of lane ids, each with an ordered list of `{id, position}` vertices. Vertex ids are
persistent within the lane, assigned explicitly in code and minted once by a future editor. Snapping occurs
in the lane-construction helper, not while reading existing vertices. Each segment is addressed by its ordered
vertex-id pair and lane id; forward follows list order.

Split all lanes at proper crossings, endpoint touches, self-crossings and T-junctions. All roads are at z=0;
there is no overpass flag. A junction permits any outgoing edge whose direction allows it. No turn restrictions
or implied priorities. Positive-length collinear overlaps, duplicate vertices in a lane and zero-length segments
are errors with lane/vertex ids. A lane segment intersecting an obstacle is an error, rather than silent deletion.

An intersection address consists of the sorted incident source-segment addresses (or vertex addresses at an
endpoint); position is data, not identity. Multiple pairwise intersections at the exact same location coalesce
into one junction using the complete incident set. Moving a crossing keeps its id while its incident set stays
the same; changing that set creates a new junction and makes old element overrides stale. An endpoint vertex
that joins another lane maps its stable vertex address to the shared junction node.

Overrides on a source lane segment apply to every split child of that segment. Each child also has an address
of source segment plus its ordered bounding junction/vertex addresses. Removing a parent segment makes its
overrides stale as A2 specifies; an override never transfers by proximity. Segment attributes remain local at
crossings: the speed limit does not leak to the other lane.

### 6.4 Ports and connectors

Transform a port and approach vector once into world coordinates (§6.1). Use the max-component-normalized
rational vector d. Find the approach point by testing p+s*d for s=approach_margin, 2*approach_margin,
4*approach_margin, ...; select the first candidate whose squared distance to the owner's stored rectangle is
at least (clearance+approach_margin)^2. Test the candidate after its single float64 conversion as well; if that
test fails or the rounded point equals p, continue doubling. This construction chooses a point on the declared
ray before rounding, with a measurable positive gap, not an ill-defined next float outside a curved boundary.
Reject nonfinite candidates or exhaustion of 2,048 candidate steps with a precision diagnostic. Validate the
outward direction against the local rectangle boundary before transforming; a corner direction must stay outside
the rectangle's interior. There is no approach-to-port alias, even at zero clearance: the default gap is
0.01 m and participates in travel. The interior port→approach segment is exempt only from its owner's obstacle;
every other obstacle is
checked. The approach→network segment has no exemptions. This also applies when the owner is not an obstacle:
the connector must exit its drawn footprint consistently.

An explicit connector target resolves by address. Automatic connectors select the nearest visible existing
base-network node by squared Euclidean distance from the approach point, ties by canonical address, within
`max_distance` when provided (the limit applies to the outer segment). Use exact rational comparisons (§6.1).
Automatic connection does not split an arc, choose a newly created connector node, or route around obstacles;
a model that needs a detour declares a lane. Select all targets against the same base graph before adding any
connector so port iteration order cannot change the result. No valid candidate is a resolution error identifying
the port, candidate distances and blockers. Default connector direction is `both`; explicit direction is
relative to port→network orientation. Port references resolve to the port node, not the approach point.

If the final stored approach coordinates equal the selected base node's coordinates, alias the approach address
to that base node and omit the zero-length outer edge. The positive-length port→base segment retains connector
direction, enabled and speed-limit behavior; required-path checks see normal directed adjacency. Do this for
automatic and explicit targets. There is one NodeBinding and one traffic resource for the shared node, with the
base node's capacity as default. An override targeting an alias resolves to that same element and participates
in the ordinary list-order/per-property precedence; it does not create a separate capacity or reservation.
There is no outer-edge element in this case: an exact override on that absent edge is stale. This alias does not
extend the owner exemption to unrelated network edges.

Generate node ids using an injective, tagged encoding of canonical addresses that contains neither slash nor
NUL and never ends in `-<digits>`, regardless of which entity kinds are registered later. Use
`layout:` followed by lowercase hexadecimal UTF-8 canonical-JSON address bytes (sorted keys, compact separators,
no ASCII escaping); this injective encoding contains no hyphen or slash. Detect collisions with all attached
entity ids before binding; error
instead of silently renaming. Logical addresses, encoded ids and aliases are saved together. Physical file
overrides may address generator elements; generated entity ids are not user-named placement targets under D30.

### 6.5 Explicit network and network overrides

`Explicit(graph)` snapshots the graph's currently observable nodes and effective directed adjacency into
layout-owned data at declaration. The caller's graph is never mutated. Explicit ids and arc endpoint pairs
address its elements. Validate observable duplicate ids on distinct nodes, foreign endpoints and zero-length
arcs. Existing LayoutGraph construction has already deduplicated identical nodes and overwritten duplicate
directed edges; their discarded input history is unavailable and is not validated or reconstructed by Layout.
For example, two originally supplied A→B arcs with speed limits 1 then 2 produce one effective edge with limit 2.
The file schema's raw explicit node/arc lists are checked for duplicate ids and conflicting repeated directed
edges before compilation, while that information is still available. No new retained-history mechanism or
change to no-layout construction is required. Bidirectional arcs are expanded into effective directed edges
when taking the snapshot, preserving each direction's actual attributes even if earlier definitions overlap.
Nodes carry `(x,y,z=0)` in layout data; the runtime `Node` remains compatible with existing `(id,x,y)` calls.
Store reverse traversal as directed adjacency; keep `Arc(bidirectional=...)` accepted for existing scripts.

Overrides are an ordered list of records `{id, target, properties}`. A target is an element address or a
geometric selector: lattice row/column, world-axis-aligned rectangle, or lane id. Rectangles select nodes by
center and edges only when both endpoints are inside (closed bounds). Element targets match exact addresses;
empty selectors are stale. Selectors keep their geometric definition, not an expanded member list.
Code overrides run in declaration order, then file overrides in file order; later matches win per property.
The file's override list replaces the inherited code list when present, in accordance with list replacement;
code effects are then absent, not applied invisibly. `properties` uses the same unset rules as placements.

The order is base geometry → split lanes → connector targets/geometry → override application → enabled-graph
validation. Node `enabled=False` removes incident traversals; overrides cannot make geometry-invalid candidates
valid. Node capacity lives here; edge properties are enabled, direction and speed_limit. Connectivity reports
weak/strong components and unreachable required handles; disconnected optional regions are warnings. Every
referenced node must be enabled. `layout.require_path(a, b)` declares a directed reachability requirement,
validated after overrides; do not assume all warehouses must reach all other warehouses.

## 7. Traffic semantics (approved A1 / D61)

Layout generation controls traversable adjacency. `enabled=False` removes an edge from routing; `direction`
compiles to one or two directed traversals; positive `speed_limit` constrains the existing speed-profile call.
The shortest path remains distance-based, not travel-time-based. Do not change dispatch policies implicitly.

Use the existing node traffic manager. Add optional per-node positive integer `capacity` to network definitions
and overrides. At bind, effective capacity is resolved-node capacity when provided, else the manager's existing
`node_capacity` (default 1). `FreeTrafficManager` keeps ignoring capacity and reservations, as explicitly selected
by the model. Capacity is still physical input; switching managers is source/model behavior, not a layout edit.

There are **no per-arc reservation resources, headway, priority, conflict groups or lane locks** in SP2.
Unknown traffic fields are schema errors. Two vehicles can still pass in opposite directions along an arc when
the node manager allows it; direction and clearance do not promise collision-free continuous vehicle motion.
This is a precise limit of the existing traffic abstraction, not a new simulation claim.

Multiple fleets sharing a resolved graph and using resource traffic must share the same manager instance.
Reject multiple resource managers over that layout graph in one environment; separate resource pools would
permit conflicting reservations. Free-traffic fleets may share the graph. Custom managers register a binder
and explicitly declare their shared resource owner; do not guess ownership from private attributes.

## 8. Layers, schema and dependency identity

### 8.1 File shape and merge

Publish `src/simulatte/layout/schema/layout-v1.schema.json` in wheel/sdist and link it from user docs. File
`schema_version` is `"1.0"`, independent of package and trace versions. Unsupported versions fail with an
upgrade message; no silent migrations. Validate strict field names/types, duplicate JSON keys, resource limits,
finite numbers, id syntax, and semantic constraints. Optional `$schema` is informational and is never fetched.

```json
{
  "schema_version": "1.0",
  "grid": {"dx": 1.0, "dy": 1.0, "origin": [0.0, 0.0, 0.0]},
  "network": {"clearance": 0.4},
  "placements": {
    "WH-A": {
      "kind": "warehouse",
      "position": [2.0, 4.0, 0.0],
      "visual": {"color": "#5b7c99"}
    }
  }
}
```

The example is a patch over code declarations, not a complete standalone network. `grid`, `network` and
`placements` may be omitted. Placements require an expected kind; grid/network patches inherit their type from
code or explicitly specify it. Maps merge recursively per leaf; a point is an atomic three-number list and lists
replace completely. A connector target is a complete typed address and replaces atomically, so changing its
address family cannot retain incompatible fields from the previous target. `{"$unset":true}` is a leaf sentinel
removing an inherited optional value; it is invalid
for required fields. Null is not an alternate spelling of unset. Unsetting a required input fails only when the
resolved model requires it, with its source path. Reset-to-inherited deletes the override from the file.

Network lanes and blocked areas are maps keyed by stable id; a lane's vertices and the overrides collection are
lists. Changing generator type retains only fields legal for the selected type; explicitly supplied incompatible
fields are errors, and inherited fields of the previous variant do not leak into the new one. Code declarations
within a stage merge in call order; a target freezes as a whole at the end of its owning stage, including
absences. A later stage cannot extend that target with a previously missing property (§9.1).

`Layout.load(path)` is allowed only before preparation and replaces the file layer; `save(path)` writes only
that layer, preserves orphan/stale entries, sorts keys and atomically replaces the destination. It never writes
the resolved layout back over author intent. Provide `to_dict()`/`from_dict()` for pure-data round trips.
Editor revision concurrency and source-bundle exclusion enforcement remain SP4 responsibilities; SP2 records
layout identity separately and documents that caller provenance must exclude layout.json from source identity.

### 8.2 Provenance of values and physical dependency closure

Every resolved leaf records its source: auto, code stage id, or file, plus whether it is a schema default.
Schema defaults explicitly defined for a declared physical object (clearance=0, enabled=True, both directions
for lattice/lane/connector segments) are valid physical inputs. Explicit effective arc records default to
forward only, preserving §6.5's directed adjacency. Auto placement defaults are not. Distinguish absent, unset
and present values.

Preparation always records network presence/variant (including none). Grid presence is also recorded; its
spacing/origin are physical only when used by lattice generation or a model read. Network generation records
the full transitive dependency of what it uses: generator variant; grid only for
lattice; obstacle selection predicates; blocked-area/obstacle/lane membership; obstacle transforms; participating
port membership, transforms and connector settings; explicit definitions; overrides including targets and
ordering; capacity and direction defaults. Non-obstacle geometry not read by a connector remains presentation.
Adding an obstacle or connector must invalidate reuse even if none existed in the previous run.

`ValueRef` is a tuple path, not a string parsed by dots, so ids may contain punctuation. `physical(ref)` records
the path, value and source eligibility. A mapping/list read records membership, order where relevant and all
returned descendants. A missing optional read with a declared default records an absence dependency, so a later
file addition invalidates reuse. Selection APIs use named schema predicates, not arbitrary callbacks; record
both the candidate collection membership and predicate leaves. There is no untracked mutable resolved view.

Bindings add their declared property reads and the resolved node/port/graph mapping to the closure. When a
handle supplies an AGV initial node or warehouse bay, its endpoint and connectivity are physical. Finalization
computes the union only after every binder succeeds. Later physical reads may return only already-covered
properties; a new dependency is an error. Presentation inspection uses `ResolvedLayout` data for display/export;
using it to control simulation is unsupported, as D33 already states.

Physical file overrides on auto-named entities fail (including ones classified physical only by a stage/binder).
Validate again against the completed closure at finalization, so a bind-time read cannot bypass D30. Add
`EntityRegistry.is_named(entity_id)`; do not infer naming from regex alone. Presentation overrides on generated
ids emit a structured warning. Generator-element addresses are definitions and use their address rules instead.

### 8.3 Hashes and presentation-only re-resolution

Hash algorithm `layout-hash-v1`: BLAKE2b-256 over a domain tag and canonical `_wire.canonical_pack` data. Geometry
numbers normalize to float64 (`1` equals `1.0`, `-0.0` becomes `0.0`); counts/indices remain integers within the
wire safe range; rotations normalize modulo 360. Preserve exact float64 values, Unicode and significant list
order; sort map keys using the existing canonical codec. No NaN/infinity in layout data. JSON writes use Python's
shortest round-trip float spelling and sorted keys; whitespace never affects a hash.

- `full_hash`: schema/algorithm ids, resolved placements/visuals/grid/network, entity-kind table and graph aliases.
- `physical_hash`: schema/algorithm ids, normalized physical values (including structural/negative dependencies),
  enabled graph and handle bindings. Include explicit/default/auto eligibility where it affects validity; do not
  include the arbitrary distinction between code and file when both provide the same eligible value.
- `layers_hash`: stored author layers and stage/topology records, including ignored overrides; identifies exact
  input provenance separately from effective full/physical identity. Orphan edits change this, not physical state.

Store the normalized dependency descriptors as well as their values, and hash the descriptors in physical_hash.
This is deliberately conservative: equal effective graph geometry does not erase a dependency on how the model
selected its inputs. Equivalent JSON formatting does not change any hash.

`reresolve_presentation(bundle, file_patch) -> PresentationResult` accepts stored data only. Reapply precedence,
compare every recorded dependency query (including absence, membership, predicates and provenance eligibility)
and graph-affecting structural predicates, then compute auto-layout/visual results using the saved static entity
set and algorithm version. Return `requires_rerun` plus changed dependency paths if anything physical changed.
Evaluate stage absence/default queries against their saved frozen explicit-layer view, before auto fills a
presentation gap; an auto default must neither satisfy an explicit physical read nor turn an unchanged absence
into a false change. New file values at such paths still change that explicit-layer view and require a rerun.
Never execute stages, binders, routing callbacks or RNG. New unrecognized input fields fail schema validation;
new physical membership cannot be waved through because the previous set was empty. Pure presentation changes
retain physical_hash, semantic digest and KPIs. Changed source/parameters are beyond this API and require a run.

## 9. Stages and lifecycle

### 9.1 Stage API

```text
Layout.stage(name: str, *, reads: Sequence[ValueRef | GraphRef],
             after: Sequence[str] = ()) -> decorator(Callable[[StageContext], None])
StageContext.env: Environment
StageContext.layout: Layout
StageContext.physical(ref: ValueRef, *, default=MISSING) -> immutable value
StageContext.graph(ref: GraphRef) -> read-only LayoutGraph
Layout.on_bind(name: str, callback: Callable[[BindContext], None],
               *, reads: Sequence[ValueRef | GraphRef] = ()) -> None
```

Stage 0 consists of all declarations made before prepare. Stage ids are unique; `after` may reference any
declared stage id or `"declare"` for stage 0. Use a stable topological sort: choose the lowest registration index
among ready stages. Unknown predecessors and cycles fail before running any callback, with the cycle chain.
Registering new stages while resolving is prohibited. Every stage runs exactly once.

`reads` grants access to frozen prior-stage paths; reading an undeclared path, same-stage output, future-stage
output or an auto value fails. Declaring a parent path grants reads of its children and conservatively tracks
its complete value. Stages may attach new entities, declare placements for their own new targets, add topology
and queue commands. A target is owned by the stage that first attaches or explicitly declares it; its complete
placement freezes when that stage finishes, including absent leaves and collection membership, after applying
the file patch once. Later stages cannot add, remove or change any leaf of that target, even one not previously
present; attempting it raises. Auto-layout may fill presentation-only gaps after the last stage, but never
changes a frozen explicit value or makes an absent physical read eligible.

Grid and the complete generator specification (including lanes/blocked areas/explicit definitions) freeze in
their declaring stage. A later stage may declare them if absent and unread, but not replace a frozen specification.
New placement obstacles/connectors and `Layout.override` records may still be declared up to the barrier.
An earlier physical read of a collection or its selection predicates freezes that collection's membership and
selected values, including absences: a later stage introducing a matching member fails even when that member is
a new target. A read of all placements similarly precludes adding more placement targets. Thus a dependency
snapshot cannot disagree with the final value. Default values for absent optional reads must be explicit in code.
Read declarations and actual dependencies are both saved; unused declared reads are conservatively physical.
`Layout.physical` delegates to the current stage/bind context during preparation and checks its read grants;
calling it during ordinary declaration is an error. After finalization it checks the frozen closure instead.

Example: stage 0 explicitly places named `hall`; a `machines` stage reads its footprint width and attaches
`machine-01`…`machine-N` with N computed from that width; another stage declared `after=("machines",)` may read
the resulting explicit placements. A file edit reducing width changes N; file entries for removed machines
become orphans. Auto positions cannot be the source of N. Named RNG draws use `ctx.env.rng("layout/<stage>/...")`;
stage callbacks are trusted model code and unmanaged external randomness is subject to existing provenance limits.

### 9.2 Ordered preparation

`Layout.prepare()` and `env.activate()` share one preparation implementation. `prepare` stops after finalization;
it does not run initializers or drain commands. Successful calls are idempotent and return the same resolved
object. `env.run` retains its existing implicit activation. `env.step` with an attached unprepared/unactivated
layout raises and points to `env.activate()`; no-layout manual stepping retains SP1 behavior.

1. **Declare:** reject explicit graph components if a Layout is attached (and reject attaching a Layout if
   explicit graph consumers already exist). Constructors attach entities with snapshots valid in the prelude.
2. **Resolve stage 0 and ordered stages:** apply file precedence to the currently declared targets, freeze each
   stage's outputs, then run the next. File entries for potential later targets remain pending, not orphans yet.
   A file entry alone never declares an entity, obstacle or connector. Only matching attached entities or
   declared definitions enter network generation; later matching a pending graph-producing patch after the
   barrier is a late-network-edit error, not permission to change the graph.
3. **Network barrier:** immediately before the first stage declaring a GraphRef read, or after all stages if none
   does. Freeze every graph-producing input and generate the graph once. A later stage may create a warehouse
   pointing at an existing node, but not introduce a port connector/obstacle/arc or alter capacities/attributes.
   Track negative dependencies so `obstacle=False` becoming true is also blocked. Non-network presentation
   declarations and entities remain possible after the barrier.
4. **Auto-layout and validate:** after all stages, resolve presentation for the complete static set; match pending
   placements against entities and graph definitions, check kinds, handles, geometry and required paths. Orphans
   and stale overrides are warnings and excluded, but a missing required handle is an error.
5. **Bind:** validate all handle ownership first; bind node entities in id order, then resolve component endpoint
   handles in entity attachment order, then bind traffic resources and fleet graph consumers, then registered
   custom binders in declaration
   order. Canonical graph Nodes are installed before initial placement. Binders may read declared physical data
   and configure their component, but cannot attach new entities, mutate layout or schedule simulation events.
   Built-in node attachment is the coordinator's explicit exception to the no-new-entities rule for binders.
   Emit ordinary prelude node/placement transitions where observer-visible state changes; never serialize refs.
   `NodeBinding` inspects the private nullable `AGV._current_node`, never the public unresolved getter. Pending
   AGVs initially contribute no node occupants; resolving them in attachment order appends each exactly once
   through the existing relocation/event path. Existing occupants are not reordered. This preserves initial
   `node.agvs` ordering and semantic-state parity with an equivalent explicit model.
6. **Finalize:** validate D30 against the full closure, compute hashes, save immutable layers and bindings, freeze
   the complete layout. Build the environment's layout manifest fields before any projection listener reads them.
7. **Activate:** existing activation initializers, initial snapshot/projection, global command queue, ordinary
   simulation. The SP1 prelude stays inspection-only; unresolved metadata never becomes semantic initial state.

Stages may schedule processes in the same way model construction does, but cannot run/step the simulation or
advance time. Process bodies execute only after activation. Binding blocks scheduling using the existing
temporary scheduling guard; activation initializers retain their stricter SP1 behavior. Direct graph-dependent
queries before bind raise `LayoutNotReady` with the component, handle and current phase. Pure nonphysical state
queries, such as initial warehouse inventory, remain available. `submit`/`cancel` retain their existing queue
ordering and pending status; there is no lazy resolution inside a command.

Reentrant preparation, activation from a stage, and lifecycle mutation after finalization fail. A failure latches
the preparation attempt as failed; no INITIAL/ready state or queued command is published/executed. No rollback
of user stage side effects is promised; retry with a fresh environment. Timeouts/run calls cannot bypass failure.

After finalization (including before activation), constructors resolve refs synchronously against the frozen
graph before publishing their entity-created snapshot; no pending handles reach runtime entities. Validate the
refs, dependency coverage and traffic-manager owner before attachment. After activation, existing runtime entity
creation remains supported when it uses graph elements and physical dependencies already bound (for example
an AGV at an existing named node). It cannot add physical placements,
graph definitions or new dependencies. Runtime entities without static layout use kind defaults/trace location;
the saved static layout is not silently rewritten. No-layout dynamic construction retains existing behavior.
New entities may consume any node/arc already covered by the finalized graph dependency; their runtime ids are
recorded as events, not added to the static binding hash. New custom property reads remain subject to closure
coverage. A new fleet must reuse the registered resource manager; if none was registered before finalization,
creating a resource manager afterward is rejected (use FreeTrafficManager or declare the manager earlier).

## 10. Intralogistics handles and compatibility

References contain an opaque owner token plus a serializable logical address; comparing refs from different
layouts never makes them interchangeable. They expose no coordinates and do not subclass Node/LayoutGraph.
`NodeRef` may address an explicit node id or a generated logical address via `layout.node_at(address)`;
`PortRef` names an entity/port; `GraphRef` identifies the owner's sole network. Dangling refs fail at validation.

Extend existing constructor keywords, retaining keyword-only structure:

| Consumer | Accepted declaration input | Bound result |
|---|---|---|
| `AGV(initial_node=...)` | `Node | NodeRef | PortRef | None` | canonical Node or None |
| `Warehouse(input_bays=..., output_bays=...)` | homogeneous sequences of Nodes or refs | immutable tuples in layout mode; existing lists without Layout |
| `ChargingStation(node=...)`, `ParkingArea(node=...)` | Node or node/port ref | canonical Node |
| `FleetCoordinator(graph=...)` | LayoutGraph or GraphRef | the one graph instance |
| `ResourceBasedTrafficManager(graph=...)` | LayoutGraph or GraphRef | that same graph and resources |

Do not store a handle in `AGV._current_node`, whose snapshot expects `.id` of a concrete node. Keep a separate
pending ref; the prelude snapshot uses node=null until binding. Public `current_node` must distinguish unbound
from intentionally unplaced: an unbound physical query raises, snapshot uses the private nullable field.
Binding updates concrete state while still in the prelude. Warehouse bay and station getters similarly reject
unresolved access. All handle references must belong to the component environment.

**After binding, physical mutation has a defined boundary (A5).** In layout mode `AGV.current_node` remains a
state-changing setter for movement, but accepts only None or the exact enabled Node instance in the bound graph.
Reject foreign nodes and equal-looking copies before the existing equality fast path or any state/event change.
Construction-time refs resolve to canonical nodes; callers moving an AGV later can obtain canonical nodes from
its already-bound graph. Internal fleet travel and built-in relocation paths enforce the same validation.

Layout-mode warehouse input/output bays are immutable tuples. Replacing those attributes, mutating their
contents, assigning a station/parking node, or replacing a fleet/traffic manager graph after binding raises.
The resolved graph's nodes, adjacency and attributes expose read-only views. Static bindings and graph metadata
therefore cannot diverge from saved hashes through supported public operations; deliberate private-field
tampering is unsupported. Bind-time installation uses internal methods before sealing. No-layout public lists,
setters and construction behavior are preserved. Runtime AGV locations change through events and are not static
bay/graph bindings; their movement does not rewrite the layout hash.

Within a layout-backed environment, directly supplied Nodes/graphs to these constructors are errors, including
an equal-looking copy of the resolved graph. Declare an explicit graph through `layout.set_network(Explicit(g))`
and pass refs. All routing, traffic checks, distance calls, bays, initial nodes, charging and parking consume the
same canonical object. Runtime custom policies receive concrete graph arguments, not handles. `BindContext`
exposes `node(ref)`, `graph(ref)` and tracked `physical(ref)` for custom preparation.

Without a Layout, preserve explicit graph behavior, insertion order, constructors, dynamic attachment and event
ordering. Do not canonical-sort existing scripts' graphs or force them through a hidden Layout. Add an
environment graph-use registration hook at component attachment so mixed mode is caught even when components
are constructed before the attempted Layout attachment. Merely constructing an environment-free graph does not
count as using it in an environment.

`build_simple_system(env, *, layout=None, ...)` gains layout mode. Omitted `layout` uses `env.layout` if attached;
if both are absent, keep the current explicit path and exact five-tuple return. In layout mode its fifth element
is GraphRef (type overloads document the difference). The builder declares the same five-node line as an
explicit network through Layout and supplies handles everywhere. With a nonempty prefix, keep the existing
`f"{prefix}agv-{i}"` names in both modes. Without a prefix, layout mode uses `vehicle-0`, `vehicle-1`, ... (explicit
names); no-layout mode retains generated `agv-0`, ... ids. Equal-id parity fixtures use a nonempty prefix.
If the caller has already declared a network, the
builder requires compatible named node refs instead of overwriting it. Add a keyword `nodes` mapping of the five
role names `WH_A_OUT`, `N1`, `N2`, `N3`, `WH_B_IN` to refs for this case (these are unprefixed role keys, not target
ids). Missing or extra keys fail. Multiple systems can share one network through that mapping and
distinct prefixes. Builder default physical declarations are code layer values, editable by the file.
For two independent prefixed lines in one layout, the caller declares a union explicit network first and passes
separate role mappings; the builder does not silently union networks. Plain graph bay nodes have no owner
clearance exemption, and the builder declares warehouses non-obstructing; use explicit port connectors for an
obstructing warehouse. This is the layout-mode composition recipe, not a change to no-layout builders.

## 11. Diagnostics and preservation of user input

Diagnostics are immutable records `{code, severity, path, stage, target, message, related_paths}`. Sort them by
stage order, path, code and target for deterministic reports. Codes distinguish schema errors, kind mismatches,
orphan placements, stale overrides, generated-id warnings, missing handles, blocked connectors, unreachable
required paths, physical-auto reads, late graph edits and lifecycle failures.

An orphan is a placement whose target is absent after all stages and definition declaration. A kind mismatch
quarantines the entire placement and reports it as stale/wrong-kind; do not apply a matching-looking property.
Neither is attached to a nearby entity or a new entity with a guessed identity. Missing required bindings still
fail even if their cause is an ignored override. Schema-invalid data fails rather than masquerading as an orphan.

Orphan/stale entries are preserved verbatim as data in the file layer and layer bundle, alongside diagnostics.
Their ineffective properties are absent from resolved hashes. No automatic deletion, reassignment or save occurs
during preparation. Stage-created ids are judged only after the final stage; graph definitions are valid targets
before NodeBinding entities exist. Transient jobs/orders cannot be static placement targets.

## 12. Trace and manifest integration

The layout bundle contains `schema_version`, algorithm ids, stage declarations (data only), static topology,
entity-kind/name table, code and file layers, dependency descriptors/values, resolved placements and network,
logical-address aliases, component bindings, diagnostics and all three hashes. Never serialize callbacks,
environment pointers or owner tokens. Reconstruct handles for inspection as addresses, never executable proxies.

Under A3, add optional `INITIAL.layout` and `Trace.layout`/TypeScript `trace.layout` returning the immutable
bundle or null. INITIAL's manifest gains `layout: {schema_version, full_hash, physical_hash, layers_hash}`;
no-layout runs store null. Before preparation, `env.manifest` reports layout as unresolved, not a final hash.
Include layout among `ACTIVATION_MANIFEST_FIELDS`; do not cache pre-bind hashes in HEADER. A failed pre-activation
trace can have HEADER/PRELUDE and a failed footer but no INITIAL/layout result. Both `kpi` and `full` retain layout.

Use the existing record size/decoding limits: default INITIAL payload maximum is 64 MiB, matching
`ReaderLimits.max_record`. The writer gains `max_initial_bytes` with that default; callers raising it must also
raise reader limits explicitly. Check the encoded INITIAL size before publishing it and fail with
a clear layout-size diagnostic. Do not silently drop stored layers or introduce an unbounded parallel metadata
file. Layout does not change CHUNK, delta operations, event cursor semantics or the semantic digest algorithm.
NodeBinding can keep x/y state; z=0 and footprint/ports belong to the bundle. Old 1.0 traces return null layout;
1.0 readers may ignore the extension in 1.1 while replaying unchanged state. Both new readers validate the shape
of known layout-v1 metadata; Python `Trace.check()` additionally recomputes its hashes and checks agreement with
the manifest. TypeScript exposes these hashes as recorded values, without a new cryptographic verifier (its
current reader also omits SP1 `check`/`verify`). Shared Python-generated fixtures verify TS data fidelity.
Unsupported layout versions remain inspectable as opaque metadata but cannot be used for presentation
re-resolution. Malformed known layout-v1 data is corruption, not absence.

Presentation edits to stored layouts do not modify the original trace or its manifest. Return a derived view
with its new full/layers hashes and reference to the original physical hash; SP4 will own its storage/revisions.

## 13. Acceptance and review

Required tests are extensions of the repository's existing core/intralogistics and TS conformance testing:

- Exact placement transforms, snap half-ties and negative coordinates; no implicit snapping; layer inheritance,
  unset/list rules, unknown versions/keys, round-trip numbers and bounded malformed input.
- Auto-layout with cycles, disconnected components and no topology; fixed seeds/hash seeds give identical output;
  callbacks that raise if called prove auto-layout is static and RNG-free.
- Lattice 4/8 connectivity, rotated obstacles, boundary contact, clearance and corner cutting; lanes crossing,
  T-junctions, overlaps, intersections moving without address churn, and stale removed-parent overrides.
- Connector owner-only exemption, rejection against other obstacles, no visibility candidate, equal-distance
  tie ordering, maximum distance, directed connectors and enabled overrides; required path failures.
- Stages computing entity counts, file overrides on late-created ids, cycles/missing predecessors, reads of
  future/self/auto values, barrier freeze, post-barrier existing-node attachment, and no events run on failure.
- Physical hashes change for values, absent→present, new obstacles/ports and membership predicates; presentation
  changes preserve digest/KPIs; D30 enforced even when bind-time reads discover physical dependencies.
- Same-instance graph throughout fleet/traffic/bays/stations; foreign handles, mixed modes and conflicting traffic
  owners rejected; public pre-bind reads fail while prelude snapshots remain valid; submit/cancel FIFO preserved.
- Layout and explicit-graph versions of a fixed transport model have equivalent trajectories/KPIs; no-layout
  regression fixtures retain their existing digests/event order. Compare layout fixtures across CPython/PyPy.
- Python and TS read shared 1.0/1.1 traces, kpi/full layout parity, malformed known extensions, bounded INITIAL,
  failed preparation without INITIAL, saved-layer re-resolution without calling Python model code.

User docs cover production-only auto layout, code/file precedence, a port-based lattice, lanes, staged entity
counts, explicit-network Layout and unchanged LayoutGraph scripts. Migration documents the builder overload,
pre-bind read errors and amendment A1's traffic limit. Add no performance checks to the simulation hot path:
all closure and geometry work belongs to preparation. Run the existing no-subscriber and recording benchmarks
before merge; report preparation cost separately for 1k/10k-node synthetic layouts without inventing a new
accepted release budget.

The two reviews and A1–A5 approval are recorded above. The plan carries task ownership and verification
commands; this document carries behavior and contracts. Implementation is authorized with subagents.
