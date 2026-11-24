# Layout-only models, acceptance

Branch `feature/layout-only-models`, spec `specs/2026-11-23-layout-only-models-design.md`. The work makes the layout
the only way to build a model. This file records the evidence each task produces for the spec's verification (§8) and
acceptance (§10): the parity run that allows explicit mode to be removed (L8), the measurements of the later tasks, and,
from Task 11, the safety run on layout-built worlds, the walk of §10 and the gates.

Each section states the commit it was measured at. The sections of Tasks 2 to 5 describe the branch while explicit mode
still existed; Task 9 removed it (`999c3a7`), and the tests and scripts those sections name for the comparison of the two
modes are gone or changed since (`benchmarks/layout_parity.py` is at `e4afae4`). Where a later task changed what a section
says, the section says so.

## Summary

| Question | Answer | Section |
|---|---|---|
| Do explicit and layout models run alike? | Yes: 6,000 pairs equal at `e4afae4`, the last commit with both modes. | Parity (Task 2) |
| Does the layout-built world stay safe? | Yes: 12,000 runs of 2,000 worlds, no violation, no exception, every run returned. Two known limits, both predating this work. | Safety run (Task 11) |
| Are the performance targets of §9 met? | Yes. Activation at 1,600 servers meets its target at the line (49.1 to 49.4 ms against 50 ms). | Final numbers against §9 (Task 11) |
| Do the gates pass? | Every gate was run and passed. | Gates (Task 11) |
| Is every item of §10 covered? | Yes, with the gaps named in the walk (items 2 and 5). | Walk of §10 (Task 11) |

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

(the script was deleted with explicit mode by Task 9; it is in the tree of `e4afae4`)

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

**Waits of a deadlock left unreported after a member's wait was withdrawn.** (Found by the Task 2 review; not met by the
parity run, which ran seeds 0 to 999 and had none. The safety run of Task 11 meets it once; see "Safety run".) Under
`ReservationController`, a deadlock that no reaction could resolve is reported once, as `traffic.deadlock` with action
`unresolved`, and the controller remembers the set of AGVs (`_unresolved`). When any wait of that set ends, the controller
forgets the whole set (`_end_wait`). If the remaining waits still form a deadlock and nothing makes the controller look
again (no new wait starts), the deadlock is never reported again: the run ends, with an empty event queue, with AGVs in
open waits that no `traffic.wait_stuck` and no unresolved-deadlock report explains. The monitor's end-of-run check calls
this "open wait with no report".

The case in seed 2000228 (`ReservationController`, `FastestRouter`): the deadlock of agv-a, agv-c, agv-d, agv-e and
agv-g is reported unresolved at 95.1 (at 92.4 for the four without agv-c) and again at 192.3, 265.9 and 378.2, each time
after one member's wait ended by interruption and began again, which makes the controller look again; the last report is
at 378.2; at 433.1 agv-a's wait ends with reason `interrupted` as a breakdown interrupts its mission
(`AgvMoveInterrupted` with reason `breakdown`, order-4 back to `PENDING`); no new wait begins; the run ends at 581.6 with the open waits of
agv-c, agv-d, agv-e and agv-g and no report after 433.1.

This is a defect of traffic control. It predates this work: the branch's diff of `reservation.py` against `e993ca8`
(35 lines) touches none of `_unresolved`, `_end_wait`, `_resolve` or `_detect`. This work did not change it, and the
maintainer decides about it. The monitor records it apart, under `known_limits` with its own name, only on the evidence
in the event stream: an unresolved deadlock was reported, a wait of that set then ended, the waits left open at the end
are those of the set's remaining members (or waits queued behind them), and no later `traffic.deadlock` listing them, no
`traffic.wait_stuck` of theirs and no end of their wait followed. Any other open wait with no report stays a violation
(`test_an_open_wait_with_no_report_for_another_reason_is_a_violation`, with every stuck report suppressed by a scratch patch
of the controller); the case itself is pinned by
`test_waits_left_open_after_a_member_of_an_unresolved_deadlock_was_withdrawn_are_a_known_limit`.

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

As of `e4afae4`. Since Task 9 the tests of `tests/intralogistics/test_random_worlds.py` run each world through the layout
only and pin the fingerprints recorded while both modes agreed (`FINGERPRINTS`); Task 11 adds to them (see "Safety run").
At `e4afae4`, `tests/intralogistics/test_random_worlds.py`: `world_spec` is equal for equal seeds, also in processes with other
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
on long runs. Commits after `1bc1e51`: `4560487` (run time), `d697ca6` (preparation), `894e31e` (one test), then the review fixes
`01b8c83` (plain lookup dicts, memo only with a network), `2c0e11b` and `f6179cd` (style). The numbers below are those of
`f6179cd`. The explicit build is gone, so the comparison with it is that recorded figure, and a reference tree of it
(outside the repository) as a check.

### Result

| | Before (`1bc1e51`) | After (`f6179cd`) |
|---|---|---|
| Layout build / explicit build, mode none, one shift | 1.093 (1.086 to 1.095, five rounds; recorded 1.095) | 1.022 (1.019 to 1.042) |
| The same, six shifts | 1.045 (1.043 to 1.046, three rounds) | 0.999 (0.997 to 1.004) |
| Run time, after / before, one shift, modes none, default, default_logging | | 0.934, 0.944, 0.940 |
| Run time, after / before, six shifts, mode none | | 0.956 (0.954 to 0.959) |
| `prepare()`, explicit square grid of 16, 100, 400, 1,600 nodes, ms | 12.2, 73.5, 303, 1,277 | 5.75, 30.6, 130, 572 |
| `prepare()`, lattice of 16, 100, 400, 1,600 nodes, ms | 6.4, 29.9, 119, 497 | 3.6, 13.5, 52.5, 229 |
| Per node at 400 and 1,600 nodes, explicit, microseconds | 758, 798 | 325, 358 |
| Per node at 400 and 1,600 nodes, lattice, microseconds | 297, 310 | 131, 143 |
| `activate/1600/layout`, ms (16 alternating rounds of 15, both orders) | 48.46 | 48.70 (+0.24, 0.5 %) |

The preparation figures are the means of the medians of two runs of `layout_overhead.py --repeat 9`, before and after
alternating (the runs agree within 1 %); the output of the runs is below. Activation of a production model: see "Review
round 1". The per-event cost of a production model and the construction ratio are what they were (`run/ratio` 0.999 to
1.001, `construct/1600` 1.21 to 1.24 in both).

The full suite at the head: see the last section (baseline 4738 passed, 1 skipped, 99.18 %).

### Run time

The graph a layout builds shows its nodes and adjacency in `_ReadOnlyDict` containers, `dict` subclasses whose mutating
methods and `__init__` raise `TypeError` ("a layout-built graph is read-only"), instead of `MappingProxyType` objects; the
unbound `dict` methods can still change one, which is not a public path. CPython takes a slower path for a subclass of
`dict` (`d[k].get(j)` 20.7 ns against 15.3 ns, `list(d[k])` 51.0 against 37.2 ns, measured in review), so `neighbors` and
`arc_between` do not read them: they read plain dicts of the same data, `LayoutGraph._lookup`, built once in
`_from_layout` and never changed (for a graph a user writes, `_lookup` is the adjacency it shows, as before). A node
outside a layout-built graph raises `KeyError` in the lookup, which both methods turn into `[]` and `None`, as before; no
allocation per call beyond the list that `neighbors` returns. `subdivide` on such a graph still raises. A graph a user
writes stays mutable as it was; the public surface of `LayoutGraph` is unchanged, so no public way to change a
layout-built graph existed and none was added. The containers do not pickle (they did not as proxies either). A test
asserts that the plain and the read-only containers hold the same nodes, edges and order.

`AGV._validate_node` accepts the canonical node with one dictionary lookup by id and an identity test against the
layout's table, with no property or method call into the layout; everything else (a copy equal by value, a disabled
node, a node of another network, an unknown node) goes on to `Layout._validate_node`, which raises as before, and the
test of `tests/layout/test_binding.py` pins each of these cases for the layout and the AGV. Setting `current_node` and
ending a segment validate once instead of twice (`_relocate` keeps its own validation: the existing test calls it
directly). A test runs a table of inputs (the canonical current node, another canonical node, copies equal by value, the
id of a node at another position, a disabled node, the canonical node of another environment, an unknown node, `None`,
non-nodes, a generated block node and its copy) through the layout, the AGV's check and the `current_node` setter and
asserts one outcome, so a condition added to one path and not the other fails it. Behaviour change: `AGV._validate_node(None)`
no longer creates the environment's layout (assigning `None` needs none); `cart.current_node = None` followed by
`Layout(env)` used to raise "the environment already has a layout" and is accepted now (pinned by a test).

The three causes of the Task 8 attribution (3 shifts, +41 ms in all) were 12.8 ms in the proxies, 7.9 ms in the
allocation and 4.7 ms in validation. On a 3-shift run (five alternating rounds, one process each, against the reference
explicit tree) the layout build took 1.055 times as long before (0.767 against 0.728 s) and 1.019 times after the change
of this part alone (0.742 s). What remains at one shift (2.2 %) is the preparation of the network (5.8 ms for this 16-node
graph, 2.5 % of the run); at six shifts the layout build equals the explicit one within the spread.

### Preparation of a network

Measured with `benchmarks/layout_overhead.py` (new cases `prepare_network/<kind>/<nodes>`, a model with the network and
no entity; the explicit graph is a square grid of two-way arcs). Preparation grows a little faster than linearly
before and after: between 1,600 and 6,400 nodes the growth exponent is 1.09 for the explicit grid (357 to 406 microseconds
per node, 2.6 s at 6,400 nodes) and 1.06 for the lattice (143 to 156 microseconds per node, 1.0 s) at the head (the
review measured 1.05 to 1.09 and 1.03 to 1.05). The profile (`cProfile`, 400 nodes) of the first run showed 5 resolutions of the layers, each checking the network
patch against the schema again (`LayerBundle.__post_init__`), failing `oneOf` branches each building a message and a
`LayoutError` that the caller threw away (8,785 at 100 nodes), and one `_network_view` resolved twice. What changed, none of which changes a
value, a hash, a bundle, a diagnostic or an error:

- `resolve_layers(..., code_checked=True)` (like `auto_checked`, Task 3): the coordinator's own patches, which
  `declare()` checked and froze, and the stages of an earlier bundle are taken as they are. A patch is still checked once,
  where it is declared; `test_preparing_checks_no_declared_patch_again_and_resolves_the_network_view_as_often_as_it_must` counts it.
  This was 35 % of the profile of a 100-node explicit network and almost nothing for a lattice, whose patch is small.
- The schema validator matches the branches of a `oneOf`/`anyOf` quietly (an internal `_Mismatch`, no message), computes
  the type check of the one kind the rule names, and no longer creates a closure per call. When every branch of a
  `oneOf` fixes `kind` to a constant (`address`, `nodeAddress`, `elementAddress`, `selector`), only the branches of the
  value's kind are evaluated, since any other rejects the value at its `kind` property; the count of matching branches is
  that of evaluating all of them. 6,014 mutated documents and definitions, run in both trees, had the same outcome each
  (accepted data equal, and for the rejected the same message, code and path; 1,423 accepted, 4,591 rejected), and a test
  compares the filtered outcome with the count over every branch for values of every kind.
- The network view that preparation asks for twice is resolved once when both asks have the same layers and targets (one
  slot, patches compared by identity), and only when a network is declared. For an explicit network it hits (one
  resolution saved, 7 to 9 % of its preparation). For a lattice it never hits: the second ask has the nodes the first one
  built among its targets, so the view is resolved twice, as before. A model without a network keeps nothing and resolves
  twice, as before; keeping the targets of a 1,600-server model (the first version of this memo did) cost more than the
  0.16 ms it could save.

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
on an Apple M4 Pro, a machine shared with other sessions: the one-minute load average was 1.2 to 2.8 between the rounds
(none of my own work ran at the same time as a measurement). The `uptime` after every round is in the kept
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
prepare_network/explicit/16     12.204       12.187       5.756        5.750
prepare_network/explicit/100    73.253       73.737      30.628       30.479
prepare_network/explicit/400   302.219      303.829     130.310      129.727
prepare_network/explicit/1600 1275.286     1278.396     572.762      571.724
prepare_network/lattice/16       6.428        6.349       3.660        3.578
prepare_network/lattice/100     30.173       29.696      13.524       13.494
prepare_network/lattice/400    118.781      119.071      52.676       52.241
prepare_network/lattice/1600   496.616      496.916     229.517      227.614
run/ratio (layout / none)        0.999        0.999       1.001        0.999
```

### Parity of the runs

`RunRecord` (the events with their fields, initial and final state, time, violations, known limits and the fingerprint
digest, as a sha256 over all of it) of the random worlds of `tests/intralogistics/random_worlds.py`, seeds 1000 to 1299
(300 worlds), under both controllers and the three routers (`shortest`, `fastest`, `congestion`): 1,800 runs, 1,990,304
events, run in a `git archive` copy of `1bc1e51` and in the head (`f6179cd`), with
`PYTHONHASHSEED=0`; all 1,800 equal, none with a monitor violation, none that failed to end (the same comparison after
`4560487` alone, and after `d697ca6`, was equal too). The six fingerprints of
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

(the schema tests pin the unchanged outcomes and pass on both; the last test was parametrized over an explicit network, a
lattice and a model without a network in the review round and renamed `..._as_often_as_it_must`).

### Review round 1

**Activation of a production model.** The first version of the network view memo built `tuple(targets.items())` on every
call and kept it; a model of 1,600 servers pays that with nothing to save (its view resolves in 0.16 ms), and activation
at 1,600 servers rose by 0.8 ms. The memo is now used only when a network is declared. Activation measured alternating
with `1bc1e51` (`activate/1600/layout` and `/none`, 15 repetitions per round): eight rounds with the base first, then eight
with the head first, because the order mattered: base first, 48.34 and 48.68 ms (slower in 7 of 8); head first, 48.69 and
48.70 ms (slower in 2 of 8). Over the sixteen rounds 48.46 against 48.70 ms for `/layout` (+0.24 ms, 0.5 %, slower in 9 of
16) and 48.77 against 49.06 ms for `/none`; `activate/10/layout` 0.487 against 0.486 ms, `construct/1600/layout` 3.015
against 3.019 ms. In-process alternation of variants of the head (the old resolution without `code_checked`, a view
without the memo test) found no difference beyond 0.3 ms, the spread of the repetitions. The target of spec §9 (under 50 ms at
1,600 servers) holds at the median in both orders. The earlier sentence that the activation figures were those they
were is replaced by these.

**Lookups.** With the read-only containers read directly, the benchmark ran 0.7 % slower than with plain dicts (review).
`neighbors` and `arc_between` now read `_lookup`, plain dicts. The head against the same tree with the containers
replaced by an explicit graph's `defaultdict` adjacency (alternating, 15 repetitions in one process, results identical):
one shift 0.9957, three shifts 0.9956, six shifts 0.9959, so the lookups are as fast as an explicit graph's (0.4 % faster
than a `defaultdict`, which is itself a `dict` subclass).

**Small items.** `_ReadOnlyDict.__init__` refuses (the three containers could be re-initialised before), the shared
empty read-only mapping and the adjacency subclass with `__missing__` are gone (a node outside the graph is answered by
the lookup; `_adjacency[unknown]` raises `KeyError` as at `1bc1e51`); the containers are built by `_ReadOnlyDict.of`. The
claim above is what holds: every public way to change a layout-built graph raises; the private containers refuse every
method of the `dict` API, and only the unbound `dict` methods bypass them. The growth of preparation with the size and
the AGV node validation test are described above.

### Run after the review fixes

Same commands as above at `f6179cd`; the intralogistics medians are in `benchmarks/intralogistics-baseline.md`.
The full suite at the head: 4929 passed, 1 skipped, coverage 99.19 % (baseline 4738 passed, 1 skipped, 99.18 %).

## Safety run of layout-built worlds (Task 11)

### Result

Spec §8.4 asks for at least 2,000 mission runs of the randomized traffic safety check on layout-built worlds, with no
violation, no exception and every run returning. A mission run here is one world under one controller and one router.
Two runs of 1,000 worlds each, 6,000 runs each, 12,000 in all: no run raised, no run failed to return (within the
wall-clock limit of 120 s, and every one ended on its own with an empty event queue), and the monitor reported no
violation in any. The only things it recognised are the two known limits described under "Known limits met by the random
worlds": the parking slot of the fleet in 14 cases, and the waits of a withdrawn deadlock in 1.

### The run

Made at commit `5f61105`, CPython 3.14.3 on an Apple M4 Pro (12 cores, 10 worker processes), from the repository root.
The seeds are not those of the parity run (0 to 999):

```
uv run --all-extras python benchmarks/layout_safety.py --worlds 1000 --first 100000
uv run --all-extras python benchmarks/layout_safety.py --worlds 1000 --first 2000000
```

The first run (the one the requirement is met by; seeds 100000 to 100999):

```
worlds 1000 (seeds 100000..100999), runs 6000, events 6570989
runs that raised 0, runs that did not return within the limit 0, runs that ended with the queue not empty 0, runs with monitor violations 0, runs with a known limit 6
wall-clock limit 120 s per run, 188 s
known limits: kind: cases, runs
  parking slot kept while charging on the way to park: 6, 6
    seed 100023 reservation shortest: 1
    seed 100023 reservation fastest: 1
    seed 100023 reservation congestion: 1
    seed 100797 reservation fastest: 1
    seed 100800 reservation congestion: 1
    seed 100885 reservation shortest: 1
```

Exit status 0. The second run (seeds 2000000 to 2000999, the range in which the Task 2 review found seed 2000228):

```
worlds 1000 (seeds 2000000..2000999), runs 6000, events 6720246
runs that raised 0, runs that did not return within the limit 0, runs that ended with the queue not empty 0, runs with monitor violations 0, runs with a known limit 9
wall-clock limit 120 s per run, 184 s
known limits: kind: cases, runs
  parking slot kept while charging on the way to park: 8, 8
    seed 2000152 reservation shortest: 1
    seed 2000345 reservation shortest: 1
    seed 2000345 reservation fastest: 1
    seed 2000447 reservation shortest: 1
    seed 2000447 reservation fastest: 1
    seed 2000490 reservation fastest: 1
    seed 2000490 reservation congestion: 1
    seed 2000583 reservation fastest: 1
  waits of an unresolved deadlock left unreported after a member's wait was withdrawn: 1, 1
    seed 2000228 reservation fastest: 1
```

Exit status 0. Together: 2,000 worlds, 12,000 runs, 13,291,235 events, 0 raised, 0 not returned, 0 violations; 14 cases of
the fleet's parking-slot limit in 14 runs, 1 case of the withdrawn-deadlock limit.

The script (`benchmarks/layout_safety.py`) is a thin runner over `tests/intralogistics/random_worlds.py`: every world
(`world_spec(seed)`) is built through `Explicit` and the layout's handles and run under both controllers
(`ReservationController`, `FreeFlowController`) and the three routers (`ShortestDistanceRouter`, `FastestRouter`,
`CongestionAwareRouter`) with `env.run()` and no `until`, under the `Monitor` (independent body model of every vehicle,
holdings ledger, replay of the deltas from the first event and from the initial state at every domain event, and the
checks at the end of a run that ended on its own; see "The monitor" under Task 2). A run that reaches the wall-clock
limit is reported as not returned and counts as a failure, as does an exception; the exit status is 1 for either and for
any violation, and 0 when only known limits occurred. Its behaviour on failure was checked by forcing each of the three
(a run that sleeps past a limit of 1 s, a run that raises, a seeded violation): each is listed by name, seed, controller
and router, and the status is 1.

Features exercised in the first run: worlds that contain the feature, worlds in which at least one of the six runs
exercised it, runs (of 6,000) that exercised it.

| Feature | Worlds containing it | Worlds exercising it | Runs exercising it |
|---|---|---|---|
| an AGV moves | 1,000 | 999 | 5,991 |
| the second fleet moves | 294 | 288 | 1,698 |
| an arc with a recorded length traversed | 556 | 508 | 2,625 |
| a block piece traversed | 416 | 416 | 2,492 |
| a one-way arc traversed | 353 | 295 | 1,346 |
| a passing segment traversed | 620 | 591 | 3,181 |
| a passing segment held by two AGVs | 620 | 62 | 120 |
| a node of capacity 2 held by two AGVs | 737 | 515 | 1,327 |
| a zone reserved | 679 | 630 | 1,846 |
| a zone waited for | 679 | 520 | 1,409 |
| a zone held by two AGVs | 679 | 192 | 507 |
| a hold-back zone reserved | 427 | 391 | 1,145 |
| a corridor reserved | 892 | 810 | 2,425 |
| parking entered | 751 | 727 | 3,658 |
| an order cancelled | 349 | 287 | 1,519 |
| a breakdown | 350 | 304 | 1,638 |
| charging | 530 | 418 | 1,794 |
| stranding | 530 | 423 | 1,835 |
| a traffic wait | | 980 | 2,914 |
| a stuck wait | | 726 | 1,696 |
| a deadlock | | 868 | 2,269 |
| a make-way move | | 855 | 2,379 |

The features are those of the Task 2 table, and they are exercised in about the same proportions. AGVs move in 5,991
of the 6,000 runs: in none of the six runs of one world, and in three other runs.

### What changed in the monitor

- It recognises the second known limit (above), narrowly: it follows the unresolved deadlocks the controller reports, the
  waits of a set that remain when one wait of the set ends, and the later reports that cover them (a deadlock listing
  them, a stuck report, the end of their own wait). At the end of a run, open waits with no report that are those
  remaining members' waits, or are queued behind them, go to `known_limits` as
  `waits of an unresolved deadlock left unreported after a member's wait was withdrawn`; every other open wait with no
  report stays the violation `open wait with no report`.
- The tests of this: `test_waits_left_open_after_a_member_of_an_unresolved_deadlock_was_withdrawn_are_a_known_limit` (seed
  2000228; RED before the change, the violation `open wait with no report` for agv-c, agv-d, agv-e and agv-g; GREEN after)
  and `test_an_open_wait_with_no_report_for_another_reason_is_a_violation` (seed 26 with every stuck report suppressed by a
  scratch patch of `ReservationController._report_one`: a violation, no known limit).
- Five fixed runs with the monitor are permanent tests
  (`test_a_run_of_the_safety_check_returns_with_no_violation[...]` in `tests/intralogistics/test_random_worlds.py`), by
  (seed, controller, router): (100017, reservation, shortest), (100039, reservation, congestion), (100083, reservation,
  fastest), (100040, reservation, fastest) and (100045, free, fastest). Each asserts that the run ended on its own, had no
  violation and no known limit, the `env.fingerprint()` digest pinned at `5f61105`, and the features it exercises; between
  them they exercise charging, stranding, a zone held by two, a passing segment held by two and a deadlock (the first
  four), and, for the last, a free-flow run with a passing segment, parking, charging, stranding and the second fleet.
  The pins hold for `PYTHONHASHSEED` 1 and 7.

## Costs measured at the end (Task 11)

### The first activation in a process

Two environments of ten servers activated one after the other in a fresh process (15 processes, CPython 3.14.3,
`5f61105`): the first `env.activate()` takes 18.35 ms (18.21 to 18.45), the second 0.60 ms (0.59 to 0.61), the third 0.77
ms. About 13.4 ms of the first is importing the layout package, `simulatte.intralogistics.graph` and `.network` (measured
by importing them first: 13.28 to 13.66 ms), and about 5.1 ms is the first use of what they hold (the first activation
after those imports takes 4.97 to 5.23 ms). The Task 4 review measured about 14 ms for the imports. A process that
activates one environment pays the 18 ms once; a script that runs many short environments pays it once.

### Many jobs before activation

Ten servers and 99,990 production jobs (two operations each, `ProductionJob`) created before `env.activate()`, with
nothing declared: activation creates the layout and prepares it. Three repetitions, `5f61105`:

| What | Result |
|---|---|
| `env.activate()` | 488 ms (487 to 539) |
| the first `env.manifest()` after it | 2.24 s (2.19 to 2.27) |
| a `TraceRecorder`, activation and close | 4.25 s (4.02 to 4.28); trace of 36.7 MB |

The activation cost per job is 4.9 microseconds here against 2.8 microseconds with 1,600 jobs (`jobs_before_layout`, 4.98
ms in total below), so preparation grows a little faster than linearly between 1,600 and 99,990 jobs (62 times the jobs,
about 108 times the time); it is not quadratic. The size of the trace depends on the jobs: with the same 99,990 jobs
and routes of four and of ten operations the traces are 40.3 MB and 51.1 MB (4.1 s and 4.4 s). The Task 4 and Task 10
reviews quoted 60 to 63 MB and 4.5 s for a model of 99,990 jobs that this measurement does not reproduce (their job
shape was not recorded); the writer's `max_initial_bytes` is 64 MiB, so a model with more per job than these jobs
carry can reach that limit. I did not separate the share of the layout's bundle in these sizes.

### Final numbers against spec §9

`benchmarks/layout_overhead.py --repeat 15` at `5f61105`, six successive processes (CPython 3.14.3, Apple M4 Pro, a machine
shared with other sessions, one-minute load average 2.1 to 3.3 at the start of the runs), after the docstring of the
script was corrected to say that its `none` variants are models whose layout is created at activation. The table gives the
median of the six medians and the range of the six. The full output of the first process is below.

| Target (spec §9) | Measured | Met |
|---|---|---|
| No cost per event after activation (the guard is a check of a field) | `run/ratio` (layout created while building, against created at activation): 1.001 (0.9999 to 1.0026), 1,331 ns per domain event; the step path reads no layout (`test_step_after_activation_reads_nothing_of_the_layout`) | yes |
| Construction with a layout within 1.5 times construction without, at 1,600 servers | 1.22 times (1.20 to 1.23): 3.05 ms (3.01 to 3.09) against 2.51 ms (2.46 to 2.52) | yes |
| Activation under 5 ms at 10 servers, nobody observing | 0.50 ms (0.49 to 0.50) with `Layout(env)`, 0.52 ms (0.51 to 0.52) when activation creates the layout | yes |
| Activation under 50 ms at 1,600 servers, nobody observing | 49.07 ms (48.87 to 49.37) with `Layout(env)`, 49.37 ms (49.19 to 49.71) when activation creates the layout | yes, at the line: the margin is 0.3 to 1.1 ms, the spread between processes is 0.5 ms, and every one of the six processes is under 50 ms |
| Linear in the jobs created before activation | 1,600 jobs and 10 servers, `Layout(env)` and `prepare()`: 4.98 ms (4.87 to 5.34); 99,990 jobs: see above | yes up to a growth a little faster than linear |
| The bundle and the hashes are built at first read | `first_bundle` 2.51 ms at 10 servers and 266.8 ms (265.2 to 269.6) at 1,600; `test_an_unobserved_run_builds_no_bundle_and_a_read_builds_it_once` | yes |
| The overhead gate of the core workload against 0.12.0 passes with its present limit | see "Gates" | yes |
| `import simulatte` imports neither `simulatte.layout` nor `simulatte.intralogistics.graph`; a layout with no network imports no fleet, traffic or AGV module | `tests/core/test_public_api.py::test_layout_is_lazy_but_discoverable`, `::test_a_layout_without_a_network_imports_no_fleet_traffic_or_agv_module`, `tests/layout/test_environment_layout.py::test_a_layout_created_at_activation_imports_no_fleet_traffic_or_agv_module` | yes |

The 50 ms target is met at the line, as Tasks 3, 4 and 9b measured (48.5 to 50.5 ms): process to process the spread is about
0.5 ms, and a loaded machine can put a run over the target. The medians of all runs of this record are under it.

Other cases of the same runs (median of the six medians, ms; range in parentheses): `prepare/10` 0.49 (0.48 to 0.50),
`prepare/1600` 47.44 (47.28 to 47.78), `construct/100` ratio 1.45, `prepare_network/explicit/16, 100, 400, 1600`
5.77, 30.44, 129.76, 569.65, `prepare_network/lattice/16, 100, 400, 1600` 3.57, 13.44, 52.16, 229.69. The preparation of a
network is that of Task 9b (5.75, 30.6, 130, 572 and 3.6, 13.5, 52.5, 229): unchanged.

First process, as printed:

```
commit 5f61105, CPython 3.14.3, arm64, repeat 15 (median / min, ms)
construct/100/none               0.121 /      0.113
construct/100/layout             0.175 /      0.166
construct/100/ratio              1.451
construct/1600/none              2.521 /      2.457
construct/1600/layout            3.030 /      3.016
construct/1600/ratio             1.202
prepare/10                       0.477 /      0.470
activate/10/layout               0.494 /      0.489
activate/10/none                 0.511 /      0.504
first_bundle/10                  2.482 /      2.458
prepare/1600                    47.291 /     47.134
activate/1600/layout            48.872 /     48.548
activate/1600/none              49.202 /     48.780
first_bundle/1600              265.204 /    263.195
prepare_network/explicit/16      5.752 /      5.687
prepare_network/explicit/100    30.366 /     30.268
prepare_network/explicit/400   129.636 /    129.034
prepare_network/explicit/1600   570.070 /    567.388
prepare_network/lattice/16       3.533 /      3.495
prepare_network/lattice/100     13.457 /     13.325
prepare_network/lattice/400     52.000 /     51.729
prepare_network/lattice/1600   230.936 /    226.030
jobs_before_layout/1600          5.052 /      4.970
run/events                       30204
run/none                        40.185 /     39.909   1330.5 ns per event (median)
run/layout                      40.249 /     39.975   1332.6 ns per event (median)
run/ratio                        1.002
```

## Gates (Task 11)

Run on bigboss (Apple M4 Pro, macOS, CPython 3.14.3 unless the row says otherwise), in this order, with nothing edited in
`src`, `tests`, `benchmarks` or `studio` after `5f61105`: the Python gates ran on the tree of that commit (the full suite
before the commit was made, on the same files). Result lines are copied from the output.

| Gate | Command | Result |
|---|---|---|
| Tests and coverage | `uv run --all-extras pytest` | `Required test coverage of 99% reached. Total coverage: 99.19%`; `4936 passed, 1 skipped, 3 warnings in 449.44s (0:07:29)` (the baseline of the branch was 4929 passed, 1 skipped: seven tests added by this task; the three warnings are deprecations in `starlette`, `websockets` and `uvicorn`) |
| Lint | `uv run --all-extras ruff check src tests` | `All checks passed!` |
| Format | `uv run --all-extras ruff format --check` | `531 files already formatted` |
| Types | `uv run --all-extras ty check src` | `All checks passed!` |
| Docs | `uv run --all-extras zensical build` | `No issues found`; `Build finished in 0.36s` |
| Studio types | `pnpm typecheck` (in `studio/`) | exit 0 |
| Studio tests | `pnpm test` | exit 0; trace 329, playback 24, scene 25, export 15 and viewer 82 tests passed (`apps/player` has no tests: `No test files found`) |
| Studio build | `pnpm build` | `built in 3.46s`, exit 0 (a chunk-size warning of the bundler only) |
| Studio browsers | `pnpm test:browser` | `36 passed (1.2m)` on chromium, firefox and webkit |
| PyPy lane | `UV_PROJECT_ENVIRONMENT=<scratch>/pypy-venv uv sync --dev --python pypy-3.11 --frozen`, then `MPLBACKEND=Agg UV_PROJECT_ENVIRONMENT=<scratch>/pypy-venv uv run --python pypy-3.11 pytest tests/core tests/execution tests/intralogistics tests/layout -o addopts="-q -p no:cacheprovider"` (job `test-pypy` of `ci.yml`; a separate project environment keeps the checkout's `.venv` on CPython) | `4805 passed, 23 skipped in 585.98s (0:09:45)` (PyPy 7.3.22, Python 3.11.15) |
| CPython 3.11 | `UV_PROJECT_ENVIRONMENT=<scratch>/py311-venv uv run --python 3.11 --all-extras pytest tests/intralogistics tests/layout --no-cov -q` | `2594 passed in 120.13s (0:02:00)` (Python 3.11.13) |
| Overhead gate of the core workload | the `bench` job's step "Mode none against released and stripped 0.12.0 (gate)" on CPython 3.14, `--warmup 1 --repeat 5 --processes 3`, run locally as described below | pass on both workloads against both baselines in three rounds |

`ci.yml` needed no change. Its lanes name directories (`tests/core tests/execution tests/intralogistics tests/layout`
for the PyPy lane) and the files of the `execution-native` lane (`tests/execution` and twelve files under `tests/core`),
which all exist; this branch added, renamed or deleted (`git diff --name-status e993ca8 HEAD -- tests benchmarks`) only
`benchmarks/layout_overhead.py`, `tests/intralogistics/random_worlds.py`, `tests/intralogistics/test_random_worlds.py`,
`tests/layout/test_environment_layout.py`, `tests/layout/test_handles_only.py` and two trace fixtures (all added), and
no file was renamed or deleted. This task added `benchmarks/layout_safety.py`, which no lane runs.

The PyPy bench variant of the overhead job runs only on the CI runner, as before.

### The overhead gate

Run as `benchmarks/README.md` describes, mode `none`, `--warmup 1 --repeat 5 --processes 3` on the two CI workloads,
CPython 3.14.3, at `5f61105`, with the load average of the machine at 2.1 to 2.6 (other sessions were running). The branch
side ran from the checkout's `.venv` (the source of `5f61105`; a wheel cannot be built without the Studio assets), 0.12.0
in its own environment with the checkout's dependency versions as constraints, and the stripped copy made by
`strip_debug.py` (12 `env.debug` statements removed). Three rounds; round 1 first.

| Baseline | Workload | Baseline median | Branch median | Overhead | Limit | Verdict |
|---|---|---|---|---|---|---|
| released 0.12.0 | `jobshop10-u90-5k` | 0.647 s | 0.484 s | -25.2 % | +5.0 % (3.0 + 2.0) | pass |
| released 0.12.0 | `jobshop10-u95-5k` | 0.681 s | 0.520 s | -23.6 % | +5.0 % (3.0 + 2.0) | pass |
| 0.12.0 without `env.debug` calls | `jobshop10-u90-5k` | 0.451 s | 0.484 s | +7.3 % | +8.5 % (6.5 + 2.0) | pass |
| 0.12.0 without `env.debug` calls | `jobshop10-u95-5k` | 0.483 s | 0.520 s | +7.5 % | +8.5 % (6.5 + 2.0) | pass |

Rounds 2 and 3 (released and stripped baselines): against released 0.12.0 -25.3 % and -25.1 % (u90), -23.5 % and -23.4 %
(u95); against the stripped copy +7.6 % and +7.7 % (u90), +7.6 % and +6.6 % (u95). The margin against the stripped copy is
0.8 to 1.9 points (Task 4 measured +6.8 % and +6.9 % at `051ac30` on the same workloads and the branch medians 0.479 s
and 0.519 s; the branch medians here, 0.483 to 0.484 s and 0.520 s, are within the spread of those). The gate passes in
all twelve comparisons of the three rounds; the stripped-copy margin is thin and the same as the branch had before this
work (spec §9: "with its present limit").

## Walk of spec §10 (Task 11)

One row per item and per sub-claim. "Covered" says what shows it; a claim nothing covers is written as not covered.

| Item | Claim | Covered by | Result |
|---|---|---|---|
| 1 | No code path in `src/simulatte` accepts a concrete `LayoutGraph` or `Node` where a handle is required | `tests/layout/test_handles_only.py::test_a_concrete_graph_or_node_raises_with_the_fix` (the seven entry points `FleetCoordinator(graph=)`, `ReservationController(graph=)`, `Warehouse(input_bays=)`, `Warehouse(output_bays=)`, `AGV(initial_node=)`, `ChargingStation(node=)`, `ParkingArea(node=)`, each with and without a declared network: 14 cases), `::test_a_concrete_node_in_the_builders_mapping_raises_with_the_fix` (2), `::test_a_handle_of_another_environments_layout_raises` (7); the signatures of the components take `NodeRef` and `PortRef` (`grep` of `src/simulatte/intralogistics` finds no union of a concrete graph or node with a handle at a construction parameter) | covered by the tests for the eight entry points; not proved for a path no test names (the Task 9 review checked a table of the strictness of every parameter) |
| 1 | `grep` finds no `_explicit_graph_use` or `_uses_explicit_graph` | `grep -rn '_explicit_graph_use\|_uses_explicit_graph\|_register_graph_use\|check_existing_endpoints' src tests examples benchmarks docs skills CLAUDE.md` at `5f61105` | no match (exit 1) |
| 2 | `tests/` contain no model built in explicit mode | explicit mode cannot be built: the entry points above raise; the whole suite passes (`4936 passed`); the words "explicit mode" remain only in docstrings that name the commit at which a fingerprint was pinned (`tests/layout/test_binding.py:347`, `tests/layout/test_builders.py:248`, `tests/intralogistics/test_random_worlds.py:49`) and in the docstring of `tests/intralogistics/test_traffic.py:128` ("the successor of the explicit-mode rule"); `tests/layout/test_traffic_fields.py::test_an_explicit_graph_and_its_layout_twin_produce_the_same_event_stream` builds its model with `Explicit` and handles, as its twin | covered |
| 2 | `examples/` contain no model built in explicit mode | `tests/intralogistics/test_examples.py` runs the intralogistics and layout examples (`test_intralogistics_simple_example_runs`, `..._intermediate_...`, `..._advanced_...`, `..._traffic_...`, `test_layout_intralogistics_example_runs`, `test_layout_stages_example_runs`) and `::test_each_transport_example_records_a_resolved_layout_with_arcs[5 examples]`; every `LayoutGraph(` in `examples/` is followed by `Explicit(graph)` and handles; `examples/studio_recording.py` (`build_simple_system(env)`) is run by no test and was run for this record, in both of its models (`--model fleet` and `--model shop`, exit 0, the trace checked and verified by the script) | covered; the other examples are production models |
| 2 | `benchmarks/` contain no model built in explicit mode | `benchmarks/intralogistics.py` builds through the layout and ran at `5f61105` (`--mode none --shifts 1 --warmup 0 --repeat 1`, exit 0; it is also the `bench` job's intralogistics step); `benchmarks/layout_overhead.py` and `benchmarks/layout_safety.py` ran for this record | covered |
| 2 | `docs/` and `skills/` contain no model built in explicit mode | `tests/test_docs_run_blocks.py::test_run_blocks_match_example_files` and `::test_intralogistics_run_blocks_match_example_files` keep the embedded example scripts equal to the examples that run; the other code blocks of `docs/` and `skills/simulatte-intralogistics/SKILL.md` were checked by the Task 10 review (every changed block run, quoted messages matched) and are not run by a test; the wording grep over docs, skills and CLAUDE.md is empty (Task 10 gates) | partly covered: the blocks that are not an embedded example are not executed by any test |
| 3 | The parity run of §8.1 is recorded with its counts | "Parity of explicit and layout models (Task 2)": 1,000 worlds, 6,000 pairs equal, 13,093,868 events compared, at `e4afae4` | recorded |
| 3 | The safety run of §8.4 is recorded with its counts | "Safety run of layout-built worlds (Task 11)": 2,000 worlds, 12,000 runs, 13,291,235 events, no violation, no exception, every run returned | recorded |
| 4 | The performance targets of §9 are measured and recorded | "Final numbers against spec §9" | recorded; all met, the 1,600-server activation at the line |
| 4 | The core gate passes | "The overhead gate" | pass, twelve comparisons |
| 5 | A trace of every example opens in Studio with its lanes drawn, checked with the TypeScript reader's tests | the traffic example only: `tests/fixtures/traces/generated/example_traffic.simtrace` is projected by `studio/packages/scene/test/traffic.test.ts` ("projects the recorded traffic example with its links and without the grid fallback": no grid fallback and links present) and a trace of an earlier version stays on the grid fallback ("projects a trace of an earlier version, no layout, on the grid fallback"); for each of the five transport examples (`intralogistics_simple`, `_intermediate`, `_advanced`, `_traffic`, `building_an_agv_system`), `tests/intralogistics/test_examples.py::test_each_transport_example_records_a_resolved_layout_with_arcs` asserts that its trace carries a resolved layout whose network graph has arcs; for a production model, `tests/layout/test_environment_layout.py::test_the_trace_of_a_production_model_carries_its_layout` and the regenerated `shop_small`, `lumscor` and `cell_ops` fixtures (Task 4) | partly covered: the other four transport examples are not projected by a TypeScript test, nor opened in Studio; the claim that their lanes are drawn rests on their traces carrying a resolved layout with arcs, like the traffic example's |
| 6 | Full suite | `uv run --all-extras pytest` | `4936 passed, 1 skipped` |
| 6 | Coverage stays at 99 % or above | same run | `Total coverage: 99.19%` |
| 6 | Lint, format, types | `ruff check src tests`, `ruff format --check`, `ty check src` | all clean (see "Gates") |
| 6 | Docs build | `zensical build` | `No issues found` |
| 6 | Studio gates | `pnpm typecheck && pnpm test && pnpm build && pnpm test:browser` | all exit 0, `36 passed` |
| 6 | The PyPy lane | the command of `ci.yml` | `4805 passed, 23 skipped` |

Also run, beyond §10: `tests/intralogistics` and `tests/layout` on CPython 3.11 (2594 passed), the floor of the supported
versions.
