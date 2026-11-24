# Layout-only models, acceptance

Branch `feature/layout-only-models`, spec `specs/2026-11-23-layout-only-models-design.md`. The work makes the layout
the only way to build a model. This file records the evidence each task produces for the spec's verification (§8) and
acceptance (§10): the parity run that allows explicit mode to be removed (L8), and the measurements and gates of the
later tasks.

## Parity of explicit and layout models (Task 2)

### Result

The same 1,000 random worlds, each built once with concrete graph objects (explicit mode) and once through
`Explicit` and layout handles (layout mode), run alike under both traffic controllers and the three routers: 6,000
compared pairs, 6,000 equal, no exception, no run left at the horizon, no monitor violation. Six pairs, in four
worlds, meet a known limit of the fleet, the same in both modes (see "Known limits met by the random worlds"); it is
not a difference between the modes. The known difference of spec §8.1 (`Explicit` dropping `Arc.length`) is gone since
Task 1 (`15a52de`, `8f54323`); the same comparison against the source before Task 1 finds it (see "The comparison
finds a difference").

This is the record of the two modes agreeing at the last commit where both exist: Task 9 removes explicit mode
after it.

### The run

Made at commit `e4afae4` (Task 8's last commit, the last one with both modes; its `src`, `tests` and `benchmarks`
extracted with `git archive` and the extracted `src` put first on `PYTHONPATH`), CPython 3.14.3 on an Apple M4 Pro, ten
worker processes:

```
uv run --all-extras python benchmarks/layout_parity.py --worlds 1000
```

```
worlds 1000 (seeds 0..999), runs 12000, pairs 6000
equal 6000, differing 0, events compared 13093868, failed in both modes 0, ended at the horizon 0, with monitor violations 0, with a known limit 6, known-limit cases 7
until 20000.0, wall-clock limit 120 s per run, 379 s
```

The counts, the feature table below and the known-limit cases are those of the first run of this record (commit
`8a8effa`, 436 s): Tasks 3 to 8 changed neither mode's behaviour on these worlds. Since Task 5 the worlds declare
per-node capacities and zones on `Explicit` in layout mode and give them to the controller in explicit mode.

The script exited with status 0. Each world (`world_spec(seed)` in
`tests/intralogistics/random_worlds.py`, seeds 0 to 999) runs twelve times: two modes, `ReservationController` and
`FreeFlowController`, and `ShortestDistanceRouter`, `FastestRouter` and `CongestionAwareRouter`. A pair is the two
modes under one controller and one router. Each run is bounded by a simulated horizon of 20,000 (orders arrive before
600) and a wall-clock limit of 120 s; every run ended on its own, with an empty event queue, before the horizon, so the
bound never cut a run short. "Events compared" counts the events of both streams of every pair.

### What is compared

Two runs are equal when their `RunRecord`s are:

- every event emitted after the prelude, domain and observer events alike, as its type and all its fields (time,
  deltas and payload) except `seq` and `ordinal`. The prelude, what is emitted before the initial state is captured at
  activation, is left out: it differs between the modes by construction, in the attachment counts (nodes are bound at
  preparation in layout mode), in layout mode's `agv.placed` events and in whether an AGV is created with its node or
  without one. These are the exclusions of the two existing parity tests
  (`tests/layout/test_binding.py::test_explicit_layout_semantic_parity_and_speed_limited_transport`,
  `tests/layout/test_builders.py::test_prefixed_explicit_and_layout_trajectories_and_kpis_match`), and no other;
- the initial state, the final state of every entity, the end time, whether the run ended on its own;
- the monitor's violations and the known limits it recognised;
- `env.fingerprint()`, as both existing parity tests compare it.

### The worlds

The generator ports the worlds of the traffic reviews (`final-scratch-control/world.py`) to a pure-data description
(`WorldSpec`), so that one description builds both modes: grids of 2 to 5 by 2 to 4 nodes with removed links, chains
and spurs, 21,829 nodes in all; 4,481 AGVs, at least two in every world's first fleet and at least one in every second
fleet; 21,469 orders. Low batteries hold 1 to 5 times the span of the grid (spacing times its width plus height), with
idle power 0, 0.02 or 0.05; the span is at least four spacings, so a full battery always covers the longest arc (1.3
spacings): no mission can be unaffordable at full charge, and no run was cut by the horizon.

For each feature, the worlds whose spec contains it, the worlds in which at least one of the twelve runs exercised it
(the recorded events show it), and the pairs (of 6,000) that exercised it; `present` and `exercised` in
`random_worlds.py` define each one. Waits, stuck waits, deadlocks and make-way moves are not part of a spec.

| Feature | Worlds containing it | Worlds exercising it | Pairs exercising it |
|---|---|---|---|
| an AGV moves | 1,000 | 1,000 | 5,994 |
| the second fleet moves | 295 | 289 | 1,702 |
| an arc with a recorded length traversed | 540 | 494 | 2,500 |
| a block piece traversed | 387 | 386 | 2,314 |
| a one-way arc traversed | 371 | 296 | 1,391 |
| a passing segment traversed | 607 | 575 | 3,023 |
| a passing segment held by two AGVs | 607 | 61 | 112 |
| a node of capacity 2 held by two AGVs | 748 | 516 | 1,342 |
| a zone reserved | 657 | 612 | 1,797 |
| a zone waited for | 657 | 516 | 1,375 |
| a zone held by two AGVs | 657 | 154 | 406 |
| a hold-back zone reserved | 422 | 377 | 1,087 |
| a corridor reserved | 877 | 797 | 2,391 |
| parking entered | 755 | 732 | 3,705 |
| an order cancelled | 382 | 326 | 1,689 |
| a breakdown (an interrupted mission, not a cancellation) | 377 | 319 | 1,737 |
| charging | 509 | 408 | 1,750 |
| stranding | 509 | 409 | 1,783 |
| a traffic wait | | 979 | 2,905 |
| a stuck wait | | 712 | 1,667 |
| a deadlock | | 866 | 2,312 |
| a make-way move | | 848 | 2,369 |

"Containing" counts what a spec declares: a passing segment, a capacity-2 node or a zone may be on no route, a
corridor exists only when derived (and the network has a chain), a low battery may last the whole run. Six of the
6,000 pairs show no move at all; in three of them, seed 2 under `ReservationController` with each router, the AGVs
never get the claim of their first leg and the run ends with its waits reported stuck or in an unresolved deadlock.

In explicit mode a world with a block length uses `LayoutGraph.subdivide(block_length)`; in layout mode
`Explicit(graph, block_length=...)`. Per-node capacities go to `ReservationController(capacities=)` in both modes (the
controller matches nodes by value) until Task 5 moves them to the layout. Zones go to the controller in explicit mode
and to `Explicit(zones=...)` in layout mode.

### Where the generator equalizes the modes, and what it leaves out

The modes differ in places that spec L7 and L13 settle for layout mode. The generator builds both sides so that none
shows:

- **Ids.** AGVs are constructed with `agv_id=`, and the fleets, warehouses, chargers and parking areas are named, as
  the brief prescribes.
- **The order of a zone's members.** A controller zone keeps its members in the order given; the layout resolves a
  zone to its nodes in id order, then its node pairs in id order (`resolve_zones` in `layout/network.py`). The order
  is the `members` field of the zone entity, so it shows in the initial state. In explicit mode the generator passes
  the members in the layout's order. Checked: with the explicit members reversed, 21 of the 26 worlds with zones among
  seeds 0 to 39 differ, first in the zone's `members`. `ReservationController(zones=)` is removed with L13.
- **Zones under a block length.** In explicit mode the generator builds a zone on the subdivided graph by the layout's
  own rule (`zone_members`): a pair stands for all its pieces and the nodes generated between them, and its end nodes
  are members only when the zone names them.

The worlds also stay within what both modes can express or what the generator chose, and the parity says nothing
beyond it:

- AGV starts, bays, chargers and parking areas sit on declared nodes only, never on a node generated by a block
  length (`layout.node(id)` addresses declared nodes).
- Bays are deduplicated within a warehouse.
- No more AGVs, of all fleets, start in a zone than its capacity, since placement would raise at activation in both
  modes (`cannot place ...`).
- Orders are created only after activation, by a process of the run, and no collector is attached
  (`default_metrics=False`).

### Known limits met by the random worlds

**A parking slot kept while charging on the way to park.** An AGV takes a slot of a parking area when it sets off to
park (`parking.entered`). When it starts a charge before it gets there, at a charger it turns to because its battery
is low or at one on its route, it keeps the slot while it charges at the charger's node, and releases it
(`parking.left`) only when the charge ends. The slot is withheld from other AGVs meanwhile. In seed 619
(`FreeFlowController`, `ShortestDistanceRouter`) agv-a enters P0 at 97.0, is rerouted to the charger at 122.0, charges
from 148.6 to 220.1, and leaves P0 at 220.1.

The run met it in 7 cases in 6 pairs, the same in both modes: seed 353 (`ReservationController`,
`CongestionAwareRouter`), seed 398 (`ReservationController`, `FastestRouter`), seed 581 (`FreeFlowController`,
`ShortestDistanceRouter`, two cases) and seed 619 (`FreeFlowController`, each of the three routers). It is behaviour
of the fleet that predates this work and that this work does not change. The monitor records it apart, under
`known_limits`, only on the case's own evidence in the event stream: the slot was entered, the AGV was then routed to
the area's node, it started a charge before reaching that node, it was flagged only while `CHARGING` at a charger's
node, and the slot was released at the instant the charge ended. A slot held by an AGV resting elsewhere in any other
way stays a violation (`test_a_parking_slot_held_by_an_agv_resting_elsewhere_for_another_reason_is_a_violation`),
and so does a slot kept through such a charge but not released as it ends.

### The comparison finds a difference

- Against the source before Task 1 (`2129469`, extracted with `git archive` and put first on `PYTHONPATH`), the
  parity tests of `tests/intralogistics/test_random_worlds.py` fail for the four of their five seeds whose worlds
  have a recorded arc length (88, 182, 236, 272) and pass for seed 26, which has none.
- With `Explicit` patched to drop every recorded length again, 35 of seeds 0 to 59 differ under
  `ReservationController` and `ShortestDistanceRouter`: exactly the 35 worlds with a recorded length. The first
  difference is an `agv.routed` event with another route or cost.

### The monitor

`Monitor` in `random_worlds.py` is the control review's monitor (`final-scratch-control/monitor.py`), ported to built
worlds. Its geometry (nodes, lengths, passing flags, adjacency, zone members, corridors) comes from the `WorldSpec`
(`reference_graph`: the spec's graph, subdivided by the block length), not from the graph the run built, and it
reports a built graph whose nodes or arcs differ from it; so after explicit mode is gone it still notices a layout
that resolves a network wrongly. After every domain event it replays the deltas from the first event and from the
initial state and compares them with the live entities. With a `ReservationController` it also keeps its own body
model of every vehicle, from the motion events only, and checks node, segment, zone and corridor admission against
it, that the holdings cover the bodies (exactly for a vehicle at rest and at the end of every travel), its own
derivation of the corridors, and the entities against the holdings ledger; under either controller, parking capacity,
parking slots of resting AGVs, and that moves start and end where the body is. At the end of a run that ended on its
own: no claim of a moving AGV, no review timer, every open wait explained by a stuck report or an unresolved deadlock,
no motion left, no parking slot held away from its node. It records violations instead of raising, and the known
limit above apart from them.

### Tests

`tests/intralogistics/test_random_worlds.py`: `world_spec` is equal for equal seeds, also in processes with other
`PYTHONHASHSEED` values; no world of seeds 0 to 199 has two nodes at one position; every world of seeds 0 to 999 has
at least two AGVs in its first fleet and one in every fleet; seeds 26, 88, 182, 236 and 272 run with equal
`RunRecord`s in both modes under `ReservationController` and the default router, end on their own, show no violation
and exercise between them charging, stranding, zones and a passing segment held by two AGVs, recorded lengths, block
pieces, the second fleet, a capacity-2 node held by two, one-way arcs, cancellations, deadlocks and make-way moves;
the monitor reports a seeded admission violation (node capacity ignored) and a layout that drops recorded lengths
(`built arcs differ from the spec`); seed 619 under `FreeFlowController` and `ShortestDistanceRouter` records the known
limit and no violation, and the same run with parking slots never given back reports `parking slot of an AGV resting
elsewhere` and no known limit.

## Cost of a layout (Task 3)

### Result

Against the targets of spec §9, at 1,600 servers and at 10:

| Target (spec §9) | Before (`2946ebb`) | After (`3a01414`) | Met |
|---|---|---|---|
| Construction with `Layout(env)` within 1.5 times construction without, at 1,600 servers | 75 to 77 times (198 to 200 ms against 2.6 ms) | 1.21 times (3.01 to 3.03 ms against 2.49 to 2.50 ms) | yes |
| Activation under 5 ms at 10 servers | 5.8 to 5.9 ms | 0.49 ms | yes |
| Activation under 50 ms at 1,600 servers | 729 ms | 48.9 to 49.2 ms | yes, by about 1 ms |
| No measurable difference per event | 13.5 to 14.0 % slower (1,546 against 1,356 to 1,363 ns per domain event) | 0.2 to 0.5 % (1,328 to 1,341 against 1,321 to 1,339 ns), within the spread of repeated runs | yes |

Jobs created before `Layout(env)` are linear: `Layout(env)` and `prepare()` with 10 servers and 1,600 jobs take 4.9 to
5.0 ms, 239 to 240 ms before. The bundle and its hashes now cost their time when first read: 2.5 ms at 10 servers,
266 ms at 1,600 (a recorded run pays it once, at INITIAL; `env.manifest()` pays it once). The bundles and the three
hashes are those of `2946ebb`: `tests/layout/test_hashes.py` pins a digest of the whole bundle of eleven layout
fixtures and four code-built models read at `2946ebb` (`PINNED_BUNDLES`), besides the pinned hashes of
`tests/layout/test_traffic_fields.py`.

The activation target at 1,600 servers is met with little margin: on a loaded machine a run can exceed it.

### The run

CPython 3.14.3 on an Apple M4 Pro (macOS), a machine shared with other sessions. Every number is the median of the
repetitions after one warm-up, each on a fresh model with `gc.collect()` outside the timing (the script also prints the
minimum); the `run` case times its two variants in alternating order (none, layout, then layout, none, and so on), so
that a change of the machine's load falls on both. Two runs on each side; the table gives the range of their medians.
Before: nine repetitions; after: fifteen.

```
uv run --all-extras python benchmarks/layout_overhead.py --repeat 15
```

Before, at `2946ebb` (the script was not yet committed; the source measured is that commit's; `--repeat 9`, the `run`
variants not yet interleaved), first run:

```
construct/100/none               0.113 /      0.112
construct/100/layout             1.693 /      1.680
construct/100/ratio             14.926
construct/1600/none              2.596 /      2.545
construct/1600/layout          200.224 /    199.513
construct/1600/ratio            77.129
prepare/10                       5.823 /      5.784
activate/10/layout               5.799 /      5.698
activate/10/none                 0.012 /      0.012
first_bundle/10                  0.000 /      0.000
prepare/1600                   722.362 /    721.190
activate/1600/layout           728.827 /    725.024
activate/1600/none               2.334 /      2.309
first_bundle/1600                0.002 /      0.002
jobs_before_layout/1600        240.342 /    237.846
run/events                       30204
run/none                        40.952 /     40.749   1355.8 ns per event (median)
run/layout                      46.682 /     46.524   1545.6 ns per event (median)
run/ratio                        1.140
```

After, at `3a01414`, first run:

```
commit 3a01414, CPython 3.14.3, arm64, repeat 15 (median / min, ms)
construct/100/none               0.117 /      0.110
construct/100/layout             0.168 /      0.160
construct/100/ratio              1.434
construct/1600/none              2.489 /      2.464
construct/1600/layout            3.009 /      2.976
construct/1600/ratio             1.209
prepare/10                       0.480 /      0.467
activate/10/layout               0.485 /      0.465
activate/10/none                 0.012 /      0.011
first_bundle/10                  2.499 /      2.465
prepare/1600                    47.443 /     47.125
activate/1600/layout            48.918 /     48.633
activate/1600/none               2.233 /      2.208
first_bundle/1600              266.121 /    263.339
jobs_before_layout/1600          4.932 /      4.888
run/events                       30204
run/none                        39.893 /     39.188   1320.8 ns per event (median)
run/layout                      40.105 /     39.875   1327.8 ns per event (median)
run/ratio                        1.005
```

The second run: `prepare/1600` 47.3 ms, `activate/1600/layout` 49.2 ms, `run/ratio` 1.002. The cases are described in
the script's docstring. `run` is a 10-server immediate-release pure job shop (`Scenario.pure_job_shop(n_servers=10)`,
no default metrics, no subscriber, seed 7) run to time 450, 30,204 domain events; nobody observes it (no recorder, no
digest, no manifest read, L14). The script prints the commit it measures, with `+changes` when `src` differs from it,
or `external` and the package path when another tree is measured through `PYTHONPATH`.

### What activation at 1,600 servers spends

`prepare()` of 1,600 servers takes 47.3 to 47.4 ms, `env.activate()` 48.9 to 49.2 ms. By timers around the functions
(median of five, with the timers' own cost):

| Part | ms |
|---|---|
| the auto layout of 1,600 targets (`auto_layout`; exact rational arithmetic with `Fraction` for the bounds) | 15 |
| the final resolution of the layers with the auto layer (`resolve_layers` in `Layout._validate`): merging the 1,600 generated placements into the layers (about 20), freezing the merged tree and building its 22,400 leaves (11) | 31.5 |
| the other five resolutions, the network, binding, finalization | 3 |

What made up the 0.72 s before: the bundle validating its own output and normalizing and freezing it (about 54 % of
the profile), the three hashes (18 %), the final resolution checking the generated auto layer against the schema
(about 11 %) and copying the merged tree and its subtrees several times, and the rest. Now the bundle and the hashes
are built on first read; the auto layer, preparation's own output, already normalized and frozen, is taken as it is;
the layers are not thawed in full before merging; the merged tree is frozen once per resolution and its leaves share
it, and `ResolvedValues` keeps them instead of copying them again; `placement_defaults` and the auto layout's exact
paddings are cached and its exact cell sizes computed once. None of this changes a value: the pins above hold, and so
does the parity run below. What remains is the merge and the auto layout's arithmetic, both linear in the targets; they
are not a floor (reducing them changes no hash and no format), but this task stops at the target.

The tests check what preparation no longer checks: an autouse fixture (`tests/conftest.py`, `check_bundles`) builds,
at the end of each test, the bundle of every layout the test prepared, and every bundle a test reads, reads it back
through `LayoutBundle.from_dict` (which validates every field), requires it equal and its hashes those of its data, and
requires the auto layer taken unchecked equal to a checked one. It checks nothing inside `prepare()`, so an error that
moved from preparation to the first read fails the test that expects it at `prepare()`. Stage and binder names, saved
in the bundle, are checked against the schema's `id` where they are declared.

### Imports and the per-event path

A layout without a network imports no fleet, traffic, reservation or AGV module, also through preparation, a run,
`env.manifest()` and a bundle read (`tests/core/test_public_api.py`); it imports `simulatte.intralogistics`,
`.graph` and `.network`, since the package now imports the names of `__all__` on first use. After activation,
`Environment.step` reads one field and imports nothing, and an entity attached after activation (a job arriving during
the run) makes no call into the layout (`tests/layout/test_activation.py`, with timeouts and with a job shop whose jobs
arrive during the steps). What stays on the per-event path of a model with AGVs is layout mode's existing check of
every node an AGV is placed on or moves to (`AGV._validate_node`, called by `AGV.current_node` and at the end of each
segment by the travel executor, which calls `Layout._validate_node`).

### Parity after the change

On the source of `3a01414`:

```
uv run --all-extras python benchmarks/layout_parity.py --worlds 100
```

```
worlds 100 (seeds 0..99), runs 1200, pairs 600
equal 600, differing 0, events compared 1180604, failed in both modes 0, ended at the horizon 0, with monitor violations 0, with a known limit 0, known-limit cases 0
```

The script exited with status 0.

## Every environment owns a layout (Task 4)

### Result

`env.layout` returns the environment's layout and creates it on first access; activation creates it when neither
`env.layout` nor `Layout(env, ...)` did. A production-only model therefore gets its layout at activation, and its trace
carries it in INITIAL and in the manifest. Until explicit mode is removed, an environment with an explicit graph consumer
has no layout (`env.layout` raises, activation creates none).

- The G1 reference shop (`tests/core/test_g1_acceptance.py`) has the ids, the 10,700 domain events and the fingerprint
  (digest `e8a442d2…`, no KPI scalars) it had at `8957ddb`; the pinned G1 digest and trace content are unchanged
  (`tests/layout/test_environment_layout.py::test_a_production_model_keeps_its_ids_events_and_fingerprint`).
- The generated trace fixtures `shop_small`, `lumscor` and `cell_ops` were regenerated: their canonical content differs
  from `8957ddb` only in `layout` (now the bundle) and in the manifest's `layout` (now its metadata); events, states,
  chunks and fingerprints are equal. The four fixtures that already had a layout are unchanged. `frozen/` is untouched.
- The CI overhead gate passes with its present limits; this change costs nothing measurable on its workload.
- `benchmarks/layout_parity.py --worlds 100`: 600 pairs equal, 1,180,604 events, as after Task 3; exit status 0.

### CI overhead gate

Run as `benchmarks/README.md` describes, mode `none`, `--warmup 1 --repeat 5 --processes 3`, CPython 3.14.3 on the
Apple M4 Pro, at `051ac30`. The branch side ran from the checkout's `.venv` (the source of `051ac30`; a wheel of the
checkout cannot be built without the Studio assets), 0.12.0 in its own environment with the checkout's dependency
versions as constraints, and the stripped copy made by `strip_debug.py` from it.

| Baseline | Workload | Baseline median | Branch median | Overhead | Limit | Verdict |
|---|---|---|---|---|---|---|
| released 0.12.0 | `jobshop10-u90-5k` | 0.642 s | 0.479 s | −25.4 % | +5.0 % (3.0 + 2.0) | pass |
| released 0.12.0 | `jobshop10-u95-5k` | 0.678 s | 0.519 s | −23.4 % | +5.0 % (3.0 + 2.0) | pass |
| 0.12.0 without `env.debug` calls | `jobshop10-u90-5k` | 0.449 s | 0.479 s | +6.8 % | +8.5 % (6.5 + 2.0) | pass |
| 0.12.0 without `env.debug` calls | `jobshop10-u95-5k` | 0.486 s | 0.519 s | +6.9 % | +8.5 % (6.5 + 2.0) | pass |

The margin against the stripped copy is 1.6 to 1.7 points. Measured in the same session, `8957ddb` against `051ac30` on the same workloads: 0.480 s against 0.480 s (u90, ratio 1.000) and
0.513 s against 0.516 s (u95, ratio 1.005, within the spread of the processes); `8957ddb` against the stripped copy:
+7.0 % and +5.7 %, a second branch run +7.0 % and +6.2 %: the margin is the one the branch had before this task. The
gate's shop creates its jobs during the run, so its layout has twelve targets (ten servers, the shop floor and the
pre-shop pool) and activation costs about half a millisecond of a run of half a second.

### What a layout costs now

`benchmarks/layout_overhead.py --repeat 15`, two runs at `051ac30`. The `none` variants no longer mean a model without a
layout: they do not call `Layout(env)`, and activation creates the layout. So `activate/*/none` now includes creating,
preparing and finalizing it, and `run/none` runs with a layout created at activation.

```
commit 051ac30, CPython 3.14.3, arm64, repeat 15 (median / min, ms)
construct/100/none               0.116 /      0.109
construct/100/layout             0.176 /      0.162
construct/100/ratio              1.515
construct/1600/none              2.499 /      2.463
construct/1600/layout            3.023 /      3.000
construct/1600/ratio             1.210
prepare/10                       0.482 /      0.470
activate/10/layout               0.491 /      0.483
activate/10/none                 0.514 /      0.504
first_bundle/10                  2.516 /      2.490
prepare/1600                    47.782 /     47.486
activate/1600/layout            49.240 /     49.010
activate/1600/none              49.769 /     49.370
first_bundle/1600              269.436 /    267.816
jobs_before_layout/1600          4.986 /      4.943
run/events                       30204
run/none                        40.227 /     39.843   1331.9 ns per event (median)
run/layout                      40.247 /     40.072   1332.5 ns per event (median)
run/ratio                        1.000
```

The second run: `activate/10/none` 0.518 ms, `activate/1600/layout` 50.2 ms, `activate/1600/none` 50.5 ms,
`prepare/1600` 47.3 ms, `run/ratio` 1.000.

| | `3a01414` (Task 3) | `051ac30` |
|---|---|---|
| activation of a model without `Layout(env)`, 10 servers | 0.012 ms (no layout) | 0.51 to 0.52 ms (layout created) |
| activation of a model without `Layout(env)`, 1,600 servers | 2.2 ms (no layout) | 49.8 to 50.5 ms (layout created) |
| activation with `Layout(env)`, 1,600 servers | 48.9 to 49.2 ms | 49.2 to 50.2 ms |
| run per event, layout created at activation against `Layout(env)` | (no layout: ratio 1.002 to 1.005) | ratio 1.000 |

Creating the layout at activation costs what `Layout(env)` costs: 0.5 ms at 10 servers, under the 5 ms target. At 1,600
servers activation is at the 50 ms target, as recorded for Task 3 (ruling T3-c): 49.2 to 50.5 ms in these runs. The
gate above decides, and it passes. The per-event path is unchanged.

### Imports

`import simulatte`, `Environment()` and building a production model import no layout module; activation imports the
layout package, `simulatte.intralogistics`, `.graph` and `.network`, and none of the fleet, traffic, reservation or AGV
modules (`tests/layout/test_environment_layout.py::test_a_layout_created_at_activation_imports_no_fleet_traffic_or_agv_module`,
besides the tests of `tests/core/test_public_api.py`).

### The test suite

Every activated environment of the suite now prepares a layout, and the autouse fixture `check_bundles` builds and checks
the bundle of each at teardown. The full suite took 378 s (4,829 passed, 1 skipped), against 375 to 377 s after Task 3:
no measurable change.

## Capacities declared on the network (Task 5)

### Result

`Explicit(graph, capacities={node_id: n})` declares the capacity of single nodes of an explicit network. The 200 random
worlds, built with `Explicit(capacities=..., zones=...)` (zone members as node ids and id pairs) in layout mode and with the controller's `capacities=` and
`zones=` in explicit mode, run alike: 1,200 pairs equal, none differing, no violation, exit status 0.

### The run

Commit `f28b89f` (the worlds declare capacities and, from this commit, zones by node id and id pair), from the repository root:

```
uv run --all-extras python benchmarks/layout_parity.py --worlds 200
```

```
worlds 200 (seeds 0..199), runs 2400, pairs 1200
equal 1200, differing 0, events compared 2433672, failed in both modes 0, ended at the horizon 0, with monitor violations 0, with a known limit 0, known-limit cases 0
until 20000.0, wall-clock limit 120 s per run, 71 s
```

The same run with the zones declared by address (`5b4469c`) gave the same counts, 68 s. The worlds that declare a node of capacity 2 are 152 of the 200 (99 of them ran two vehicles on such a node), and 124
contain a zone, as in Task 2. The monitor now also compares, from the `WorldSpec`, the node capacities and the zones
(members, capacity, hold-back) that the controller's network resolved, in both modes; it found no difference.

### What was there

A per-node capacity of an explicit node could already be declared: the schema's `explicitNode` has an optional
`capacity`, the resolver reads it, and a `NetworkOverride` on the node address sets it too. Only the code-side
`ExplicitNode` did not carry it. `capacities` fills that field, so no schema field is added and the bundles of networks
that declare no capacity are unchanged (the pinned hashes of `tests/layout/test_traffic_fields.py` hold). The zones of
schema 1.1 were already declarable on `Explicit`, by address; `Explicit(zones=...)` now also takes a node id and a pair of ids as a member (see below). A test compares `network.leg(u, v)` of every leg of a five-node world
for a zone with node and pair members, declared by address, by id, with a reversed pair and mixed, and given to the controller.

### Both places at once

While `ReservationController(capacities=)` exists, a capacity for the same node in both places is not rejected: the
controller's value wins silently (`{**layout, **controller}` in `_install_layout_graph`). With `Explicit(capacities=
{"N1": 2})` and `capacities={N1: 3}` on the controller, the node has capacity 3, and the other way round 2. A zone id
defined in both places raises. Task 9 removes the second place.

### Monitor checks added

The monitor reports `built node capacities differ from the spec` and `built zones differ from the spec`, and a move on
an arc the spec's graph lacks as `move on an arc the spec does not have` instead of raising; a test for each cuts the
built side in a scratch monkeypatch (a lost node capacity, a zone with another capacity, hold-back or fewer members, a
missing zone, a reference graph without an arc the vehicles use) and checks the violation is reported.

### Zone members by id

A member of `Explicit(zones=...)` may be a node id or a pair of ids (2-tuple or 2-list of `str`); the constructor turns
them into the `explicit` and `explicit_arc` addresses of schema 1.1, so the bundle and the hashes are those of the same
zone declared by address (tested), and `Lattice` and `Lanes` take addresses only. A pair is the segment between the two
nodes, as the pair of a controller `Zone` is (`TrafficNetwork` accepts it when the nodes are adjacent in either
direction): it becomes the address of `a -> b` when the graph has that arc, else of `b -> a`; with both directions the
two addresses resolve to the same pair. An unknown id, or a pair that is not an arc in either direction, raises
`LayoutError` naming the zone and the member at declaration. (The address form never raises for the same mistakes: it
gives a `stale-zone-member` warning at resolution, and the shorthand is stricter because the graph is at hand.)

## Cost of a layout-built network (Task 9b)

Since Task 9 a transport model can only be built through the layout. The Task 8 review measured what that costs against
the explicit build of the same model (`benchmarks/intralogistics.py`): 1.095 times at one shift in every mode, about 1.04
on long runs. Commits: `4560487` (run time), `d697ca6` (preparation) and `894e31e` (one test), after `1bc1e51`. The explicit build is gone, so
the comparison with it is that recorded figure, and a reference tree of it (outside the repository) as a check.

### Result

| | Before (`1bc1e51`) | After (`d697ca6`) |
|---|---|---|
| Layout build / explicit build, mode none, one shift | 1.096 (1.079 to 1.100, five rounds; recorded 1.095) | 1.038 (1.019 to 1.043) |
| The same, six shifts | 1.044 (1.040 to 1.045, three rounds) | 1.012 (1.008 to 1.016) |
| Run time, after / before, one shift, modes none, default, default_logging | | 0.947, 0.950, 0.952 |
| Run time, after / before, six shifts, mode none | | 0.964 (0.948 to 0.975) |
| `prepare()`, explicit square grid of 16, 100, 400, 1,600 nodes, ms | 12.5, 73.2, 303, 1,278 | 5.8, 30.5, 130, 571 |
| `prepare()`, lattice of 16, 100, 400, 1,600 nodes, ms | 6.4, 30.1, 119, 498 | 3.6, 13.5, 52.3, 229 |
| Per node at 400 and 1,600 nodes, explicit, microseconds | 758, 799 | 325, 357 |
| Per node at 400 and 1,600 nodes, lattice, microseconds | 297, 311 | 131, 143 |

The explicit-graph figures are the mean of the two medians of two runs (the runs agree within 1 %); the full output of
all four runs is below. The per-event cost of a production model, the activation targets of Task 3 and the construction
ratio are those they were (`run/ratio` 1.000 to 1.002, `activate/1600/layout` 48.7 and 48.9 against 49.8 and 49.6 ms, `construct/1600`
1.22 to 1.24 in both).

The full suite at the head: 4921 passed, 1 skipped, coverage 99.19 % (baseline 4738 passed, 1 skipped, 99.18 %).

### Run time

The graph a layout builds keeps its nodes and adjacency in `_ReadOnlyDict` containers, `dict` subclasses whose mutating
methods raise `TypeError` ("a layout-built graph is read-only"), instead of `MappingProxyType` objects: a lookup is a
`dict` lookup, so `neighbors` and `arc_between` are one body for both kinds of graph, with no allocation beyond the list
that `neighbors` returns. The adjacency of a frozen graph answers a node outside the graph through `__missing__` with a
shared empty read-only mapping, as before (`[]` and `None`), without inserting anything. `subdivide` on such a graph
still raises. The private `_adjacency`/`_nodes` of a layout-built graph still refuse every change, and a graph a user
writes stays mutable as it was; the public surface of `LayoutGraph` is unchanged, so no public way to change a
layout-built graph existed and none was added. The containers do not pickle (they did not as proxies either).

`AGV._validate_node` accepts the canonical node with one dictionary lookup by id and an identity test against the
layout's table, with no property or method call into the layout; everything else (a copy equal by value, a disabled
node, a node of another network, an unknown node) goes on to `Layout._validate_node`, which raises as before, and the
test of `tests/layout/test_binding.py` pins each of these cases for the layout and the AGV. Setting `current_node` and
ending a segment validate once instead of twice (`_relocate` keeps its own validation: the existing test calls it
directly).

The three causes of the Task 8 attribution (3 shifts, +41 ms in all) were 12.8 ms in the proxies, 7.9 ms in the
allocation and 4.7 ms in validation. On a 3-shift run (five alternating rounds, one process each, against the reference
explicit tree) the layout build took 1.055 times as long before (0.767 against 0.728 s) and 1.019 times after the change
of this part alone (0.742 s). The rest of
the 3.8 % that remains at one shift is preparation (5.8 ms for this 16-node graph, 2.5 % of the run) and about one point
that was not attributed further.

### Preparation of a network

Measured with `benchmarks/layout_overhead.py` (new cases `prepare_network/<kind>/<nodes>`, a model with the network and
no entity; the explicit graph is a square grid of two-way arcs). Preparation is linear in the number of nodes before and
after. The profile (`cProfile`, 400 nodes) of the first run showed 5 resolutions of the layers, each checking the network
patch against the schema again (`LayerBundle.__post_init__`), failing `oneOf` branches each building a message and a
`LayoutError` that the caller threw away (8,785 at 100 nodes), and one `_network_view` resolved twice. What changed, none of which changes a
value, a hash, a bundle, a diagnostic or an error:

- `resolve_layers(..., code_checked=True)` (like `auto_checked`, Task 3): the coordinator's own patches, which
  `declare()` checked and froze, and the stages of an earlier bundle are taken as they are. A patch is still checked once,
  where it is declared; `test_preparing_checks_no_declared_patch_again_and_resolves_the_network_view_once` counts it.
  This was 35 % of the profile of a 100-node explicit network and almost nothing for a lattice, whose patch is small.
- The schema validator matches the branches of a `oneOf`/`anyOf` quietly (an internal `_Mismatch`, no message), computes
  the type check of the one kind the rule names, and no longer creates a closure per call. When every branch of a
  `oneOf` fixes `kind` to a constant (`address`, `nodeAddress`, `elementAddress`, `selector`), only the branches of the
  value's kind are evaluated, since any other rejects the value at its `kind` property; the count of matching branches is
  that of evaluating all of them. 6,014 mutated documents and definitions, run in both trees, had the same outcome each
  (accepted data equal, and for the rejected the same message, code and path; 1,423 accepted, 4,591 rejected), and a test
  compares the filtered outcome with the count over every branch for values of every kind.
- The network view that preparation asks for twice with the same layers and targets is resolved once (one slot, compared
  by identity of the patches).

Evidence that nothing changed: the bundle (a sha256 of the whole `to_dict()`), the three hashes and the diagnostic codes of
300 random worlds under two controllers (seeds 2000 to 2299) and of three explicit grids and three lattices are equal in
`1bc1e51` and in the head (606 bundles); `PINNED_ALL_CHORD`, `PINNED_1_0`, `PINNED_BUNDLES` and `tests/layout/test_hashes.py`
pass.

What remains, from the profile of the head at 400 nodes (`cProfile`, shares of the profiled time; absolute numbers above):
an explicit network spends 41 % in four resolutions of the layers (merge and freeze of an 800-entry patch, now the
largest part), 13 % validating each generated `Address` (every node and arc address is checked against the schema when
built, and a lattice index outside the exact range of a double is rejected there, so the check is an input check), 18 %
in the auto layout of presentation placements (`_BoundsIndex` over exact fractions, twice) and 14 % in the dependency
closure's queries. A lattice spends 35 % in address validation, 37 % in the auto layout, 12 % in the layers. Removing any
of these means changing what is validated or restructuring the auto layout or the closure, which the task excludes; no
target was set. At 1,600 nodes preparation costs 0.57 s for the explicit grid and 0.23 s for the lattice, which a model
that large pays once.

### The runs

Commands, from the repository root for the head (`benchmarks/` as the working directory for the intralogistics runs) and
with `PYTHONPATH=<git archive of 1bc1e51>/src` for the base, the two alternating; the repository's `.venv`, CPython 3.14.3
on an Apple M4 Pro, a machine shared with other sessions: the one-minute load average was 1.4 to 3.6 between the rounds
(the runs below were not run at the same time as any other work of mine). The `uptime` after every round is in the kept
output.

```
python intralogistics.py --mode {none,default,default_logging} --shifts 1 --warmup 1 --repeat 5 --processes 3 --json ...
python intralogistics.py --mode none --shifts 6 --warmup 1 --repeat 5 --processes 3 --json ...
python benchmarks/layout_overhead.py --repeat 9 --json ...
```

The intralogistics medians and ranges are in `benchmarks/intralogistics-baseline.md`, section "Cost of a layout-built
network, 2026-11-24". `layout_overhead.py --repeat 9`, medians in ms (two runs each, before then after):

```
                               before 1     before 2     after 1      after 2
prepare_network/explicit/16     12.912       12.121       5.766        5.812
prepare_network/explicit/100    73.086       73.271      30.379       30.640
prepare_network/explicit/400   302.906      303.595     129.668      130.758
prepare_network/explicit/1600 1276.426     1280.413     569.035      573.894
prepare_network/lattice/16       6.317        6.580       3.600        3.606
prepare_network/lattice/100     29.361       30.877      13.431       13.507
prepare_network/lattice/400    118.354      119.442      52.240       52.455
prepare_network/lattice/1600   496.763      498.325     228.543      229.306
run/ratio (layout / none)        1.002        1.000       1.002        1.002
```

### Parity of the runs

`RunRecord` (the events with their fields, initial and final state, time, violations, known limits and the fingerprint
digest, as a sha256 over all of it) of the random worlds of `tests/intralogistics/random_worlds.py`, seeds 1000 to 1299
(300 worlds), under both controllers and the three routers (`shortest`, `fastest`, `congestion`): 1,800 runs, 1,990,304
events, run in a `git archive` copy of `1bc1e51` and in the head (`d697ca6`, the source of `894e31e`), with
`PYTHONHASHSEED=0`; all 1,800 equal, none with a monitor violation, none that failed to end (the same comparison after
`4560487` alone, run before the preparation commit, was equal too). The six fingerprints of
`tests/intralogistics/test_random_worlds.py` hold.

### TDD

The read-only test (`TestLayoutBuiltGraphIsReadOnly`) failed before the change on the message of the refusal (`1 failed, 5
passed`: "'mappingproxy' object does not support item assignment" against "read-only"); the node-validation tests pin the
contract and passed before and after. For preparation, run against the tree of `1bc1e51`:

```
FAILED tests/layout/test_layers.py::test_code_stages_taken_as_checked_resolve_like_the_ones_checked_again
FAILED tests/layout/test_activation.py::test_preparing_checks_no_declared_patch_again_and_resolves_the_network_view_once[4]
FAILED tests/layout/test_activation.py::test_preparing_checks_no_declared_patch_again_and_resolves_the_network_view_once[40]
3 failed, 171 passed
```

(the schema tests pin the unchanged outcomes and pass on both).
