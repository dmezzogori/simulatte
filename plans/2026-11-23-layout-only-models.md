# Layout as the only way to build a model — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** written on 2026-11-23 from the approved design. No task is implemented yet.

**Goal:** Every environment owns one layout, a transport network exists only as a declaration on it, components take handles, and explicit mode is removed from the code, the tests, the examples and the docs.

**Architecture:** The work first makes the layout path complete and cheap (arc lengths through `Explicit`, no cost per event, linear activation), proves on random worlds that it gives the same event streams as explicit mode, then moves every test, example and benchmark to it, and only then removes explicit mode from `src`. The traffic safety check is run again at the end on layout-built worlds.

**Stack:** Python 3.11+ standard library; the TypeScript workspace under `studio/` for the bundle reader; Zensical for the docs.

**Spec:** [`specs/2026-11-23-layout-only-models-design.md`](../specs/2026-11-23-layout-only-models-design.md). Section numbers and decision ids (`L1` to `L16`) below refer to it. Signatures in this plan are boundaries, not licence to weaken a contract stated there. The layout subsystem's own design is [`specs/2026-10-10-sp2-layout-design.md`](../specs/2026-10-10-sp2-layout-design.md).

**Follows:** the AGV traffic work on `feature/agv-traffic` (pull request #93). This branch, `feature/layout-only-models`, is stacked on it. Nothing is pushed and no pull request is opened by this plan; the release step of 0.18.0 belongs to the last of the 0.18.0 works, not to this one.

## Global constraints

- Python >= 3.11; must also run on PyPy 3.11; no new runtime dependency.
- Coverage stays >= 99 % branch for the full suite (`uv run --all-extras pytest`); `uv run --all-extras ruff check src tests`, `uv run --all-extras ruff format --check` and `uv run --all-extras ty check src` stay clean. Read the output of each before reporting it.
- Every state change is an event emitted behind `if env.wants(Cls): env.emit(Cls(...))`; every ordering that decides something is explicit; results do not depend on `PYTHONHASHSEED` or on observers.
- The migration of tests is a migration, not a thinning: no assertion is removed or weakened, and the number of test functions of a migrated file does not go down, except where a task of this plan names the tests it removes.
- Wherever the two modes differ, the surviving behaviour is today's layout mode (L7), except where the spec says otherwise.
- No new layout feature and no change to stages, file precedence, hashes or the bundle format, except the optional `length` of explicit arcs in schema 1.1 (spec §5.2) and what Task 5 adds for capacities.
- Any task that changes a pinned digest, a layout hash, a trace fixture or a recorded JSON re-pins it in that task and states the cause in its report, with evidence that the cause is the only one.
- `import simulatte` imports neither `simulatte.layout` nor `simulatte.intralogistics.graph` (the test in `tests/core/test_public_api.py` stays).
- Traces of earlier versions, which have no layout, stay readable by the Python reader, the TypeScript reader and Studio (L10): the frozen fixtures under `tests/fixtures/traces/frozen/` are never regenerated.
- Nothing about deadlines, venues or papers is written anywhere in the repository.
- Conventional commits; each task commits only its own files.

## Review focus

| Risk the happy path misses | Owning test |
| --- | --- |
| A script reads `agv.current_node`, `fleet.graph` or a warehouse's bays before the model is prepared: the error must say what to call. | Task 9 `tests/layout/test_handles_only.py::test_reading_before_preparation_names_the_fix` |
| `Layout(env, file=...)` is called after `env.layout` was already used: the error must name the order, not "explicit graph consumers". | Task 4 `tests/layout/test_environment_layout.py::test_constructor_after_first_use_raises` |
| A component is constructed after activation with a handle (a late AGV, a late parking area): it resolves at once and works; a late occupancy controller raises. | Task 9 `tests/layout/test_handles_only.py::test_late_components_resolve_at_once` |
| A production script creates more entities before activation than `LayoutLimits.max_placements`: the error names the limit and `Layout(env, limits=...)`. | Task 4 `tests/layout/test_environment_layout.py::test_too_many_targets_names_the_limit` |
| A trace written before this version (no layout) is opened by the Python reader and by the TypeScript reader: `layout` is `None`/absent and the scene uses its labelled fallback. | Task 8 `tests/core/test_trace_reader.py::test_a_trace_without_layout_stays_readable` and `studio/packages/scene/test/traffic.test.ts` (case "no layout") |

## Sequence and gates

Tasks run in order. Explicit mode is removed in Task 9 and not before: until then both modes work and the suite is green after every task. Every task ends with the full suite.

---

### Task 1 — `Explicit` keeps arc lengths

**Modify:** `src/simulatte/layout/types.py` (`ExplicitArc`, `Explicit`), `src/simulatte/layout/network.py` (resolution of explicit arcs and of their blocks), `src/simulatte/layout/schema/layout-v1.schema.json` (`explicitArc`), `src/simulatte/layout/schema/__init__.py` (the list of 1.1 fields), `src/simulatte/layout/bundle.py` (exact key sets), `studio/packages/trace/src/layout.ts` and `layout-schema.ts` with their tests, `tests/layout/test_traffic_fields.py`, `tests/layout/test_binding.py`.
**Interfaces (produces):** `ExplicitArc.length: float | None = None`; a resolved explicit arc has `Arc.length` equal to the recorded float, or its chord when none is recorded.

Rule (spec §5.2): `Explicit` records an arc's `length` when `arc.length != chord of its endpoints`, comparing floats exactly, with the chord computed as the layout computes it. With `block_length`, pieces of an arc with a recorded length are `length / count`. The field makes a bundle schema 1.1.

- [ ] Tests: an arc of chord 10 with `length=25.0` resolves to length 25.0 and its travel time under a constant-speed profile follows; an arc without an explicit length resolves to its chord and its bundle has no `length` key, so the hashes of an all-chord explicit network equal the ones at the base commit (pin two existing hashes before the change); a graph produced by `LayoutGraph.subdivide(3.3)` on nodes at `x = 3.3 * i` resolves through `Explicit` to arcs whose lengths equal the subdivided graph's, float for float; `block_length` on a 30 m arc with `length=45.0` gives pieces of `45.0 / count`; a bundle with `length` reports schema 1.1 and round-trips through `LayoutBundle.from_dict`; the TypeScript reader accepts the field and rejects an unknown sibling key.
- [ ] Implement.
- [ ] `uv run --all-extras pytest tests/layout --no-cov -q`; in `studio/`: `pnpm typecheck && pnpm test`; full suite; commit `fix(layout): explicit networks keep arc lengths`.

### Task 2 — Random worlds and the parity run

**Create:** `tests/intralogistics/random_worlds.py` (generator and monitor, test support that stays), `benchmarks/layout_parity.py` (a script, removed by Task 9), `specs/research/2026-11-23-layout-only-models-acceptance.md`.
**Interfaces (produces):**

```python
@dataclass(frozen=True)
class WorldSpec:  # pure data, no simulatte objects
    seed: int
    ...


def world_spec(seed: int) -> WorldSpec: ...
def build_world(env: Environment, spec: WorldSpec, *, mode: Literal["explicit", "layout"]) -> BuiltWorld: ...
def run_world(spec: WorldSpec, *, mode: str, router: str, controller: str, until: float | None) -> RunRecord: ...


class Monitor: ...  # independent body model, holdings, delta replay at every event
```

`BuiltWorld` exposes `fleets`, `agvs`, `controller`; `RunRecord` holds the event stream after the prelude (sequence numbers and ordinals stripped, as `tests/layout/test_builders.py` does), the final states and the monitor's violations. `mode="explicit"` exists until Task 9 removes it.

The generators of the traffic reviews are the starting point and are on this machine, git-ignored: `.superpowers/sdd/2026-11-21-agv-traffic-c-routing/final-scratch-control/world.py` and `monitor.py`, `final-scratch-rereview/rr_gen.py` and `twin.py`. Port what is needed; do not import from there. Worlds cover: grids with removed links, chains, spurs, one-way arcs, passing segments, arcs with explicit lengths, block lengths, zones with and without hold-back, nodes of capacity 2, chargers, parking, low batteries, cancellations, two fleets on one controller; AGVs are constructed directly with `agv_id=` so that ids are equal in both modes. No world has two nodes at the same position. Zones are declared with `Explicit(zones=...)` in layout mode and passed to the controller in explicit mode; per-node capacities go to the controller's `capacities=` in both modes until Task 5 (the controller matches nodes by value).

- [ ] Tests (`tests/intralogistics/test_random_worlds.py`): `world_spec` is deterministic per seed; five fixed seeds build and run in both modes with equal `RunRecord`s under `ReservationController` and the default router; the monitor reports a seeded violation (a patched admission) so that it is known to detect one.
- [ ] `benchmarks/layout_parity.py --worlds 1000`: every world, both modes, both controllers, the three routers; prints the count of equal and differing runs and, for each differing run, the seed and the first differing event. Run it. Every difference is either fixed in the layout path (in this task, with a test) or reported as a blocker; none may be left unexplained.
- [ ] Record in the acceptance file: worlds, runs, events compared, differences found and their causes, the commit.
- [ ] Full suite; commit `test(intralogistics): random worlds and the parity of explicit and layout models`.

### Task 3 — A layout costs nothing per event and is linear at activation

**Modify:** `src/simulatte/environment.py` (`_guard_layout_advance`, `step`), `src/simulatte/layout/lifecycle.py` (`attach_target`), `src/simulatte/layout/layout.py`, `src/simulatte/layout/bundle.py`, `src/simulatte/layout/binding.py` (imports), `tests/layout/test_lifecycle.py` or the nearest existing file, `tests/core/test_public_api.py`.
**Create:** `benchmarks/layout_overhead.py`.
**Interfaces (produces):** `prepare()` returns the same result object as today; its `bundle`, `full_hash`, `physical_hash` and `layers_hash` are computed on first read and cached (L14). No signature changes.

Spec §9. Measure first with `benchmarks/layout_overhead.py` at the base commit and record the numbers in the acceptance file: construction of 100 and 1,600 servers with and without `Layout(env)`, `prepare()` for 10 and 1,600 servers, a 10-server immediate-release shop run of about 30,000 domain events with and without a layout, 1,600 jobs created before `Layout(env)`.

- [ ] Tests: after activation `Environment.step` performs no import and no call into the layout package (assert through `sys.modules` deltas and a patched `PreparationCoordinator` attribute that counts reads); attaching 400 entities before preparation calls the layer resolution a number of times that does not grow with the count (count calls of the resolver); `prepare()` on a model without a network does not import `simulatte.intralogistics.fleet`, `.traffic`, `.reservation` or `.agv` (subprocess test in the style of `tests/core/test_public_api.py`); `prepare().bundle` and the three hashes are equal to the ones at the base commit for the existing layout fixtures (they are pinned in `tests/layout/test_traffic_fields.py` and `tests/layout/test_hashes.py`); a model error (an unknown placement kind, a missing required handle) still raises at `prepare()`, not at first read of the bundle.
- [ ] Implement: the guard reads one field once the layout is finalized; attaching defers or increments resolution; the bundle and hashes become lazy; the self-validation of a freshly built bundle moves to the tests; the model-free preparation path imports no fleet, traffic or AGV module.
- [ ] Run `benchmarks/layout_overhead.py` again and record both sets in the acceptance file with the targets of spec §9 (construction within 1.5 times the no-layout construction at 1,600 servers; activation under 50 ms at 1,600 servers and under 5 ms at 10; no measurable difference per event). A target that is missed is reported with the measured floor and the profile, not hidden.
- [ ] Full suite; commit `perf(layout): no cost per event and linear preparation`.

### Task 4 — The environment owns its layout

**Modify:** `src/simulatte/environment.py`, `src/simulatte/layout/layout.py`, `src/simulatte/trace/writer.py`, `tests/core/test_trace_writer.py`, `tests/core/test_provenance.py`, `tests/core/test_activation.py`, `tests/fixtures/traces/generate.py` and the generated fixtures.
**Create:** `tests/layout/test_environment_layout.py`.
**Interfaces (produces):** `Environment.layout` always returns a `Layout`, creating it on first access; `Environment._layout` stays the internal field that library code checks without creating. Activation creates the layout when none exists. Library code that only asks whether a layout exists reads `_layout` and never the property (`grep -rn "env.layout\|\.layout is None\|\.layout is not None" src`: `entities.py`, `layout/binding.py`, `intralogistics/builders.py`, `fleet.py`, `graph.py`, `charging.py`, `parking.py`, `trace/writer.py` and the rest), so that `build_simple_system(env)` without a layout keeps its explicit branch until Task 9. Transitional rule until Task 9: an environment that has an explicit graph consumer has no layout, `env.layout` raises the existing "explicit graph consumers" error for it, and activation does not create one.

- [ ] Tests: `Environment().layout` is a `Layout` and the same object on every access; `Layout(env)` on a fresh environment still works and `env.layout` is then that object; `test_constructor_after_first_use_raises`: `env.layout` then `Layout(env, file=...)` raises `LayoutError` whose message says the layout already exists and that `Layout(env, ...)` must come before the first use of `env.layout`; a production-only model (the reference shops of `tests/core/test_g1_acceptance.py`) has the same ids, event count and `env.fingerprint()` as at the base commit, with the pinned digests unchanged; its trace has `layout` in INITIAL and in the manifest; `test_too_many_targets_names_the_limit`: 30 jobs created before activation with `Layout(env, limits=LayoutLimits(max_placements=20))` raise an error naming `max_placements` and `Layout(env, limits=...)`; `env.step()` on a fresh production environment raises the error that names `env.activate()`; `env.on_activate(env.step)` raises `LayoutError` (the changed pin of `tests/core/test_activation.py:123`).
- [ ] Implement. Re-pin the three `layout: None` pins. Regenerate `tests/fixtures/traces/generated/` with `tests/fixtures/traces/generate.py`; leave `frozen/` alone.
- [ ] Run `benchmarks/layout_overhead.py` and the CI overhead gate as `benchmarks/README.md` describes (`run.py`, `compare.py` against the stripped 0.12.0); record both in the acceptance file. The gate must pass with its present limit.
- [ ] Full suite; in `studio/`: `pnpm test`; commit `feat(layout)!: every environment owns a layout`.

### Task 5 — Capacities and zones in the network declaration

**Modify:** `src/simulatte/layout/types.py`, `src/simulatte/layout/network.py`, the schema files if a field is needed, `tests/layout/test_traffic_fields.py`, `tests/intralogistics/random_worlds.py`.
**Interfaces (produces):** `Explicit(graph, *, clearance=0, blocked=None, capacity=None, capacities: Mapping[str, int] | None = None, block_length=None, zones=None)` with `capacities` keyed by node id; zones declared as today's schema 1.1 mapping `{zone_id: {"members": [node_id | [node_id, node_id], ...], "capacity": int, "hold_back": bool}}`. `ReservationController(capacities=, zones=)` are untouched in this task.

First check whether a per-node capacity of an explicit node can already be declared (network definitions, `NetworkOverride`); if a spelling exists, `capacities` is sugar for it and adds no schema field; if none exists, add the optional field to schema 1.1 as Task 1 did.

- [ ] Tests: `Explicit(graph, capacities={"N1": 2})` gives `controller.network.capacity(node N1) == 2` after binding and the default elsewhere; an unknown node id raises `LayoutError` naming it; a capacity below 1 raises; a zone declared on the network with node and pair members gives the same derived claims as the same zone passed to the controller today (compare `network.leg(u, v)` for every leg of a five-node world); the fallback order explicit capacity, facility capacity, `node_capacity` is unchanged.
- [ ] Implement.
- [ ] Switch the layout mode of `tests/intralogistics/random_worlds.py` to `Explicit(capacities=...)` and run `benchmarks/layout_parity.py --worlds 200`: no differing run. Record it in the acceptance file.
- [ ] `uv run --all-extras pytest tests/layout tests/intralogistics/test_network.py --no-cov -q`; full suite; commit `feat(layout): node capacities by id in explicit networks`.

### Task 6 — Test support and the traffic tests on the layout

**Modify:** `tests/intralogistics/traffic_support.py`, `tests/intralogistics/conftest.py`, and the 17 test files that import `traffic_support` (16 in `tests/intralogistics`, `tests/layout/test_traffic_fields.py`).
**Interfaces (produces), in `traffic_support.py`:**

```python
@dataclass
class World:
    env: Environment
    graph: GraphRef
    def ref(self, node_id: str) -> NodeRef: ...   # for constructors
    def node(self, node_id: str) -> Node: ...     # prepares the layout if needed; the canonical node

def declare(env: Environment, graph: LayoutGraph, **network: Any) -> World: ...   # env.layout.set_network(Explicit(graph, **network))
def build(env, points, links, **network) -> World
def line(env, n, **network) -> World
def t_layout(env, **network) -> World
def two_junction_corridor(env, **network) -> World
def loop_with_side_node(env, **network) -> World
```

`conftest.py` keeps `node_a` and `node_b` as the plain `Node` values they are (they are also used to write graphs) and gains nothing that hides the layout. Capacities and zones of migrated tests move to the declaration (`capacities={"N1": 2}`, `zones={...}`) with Task 5's spelling.

Rules for every migrated test: construction takes `world.graph` and `world.ref(id)`; anything read or passed at run time takes `world.node(id)`; `is` against a test's own `Node` becomes `==` against `world.node(id)`; a read of a physical field before the run comes after `env.layout.prepare()` or `env.activate()`; no assertion is removed or weakened. The two tests of arcs between coincident nodes (`test_travel_motion.py:513`, `test_turning.py:234`) are left on explicit mode for Task 9, which removes them.

- [ ] Migrate the helpers and the 17 files. Before starting, record per file the number of test functions and of `assert` statements (`grep -c`); after, the numbers are equal or higher, and the report lists both.
- [ ] Gate: in the migrated files `grep -nE "LayoutGraph\(|graph=graph\b"` finds a concrete graph only as the argument of `declare`/`Explicit`; pinned digests and fixtures of these files are unchanged, or re-pinned with the cause (an id or a prelude count) shown.
- [ ] `uv run --all-extras pytest tests/intralogistics tests/layout --no-cov -q`; full suite; commit `test(intralogistics): traffic tests build their worlds through the layout`.

### Task 7 — The remaining intralogistics tests on the layout

**Modify:** every other file under `tests/intralogistics/` that constructs a fleet, AGV, warehouse, charging station, parking area or builder model (`test_coordinator.py`, `test_charging.py`, `test_parking.py`, `test_policies.py`, `test_agv.py`, `test_entities.py`, `test_warehouse.py`, `test_builders.py`, `test_integration.py`, `test_invariance.py`, `test_collectors*.py`, `test_fleet_events.py`, `test_resource_events.py`, `test_logging.py`, `test_activation.py`, `test_bindings.py`, `test_idle_power.py`, `test_routing.py`, `test_graph.py` where it builds models, `test_examples.py`), and the recorded fixtures they pin.
**Interfaces (consumes):** `World`, `declare` and the builders of Task 6. Files that do not import `traffic_support` today may import it or use `env.layout` directly; one way per file.

Same rules and the same counts as Task 6. `build_simple_system(env)` is called with the environment's layout from here on (`layout=env.layout`), so its AGVs are named `vehicle-<i>`: tests that hard-code `agv-<n>` for builder models change the literal, and the digest pinned in `test_builders.py::test_no_layout_builder_digest_remains_compatible` is re-pinned under a name that says what it pins (`test_builder_digest`), with the cause stated: the AGV ids. `test_warehouse.py:162` (assignment to `input_bays`) becomes the assertion that the assignment raises. Tests of pure graph functions (`shortest_path`, `subdivide`, `Arc` validation, routers on a bare view) keep their plain `LayoutGraph`: they build no model.

- [ ] Migrate, with the per-file counts in the report.
- [ ] Gate: `grep -rnE "FleetCoordinator\(|ReservationController\(|Warehouse\(|ChargingStation\(|ParkingArea\(|AGV\(" tests/intralogistics` shows no call that receives a concrete `LayoutGraph` or `Node` (check each remaining concrete `Node(` use is graph writing or a pure-function test).
- [ ] Full suite; commit `test(intralogistics): fleet tests build their models through the layout`.

### Task 8 — Examples, benchmark and fixtures

**Modify:** `examples/intralogistics_simple.py`, `_intermediate.py`, `_advanced.py`, `_traffic.py`, `examples/building_an_agv_system.py`, `docs/examples/intralogistics.md` and `docs/tutorials/building-an-agv-system.md` (their run blocks must equal the scripts, `tests/test_docs_run_blocks.py`), `tests/intralogistics/test_examples.py`, `benchmarks/intralogistics.py`, `benchmarks/intralogistics-baseline.md`, `tests/core/test_trace_reader.py`, `studio/packages/scene/test/traffic.test.ts`, `tests/fixtures/traces/generate.py`.
**Interfaces (produces):** a generated fixture `tests/fixtures/traces/generated/example_traffic.simtrace` with its `.expected.json`, recorded from `examples/intralogistics_traffic.py`.

- [ ] Rewrite the five scripts with `env.layout`, `set_network(Explicit(...))` and handles; keep what each example teaches and its printed quantities; regenerate the documented outputs and the numbers pinned in `test_examples.py`, stating for each changed number whether the cause is an id, a name or nothing (the event streams of the transport itself must be equal to the explicit versions: check with `benchmarks/layout_parity.py`'s comparison on each example before and after).
- [ ] `benchmarks/intralogistics.py` builds through the layout on the branch side; run it as `benchmarks/intralogistics-baseline.md` describes and record the numbers and the reason for any change.
- [ ] Tests: `test_a_trace_without_layout_stays_readable` opens a frozen fixture and asserts `Trace.layout is None`, `check()` and `verify()`; a test that each of the five examples, recorded, has a resolved layout whose `network.graph.arcs` is not empty; in the TypeScript scene test, the new fixture projects with links (`projection.links.length > 0`, `fallback === false`) and a frozen no-layout fixture projects with `fallback === true` (case "no layout").
- [ ] Full suite; in `studio/`: `pnpm typecheck && pnpm test`; `uv run --all-extras zensical build`; commit `docs(examples): intralogistics examples declare their network on the layout`.

### Task 9 — Explicit mode is removed

**Modify:** `src/simulatte/layout/binding.py`, `layout.py`, `src/simulatte/environment.py`, `src/simulatte/entities.py`, `src/simulatte/intralogistics/fleet.py`, `reservation.py`, `traffic.py`, `warehouse.py`, `agv.py`, `charging.py`, `parking.py`, `graph.py` (`NodeBinding`), `builders.py`, `travel.py`, `routing.py`, `network.py`, `src/simulatte/intralogistics/__init__.py`, `tests/intralogistics/test_travel_motion.py`, `test_turning.py`, `tests/layout/test_binding.py`, `test_builders.py`, `tests/intralogistics/random_worlds.py`.
**Create:** `tests/layout/test_handles_only.py`.
**Delete:** `benchmarks/layout_parity.py`.
**Interfaces (produces):** component signatures name only handles (`GraphRef`; `NodeRef | PortRef`); `ReservationController(*, env, graph, node_capacity=1, derive_corridors=True, hold_back_dead_ends=True, policy=None, priority_fn=None)` without `capacities` and `zones`; `build_simple_system` returns a `GraphRef` always; `random_worlds.build_world` loses `mode`.

Error messages (spec §5.3), code `foreign-handle`:

- graph: `"<Component>(graph=...) takes layout.graph(); declare the graph with env.layout.set_network(Explicit(graph))"`
- node: `"<Component>(<parameter>=...) takes layout.node(id) or layout.port(...) handles, not Node objects"`

- [ ] Tests (`test_handles_only.py`): each of `FleetCoordinator`, `ReservationController`, `Warehouse` (both bay parameters), `AGV`, `ChargingStation`, `ParkingArea` raises the message of its case for a concrete object, parametrized; a handle of another environment's layout raises; `AGV(initial_node=None)` and a warehouse without bays are accepted; `test_reading_before_preparation_names_the_fix`: each physical getter of spec §2 raises `LayoutNotReady` whose message names `env.layout.prepare()` and `env.activate()`; `test_late_components_resolve_at_once`: after activation an `AGV` and a `ParkingArea` constructed with handles have their canonical node at once, and a `ReservationController` constructed then raises; one network per environment: a second `set_network` raises as today, and two fleets with `layout.graph()` share the graph; `Explicit` rejects an arc between two nodes at the same position (L16).
- [ ] Remove (spec §5.9): the no-layout branches of `node_input` and `graph_input`; `_explicit_graph_use`, `_register_graph_use`, `_uses_explicit_graph`, `check_existing_endpoints` and the two "explicit graph consumers" errors; the transitional rule of Task 4; construction-time node binding in `FleetCoordinator`; mutable bay lists; the concrete halves of the union types; `capacities=` and `zones=` of `ReservationController` with the both-places rule; the explicit branch of `build_simple_system` and "nodes mapping requires a layout"; the graph comparison in `FreeFlowController.register_fleet`.
- [ ] Remove for L16: `test_zero_length_arc_is_traversed_in_zero_time`, `test_an_arc_whose_chord_has_no_direction_leaves_the_heading_unchanged`, and every branch in `travel.py`, `routing.py` (`arc_direction`, `heading_changes`, the fastest router's zero-chord rule) and `kinematics.py` that can only be reached with a chord of length 0. Show with coverage that no line became unreachable; a branch that is still reachable (an arc of length 0 cannot exist, a run of length 0 can) stays.
- [ ] Remove the tests that pinned the two-mode rules (the explicit-then-layout errors in `tests/layout/test_binding.py`, the explicit side of the two parity tests) and `benchmarks/layout_parity.py`.
- [ ] Gates: `grep -rn "_explicit_graph_use\|_uses_explicit_graph\|_register_graph_use" src` finds nothing; `uv run --all-extras ty check src` is clean with the narrowed signatures.
- [ ] Full suite; commit `feat(intralogistics)!: models are built through the layout only` with a `BREAKING CHANGE:` footer naming handles, the removed `capacities=`/`zones=` and arcs between coincident nodes.

### Task 10 — Documentation and migration notes

**Modify:** `docs/guides/layout.md`, `docs/guides/intralogistics.md`, `docs/guides/events-and-traces.md`, `docs/api/layout.md`, `docs/api/intralogistics.md`, `docs/introduction/architecture.md`, `docs/guides/index.md`, `docs/development/studio.md` where it explains the layout of a managed model, `CHANGELOG.md` (the 0.18.0 entry; its date is set by the release step of the last 0.18.0 work, not here), `skills/simulatte-intralogistics/`, `CLAUDE.md`, `specs/2026-10-10-sp2-layout-design.md` (an appended amendment section), `tests/layout/test_acceptance.py` and `tests/test_docs_run_blocks.py` for the changed blocks.

- [ ] The layout guide opens with: every model has a layout; a production-only model needs no layout code; the three ways to declare a network; handles; reading physical fields after preparation; what is frozen. The "Migration: explicit graphs" framing and the "Existing scripts remain valid" block go. `docs/api/layout.md` loses the two-mode table. The intralogistics guide's zone and capacity examples use the declaration.
- [ ] `CHANGELOG.md`, 0.18.0: under Changed and Removed, the facts of spec §5; under Migration, the table of spec §6 with one worked example (the two-node model of the layout guide before and after), and the statements that traces always carry the layout, that builder AGVs are named `vehicle-<i>`, that a lift or transfer point needs a small offset and an explicit `length`, and that more than 100,000 entities created before activation need `Layout(env, limits=...)`.
- [ ] SP2: append "Layout-only amendment (2026-11-23)": `env.layout` always exists; A3 to A5 and the "Without a Layout" paragraphs are superseded; the import rule as narrowed by L9; `Explicit` arcs carry `length`; one network per environment.
- [ ] Every Python block added or changed runs (`tests/test_docs_run_blocks.py`, `tests/layout/test_acceptance.py`, and by hand for blocks those do not cover; say which). `uv run --all-extras zensical build` reports no issues. `grep -rnE "without a layout|explicit mode|no-layout" docs skills CLAUDE.md` finds only the changelog's migration section and release history.
- [ ] Full suite; commit `docs: every model is built through its layout`.

### Task 11 — Verification and acceptance

**Modify:** `specs/research/2026-11-23-layout-only-models-acceptance.md`, `tests/intralogistics/test_random_worlds.py`, `.github/workflows/ci.yml` only if a lane lists test files that were added or renamed.

- [ ] Safety run (spec §8.4): at least 2,000 mission runs of `random_worlds` on layout-built worlds, both controllers and the three routers, `env.run()` without `until`, with the monitor: no physical violation, no holdings or replay mismatch, no exception, every run returns. Record counts and events. Add five fixed seeds with the monitor to `test_random_worlds.py` as permanent tests.
- [ ] Walk spec §10 line by line and record, per item, the test or the measurement that covers it.
- [ ] Gates, each run and its result line recorded: full suite with coverage; ruff check; ruff format; ty; zensical build; in `studio/`: `pnpm typecheck && pnpm test && pnpm build && pnpm test:browser`; the PyPy lane command from `ci.yml`; the overhead gate; `tests/intralogistics` and `tests/layout` under CPython 3.11 (`uv run --python 3.11 --all-extras pytest tests/intralogistics tests/layout --no-cov -q`).
- [ ] Record the final numbers of `benchmarks/layout_overhead.py` against the targets of spec §9.
- [ ] Commit `test(layout): acceptance record of layout-only models`.

**Acceptance:** spec §10. The branch is not pushed by this plan.

## Review handoff

Per task, a reviewer checks the diff against the spec sections the task names and runs its own measurement where the task has one (parity, counts of tests and assertions, overhead). The final review covers the whole branch from `e993ca8` and reruns the parity argument indirectly through the safety run, since explicit mode no longer exists to compare with.
