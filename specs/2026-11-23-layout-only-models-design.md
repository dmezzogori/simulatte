# Layout as the only way to build a model

- **Status:** approved by Davide on 2026-11-23, with his answer to the one open question recorded as L16.
  Nothing is implemented; approval is not verification evidence.
- **Baseline:** branch `feature/agv-traffic` at `e993ca8` (pull request #93, version 0.18.0 prepared, not released).
- **Branch:** `feature/layout-only-models`, from `feature/agv-traffic`. Part of the 0.18.0 release, which is not
  tagged until this work, the Studio map view and the standard-search performance fix are merged.
- **Amends:** [SP2](2026-10-10-sp2-layout-design.md) §4 (`env.layout` initially `None`; the import rule), A3 to A5
  (no-layout manifests, stepping and mutation), and the "Without a Layout" paragraphs of its builder and
  migration sections. The last task of the plan appends an amendment section to SP2.
- **Decision ids:** `L1` to `L16`, recorded in §4 of this document only.
- **Builder check:** a reviewer read the first draft on 2026-11-23 as a builder would; its four questions and four
  corrections are answered in the text (L13 to L16, §5.2, §5.5, §9) and listed in §13.

## 1. Outcome and boundaries

### 1.1 Intent

A model can be built in two ways today. In explicit mode the script creates `LayoutGraph`, `Node` and `Arc`
objects and passes them to the components. In layout mode a `Layout` is attached to the environment, the network
is declared on it, and components receive handles that are bound when the layout is prepared. Managed execution
(`simulatte run`, Studio, experiments, replay) already builds every model in layout mode. Scripts, tests and
examples mostly use explicit mode.

The two modes cost twice the testing and documentation, and they differ in what a trace contains. A trace of an
explicit model carries no network, so Studio cannot draw its lanes and falls back to a grid of boxes. The
components planned for the next release (racks, staging areas, docks) need footprints and names that only a
layout holds.

Davide decided on 2026-11-23 that from 0.18.0 a model is built through its layout and in no other way.

### 1.2 Goals

1. Every environment has exactly one layout. A script reaches it as `env.layout`.
2. A transport network exists only as a declaration on the layout. Components that need a graph or a node take
   handles of that layout. Passing a concrete `LayoutGraph` or `Node` to a component raises an error that says
   what to write instead.
3. Explicit mode is removed from the code, the tests, the examples, the benchmarks and the documentation. No
   compatibility alias and no second spelling remain.
4. Every trace written by this version carries the resolved layout, for production-only models too.
5. A production-only script (servers, shop floor, no network) runs unchanged and pays nothing per event for the
   layout.
6. The evidence gathered for the AGV traffic work holds for models built through the layout: the randomized
   checks are run again on layout-built worlds.

### 1.3 Non-goals

- New layout features: no new generators, no new schema fields, no change to stages, file precedence, hashes or
  the bundle format beyond what §5.2 needs.
- The Studio map view and the performance of the standard search. Each has its own design.
- Racks, staging areas and docks (next release).
- Changing how managed execution creates and passes the layout (`build(env, layout, params)` stays).

### 1.4 Anti-goals

- A hidden conversion: a concrete graph passed to a component must not be wrapped silently. Davide chose the
  strict form over that (L2).
- A slower default: production-only runs must not become measurably slower per event (§9).
- A lost property: no physical input of an explicit graph may be dropped on the way through the layout (§5.2).
- Unreadable old traces: traces without a layout, written by earlier versions, stay readable by the Python
  reader, the TypeScript reader and Studio.

### 1.5 Constraints

- Same rules as the AGV traffic design §1.5: Python 3.11+, PyPy 3.11, no new runtime dependency, coverage of at
  least 99 % branch, events behind `env.wants`, explicit orderings, results independent of `PYTHONHASHSEED` and of
  observers.
- All APIs are unstable by declaration (`CLAUDE.md`); this is a breaking change with a migration section in the
  changelog, without deprecation.
- `import simulatte` still imports neither the layout package nor the intralogistics graph (L9).

## 2. Evidence from the current code

Mapped on 2026-11-23 at `e993ca8`.

- `Environment._layout` is `None` until `Layout(env)` is called (`environment.py:146`, `:363`). The managed
  driver always creates one before the user's `build` (`execution/driver.py:202`).
- `layout/binding.py` holds the two-mode switch: `node_input` and `graph_input` accept a concrete object when no
  layout is attached and a handle of the attached layout otherwise. Mixed use is rejected in both directions
  (`layout.py:82`, `binding.py:66`, `entities.py:312`, `env._explicit_graph_use`).
- Per component: `FleetCoordinator` binds every node at construction in explicit mode (`fleet.py:244`) and at
  preparation in layout mode; `Warehouse` bays are a mutable list or a frozen tuple; `AGV.current_node`,
  `Warehouse.input_bays`/`output_bays`, `ChargingStation.node`, `ParkingArea.node`, `FleetCoordinator.graph` and
  `ReservationController.graph` raise `LayoutNotReady` before binding in layout mode only.
- `Explicit(graph)` copies node ids and positions, and of each arc only `speed_limit` and `passing`
  (`layout/types.py:290-306`). `Arc.length` is dropped, so an arc longer than its chord loses its length in
  layout mode. `LayoutGraph.subdivide` raises on a layout-built graph (`graph.py:219`).
- The canonical nodes of a prepared layout are new `Node` objects equal to the user's by value, not identical
  (`layout/network.py`).
- `build_simple_system` names AGVs `vehicle-<i>` in layout mode and leaves generated `agv-<n>` ids in explicit
  mode when no prefix is given (`intralogistics/builders.py:213`).
- A layout on a production-only model changes neither ids nor the semantic digest (probe on two shops; no test
  says so). Its cost today: `Layout(env)` imports the whole layout and intralogistics packages; every entity
  attached before preparation resolves the layers again (1,600 servers: 183 ms against 2.4 ms); preparation takes
  0.68 s for 1,600 servers; `_guard_layout_advance` runs two function-level imports on every scheduler step,
  which made a 10-server shop run 11 to 15 % slower.
- The trace writer puts the bundle into INITIAL only when a layout exists (`trace/writer.py:535`); the manifest
  field `layout` is `None` otherwise. The Studio scene reads placements and arcs from `frame.layout` and falls
  back to a grid without one (`studio/packages/scene/src/project.ts:65-91`).
- Size of the migration: 171 `LayoutGraph(` constructions in 28 test files (20 in `tests/intralogistics`, 8 in
  `tests/layout`); 30 of 44 intralogistics test files build fleets or their parts; `traffic_support.py` is used
  by 17 files; `current_node is <node>` occurs 44 times; four example scripts, the AGV tutorial, the
  intralogistics benchmark and the agent skill teach explicit mode.
- Two parity tests already compare the modes (`tests/layout/test_binding.py:363`,
  `tests/layout/test_builders.py:~244`): same semantic digest for the same model when ids are equal.

## 3. Approaches considered

1. **Keep the plain graph; record the network in every trace.** Smallest change and no migration; Studio could
   draw any model. Two ways to build a model remain. Recommended by the assistant for 0.18.0; not chosen.
2. **Strict layout (chosen).** The environment owns a layout, the network is declared on it, components take
   handles, and a concrete graph or node raises. One way to build a model, explicit errors, a large migration.
3. **One mechanism, old spelling accepted.** A concrete graph passed to a component is taken as a declaration.
   Scripts change less, but two spellings stay and the layout rules apply to users who did not ask for a layout.

Davide chose 2 on 2026-11-23.

## 4. Decisions

| Id | Decision | By |
|---|---|---|
| L1 | From 0.18.0 the layout is the only way to build a model. | Davide |
| L2 | Strict form: components take handles; a concrete `LayoutGraph` or `Node` raises. No silent wrapping. | Davide |
| L3 | The environment owns its layout: `env.layout` always returns one, created on first use (§5.1). | assistant |
| L4 | Production-only models need no layout code; their layout is created at activation (§5.5). | assistant |
| L5 | `Explicit` carries every physical arc property: `length`, `speed_limit`, `passing`, direction (§5.2). | assistant |
| L6 | One network and at most one occupancy-controlling traffic controller per environment (§5.6). | assistant |
| L7 | The surviving behaviour is today's layout mode wherever the modes differ (ids, frozen endpoints, binding order, `LayoutNotReady`), except where this document says otherwise. | assistant |
| L8 | Explicit mode is removed only after a randomized parity run shows equal event streams for the same worlds built both ways (§8.1). | assistant |
| L9 | `import simulatte` stays light; creating or activating an environment may import the layout package, and must not import the intralogistics package for a model that has no network (§9). | assistant |
| L10 | Traces without a layout written by earlier versions remain readable; the viewer keeps its fallback for them only. | assistant |
| L11 | The work lands on a branch stacked on `feature/agv-traffic` and is released as part of 0.18.0. | assistant |
| L12 | No deprecation period: the changelog's migration section is the only bridge. | assistant |
| L13 | Node capacities and zones are network properties: they are declared on the layout only. `ReservationController(capacities=, zones=)` are removed; `node_capacity` stays (§5.2). | assistant |
| L14 | The activation targets of §9 are for a run that nobody observes. The bundle and its hashes are built when first read (§9). | assistant |
| L15 | Jobs and orders created before activation stay layout targets, as in layout mode today; their cost becomes linear (§5.5). | assistant |
| L16 | Arcs between two nodes at the same position are dropped: the layout keeps rejecting them, and the code and tests that support them are removed (§5.2). | Davide |

## 5. Design

### 5.1 The environment's layout

`env.layout` is a property that always returns the environment's layout. The layout is created the first time
it is needed: on first access of `env.layout`, when `Layout(env, ...)` or `Layout.from_dict(env, ...)` is called,
or at activation if neither happened. Creation registers the entities already attached, as `Layout.__init__` does
today.

`Layout(env, file=..., limits=...)` and `Layout.from_dict` stay as the way to create the layout with a file
layer or limits. They raise, as today, when the environment already has a layout or has been activated. A script
that needs neither a file nor limits writes `env.layout` and never calls the constructor. Managed execution is
unchanged: the driver creates the layout and passes it to `build(env, layout, params)`; inside `build`,
`env.layout is layout`.

The lifecycle is today's: declare, resolve, network barrier, validate, bind, finalize; `layout.prepare()` and
`env.activate()` share one preparation; `env.run()` activates on first use; `env.step()` before activation
raises and names `env.activate()`. This now holds for every environment, including production-only ones.

### 5.2 Declaring the network

The network is declared once per environment with `layout.set_network(...)`: `Lattice`, `Lanes` or `Explicit`,
unchanged. `Explicit(graph)` is the way to bring a hand-written `LayoutGraph` into a model.

`Explicit` copies, for each arc, its direction, `speed_limit`, `passing` and `length`. An `Arc` does not know
whether its length was given (a missing length is replaced by the chord at construction), so the rule is by
value: `Explicit` records an arc's `length` when it differs from the chord of its endpoints, and the resolved arc
then has exactly that float; otherwise the resolved arc gets its chord, as today. A graph produced by
`subdivide` therefore keeps the lengths of its pieces to the float. With `block_length`, the pieces of an arc
with a recorded length are `length / count`, as §12 of the traffic design states for the layout's blocks.

This is the one permitted schema change: explicit arcs gain an optional `length` in schema version 1.1, which
no released version reads. It touches the JSON schema (`explicitArc` is closed), the detection of 1.1 fields,
the bundle writer and both bundle readers (Python and TypeScript, which check exact key sets) and their tests.
The recorded length enters the physical hash. Bundles of explicit networks whose lengths are all chords do not
change.

An arc between two nodes at the same position cannot be declared (L16): `Explicit` and the resolver reject it, as
SP2 rules, and no exception is added. The two tests of such arcs
(`tests/intralogistics/test_travel_motion.py:513`, `tests/intralogistics/test_turning.py:234`) are removed with
the branches that exist only for a chord of length 0 in travel, turning, the heading helpers and the fastest
router, so that no unreachable code stays. A lift or a transfer point is modelled with a small offset between
the nodes and an explicit arc length; the migration section says so.

`LayoutGraph`, `Node` and `Arc` stay public: they are how an explicit network is written and what
`result.network.graph` returns. `LayoutGraph.subdivide` stays for graphs that are then declared with `Explicit`;
it still refuses a layout-built graph, whose blocks come from `block_length`.

Node capacities and zones are properties of the network, so they are declared with it and nowhere else (L13):
the `capacity` and `zones` of `Explicit`, `Lattice` and `Lanes`, the per-node capacities of network definitions
and the zones of schema 1.1, with members named by node ids and id pairs. `ReservationController(capacities=,
zones=)`, which took `Node` objects, are removed, and with them the rule about a zone id defined in both places.
`ReservationController(node_capacity=)` stays as the fallback capacity; it names no node. If declaring a capacity
for single nodes of an explicit graph has no convenient spelling today, `Explicit` gains a
`capacities: Mapping[str, int]` argument keyed by node id. The runtime `Zone` resource is still built from the
resolved network at binding; user code no longer constructs it. About 60 test call sites and the guide's
example move to the declaration.

### 5.3 Components take handles

`FleetCoordinator(graph=)`, `ReservationController(graph=)`, `Warehouse(input_bays=, output_bays=)`,
`AGV(initial_node=)`, `ChargingStation(node=)`, `ParkingArea(node=)` and every other parameter that names a graph
or a node accept only `GraphRef`, `NodeRef` or `PortRef` of the environment's layout (`layout.graph()`,
`layout.node(id)`, `layout.node_at(address)`, `layout.port(entity, name)`). `AGV(initial_node=None)` and a
warehouse without bays stay valid.

A concrete `LayoutGraph` or `Node` raises `LayoutError` with one message per case that names the fix, for
example: "FleetCoordinator(graph=...) takes layout.graph(); declare the graph with
env.layout.set_network(Explicit(graph))" and "Warehouse(input_bays=...) takes layout.node(id) or
layout.port(...) handles, not Node objects". A handle of another environment's layout raises as today.

At run time nothing changes: after binding, `agv.current_node`, `fleet.graph` and the other physical fields hold
the canonical objects of the resolved network, and moving an AGV takes canonical nodes only.

### 5.4 Reading physical fields

Before the layout is prepared, the physical getters listed in §2 raise `LayoutNotReady`. A script that wants to
inspect them before running calls `env.layout.prepare()` or `env.activate()`. After preparation the network and
the static endpoints are read-only. Components constructed after preparation resolve their handles at once and
may attach to the frozen network, as today; they cannot change it.

Canonical nodes are equal to the nodes of the declared graph and are different objects. Code compares nodes
with `==` or looks them up in `result.network` (`network.node(layout.node(id).address)`), never with `is` against
a node it created itself.

### 5.5 Production-only models

A script that builds servers, a shop floor or a production builder and never mentions a layout keeps working
without a change. Its layout is created at activation, positions the entities automatically, and is written to
the trace. Ids, event order and the semantic digest of such a model are the same as before; a test pins this for
the reference production models. A trial with a layout attached at every activation passed 1,481 of 1,485 tests
of `tests/core`, `tests/experimental` and `tests/test_environment.py`; the four that change are three pins of
`layout: None` and `tests/core/test_activation.py:123`, where `env.on_activate(env.step)` now raises `LayoutError`
instead of `RuntimeError`.

Jobs and orders created before activation are layout targets, as in layout mode today (L15): each gets an
automatic placement and an entry in the bundle. Their registration becomes linear (§9). A script that creates
more than `LayoutLimits.max_placements` entities (100,000 by default) before activation fails at preparation
with an error that names the limit and `Layout(env, limits=...)`; the changelog says so. Entities created during
the run are not targets and cost nothing. Whether transient entities should be targets at all is a question for
the layout subsystem, outside this work.

What changes for these models: the INITIAL record and the manifest of every trace now carry the layout (the
manifest's `layout` is no longer `None`), and activation does the work of §9.

### 5.6 One network per environment

An environment has one layout, hence one network, and at most one traffic controller that controls occupancy.
Several disconnected regions are one declared network. Several fleets share the network and its controller.
The checks that compared the graphs of fleets and controllers in explicit mode go away with it
(`FreeFlowController.register_fleet`'s graph comparison becomes a check that the handle is the layout's graph).
Models that used two graphs in one environment declare their union.

### 5.7 Builders and ids

`build_simple_system`, the one intralogistics builder, uses `env.layout` when no layout is passed. Without a
declared network it declares its own, as the layout branch does today; with one it requires the role
mapping. It returns a `GraphRef`. AGVs get the names of today's layout branch (`vehicle-<i>` without a prefix,
`<prefix>agv-<i>` with one). An `AGV` constructed directly keeps its generated `agv-<n>` id unless `agv_id` is
given.

Because ids of builder models change, their digests change; every pinned digest and recorded fixture that moves
is re-pinned with the cause stated (§8.3).

### 5.8 Traces and Studio

Every trace written by this version has `layout` in INITIAL and in the manifest. `Trace.layout` is `None` only
for traces of earlier versions. The Studio scene keeps its grid fallback for those traces and labels it as today;
no trace written by this version reaches it. A test pins that the four intralogistics examples and a production
example produce traces with a resolved layout whose `network.graph.arcs` (for the transport examples) is not
empty.

### 5.9 What is removed

- The no-layout branches of `node_input` and `graph_input`, `Environment._explicit_graph_use` and
  `_register_graph_use`, the `_uses_explicit_graph` flags, `check_existing_endpoints` and the two "explicit graph
  consumers" errors.
- The construction-time binding of graph nodes in `FleetCoordinator`, mutable bay lists, and every union type
  that names a concrete graph or node next to a handle in a component signature.
- The explicit branches of the builders, and `nodes mapping requires a layout`.
- From the documentation: the "Migration: explicit graphs with a Layout" framing and the "Existing scripts remain
  valid" block of the layout guide, the two-mode table of `docs/api/layout.md`, and every explicit-mode example.

## 6. Migration of users

The changelog's migration section for 0.18.0 gains a table and one worked example:

| Before | After |
|---|---|
| `graph = LayoutGraph(nodes, arcs)` passed to components | `env.layout.set_network(Explicit(LayoutGraph(nodes, arcs)))`, then `graph = env.layout.graph()` |
| `Warehouse(input_bays=[node])`, `AGV(initial_node=node)`, `ChargingStation(node=node)`, `ParkingArea(node=node)` | `env.layout.node("id")` in place of the node |
| reading `agv.current_node` or `fleet.graph` right after construction | after `env.layout.prepare()` or `env.activate()` |
| `agv.current_node is my_node` | `agv.current_node == my_node` |
| `wh.input_bays.append(node)` | declare the bays at construction; endpoints are frozen |
| two graphs in one environment | one declared network with both regions |
| `ReservationController(capacities={node: 2}, zones=[Zone(...)])` | capacities and zones in the network declaration, by node id |
| an arc between two nodes at the same position | a small offset between the nodes and an explicit `length` |
| `env.step()` on a fresh environment | `env.activate()` first |

It also states that traces now always carry the layout, that builder AGVs are named `vehicle-<i>`, and that
digests of builder models change for that reason.

## 7. Documentation, examples, benchmarks

- The layout guide opens with the fact that every model has a layout, then production-only models, then the
  three ways to declare a network. The intralogistics guide, the AGV tutorial, the examples page and the API pages
  show only handles. The four intralogistics examples, `building_an_agv_system.py` and
  `intralogistics_simple.py` are rewritten; their documented outputs are regenerated.
- `benchmarks/intralogistics.py` builds through the layout on the branch side. Its comparison with 0.12.0 is
  already report-only with different trajectories; the baseline file records the new numbers.
- `skills/simulatte-intralogistics/` and `CLAUDE.md` follow.

## 8. Verification

### 8.1 Parity before removal

While both modes still exist, a randomized run builds the same worlds twice, once with concrete objects and once
through `Explicit` and handles, and compares the event streams after the prelude (sequence numbers and prelude
attachment counts are excluded, as the existing parity tests do) and the final states. Worlds: at least 1,000,
under both traffic controllers and the three routers, with zones, corridors, passing segments, arcs with explicit
lengths, block lengths, chargers, parking, low batteries and two fleets on one controller. The generators of the
traffic work's reviews are the starting point (`.superpowers/sdd/2026-11-21-agv-traffic-c-routing/` on the
maintainer's machine, git-ignored: `final-scratch-control/world.py` and `monitor.py`,
`final-scratch-rereview/rr_gen.py` and `twin.py`; the plan's first task ports what it needs into a test helper
or a script under `benchmarks/`). Every difference is explained or fixed before explicit mode is removed.
`Explicit` dropping `Arc.length` is the known one.

### 8.2 Tests

The test suite is migrated, not thinned: every test that exists keeps asserting what it asserts, built through
the layout. The shared helpers (`tests/intralogistics/traffic_support.py`, the `conftest.py` fixtures) become
the place where a graph is declared and handles are produced, so that most test bodies change little. Tests that
pinned the two-mode rules (the explicit-then-layout errors, the no-layout builder digest) are replaced by tests of
the new rules: concrete objects raise with the messages of §5.3; `env.layout` exists on a fresh environment; a
production-only model has the same digest as before.

### 8.3 Re-pins

Each pinned digest, layout hash, trace fixture and recorded JSON that changes is re-pinned in the task that
changes it, with the cause stated and shown to be the only cause (for builder models: the AGV ids).

### 8.4 The traffic evidence, again

After the removal, the randomized safety check of the traffic work (independent body model, holdings, replay of
deltas at every event, `env.run()` returns) runs on layout-built worlds: at least 2,000 mission runs, no
violation, no exception, every run returns.

## 9. Performance and imports

- **Per event:** a model pays nothing for its layout after activation. `_guard_layout_advance` becomes a check of
  a field, with no import and no call on the step path once the layout is finalized.
- **Construction:** attaching an entity before preparation does not resolve the layers again; resolution is
  deferred to preparation or done incrementally. Target: construction with a layout within 1.5 times
  construction without one today, at 1,600 servers.
- **Activation:** preparation is linear in the number of entities, including jobs and orders created before
  activation. Targets, for a run that nobody observes (no recorder, no manifest or bundle read): under 50 ms for a
  production model of 1,600 servers on the development machine, and under 5 ms for 10 servers (0.68 s and 7.6 ms
  today). About three quarters of today's time is the bundle validating its own output and the three hashes
  (`layout/bundle.py:516`, `layout/identity.py:135`). The bundle and the hashes are therefore built when first
  read (`prepare().bundle`, the hashes, the manifest, the INITIAL record of a recorder), not at preparation;
  validation of the declared inputs stays at preparation, so a model error still surfaces there. The
  self-validation of a freshly built bundle may move to the tests. A recorded run builds the bundle once at
  INITIAL and has no target beyond linear growth. If a target cannot be met without changing hashes or the bundle
  format, the builder records the measured floor and the gate below decides.
- **Gate:** the no-subscriber overhead gate of the core workload against 0.12.0 still passes with its present
  limit.
- **Imports:** `import simulatte` imports neither `simulatte.layout` nor `simulatte.intralogistics.graph` (the
  existing test stays). Creating the layout of a model without a network does not import the intralogistics fleet,
  traffic or AGV modules; a test pins it.

## 10. Acceptance

1. No code path in `src/simulatte` accepts a concrete `LayoutGraph` or `Node` where a handle is required;
   `grep` finds no `_explicit_graph_use` or `_uses_explicit_graph`.
2. `tests/`, `examples/`, `benchmarks/`, `docs/` and `skills/` contain no model built in explicit mode.
3. The parity run of §8.1 and the safety run of §8.4 are recorded in the acceptance file of this work with their
   counts.
4. The performance targets of §9 are measured and recorded; the core gate passes.
5. A trace of every example opens in Studio with its lanes drawn (checked with the TypeScript reader's tests, as
   the traffic work did).
6. Full suite, lint, format, types, docs build, Studio gates and the PyPy lane pass; coverage stays at 99 % or
   above.

## 11. Risks

- **Size.** The migration touches most intralogistics tests. Mitigation: the helpers carry most of it; the parity
  run protects behaviour while the tests move.
- **Hidden differences between the modes** (neighbour order, binding time, prelude events). Mitigation: §8.1
  before removal.
- **Production overhead.** The measured 11 to 15 % comes from a guard that §9 removes; the gate decides.
- **Lifecycle friction for small scripts** (`LayoutNotReady`, frozen endpoints). Accepted with L1 and L2; the
  error messages and the migration table carry it.
- **Users with two graphs in one environment.** Accepted with L6.

## 12. Left to the builder

The exact wording of error messages beyond the two examples; whether resolution at attach is deferred or
incremental; how the helpers of the test suite expose handles and canonical nodes; the order in which test files
are migrated; whether the parity generator lives under `tests/` or `benchmarks/`; the names of new tests.

## 13. Builder check

Answered in the text: capacities and zones (L13, §5.2); the activation targets and the lazy bundle (L14, §9);
jobs and orders as targets (L15, §5.5); how `Explicit` decides that a length is recorded and how wide the schema
change is (§5.2); one builder, not several (§5.7); the four core tests that change (§5.5). The acceptance file of
this work is `specs/research/2026-11-23-layout-only-models-acceptance.md`.

**Decided by Davide (L16).** Arcs between two nodes at the same position are dropped; the layout does not
learn to accept them (§5.2).
