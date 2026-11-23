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

### The run

Made at commit `8a8effa` (the source is that of `8f54323`; the commits after it change only the generator, its tests
and the script), CPython 3.14.3 on an Apple M4 Pro, ten worker processes:

```
uv run --all-extras python benchmarks/layout_parity.py --worlds 1000
```

```
worlds 1000 (seeds 0..999), runs 12000, pairs 6000
equal 6000, differing 0, events compared 13093868, failed in both modes 0, ended at the horizon 0, with monitor violations 0, with a known limit 6, known-limit cases 7
until 20000.0, wall-clock limit 120 s per run, 436 s
```

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

| Target (spec §9) | Before (`2946ebb`) | After (`e0f4dde`) | Met |
|---|---|---|---|
| Construction with `Layout(env)` within 1.5 times construction without, at 1,600 servers | 75 to 77 times (198 to 200 ms against 2.6 ms) | 1.20 to 1.23 times (3.07 to 3.10 ms against 2.52 to 2.56 ms) | yes |
| Activation under 5 ms at 10 servers | 5.8 to 5.9 ms | 0.67 ms | yes |
| Activation under 50 ms at 1,600 servers | 729 ms | 77 to 80 ms | no: floor below |
| No measurable difference per event | 13.5 to 14.0 % slower (1,546 against 1,356 to 1,363 ns per domain event) | 0.4 to 0.5 % (interleaved: 0.4 and 0.9 %), within the spread of repeated runs | yes |

Jobs created before `Layout(env)` are linear: `Layout(env)` and `prepare()` with 10 servers and 1,600 jobs take 5.7 to
6.0 ms, 239 to 240 ms before. The bundle and its hashes now cost their time when first read: 2.5 ms at 10 servers,
267 to 276 ms at 1,600 (a recorded run pays it once, at INITIAL; `env.manifest()` pays it once). The bundles and the
three hashes are those of `2946ebb`: `tests/layout/test_hashes.py` pins a digest of the whole bundle of eleven layout
fixtures and four code-built models read at `2946ebb` (`PINNED_BUNDLES`), besides the pinned hashes of
`tests/layout/test_traffic_fields.py`.

### The run

CPython 3.14.3 on an Apple M4 Pro (macOS), a machine shared with other sessions; every number is the median of nine
repetitions after one warm-up, each on a fresh model with `gc.collect()` outside the timing (the script also prints the
minimum). Two runs on each side; the table gives the range of their medians.

```
uv run --all-extras python benchmarks/layout_overhead.py --repeat 9
```

Before, at `2946ebb` (the script was not yet committed; the source measured is that commit's), first run:

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

After, at `e0f4dde`, first run:

```
construct/100/none               0.120 /      0.113
construct/100/layout             0.180 /      0.173
construct/100/ratio              1.496
construct/1600/none              2.558 /      2.535
construct/1600/layout            3.073 /      3.035
construct/1600/ratio             1.201
prepare/10                       0.662 /      0.644
activate/10/layout               0.667 /      0.654
activate/10/none                 0.012 /      0.012
first_bundle/10                  2.479 /      2.444
prepare/1600                    74.989 /     74.561
activate/1600/layout            76.851 /     76.568
activate/1600/none               2.269 /      2.203
first_bundle/1600              266.877 /    264.403
jobs_before_layout/1600          5.695 /      5.652
run/events                       30204
run/none                        40.384 /     40.086   1337.0 ns per event (median)
run/layout                      40.582 /     39.996   1343.6 ns per event (median)
run/ratio                        1.005
```

The cases are described in the script's docstring. `run` is a 10-server immediate-release pure job shop
(`Scenario.pure_job_shop(n_servers=10)`, no default metrics, no subscriber, seed 7) run to time 450, 30,204 domain
events; nobody observes it (no recorder, no digest, no manifest read, L14). For the per-event row the same run was
also timed in 25 interleaved pairs after a warm-up (a scratch script, not committed): medians 40.37 against
40.73 ms (ratio 1.0087) at `e0f4dde`, and 39.08 against 39.23 ms (1.0037) on the same source before the commit.

### Activation at 1,600 servers: the floor

`prepare()` of 1,600 servers takes 75 to 79 ms, `env.activate()` 77 to 80 ms. Where the time goes (wall clock of
the functions, by timers around them, median of five):

| Part | ms |
|---|---|
| the auto layout of 1,600 targets (`auto_layout`, exact rational arithmetic with `Fraction`) | 21 |
| the final resolution of the layers with the auto layer (`resolve_layers` in `Layout._validate`): merging the 1,600 generated placements, freezing the merged tree, building its 22,400 leaves | 52 |
| the other five resolutions, the network, binding, finalization | 3 |

What made up the 0.72 s before: the bundle validating its own output and normalizing and freezing it (about 54 % of
the profile), the three hashes (18 %), the final resolution checking the generated auto layer against the schema
(about 11 %) and copying every subtree once per leaf, and the rest. The bundle and the hashes are now built on first
read; the auto layer, generated by preparation from validated inputs, is not checked against the schema again; leaves
share one frozen tree. The tests check what preparation no longer checks: an autouse fixture (`tests/conftest.py`,
`check_bundles`) builds the bundle of every layout the suite prepares, reads it back through
`LayoutBundle.from_dict` (which validates every field), requires it equal and its hashes those of its data, and
requires the unchecked auto layer equal to a checked one.

Under 50 ms needs the remaining linear work to shrink: the final resolution copies the merged tree several times (the
applicable layer thawed, the merge stamping thawed values, the tree frozen for the explicit and the full leaves and
again by `ResolvedValues`), and the auto layout computes in `Fraction`. Neither changes a hash or the bundle format;
both are reworkings of `layout/layers.py` and `layout/auto.py` that this task did not take on.

### Imports and the per-event path

A layout without a network imports no fleet, traffic, reservation or AGV module, also through preparation, a run,
`env.manifest()` and a bundle read (`tests/core/test_public_api.py`); it imports `simulatte.intralogistics`,
`.graph` and `.network`, since the package now imports the names of `__all__` on first use. After activation,
`Environment.step` reads one field and imports nothing (`tests/layout/test_activation.py`). Entities attached during
the run still make one call into the layout (`Layout._before_attach`), which returns at its first check.

### Parity after the change

On the source of `e0f4dde`:

```
uv run --all-extras python benchmarks/layout_parity.py --worlds 100
```

```
worlds 100 (seeds 0..99), runs 1200, pairs 600
equal 600, differing 0, events compared 1180604, failed in both modes 0, ended at the horizon 0, with monitor violations 0, with a known limit 0, known-limit cases 0
```

The script exited with status 0.
