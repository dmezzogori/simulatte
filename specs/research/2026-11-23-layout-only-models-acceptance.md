# Layout-only models, acceptance

Branch `feature/layout-only-models`, spec `specs/2026-11-23-layout-only-models-design.md`. The work makes the layout
the only way to build a model. This file records the evidence each task produces for the spec's verification (§8) and
acceptance (§10): the parity run that allows explicit mode to be removed (L8), the measurements of the later tasks,
from Task 11, the safety run on layout-built worlds, the walk of §10 and the gates, the fix wave of the final review
and, last, Assignment 2: the residuals of that wave, three traffic defects the random worlds found, the band of the core
overhead gate, and the gates at the final head with what stays open.

Each section states the commit it was measured at. The sections of Tasks 2 to 5 describe the branch while explicit mode
still existed; Task 9 removed it (`999c3a7`), and the tests and scripts those sections name for the comparison of the two
modes are gone or changed since (`benchmarks/layout_parity.py` is at `e4afae4`). Where a later task changed what a section
says, the section says so. The sections of Tasks 2 to 11 and of the final review are kept as they were written, with the
notes that later commits added; where a statement in them was true at its commit and is no longer true at the final
head, the section says so at that place, and "Gates at the final head (Assignment 2)" gives the numbers that hold at the
end.

## Summary

| Question | Answer | Section |
|---|---|---|
| Do explicit and layout models run alike? | Yes: 6,000 pairs equal at `e4afae4`, the last commit with both modes. | Parity (Task 2) |
| Does the layout-built world stay safe? | Yes: at the final head (`229ea61`) 12,000 runs of 2,000 worlds (seeds 100000..100999 and 2000000..2000999), no violation, no exception, every run returned; 13,290,230 events. The two known limits that Task 11 recorded (a parking slot kept while charging on the way to park; a deadlock not detected again after a member's wait was withdrawn) predated this work, are fixed in Assignment 2 (B2 and B1) and the monitor has no exemption for a known limit any more. Two seeds outside those ranges still end with a violation and are open (below). | Safety run (Task 11); B1; B2; Gates at the final head |
| Are the performance targets of §9 met? | Yes. Activation at 1,600 servers meets its target at the line (49.1 to 49.4 ms against 50 ms). | Final numbers against §9 (Task 11) |
| Do the gates pass? | At the final head (`229ea61`) every gate was run and passed, the overhead gate in three rounds against both baselines with the limit of Assignment 2, C (+12.5 % against the stripped copy). Earlier: at `ab4d430` every gate passed; at the head of the final review's fix wave the local overhead gate against the stripped copy was at its old limit (6 of 18 comparisons over +8.5 %), which Assignment 2, C widened. | Gates at the final head; Gates (Task 11); Final review; C |
| What did the final review find? | No behavioural defect. Documentation, error messages of the strict contract and this record were fixed in one wave; no event, digest, bundle or hash changed. | Final review |
| What did Assignment 2 do? | A: the six residuals of the fix wave fixed. B1: a deadlock is looked at again when a member's wait ends (seeds 2000228, 52118, 8103801 and others clean). B2: a parking slot is given back when the trip to it turns to charge. C: the CPython budget against the stripped copy is 10.5 % (limit 12.5 %). B1 and B2 change the behaviour of the fleet in the cases they measure (B1: 302 of 24,000 paired reservation runs changed their fingerprint; B2: 51 of 36,000 runs), and seed 619's pinned fingerprint was re-pinned by B2; the counts are those of the sections. | Assignment 2, A, B1, B2, C |
| What is open after Assignment 2? | Six things, for the maintainer: a stuck wait not reported after a gate waiver (seed 7702388), a physical conflict from a direction re-claim through look-ahead (seed 8101904), waits asleep behind a reported wait with no report of their own, an AGV that charged on its way to park idle on the charger's node, `agv.stranded` naming the node where a diversion started, and the noise band of the overhead gate. | Open after Assignment 2 |
| Is every item of §10 covered? | All but two are covered. Partly covered: item 2 (the code blocks of `docs/` and `skills/` that are not embedded example scripts are executed by no test) and item 5 (only the traffic example is projected by a TypeScript test; the other transport examples are checked for a recorded layout with arcs only). | Walk of §10 (Task 11) |

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

Both known limits are fixed since this section was written, and the monitor has no mechanism left that lets a run end
with a limit: `KNOWN_LIMIT_WITHDRAWN` and its tracking were removed at `5b3bb91` (Assignment 2, B1), `KNOWN_LIMIT`,
`Monitor.known_limits` and `RunRecord.known_limits` at `4bc6624` (B2), and the output of `benchmarks/layout_safety.py`
names no known limit (the final gates run it on both recorded ranges). The first limit is fixed by `61e5b27` and
`0d08071` (its section is "B2. Parking slots during a charge elsewhere"); the second by `df2f7c3` and `c6cb563` ("B1.
Waits after a deadlock member's wait ends"). What follows is the record as it was written when the limits were met, at
Task 2 and Task 11, before the fixes: the line numbers of `reservation.py` are those of that time and the counts are
those of the runs of that time. A case that looks like the second family and is not fixed is a different defect, a third
mechanism found later (seed 7702388); it is under "Open after Assignment 2, for the maintainer".

**A parking slot kept while charging on the way to park.** **Fixed** (Assignment 2, B2): since `61e5b27` the slot goes
back as the trip to park turns to charge, and the monitor no longer exempts the case (`4bc6624`); see B2. What follows
describes the fleet before the fix. An AGV takes a slot of a parking area when it sets off to
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

**A deadlock is not detected again after a member's wait was withdrawn.** **Fixed in two halves** (Assignment 2, B1):
`df2f7c3` runs detection again when a member's wait ends by withdrawal or by a staged `on_blocked` reaction, which finds
the members left when they still form a deadlock; `c6cb563` gives a member left in no deadlock, whose wait had opened
into the set, the `on_blocked` call and review it never had. The first half alone left that second shape open: seed
8103801 with `FastestRouter` (found by the review of B1). The monitor no longer exempts the case (`5b3bb91`). These
statements hold for the seeds run (see B1). (Found by the Task 2 review; not met by the parity run, which ran seeds 0 to
999 and had none. The safety run of Task 11 meets it once; see "Safety run".) It is a missed detection in
`ReservationController`, not only a missing report. In `src/simulatte/intralogistics/reservation.py` deadlock detection
(`_detect`) runs only when a wait starts, from `stopped()` (line 287). A member whose wait opens into
a set reported unresolved returns from `stopped()` at once (lines 288 to 289) and gets no review timer (a member that was
already waiting keeps what it had: in seed 2000228 agv-e had had two reviews and is dormant), and `_report_one` (line
578) refuses every member a stuck report while the set stands. The set is remembered in `_unresolved`. `_end_wait` (line 631) drops
every remembered set that holds the AGV whose wait ended, for any reason. When the wait was withdrawn (`withdraw`, lines
338 to 345, reason `interrupted`) the controller then only scans for grants. Nothing looks at the remaining waits again
unless a new wait opens, `idle()` fires for one of their blockers, or a stuck chain reaches them. If they still form a
deadlock the run ends, with an empty event queue, with AGVs in open waits that no `traffic.wait_stuck` and no
unresolved-deadlock report explains: the monitor's end-of-run check calls this "open wait with no report".

The case in seed 2000228 (`ReservationController`, `FastestRouter`): the deadlock of agv-a, agv-c, agv-d, agv-e and
agv-g is reported unresolved at 95.1 (at 92.4 for the four without agv-c) and again at 192.3, 265.9 and 378.2, each time
after one member's wait ended by interruption and began again, which makes the controller look again; the last report is
at 378.2. At 433.1 agv-a's wait ends with reason `interrupted` as a breakdown interrupts its mission (`AgvMoveInterrupted`
with reason `breakdown`, order-4 back to `PENDING`); no new wait begins; the run ends at 581.6 with the open waits of
agv-c, agv-d, agv-e and agv-g, blocked by each other only, and no report after 433.1.

Consequence, measured by the review of this task with a scratch patch that calls `_detect()` after a member's withdrawal
(the patch is not part of the branch, and no source was changed by this work): in seed 2000228 the policy stages two
step-asides at 433.116 for the remaining four, agv-e and agv-d move (29 moves against 25), the three waits left are
reported stuck at 481.8, there is no known limit and no violation, and the completed orders stay at 2 (the batteries are
nearly spent and agv-e strands at 439.4). Over 900 runs (seeds 2000150 to 2000449, `ReservationController`, three
routers) a member's withdrawal drops a remembered set in 27 runs; a deadlock is still there at that instant in 9;
re-detection stages a resolution in 3; 8 fingerprints change; the completed orders rise in no run and fall in one
(seed 2000274, `FastestRouter`, 13 to 12). The defect is real but rare, and re-detecting is not free of consequences.

It predates this work: the branch's diff of `reservation.py` against `e993ca8` (35 lines) touches none of `_unresolved`,
`_end_wait`, `_resolve` or `_detect`, and the review ran seed 2000228 at `e993ca8` with the same 38 traffic events. This
work did not change it, and the maintainer decides about it.

The monitor records it apart, under `known_limits` with its own name, on the evidence of the event stream and of the
end state, and nothing broader. An unresolved deadlock was reported (`traffic.deadlock` with action `unresolved`). A wait
of that set ended with reason `interrupted`, the end that comes from `withdraw`, which only scans. This is the
conservative choice: a reroute staged by a review (lines 489 to 498) also ends a member's wait with a scan only, and the
other reasons come from a grant or from the deadlock resolution, which detects again; a case the trigger leaves out would
stay a violation. The other members are then orphans until a later report covers them (a `traffic.deadlock` that lists
them, a `traffic.wait_stuck` of theirs, the end of their own wait), and they stop being orphans if any wait starts after
the withdrawal, since the controller detects at every new wait: that wait either reports them or they are not this case.
At the end of the run the exempted waits are the orphans' open waits and the waits queued behind them, and they must
still be a deadlock: each is blocked, and only by waits of the set (the orphans' and those queued behind them). A wait
queued behind an orphan's claim, not itself a member of the deadlock, is exempted with it. A wait that is not blocked at
all, or is blocked by an AGV that is idle, stranded or moving, is not the known limit, whatever its history. Any other open wait with no report stays the
violation `open wait with no report`. The tests are in "What changed in the monitor".

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
model of every vehicle, from the motion events only, and checks against it node, zone and corridor admission and the
direction of the vehicles on a single-lane segment (the spacing of vehicles that follow each other on a segment is not
checked on the bodies: see "What the monitor does not check" under "Safety run"), that the holdings cover the bodies
(exactly for a vehicle at rest and at the end of every travel), its own derivation of the corridors, and the entities
against the holdings ledger, including the ledger's rule that the extents of a segment's holders fit in its length; under either controller, parking capacity,
parking slots of resting AGVs, and that moves start and end where the body is. At the end of a run that ended on its
own: no claim of a moving AGV, no review timer, every open wait explained by a stuck report or an unresolved deadlock,
no motion left, no parking slot held away from its node. It records violations instead of raising, and the known
limits apart from them. At `e4afae4` that was the first known limit only; Task 11 adds the recognition of the second, with
the narrow boundary described above (see "What changed in the monitor" under "Safety run"). Assignment 2 removed both
recognitions (`5b3bb91`, `4bc6624`): the monitor records violations only.

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
worlds": the parking slot of the fleet in 14 cases, and the waits of a withdrawn deadlock in 1. Both limits are fixed
since (Assignment 2, B2 and B1); at the final head the same two ranges give 12,000 runs with no violation and no known
limit (see "Gates at the final head (Assignment 2)"). The counts of this section are those of `ab4d430`.

### The run

Made at commit `ab4d430` (the monitor as committed there; the runs at `5f61105` and at `bcce8cc` gave the same counts, the same cases and the same feature tables), CPython 3.14.3 on an Apple M4 Pro (12 cores, 10 worker processes), from the repository root.
The seeds are not those of the parity run (0 to 999):

```
uv run --all-extras python benchmarks/layout_safety.py --worlds 1000 --first 100000
uv run --all-extras python benchmarks/layout_safety.py --worlds 1000 --first 2000000
```

The first run (the one the requirement is met by; seeds 100000 to 100999):

```
worlds 1000 (seeds 100000..100999), runs 6000, events 6570989
runs that raised 0, runs that did not return within the limit 0, runs that ended with the queue not empty 0, runs with monitor violations 0, runs with a known limit 6
wall-clock limit 120 s per run, 181 s
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
any violation, and 0 when only known limits occurred. The limit is a positive number of seconds (a limit of 0 is
refused, since it would remove the bound) and `--jobs 1` runs the worlds in the process itself. The exit status is
tested by `tests/intralogistics/test_layout_safety.py` (clean runs, a run that raises, a run cut by a limit of 0.05 s, a
seeded violation, a limit of 0 and of -1). CI lints `src` and `tests` only; `ruff check benchmarks/layout_safety.py` is
clean and `ruff format --check` covers the file.

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

- Since `5b3bb91` (Assignment 2, B1) the second known limit is fixed and this exemption, its orphan tracking and its tests
  are removed; what follows describes the monitor from `ab4d430` to `6ff842f`. Since `4bc6624` (Assignment 2, B2) the
  first known limit is fixed too, its exemption is removed, and the monitor, `RunRecord` and `layout_safety.py` have no
  known limits any more.
- It recognises the second known limit (see "Known limits met by the random worlds"), and only while the case stands.
  It follows the unresolved deadlocks the controller reports. The members left when a wait of a set ends with reason
  `interrupted` (the end that comes from `withdraw`) become orphans, until a later report covers them (a
  `traffic.deadlock` that lists them, a `traffic.wait_stuck` of theirs, the end of their own wait) or until any wait
  starts (the controller detects at every new wait). At the end of a run that ended on its own, the open waits of
  orphans, and the waits queued behind them, go to `known_limits` as
  `waits of an unresolved deadlock left unreported after a member's wait was withdrawn` only if they still are a
  deadlock: each is blocked, and only by waits of the exempted set. The blockers are those of the controller's ledger,
  which the monitor checks against the entities and its body model at every event. Every other open wait with no report
  stays the violation `open wait with no report`.
- The first version of this exemption (`5f61105`, record `aad5b29`) counted any orphan with an open wait. The review found
  five mutant runs that ended as a known limit with no violation: a stuck wait whose blocker is stranded or idle (the
  stuck reports suppressed, seed 2000228 with `shortest` or `congestion`), and waits that are not blocked at all (releases
  that do not scan, seed 2000156 with `fastest`; a wait once in an unresolved set never granted, seeds 500005 and 500184
  with `congestion`). Each ended as `open wait with no report` after the first fix (`bcce8cc`). The second review found 16 of 1,800 runs of
  one more mutant that ended as a known limit with no violation (after a member's withdrawal the controller neither
  detects nor reports stuck: seeds such as 2000173 with `fastest`, 2000245 with all three routers, 2000254 with
  `congestion`), because the rule did not use the condition stated for the case, that no new wait begins; since
  `ab4d430` any wait that starts clears the orphans and those runs are violations.
- The tests are in `tests/intralogistics/test_random_worlds.py`.
  `test_waits_left_open_after_a_member_of_an_unresolved_deadlock_was_withdrawn_are_a_known_limit` pins the case (seed
  2000228, `FastestRouter`). `test_an_open_wait_with_no_report_for_another_reason_is_a_violation` has seven runs that must
  be violations with no known limit: seed 26 with the stuck reports suppressed (a boundary the exemption does not
  reach: no unresolved deadlock is ever reported there), seed 2000228 with `shortest` and with `congestion` under the same
  patch, seed 2000156 with `fastest` under a `_drop` that does not scan (a reroute orphans the waits there, so the
  trigger decides it), and seeds 2000173 `fastest`, 2000245 `shortest` and 2000254 `congestion` under a controller that
  is blind to detection and stuck reports after a member's withdrawal. The six runs after seed 26 reach the exemption:
  the first three fail against the monitor as it was at `aad5b29` (RED, first round: `4 failed, 27 passed`, the three
  runs and a trigger test); the last three fail against the monitor at `2dd72b6` (RED, second round: `4 failed, 35
  passed`, these three and the new trigger test). The trigger
  is tested on real events: `test_only_a_withdrawal_leaves_the_rest_of_an_unresolved_deadlock_unreported`,
  `test_a_later_report_or_the_end_of_their_own_wait_covers_the_rest_of_the_deadlock` and
  `test_a_wait_that_starts_after_the_withdrawal_disqualifies_the_orphans`. The closure of the end-of-run check is tested
  on a monitor fed synthetic waits, since no seeded run reaches its clauses alone:
  `test_waits_that_are_still_a_closed_deadlock_are_the_known_limit_with_the_waits_queued_behind_them` and
  `test_a_wait_that_is_not_blocked_or_not_only_by_the_set_is_not_the_known_limit` (a wait not blocked at all, a wait
  blocked by an AGV outside the set, a wait queued behind the set and also blocked from outside).
- Mutants of `ReservationController`, each applied in every worker (a scratch patch, not on disk), run under the monitor
  as committed at `ab4d430`: seeds 2000150 to 2000449 and 500000 to 500299, `ReservationController`, three routers
  (900 runs per range, 1,800 per mutant; the table is that of `bcce8cc` plus the row of the mutant added by the second
  review, all rerun at `ab4d430` with the same counts). No run ends as a known limit without a violation except the case
  of the unmutated controller, seed 2000228 with `FastestRouter`. The mutants that no run shows are not detected by the
  monitor (a mutant that changes nothing the run shows, or one that sets `wait.stuck` without emitting the event, which the
  monitor reads from the controller's state: see the follow-up below).

| Mutant of `ReservationController` | Runs | With `open wait with no report` | Known limit and no violation |
|---|---|---|---|
| no mutant | 1,800 | 0 | 1 (2000228, `FastestRouter`) |
| every stuck report suppressed | 1,800 | 635 | 1 (2000228, `FastestRouter`) |
| stuck reports of four of the AGVs suppressed | 1,800 | 370 | 1 (2000228, `FastestRouter`) |
| no stuck report once any wait was withdrawn | 1,800 | 146 | 1 (2000228, `FastestRouter`) |
| no stuck report for a former member of an unresolved set | 1,800 | 3 | 1 (2000228, `FastestRouter`) |
| after a member's withdrawal the controller neither detects nor reports stuck | 1,800 | 33 | 0 |
| an unresolved deadlock is never reported nor remembered | 1,800 | 5 | 0 |
| the remembered set is cleared as soon as it is reported | 1,800 | 4 | 1 (2000228, `FastestRouter`) |
| the set is remembered, `traffic.deadlock` is not emitted | 1,800 | 1 | 0 |
| a wait is marked stuck, `traffic.wait_stuck` is not emitted | 1,800 | 0 | 1 (2000228, `FastestRouter`) |
| every review timer dropped | 1,800 | 364 | 1 (2000228, `FastestRouter`) |
| the timer of the second and later reviews dropped | 1,800 | 10 | 1 (2000228, `FastestRouter`) |
| every third release does not scan | 1,800 | 1 | 1 (2000228, `FastestRouter`) |
| releases never grant (only a new request scans) | 1,800 | 6 | 1 (2000228, `FastestRouter`) |
| any withdrawal forgets every unresolved set | 1,800 | 0 | 1 (2000228, `FastestRouter`) |
| any release forgets every unresolved set | 1,800 | 0 | 1 (2000228, `FastestRouter`) |
| any grant forgets every unresolved set | 1,800 | 0 | 1 (2000228, `FastestRouter`) |
| the withdrawal of a non-member makes the controller forget every set for good | 1,800 | 1 | 1 (2000228, `FastestRouter`) |
| a wait once in an unresolved set is never granted | 1,800 | 102 | 1 (2000228, `FastestRouter`) |
| the same, and it does not gate the waits behind it | 1,800 | 101 | 1 (2000228, `FastestRouter`) |

- Five fixed runs with the monitor are permanent tests
  (`test_a_run_of_the_safety_check_returns_with_no_violation[...]` in `tests/intralogistics/test_random_worlds.py`), by
  (seed, controller, router): (100017, reservation, shortest), (100039, reservation, congestion), (100083, reservation,
  fastest), (100040, reservation, fastest) and (100045, free, fastest). Each asserts that the run ended on its own, had no
  violation and no known limit, the `env.fingerprint()` digest pinned at `5f61105` (the monitor does not change a run;
  the pins hold at `ab4d430`), and the features it exercises; between them they exercise charging, stranding, a zone held
  by two, a passing segment held by two and a deadlock (the first four), and, for the last, a free-flow run with a passing
  segment, parking, charging, stranding and the second fleet. The pins hold for `PYTHONHASHSEED` 1 and 7.
- Follow-up, not done here: the monitor's end-of-run check reads `wait.stuck` and `controller._unresolved` (private state)
  and not the emitted reports, so a controller that sets the flag but never emits `traffic.wait_stuck`, or the unresolved
  `traffic.deadlock`, passes. This predates this task.

### What the monitor does not check

Added by the final review (see "Final review"); the monitor was unchanged by that review. Assignment 2 added the last
two items (B1, B2) and removed its two exemptions: since `4bc6624` the monitor has no known limits. Since then an open
wait at the end of a run that no stuck report, no unresolved deadlock and no reported blocker explains is a violation,
and so is a parking slot held by an AGV at rest away from its area's node. Two seeds at the final head still end with a
violation, which the monitor does report (7702388 and 8101904, under "Open after Assignment 2, for the maintainer"); the
items below are what it would not report. Since `afb3b62` (Assignment 3, T2) the end check and the content of the
reports are decided from the events; see "T2. The strict monitor".

- **The spacing of followers on a segment, on the bodies.** The body model checks the direction of the vehicles on a
  single-lane segment (`PHYSICAL opposite directions on a segment`), not that vehicles following each other on it keep
  their extents (`length + min_gap`) within its length. That holds by two checks together: the holdings cover the
  bodies (`body not covered`, `holdings != body`), and the ledger satisfies the rule for its holders (`LEDGER extents
  exceed the segment`). A controller whose holdings cover the bodies and satisfy the rule keeps followers apart; the
  bodies are not compared with each other on a segment.
- **The content of a `traffic.deadlock` report.** The monitor used these reports to follow the unresolved deadlocks
  for the second known limit (until `5b3bb91`) and uses them to count the feature "a deadlock"; it does not check
  that the AGVs a report lists are in a deadlock, nor that every deadlock is reported. A wrong or spurious report
  passes. (Open waits at the end of a run are explained from the controller's state, see the follow-up above.) Checked
  since Assignment 3, T2: see "T2. The strict monitor".
- **A sleeping wait queued behind a reported one.** At the end of a run the monitor explains an open wait that is
  blocked by a wait reported stuck or unresolved, transitively, without asking that the wait itself was reported. Added
  by Assignment 2, B1: eight runs at `5b00d9f` end with such a wait, asleep (review delay kept, no timer) and with no
  report of its own, behind a wait flagged earlier: 8102359 `congestion` (agv-f behind bot-x), 8103231 `shortest`
  (agv-a behind agv-c), 100281 `shortest` and `fastest` (agv-f behind agv-c), 7700058 with the three routers (agv-e
  behind bot-x) and 7701490 `fastest` (agv-f behind bot-y and bot-z). The monitor passes them; §7.9's stuck chain
  reaches a wait behind a reported one only at the instant of that report. Checked since Assignment 3, T2: see "T2. The
  strict monitor".
- **A parking slot held during travel.** Added by Assignment 2, B2. The parking check reports a slot held by an AGV at
  rest away from the area's node (`parking slot of an AGV resting elsewhere`), and at the end of a run a slot held away
  from its node; it does not look at a slot while its AGV travels. A slot kept while the AGV travels to a charger
  elsewhere and given back as the charge starts would pass the monitor. The hand-built tests of B2 in
  `tests/intralogistics/test_idle_traffic.py` catch it (the slot goes back at the turn, before the travel to the
  charger). Checked since Assignment 3, T2: see "T2. The strict monitor".

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

Cost per job, like for like, on a loaded machine (one-minute load average 9 to 10; medians of nine repetitions at 1,600 and
25,000 jobs and of five at 99,990; two runs, the second in parentheses; `src` is the same at `5f61105` and `bcce8cc`):

| Jobs | `Layout(env)` and `prepare()` | `env.activate()` |
|---|---|---|
| 1,600 | 4.7 ms, 2.94 (2.90) microseconds per job | 7.1 ms, 4.42 (4.38) microseconds per job |
| 25,000 | 65.5 ms, 2.62 (2.58) microseconds per job | 101.9 ms, 4.08 (4.02) microseconds per job |
| 99,990 | 316.9 ms, 3.17 (3.13) microseconds per job | 470.3 ms, 4.70 (4.69) microseconds per job |

From 1,600 to 99,990 jobs (62.5 times the jobs) `prepare()` takes about 67 times as long and `env.activate()` about 66
times: a little faster than linear, not quadratic. (The activation figure of the table above, 488 ms, was measured on a
quieter machine with a fresh model per repetition and is of the same size.)

The trace size depends on where the recorder is attached and on the jobs. With the 99,990 jobs above the INITIAL payload,
the part `max_initial_bytes` (64 MiB) limits, is 36.7 MB (35.0 MiB, 55 % of the limit): 17.9 MB of layout bundle and 18.8 MB
of state. The trace file is 36.7 MB when the recorder is attached after the jobs are created (the case measured above) and
63.0 MB when it is attached before them, because their creation events are recorded too (the other 26.3 MB). The 60 to 63
MB that the Task 4 and Task 10 reviews quoted is the second case, and it reproduces exactly (`reviewer-scratch-t10/jobs.py`
attaches the recorder first). With routes of four and of ten operations the INITIAL payload is 40.3 MB (38.4 MiB) and 51.1
MB (48.7 MiB): jobs with longer routes take more of the limit. The documentation (`docs/guides/layout.md`,
`CHANGELOG.md`) says this.

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
| Linear in the jobs created before activation | 1,600 jobs and 10 servers, `Layout(env)` and `prepare()`: 4.98 ms (4.87 to 5.34) in `jobs_before_layout`; per job 2.9, 2.6 and 3.2 microseconds at 1,600, 25,000 and 99,990 jobs (see above) | yes, up to a growth a little faster than linear (about 67 times the time for 62.5 times the jobs) |
| The bundle and the hashes are built at first read | `first_bundle` 2.51 ms at 10 servers and 266.8 ms (265.2 to 269.6) at 1,600; for a network of 1,600 nodes (final review, see below) 1.05 s for the explicit grid and 0.53 s for the lattice; `test_an_unobserved_run_builds_no_bundle_and_a_read_builds_it_once` | yes |
| The overhead gate of the core workload against 0.12.0 passes with its present limit | see "Gates" | yes |
| `import simulatte` imports neither `simulatte.layout` nor `simulatte.intralogistics.graph`; a layout with no network imports no fleet, traffic or AGV module | `tests/core/test_public_api.py::test_layout_is_lazy_but_discoverable`, `::test_a_layout_without_a_network_imports_no_fleet_traffic_or_agv_module`, `tests/layout/test_environment_layout.py::test_a_layout_created_at_activation_imports_no_fleet_traffic_or_agv_module` | yes |

The 50 ms target is met at the line, as Tasks 3, 4 and 9b measured (48.5 to 50.5 ms): process to process the spread is about
0.5 ms, and a loaded machine can put a run over the target. The medians of the six processes of this record are all under it, but not every earlier run of this file: the second run of Task 4 (at `051ac30`) measured 50.2 ms with `Layout(env)` and 50.5 ms when activation creates the layout. The target is met on the median of repeated runs, with 0.3 to 1.1 ms to spare, and can be missed by a single loaded run.

Other cases of the same runs (median of the six medians, ms; range in parentheses): `prepare/10` 0.49 (0.48 to 0.50),
`prepare/1600` 47.44 (47.28 to 47.78), `construct/100` ratio 1.45, `prepare_network/explicit/16, 100, 400, 1600`
5.77, 30.44, 129.76, 569.65, `prepare_network/lattice/16, 100, 400, 1600` 3.57, 13.44, 52.16, 229.69. The preparation of a
network is that of Task 9b (5.75, 30.6, 130, 572 and 3.6, 13.5, 52.5, 229): unchanged.

The "before" of the network figures in this section and in Task 9b's is `1bc1e51`, the commit before Task 9b, not the
base of the branch (`e993ca8`). The first read of the bundle of a network, which `layout_overhead.py` does not time
(its `first_bundle` cases have servers only), was measured by the final review on the same 1,600-node models (a fresh
model per repetition, `gc.collect()` outside the timing, the median of seven after one warm-up; the script is kept with
the fix wave's scratch files, `scratch-final-fix/c1/net_bundle.py`), at `732f315`, whose `src` is that of the head of the
fix wave:

| 1,600 nodes | `prepare()` | first read of `bundle.full_hash` | An observed run pays (both) | At `e993ca8` (`prepare()`, which built the bundle) |
|---|---|---|---|---|
| explicit square grid | 0.610 s (0.602 to 0.617) | 1.049 s (1.044 to 1.055) | 1.66 s | 4.84 s (4.82 to 4.86; median of five) |
| unit lattice | 0.237 s (0.236 to 0.239) | 0.526 s (0.523 to 0.537) | 0.76 s | 3.06 s (3.05 to 3.07; median of five) |

A run that nobody records and whose manifest nobody reads pays the preparation only. The reviewer of the final review
measured 1.05 s and 0.51 s for the first read, 1.66 s and 0.75 s in all, and 4.98 s and 3.09 s at `e993ca8`.

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

Run on bigboss (Apple M4 Pro, macOS, CPython 3.14.3 unless the row says otherwise), at `ab4d430`, the last commit that
changes `src`, `tests`, `benchmarks` or `studio` (the commit after it changes this record only; the record is read by no
test. Two documentation sentences of `docs/guides/layout.md` and `CHANGELOG.md` changed at `2dd72b6`, before it, and
`tests/layout/test_acceptance.py` reads `docs/guides/layout.md`: the docs gates ran after that change). The Studio and overhead rows were run at
`5f61105` and are kept, for the reason given in the row. Result lines are copied from the output.

| Gate | Command | Result |
|---|---|---|
| Tests and coverage | `uv run --all-extras pytest` | `Required test coverage of 99% reached. Total coverage: 99.19%`; `4954 passed, 1 skipped, 3 warnings in 452.88s (0:07:32)` (the baseline of the branch was 4929 passed, 1 skipped: twenty-five tests added by this task; the three warnings are deprecations in `starlette`, `websockets` and `uvicorn`). At `bcce8cc`: 99.20 %, 4946 passed; at `5f61105`: 99.19 %, 4936 passed |
| Lint | `uv run --all-extras ruff check src tests`; also `ruff check benchmarks/layout_safety.py`, which CI does not lint | `All checks passed!` for both |
| Format | `uv run --all-extras ruff format --check` | `532 files already formatted` |
| Types | `uv run --all-extras ty check src` | `All checks passed!` |
| Docs | `uv run --all-extras zensical build` | `No issues found`; `Build finished in 0.37s`; with `tests/layout/test_acceptance.py tests/test_docs_links.py tests/test_docs_run_blocks.py`: `8 passed` |
| Studio types | `pnpm typecheck` (in `studio/`), run at `5f61105` | exit 0 |
| Studio tests | `pnpm test` (at `5f61105`) | exit 0; trace 329, playback 24, scene 25, export 15 and viewer 82 tests passed (`apps/player` has no tests: `No test files found`) |
| Studio build | `pnpm build` (at `5f61105`) | `built in 3.46s`, exit 0 (a chunk-size warning of the bundler only) |
| Studio browsers | `pnpm test:browser` (at `5f61105`) | `36 passed (1.2m)` on chromium, firefox and webkit. Not rerun: `git diff --stat 5f61105 HEAD -- src studio pyproject.toml uv.lock` is empty, and the four Studio rows read only `studio/` and the trace fixtures, which are unchanged |
| PyPy lane | `UV_PROJECT_ENVIRONMENT=<scratch>/pypy-venv uv sync --dev --python pypy-3.11 --frozen`, then `MPLBACKEND=Agg UV_PROJECT_ENVIRONMENT=<scratch>/pypy-venv uv run --python pypy-3.11 pytest tests/core tests/execution tests/intralogistics tests/layout -o addopts="-q -p no:cacheprovider"` (job `test-pypy` of `ci.yml`; a separate project environment keeps the checkout's `.venv` on CPython) | `4823 passed, 23 skipped in 572.27s (0:09:32)` (PyPy 7.3.22, Python 3.11.15), rerun at `ab4d430` because the lane covers `tests/intralogistics`, which changed (at `bcce8cc`: 4815 passed; at `5f61105`: 4805 passed) |
| CPython 3.11 | `UV_PROJECT_ENVIRONMENT=<scratch>/py311-venv uv run --python 3.11 --all-extras pytest tests/intralogistics tests/layout --no-cov -q` | `2612 passed in 118.03s (0:01:58)` (Python 3.11.13), rerun at `ab4d430` for the same reason (at `bcce8cc`: 2604; at `5f61105`: 2594) |
| Overhead gate of the core workload | the `bench` job's step "Mode none against released and stripped 0.12.0 (gate)" on CPython 3.14, `--warmup 1 --repeat 5 --processes 3`, run locally as described below | pass on both workloads against both baselines in three rounds, at `5f61105`. Not rerun: it measures `src` (empty diff since `5f61105`) with `benchmarks/run.py` and `compare.py`, unchanged; the commit after it changes only `tests/` and `benchmarks/layout_safety.py` |

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

Since Assignment 2 the limit against the stripped copy is 12.5 % (10.5 % + 2.0 %), not the +8.5 % of this table
(decision D106); see "C. The band of the core overhead gate" and the rounds at the final head in "Gates at the final
head (Assignment 2)".

## Walk of spec §10 (Task 11)

One row per item and per sub-claim. "Covered" says what shows it; a claim nothing covers is written as not covered.

| Item | Claim | Covered by | Result |
|---|---|---|---|
| 1 | No code path in `src/simulatte` accepts a concrete `LayoutGraph` or `Node` where a handle is required | `tests/layout/test_handles_only.py::test_a_concrete_graph_or_node_raises_with_the_fix` (the seven entry points `FleetCoordinator(graph=)`, `ReservationController(graph=)`, `Warehouse(input_bays=)`, `Warehouse(output_bays=)`, `AGV(initial_node=)`, `ChargingStation(node=)`, `ParkingArea(node=)`, each with and without a declared network: 14 cases), `::test_a_concrete_node_in_the_builders_mapping_raises_with_the_fix` (2), `::test_a_handle_of_another_environments_layout_raises` (7); the signatures of the components take `NodeRef` and `PortRef` (`grep` of `src/simulatte/intralogistics` finds no union of a concrete graph or node with a handle at a construction parameter) | covered by the tests for the eight entry points; not proved for a path no test names (the Task 9 review checked a table of the strictness of every parameter) |
| 1 | `grep` finds no `_explicit_graph_use` or `_uses_explicit_graph` | `grep -rn '_explicit_graph_use\|_uses_explicit_graph\|_register_graph_use\|check_existing_endpoints' src tests examples benchmarks docs skills CLAUDE.md` at `ab4d430` | no match (exit 1) |
| 2 | `tests/` contain no model built in explicit mode | explicit mode cannot be built: the entry points above raise; the whole suite passes (`4936 passed`); the words "explicit mode" remain only in docstrings that name the commit at which a fingerprint was pinned (`tests/layout/test_binding.py:347`, `tests/layout/test_builders.py:248`, `tests/intralogistics/test_random_worlds.py:49`) and in the docstring of `tests/intralogistics/test_traffic.py:128` ("the successor of the explicit-mode rule"); `tests/layout/test_traffic_fields.py::test_an_explicit_graph_and_its_layout_twin_produce_the_same_event_stream` builds its model with `Explicit` and handles, as its twin | covered |
| 2 | `examples/` contain no model built in explicit mode | `tests/intralogistics/test_examples.py` runs the intralogistics and layout examples (`test_intralogistics_simple_example_runs`, `..._intermediate_...`, `..._advanced_...`, `..._traffic_...`, `test_layout_intralogistics_example_runs`, `test_layout_stages_example_runs`) and `::test_each_transport_example_records_a_resolved_layout_with_arcs[5 examples]`; every `LayoutGraph(` in `examples/` is followed by `Explicit(graph)` and handles; `examples/studio_recording.py` (`build_simple_system(env)`) is run by no test and was run for this record, in both of its models (`--model fleet` and `--model shop`, exit 0, the trace checked and verified by the script) | covered; the other examples are production models |
| 2 | `benchmarks/` contain no model built in explicit mode | `benchmarks/intralogistics.py` builds through the layout and ran at `5f61105` (`--mode none --shifts 1 --warmup 0 --repeat 1`, exit 0; it is also the `bench` job's intralogistics step); `benchmarks/layout_overhead.py` and `benchmarks/layout_safety.py` ran for this record | covered |
| 2 | `docs/` and `skills/` contain no model built in explicit mode | `tests/test_docs_run_blocks.py::test_run_blocks_match_example_files` and `::test_intralogistics_run_blocks_match_example_files` keep the embedded example scripts equal to the examples that run; the other code blocks of `docs/` and `skills/simulatte-intralogistics/SKILL.md` were checked by the Task 10 review (every changed block run, quoted messages matched) and are not run by a test; the wording grep over docs, skills and CLAUDE.md is empty (Task 10 gates) | partly covered: the blocks that are not an embedded example are not executed by any test |
| 3 | The parity run of §8.1 is recorded with its counts | "Parity of explicit and layout models (Task 2)": 1,000 worlds, 6,000 pairs equal, 13,093,868 events compared, at `e4afae4` | recorded |
| 3 | The safety run of §8.4 is recorded with its counts | "Safety run of layout-built worlds (Task 11)": 2,000 worlds, 12,000 runs, 13,291,235 events, no violation, no exception, every run returned | recorded |
| 4 | The performance targets of §9 are measured and recorded | "Final numbers against spec §9" | recorded; all met, the 1,600-server activation at the line |
| 4 | The core gate passes | "The overhead gate" | pass, twelve comparisons |
| 5 | A trace of every example opens in Studio with its lanes drawn, checked with the TypeScript reader's tests | the traffic example only: `tests/fixtures/traces/generated/example_traffic.simtrace` is projected by `studio/packages/scene/test/traffic.test.ts` ("projects the recorded traffic example with its links and without the grid fallback": no grid fallback and links present) and a trace of an earlier version stays on the grid fallback ("projects a trace of an earlier version, no layout, on the grid fallback"); for each of the five transport examples (`intralogistics_simple`, `_intermediate`, `_advanced`, `_traffic`, `building_an_agv_system`), `tests/intralogistics/test_examples.py::test_each_transport_example_records_a_resolved_layout_with_arcs` asserts that its trace carries a resolved layout whose network graph has arcs; for a production model, `tests/layout/test_environment_layout.py::test_the_trace_of_a_production_model_carries_its_layout` and the regenerated `shop_small`, `lumscor` and `cell_ops` fixtures (Task 4) | partly covered: the other four transport examples are not projected by a TypeScript test, nor opened in Studio, and two transport models are not even checked for a recorded layout with arcs: `examples/layout_intralogistics.py` (a test runs it and checks its output) and `examples/studio_recording.py --model fleet` (no test runs it; it was run for this record and its trace checked by the script); the claim that their lanes are drawn rests on their traces carrying a resolved layout with arcs, like the traffic example's |
| 6 | Full suite | `uv run --all-extras pytest` | `4954 passed, 1 skipped` |
| 6 | Coverage stays at 99 % or above | same run | `Total coverage: 99.19%` |
| 6 | Lint, format, types | `ruff check src tests`, `ruff format --check`, `ty check src` | all clean (see "Gates") |
| 6 | Docs build | `zensical build` | `No issues found` |
| 6 | Studio gates | `pnpm typecheck && pnpm test && pnpm build && pnpm test:browser` | all exit 0, `36 passed` |
| 6 | The PyPy lane | the command of `ci.yml` | `4823 passed, 23 skipped` |

Also run, beyond §10: `tests/intralogistics` and `tests/layout` on CPython 3.11 (2612 passed), the floor of the supported
versions.

The walk and its counts are as of `ab4d430`. The gate rows, the test counts and the safety counts at the final head are
in "Gates at the final head (Assignment 2)"; no row of the walk is contradicted by them (the safety run of item 3 now
has no known limit; the 12,000 runs of item 3 are those of `ab4d430`).

## Final review

### What was reviewed and measured

Two final reviewers read the whole branch at `e1b3d1c` against its base `e993ca8` and measured it end to end:
production models are identical apart from the layout fields (21 written by hand, 400 random); 8,400 transport pairs,
explicit mode at `e993ca8` against the layout at `e1b3d1c`, are identical; the bundles of layout models that existed
before the branch are byte-identical except where an explicit arc records a length; no concrete object is accepted
where a handle is required (611 probes). They found no critical finding and no behavioural defect. They found
documentation that was false or missing, error messages of the strict contract that did not name the fix, and gaps in
this record. One fix wave followed; these are its changes.

### What the fix wave changed

| Commit | Change |
|---|---|
| `751959a` | `fix(intralogistics)`: the route check (`_check_route`) compares each hop's endpoints with the resolved arc's by identity (and a route of one node with the AGV's node), so a router that returns copies of the graph's nodes is refused when the route is returned, naming the router, instead of failing at the first arrival with the move started. |
| `67d5699` | `fix(layout)`: a node the script built, given to `agv.current_node` or `env.entities.bind_node`, is refused with the canonical node named (`fleet.graph`, or `env.layout.prepare().network.node(env.layout.node(id).address)`); a handle there is refused as a handle, which is for construction; assigning the node of an AGV whose node is pending says "before assigning it". Reading what a `Node` or `LayoutGraph` has (`id`, `x`, `nodes`, `length`, `distance`, `shortest_path` and the rest) from a handle raises `LayoutNotReady` with one message naming `fleet.graph` and `env.layout.prepare().network`; other names, `copy`, `pickle` and `repr` of handles behave as before (probing these names with `hasattr` or `getattr` with a default does not: see "Residual findings" below). An unregistered custom traffic controller names `layout.register_resource_owner` and `layout.on_bind`; a `ReservationController` of another environment has its own sentence. `env.step()` after a failed activation says that activation is in progress or failed. `Explicit(zones=)` with `Zone` objects or `Node` members, `layout.node(node)` and `layout.set_network(graph)` name what to write. |
| `f652848` | `docs`: a lift's offset lies along the direction of travel under `corner_speed` or `turn_time` (changelog, layout guide, skill); resolved arcs are one-way and only nodes compare equal to the declared ones (changelog, layout and intralogistics guides, skill, a migration row); migration rows and notes for a capacity on a block node, `agv.current_node`, custom traffic controllers, entity names with a lone surrogate, non-finite coordinates and self-loops; the full order of node capacities; the layout guide's sentence that no viewer is included is gone; the docstrings of `Trace.layout` and of the second known limit. |
| `732f315` | `docs(specs)`: spec §5.8, `Trace.layout` is also `None` for a trace whose activation failed. |
| `b01c3af` | `test(layout)`: the test of the handle protocols added by `67d5699` accepts each interpreter's error for copying or pickling a handle (whose owner holds the environment): `TypeError` on CPython 3.14, `AttributeError` on 3.11, `PicklingError` on PyPy. The first runs of the PyPy lane and of CPython 3.11, at `732f315`, failed this one test (`1 failed, 4832 passed, 23 skipped` and `1 failed, 2620 passed`). |
| this commit | `docs(specs)`: this section, the first bundle read of a network (under "Final numbers against spec §9"), what the monitor does not check (under "Safety run"). |

The error paths changed, and two inputs that were accepted are now refused ("Residual findings" below); nothing
else changed: the canonical node still passes on one identity check (the AGV's fast path
gains a `try`, which costs nothing when nothing is raised), and the activated `env.step()` still reads one field
(`tests/layout/test_binding.py` and `tests/layout/test_activation.py` hold unchanged; the table of
`test_the_agv_and_the_layout_accept_the_same_nodes` gained handles and an object whose `id` is not ready). Each code
change has a test that fails on the source of `e1b3d1c` (the final test files run with that source first on
`PYTHONPATH`: `11 failed, 146 passed`).

### Evidence that no model changed

- The six fingerprints of `tests/intralogistics/test_random_worlds.py` (seeds 26, 88, 182, 236, 272 and 619) hold, and
  so do the bundle and hash pins (`tests/layout/test_hashes.py`, `tests/layout/test_traffic_fields.py`: `177 passed`).
- The safety sample, run at `e1b3d1c` and at the head with
  `uv run --all-extras python benchmarks/layout_safety.py --worlds 300 --first 100000` and `--first 2000000`, printed
  the same output apart from the wall-clock line, exit status 0 for all four runs:
  seeds 100000 to 100299, 1,800 runs, 2,052,398 events, none raised, none not returned, no violation, 3 runs with the
  parking-slot known limit; seeds 2000000 to 2000299, 1,800 runs, 2,145,016 events, none raised, none not returned, no
  violation, one run with the parking-slot limit and one with the withdrawn-deadlock limit (seed 2000228, `fastest`).
- The stdout of the transport examples (`intralogistics_simple`, `_intermediate`, `_advanced`, `_traffic`,
  `building_an_agv_system`, `layout_intralogistics`) is byte-identical at `e1b3d1c` and at the head.
- The route check costs nothing measurable: `benchmarks/intralogistics.py --mode none --shifts 1 --warmup 1 --repeat 5
  --processes 3`, five rounds alternating the source of `e1b3d1c` and the head, gives a ratio of 1.005 (0.997 to 1.022)
  with the same order trajectory in every run. The check runs 2,336 times in that shift and checks 9,165 hops; the
  added identity tests cost 7.6 ns per hop, 0.07 ms of a 239 ms run (0.03 %).

### Gates at the head

Run on bigboss (Apple M4 Pro, macOS, CPython 3.14.3 unless the row says otherwise). The test suites, lint and format ran
at `b01c3af`, the last commit that changes `src`, `tests`, `docs` or `benchmarks` (the commit after it changes this
record only); types, the docs, the safety sample, the pins and every timing ran at `732f315`, whose `src`, `docs` and
`benchmarks` are those of `b01c3af` (it changes one test). Result lines are copied from the output.

| Gate | Command | Result |
|---|---|---|
| Tests and coverage | `uv run --all-extras pytest` | `Required test coverage of 99% reached. Total coverage: 99.19%`; `4964 passed, 1 skipped, 3 warnings in 464.59s (0:07:44)` (the same counts at `732f315`; at `e1b3d1c`, run by this wave: 99.19 %, `4954 passed, 1 skipped`). No line of `src` is uncovered at the head that was covered at `e1b3d1c` (the two `coverage.xml` compared by file and line text: 0 lines) |
| Lint | `uv run --all-extras ruff check src tests benchmarks` | `All checks passed!` |
| Format | `uv run --all-extras ruff format --check` | `532 files already formatted` |
| Types | `uv run --all-extras ty check src` | `All checks passed!` |
| Docs | `uv run --all-extras zensical build`; `tests/layout/test_acceptance.py tests/test_docs_links.py tests/test_docs_run_blocks.py` | `No issues found`, `Build finished in 1.60s`; `8 passed` |
| Safety sample | `benchmarks/layout_safety.py --worlds 300` from 100000 and from 2000000 | the counts above, exit 0, identical to `e1b3d1c` |
| PyPy lane | `UV_PROJECT_ENVIRONMENT=<scratch>/pypy-venv uv sync --dev --python pypy-3.11 --frozen`, then `MPLBACKEND=Agg UV_PROJECT_ENVIRONMENT=<scratch>/pypy-venv uv run --python pypy-3.11 pytest tests/core tests/execution tests/intralogistics tests/layout -o addopts="-q -p no:cacheprovider"` (job `test-pypy` of `ci.yml`, in an environment of its own) | `4833 passed, 23 skipped in 613.41s (0:10:13)` (PyPy 7.3.22, Python 3.11.15; at `ab4d430`: 4823 passed) |
| CPython 3.11 | `UV_PROJECT_ENVIRONMENT=<scratch>/py311-venv uv run --python 3.11 --all-extras pytest tests/intralogistics tests/layout --no-cov -q` | `2621 passed in 131.16s (0:02:11)` (Python 3.11.13; at `ab4d430`: 2612 passed) |
| Overhead gate of the core workload | the `bench` job's step "Mode none against released and stripped 0.12.0 (gate)", `--warmup 1 --repeat 5 --processes 3`, run locally as described below | against released 0.12.0, pass in all 18 comparisons of nine rounds (-26.5 % to -19.8 %, limit +5.0 %); against 0.12.0 without `env.debug` calls, **6 of 18 fail**, by up to 2.0 points (overhead +5.2 % to +10.5 %, median +8.5 % on u90 and +8.1 % on u95, limit +8.5 %). The source of `e1b3d1c`, run in the same session, fails 2 of 6 (+7.8 % to +8.8 %). See below: the margin against the stripped copy is gone on this machine today for `e1b3d1c` as for the head, and nothing on the measured path changed |
| Studio | not rerun | last run at `5f61105` (see "Gates (Task 11)"). Nothing it covers changed: `git diff --stat e1b3d1c HEAD -- studio` is empty, and the trace format is unchanged (`src/simulatte/trace/` changed in one docstring) |

### The overhead gate at the head

Run as `benchmarks/README.md` and the `bench` job describe it: mode `none` on the two CI workloads, `--warmup 1
--repeat 5 --processes 3`, `compare.py --budget 0.03 --noise 0.02` against released 0.12.0 and `--budget 0.065 --noise
0.02` against the stripped copy. 0.12.0 runs in the environment Task 11 built (`simulatte==0.12.0` with the checkout's
dependency versions as constraints) and the stripped copy is the one `strip_debug.py` made from it then; each run
printed the package it imported. The branch side is the checkout's `.venv` (or, for `e1b3d1c`, the `git archive` of its
`src` first on `PYTHONPATH`). One-minute load average 1.3 to 3.6 (other sessions were running).

| Rounds | Branch side | vs released, u90 | vs released, u95 | vs stripped, u90 | vs stripped, u95 |
|---|---|---|---|---|---|
| 1 to 6 | head | -25.3, -26.5, -24.1, -24.7, -25.4, -24.2 % | -23.5, -19.8, -23.3, -23.0, -23.3, -23.7 % | +8.3, +5.2, +7.4, **+9.0**, +7.3, **+8.9** % | +7.9, **+10.5**, +6.9, +8.1, +8.1, +7.8 % |
| 7 to 9, alternating with the next row | head | -24.3, -24.4, -24.2 % | -23.1, -22.5, -23.3 % | **+9.2**, +8.5, **+9.0** % | **+8.7**, +8.5, +8.0 % |
| 7 to 9, alternating with the row above | `e1b3d1c` | -24.5, -24.5, -24.5 % | -22.6, -22.7, -23.1 % | **+8.8**, +7.8, +8.2 % | **+8.7**, +7.8, +8.1 % |

Bold is a comparison over the limit of +8.5 %. The branch medians are 0.486 to 0.505 s (u90) and 0.524 to 0.547 s (u95)
at the head and 0.487 to 0.489 s and 0.525 to 0.526 s at `e1b3d1c`; the stripped copy's 0.449 to 0.471 s and 0.483 to
0.497 s. Task 11 measured the same source (`src` of `e1b3d1c` is that of `5f61105`) at +7.3 % to +7.7 % and +6.6 % to
+7.6 % against the stripped copy, with branch medians of 0.484 s and 0.520 s: the machine measures the branch about 0.6
points worse against the stripped copy today, and the margin of 0.8 to 1.9 points that record noted is gone, for
`e1b3d1c` and for the head alike.

Nothing on the measured path changed. The core workload is a production job shop: it runs none of the changed
intralogistics or layout code, and the one change it executes, `Environment.step`, is a new branch inside
`if not self._activated`, which an activated run never enters. Measured directly, alternating the source of `e1b3d1c`
and the head in separate processes:

- the gate's workloads, `run.py --mode none`, four rounds each: head / `e1b3d1c` 1.006 (0.991 to 1.031) on u90 and
  1.005 (0.987 to 1.016) on u95, within the spread of the rounds;
- a process waiting on 300,000 timeouts after activation (median of nine runs per process), four rounds: 439.6 to
  448.5 ns per timeout at the head, 445.2 to 464.3 ns at `e1b3d1c`.

The gate is a CI gate and runs on the CI runner, whose calibration set the limits (README); it was not run there for this
wave. Locally it passes against released 0.12.0 and is at its limit against the stripped copy, as it is for `e1b3d1c`.

The limit against the stripped copy stated here (+8.5 %) is that of this date: since Assignment 2 it is 12.5 % (decision
D106); see "C. The band of the core overhead gate".

## Residual findings after the fix wave

The fix wave was reviewed once, at `9c3fba4`, by a reviewer who had not seen the branch. All sixteen items were
found addressed and no model changed (520 random worlds, 3,120 runs, 3,464,917 events byte-identical between
`e1b3d1c` and `9c3fba4`; 17 example scripts, 21 production models and 820 layouts identical). The plan allows one fix
wave, so what that review found was recorded here and left to the maintainer. Status after Assignment 2: items 1 to 6
are fixed in A, item 7 is fixed in B1, item 8 was decided in C, and item 9 (found later, by the review of B1) is open.
Each item says so where it stands; the open ones are listed under "Open after Assignment 2, for the maintainer".

Introduced by the fix wave:

1. `Explicit(graph, zones=[])` and `zones=()` now raise ("takes a mapping ..., not Zone objects"). Before the wave
   they declared an empty zone map, as `zones={}` does (`src/simulatte/layout/types.py:107-113`).
   **Fixed in `060a055`** (Assignment 2, A).
2. `hasattr(handle, name)` and `getattr(handle, name, default)` now raise `LayoutNotReady` for `id`, `distance`,
   `length`, `path_length`, `path_distance`, `shortest_path` and `subdivide` (`src/simulatte/layout/refs.py:71-78`);
   before the wave they returned `False` and the default. `LayoutNotReady` is a `ValueError`, not an
   `AttributeError`, so `hasattr` does not catch it. The six names that raised already (`x`, `y`, `z`, `nodes`,
   `neighbors`, `arc_between`) are unchanged. One effect inside the library: `env.entities.is_live(handle)` raises
   where it returned `False`. A class deriving from both `LayoutNotReady` and `AttributeError` would restore the
   probes and keep the message of a direct read; it was checked on CPython 3.14, 3.11 and PyPy 3.11 by the reviewer,
   not applied.
   **Fixed in `060a055`**, with that class.
3. `layout.place(handle, ...)`, `layout.flow([handle, ...])` and `env.entities.retire(handle)` now answer with the
   handle message, whose advice to read `fleet.graph` does not fit those calls.
   **Fixed in `060a055`** (`layout.port` too).
4. One refusal message serves every node that is not canonical (`src/simulatte/layout/layout.py:338-341`): a
   disabled node, a node of another environment and an unknown id are told to take the node of the same id from
   `fleet.graph`, which does not help them.
   **Fixed in `060a055`**; see the differences listed in Assignment 2, A.
5. The message for a controller of another environment is chosen by a private attribute (`layout.py:401`): a custom
   controller with any `_env` that is not the fleet's is told it belongs to another environment.
   **Fixed in `060a055`.**
6. `Trace.layout` is `None` for any trace without an INITIAL record. The docstring and spec §5.8 name traces of
   earlier versions and failed activations; a recorder on an environment closed without ever activating is a third
   case (outcome `completed`).
   **Fixed in `060a055`** (the docstring; spec §5.8 is the controller's).

Found by that review outside the fix wave:

7. World 52118 under `ReservationController` and `ShortestDistanceRouter` ends with a monitor violation that is not
   one of the two known limits: `open wait with no report ['agv-g', 'bot-y']` at t=576.19
   (`uv run --all-extras python benchmarks/layout_safety.py --worlds 1 --first 52118`). It is identical at `e1b3d1c`
   and at `9c3fba4`, and a tree that still had explicit mode gives the same 829 events and the same violation in both
   modes, so it comes neither from the fix wave nor from the layout. From the trace: an unresolved deadlock of five
   at t=172.8; the wait of one member is withdrawn at t=293.7; two of the others get stuck reports; `agv-g` and
   `bot-y`, blocked by an AGV that is not waiting, get none. It looks like the missed detection of the second known
   limit in a shape that the monitor's exemption, which requires a closed set, does not recognise; this was not
   investigated further. It was 1 run of that review's 3,120. The safety run recorded above (seeds 100000 to 100999
   and 2000000 to 2000999) has no violation; the statement does not extend to other seeds.
   **Fixed in `df2f7c3`** (Assignment 2, B1): it is the second known limit's defect in another shape, and the first
   half of that defect's fix makes the run clean. The second half (`c6cb563`) covers the shape the first left open,
   seed 8103801 `fastest`. The statement holds for the seeds run (see B1).
8. The overhead gate against the stripped 0.12.0 is at its limit on this machine for the base and the head alike:
   over eight alternating rounds, `e1b3d1c` gave +6.3 to +9.2 % (u90) and +6.9 to +8.0 % (u95), `9c3fba4` +7.3 to
   +9.3 % and +8.1 to +9.0 %, against a limit of +8.5 %; a whole gate passed in 4 of 8 rounds for each. Two stripped
   runs of the same round differ by -1.0 to +1.2 %. The gate workloads execute four statements changed by the wave,
   all at import. The head is not slower than the base of the branch (-0.50 % and -0.38 % against `e993ca8`). On the
   CI runner the `bench` job passed at `e993ca8` with +6.8 % and +7.7 %, and has failed once on `main` for a commit
   that touched no source.
   **Decided in C** (Assignment 2): the CPython budget against the stripped copy is 10.5 %, the limit 12.5 % (decision
   D106); the gate passes in all comparisons at the final head (see "Gates at the final head (Assignment 2)"). The noise
   band is left open (item (f) of "Open after Assignment 2, for the maintainer").

Found by the review of Assignment 2, B1 (open, not changed; for the maintainer):

9. Seed 7702388, `ReservationController`, `ShortestDistanceRouter`, ends with `open wait with no report ['agv-e']` at
   586.206 (`benchmarks/layout_safety.py --worlds 1 --first 7702388`), identical at `6ff842f`, `687ad66` and `5b00d9f`
   (1,258 events, fingerprint `8a40c7e16f04`). No remembered unresolved set is involved. See "Found beyond this task"
   under B1, fix round 2, for the cause as confirmed. **Open** (Ruling A2-h; item (a) of "Open after Assignment 2, for
   the maintainer"): seed 7702388 still ends with the violation at the final head.

## Assignment 2

### A. Residuals of the fix wave

Items 1 to 6 of "Residual findings after the fix wave" are fixed in `060a055`
(`fix(layout): handles answer attribute probes, empty zone sequences, messages that fit the call`). Items 7 and 8 of that
section are not part of this task. No event, digest, bundle or hash of any model changed.

| Item | What changed |
|---|---|
| 1. `zones=[]`, `zones=()` | An empty list or tuple declares the empty zone map, as `zones={}` does. A non-empty sequence, `Zone` objects in a sequence or as mapping values, and `Node` members raise the messages they raised. |
| 2. Probes of a handle | Reading a physical attribute of a handle (the thirteen names: `id`, `x`, `y`, `z`, `nodes`, `neighbors`, `arc_between`, `distance`, `length`, `path_length`, `path_distance`, `shortest_path`, `subdivide`) raises a private class derived from `LayoutNotReady` and `AttributeError`. `hasattr` is False, `getattr` with a default returns it, a direct read raises the same message, and `except LayoutNotReady` and `except LayoutError` still catch it. `env.entities.is_live(handle)` is False again. No public name is added. |
| 3a. `place`, `flow`, `port`, `retire` | Each answers a handle with a message that says what the call takes; `layout.place` with a node handle of an explicit id adds `layout.place('A', kind='node', ...)`. `layout.port` had the same fault and is changed with them. |
| 3b. Refused nodes | The refusal after the identity check is chosen by `Layout._refused_node` (the accepted path is unchanged): equal to the canonical node (keeps the `fleet.graph` advice); disabled; an id that is not in the network; an id of the network at another position; an object that is not a `Node`; a layout with no resolved network. |
| 3c. Controller of another environment | The layout recognises a built-in `ReservationController` that is not registered with it (it registers with the layout of its own environment at construction); any other unregistered controller is told to register, whatever private attributes it has. A `ReservationController` subclass that never ran the constructor carries no environment and is told to register (fix round 1: the first version told it it belonged to another environment). |
| 3d. `Trace.layout` | Docstring (corrected in fix round 1; the first wording said earlier traces have no INITIAL record, and the frozen fixtures have one): "None when the trace has no INITIAL record (an activation that failed before the initial state was recorded, outcome ``failed``; a recorder closed on an environment that was never activated, outcome ``completed``) or when its INITIAL record carries no layout (traces of versions without a layout)." The test checks the three facts (a frozen fixture, a failed activation, a never-activated recorder). |

Differences from the brief, found while doing it:

- `Explicit(graph, zones={})` is not the same bundle as no `zones` argument: it declares schema 1.1 with an empty zone
  list, and no argument gives schema 1.0 (this was so before the fix wave too: `zones=[]` was `dict([])`, an empty
  map). The fix gives `zones=[]` and `zones=()` the bundle and hashes of `zones={}`, which the test pins; it does not
  make `zones={}` equal to no argument.
- A node of another environment's network cannot be told from a node the script built: `Node` carries no owner and the
  layout holds no registry of other layouts. A node of another network is told it is not a node of this network when
  its id is absent, that it lies elsewhere when its id is present at another position, and, when it is equal to the
  canonical node, that it is "one the script built, or one of another environment's network".
- `ReservationController` has no public `env`; the layout decides from the class of a controller it did not register
  (looked up only if its module is imported) and the environment its constructor set (`_env`, read only for that
  class): a built-in controller of another environment is told so, anything else unregistered is told to register.
- Of the five `getattr(obj, "id", None)` idioms, four are reached with a handle and tested (`attach`, `retire`,
  `is_live`, a collector's scope); the fifth (`layout/binding.py`, the `target` of the not-ready error) takes a
  component and is never given a handle.

Evidence. RED: at `66a5a96` (source stashed) the new and changed tests of `test_handles_only.py`, `test_binding.py` and
`test_trace.py` gave `8 failed, 137 passed`. The item 1 test was then written as a comparison with no `zones` argument
and corrected afterwards to compare with `zones={}`; it fails at `66a5a96` on the first `Explicit(..., zones=[])`.
GREEN: `954 passed` for `tests/layout`, `tests/core/test_entities.py`, `tests/intralogistics/test_entities.py` and
`tests/intralogistics/test_fleet_events.py`. CPython 3.11.13: `919 passed`; PyPy 7.3.22 (Python 3.11.15): `918 passed,
1 skipped`, for `tests/layout tests/core/test_entities.py tests/intralogistics/test_entities.py`. Full suite at
`060a055`: `Required test coverage of 99% reached. Total coverage: 99.19%`; `4974 passed, 1 skipped, 3 warnings in
442.30s (0:07:22)`. `ruff check src tests benchmarks`: `All checks passed!`; `ruff format --check`: `532 files already
formatted`; `ty check src`: `All checks passed!`; `zensical build`: `No issues found`. The stdout of seven transport and
layout examples and `layout_safety.py --worlds 300` from 100000 and from 2000000 are byte-identical before and after;
`--first 100000 --worlds 200` (1,200 runs, 1,361,222 events, no violation, 3 runs with the parking-slot limit) is
identical apart from the wall-clock line, exit 0 each. The hash and bundle pins and the six fingerprints of
`tests/intralogistics/test_random_worlds.py` hold unchanged (full suite).

Fix round 1 (after the scoped review of A): `Trace.layout` docstring and its test corrected as in the 3d row; the
other-environment message only for a `ReservationController` that carries another environment, with a test of a
subclass that never ran the constructor; the place, flow, port and retire messages say a handle "stands for a part of
the layout" (true for `GraphRef`, `NodeRef`, `PortRef` and `ValueRef`, all four now used by the tests); a refused node on
a layout whose network is declared and not prepared is told to call `env.layout.prepare()` or `env.activate()`, and one
on a layout with no network to declare it. The added tests are: `test_handles_only.py` 4 (the old one changed to four
handle kinds), `test_binding.py` 3, `test_trace.py` 1 (replacing a docstring check). Full suite at `d3723c3`: `Required
test coverage of 99% reached. Total coverage: 99.19%`; `4975 passed, 1 skipped, 3 warnings in 452.89s (0:07:32)`;
`ruff check src tests benchmarks`, `ruff format --check` (532 files) and `ty check src` clean.

### B1. Waits after a deadlock member's wait ends

Decided by the maintainer on 2026-11-24: fix, before 0.18.0 is tagged, the two cases the random worlds found where a wait
is left open and unreported, and delete the monitor's exemption for the first. Commits `df2f7c3`
(`fix(intralogistics): a deadlock is examined again when a member's wait ends`) and `5b3bb91`
(`test(intralogistics): the monitor no longer exempts waits left after a withdrawn deadlock member`), base `6ff842f`.

**Cause, item 1 (known limit 2, seed 2000228, `FastestRouter`).** As recorded under "Known limits met by the random
worlds": `_end_wait` drops every remembered unresolved set that holds the AGV whose wait ends; `withdraw` then only
scans, and nothing looks at the other members again. A reaction staged by a review (`_react`) ends a member's wait the
same way. Members whose wait opened into the set have no review timer, and the reviews of the others may sleep (T23).

**Cause, item 2 (residual item 7, seed 52118, `ShortestDistanceRouter`).** The same defect in another shape. The set
`{agv-d, agv-f, agv-g, bot-z}` is reported unresolved at 153.2, `{agv-d, agv-f, agv-g, bot-y, bot-z}` at 158.2 and
again at 172.8. At 198.4 agv-c strands and
`idle()` reviews bot-y and agv-g; their reviews at 228.4 find no change and sleep, and `_report_one` refuses them as
members. At 293.7 a cancellation withdraws agv-f's wait: `_end_wait` drops the set, `withdraw` only scans, nothing is
granted or released, so the sleeping reviews are not woken; `_reduce` without gates over the waits left gives
`[bot-y, agv-g]`. agv-f then strands and `idle(agv-f)` reviews only the wait it blocks (agv-d), whose stuck report
reaches the wait behind it (bot-z); agv-a's stuck report at 317.5 reaches only the waits behind agv-a. No wait starts
afterwards. bot-y and agv-g are also blocked by the stranded agv-c, so they are not a closed set and the monitor's
exemption did not recognise them: a violation instead of the known limit. No review timer was lost and the stuck chain
behaves as §7.9 states.

**Change.** In `withdraw`, and in `_react` after a staged reaction, when the AGV whose wait ends was a member of a set
left unresolved, the controller runs `_detect()` again if `_reduce(gates=False)` over the open waits is not empty
(`_detect_again`, `_unresolved_member`). This is the fix the reviewer measured (`final-scratch-area2/headfix`,
variant 2), written without the instance flag: the membership is read before the wait ends. The detection that runs
is the whole of §7.8, so its gate-waiver pass (gating claims of the remainder with gates marked yielding) runs there
too; nothing else in admission, the gate, review timing, what the policy may stage, make-way or charging changed. Under
`FreeFlowController` the controller is not involved. (Fix round 1 below removes the `_reduce` guard and adds the
second half.)

**Monitor.** `KNOWN_LIMIT_WITHDRAWN`, the orphan tracking (`_track_deadlocks`, `_standing`, `_orphans`) and the closure
in `Monitor.final` are removed; `final` is again the check of `e4afae4`: every open wait at quiescence is explained by a
stuck report or an unresolved deadlock, or queued behind one, or it is `open wait with no report`. Removed tests:
`test_waits_left_open_after_a_member_of_an_unresolved_deadlock_was_withdrawn_are_a_known_limit`,
`test_a_wait_that_starts_after_the_withdrawal_disqualifies_the_orphans`,
`test_waits_that_are_still_a_closed_deadlock_are_the_known_limit_with_the_waits_queued_behind_them`,
`test_a_wait_that_is_not_blocked_or_not_only_by_the_set_is_not_the_known_limit` (3 cases),
`test_only_a_withdrawal_leaves_the_rest_of_an_unresolved_deadlock_unreported` and
`test_a_later_report_or_the_end_of_their_own_wait_covers_the_rest_of_the_deadlock`. The pin on seed 2000228 became
`test_waits_left_after_a_member_of_an_unresolved_deadlock_was_withdrawn_are_resolved_or_reported` for seeds 2000228
(`fastest`) and 52118 (`shortest`): no violation, no known limit, and a `traffic.deadlock` at the instant of the
withdrawal (2000228: `agv-c, agv-d, agv-e, agv-g`, victim agv-e, `step_aside`; 52118: `agv-g, bot-y`, `unresolved`).
The seven runs of mutant controllers that must end with `open wait with no report` stay
(`test_an_open_wait_with_no_report_is_a_violation`). The monitor now exempts only the parking slot (`KNOWN_LIMIT`),
until Task B2.

**Measurement.** The base `6ff842f` and the head `5b3bb91`, each a `git archive` first on `PYTHONPATH`, the same worlds
under each tree's own monitor, both controllers and three routers: seeds 100000..100999, 2000000..2000999 and
52000..53999 (4,000 worlds, 24,000 runs; 12,000 under `ReservationController`). Every run returned, at both commits.
Pooled over the 12,000 reservation runs, paired by (seed, router); the interval is the 95 % t interval of the sum of
the paired differences. "Open waits without a report" is counted by the measuring script with the same rule for both
trees (not stuck, not in an unresolved set, not queued behind such a wait), independently of the exemption.

| Measure | `6ff842f` | `5b3bb91` | Difference, 95 % interval | Runs that differ |
|---|---|---|---|---|
| runs that returned | 12,000 | 12,000 | 0 | 0 |
| runs with a violation (all `open wait with no report`) | 1 | 0 | -1 [-3.0, +1.0] | 1 |
| runs with the withdrawn-deadlock known limit | 1 | 0 | -1 [-3.0, +1.0] | 1 |
| runs with the parking-slot known limit | 23 | 23 | 0 | 0 |
| open waits without a report | 6 (2 runs) | 0 | -6 [-14.8, +2.8] | 2 |
| `traffic.wait_stuck` | 26,846 | 26,835 | -11 [-28.0, +6.0] | 11 |
| `traffic.deadlock` resolved | 149,004 | 149,042 | +38 [-112.3, +188.3] | 16 |
| `traffic.deadlock` unresolved | 10,530 | 10,667 | +137 [+101.5, +172.5] | 102 |
| orders completed | 149,177 | 149,166 | -11 [-24.4, +2.4] | 7 |
| runs whose fingerprint changed | | | | 122 |

By range: the safety ranges 100000..100999 and 2000000..2000999 (6,000 reservation runs) have 55 fingerprints changed,
orders completed down in 3 runs and up in none (net -7), and the withdrawn-deadlock known limit gone (2000228); the
reviewer's variant gave 52 to 55, 0 up and 3 down, net -7. Seeds 52000..53999: 67 fingerprints changed, the violation of
52118 gone, orders completed up in 2 runs and down in 2 (net -4). The added unresolved reports are sets reported again
after a member left (each is a new set); in 89 of the 122 changed runs the only count that differs is that of the
unresolved reports, and in 12 no count differs (a report or a resolution comes earlier: in 100460 `congestion` the same
set is reported unresolved at 168.2 instead of 174.6; in 52530 `congestion` the same step-aside of bot-z is staged at
the withdrawal instead of at the next wait of the same instant, and the run then diverges). Counted by a wrapper of
`_detect_again` on the head (safety ranges / seeds 52000..53999): called in 164 / 202 runs, a deadlock still there in
78 / 84, a resolution staged in 13 / 17, a new set reported unresolved in 50 / 53. Free-flow runs: 12,000 of 12,000
`RunRecord`s identical (sha256 of events, initial and final state, time, violations, known limits and fingerprint).
The stdout of seven transport and layout examples (`building_an_agv_system`, `intralogistics_simple`,
`_intermediate`, `_advanced`, `_traffic`, `layout_intralogistics`, `layout_stages`) is identical at both commits, apart
from the tree path in matplotlib's warnings. No pinned digest, fingerprint or fixture moved (the six fingerprints and
five safety pins of `test_random_worlds.py`, `tests/fixtures/traces/generated/`, `test_examples.py`,
`test_traffic_scenarios.py`: full suite green, nothing re-pinned).

**Safety run at `5b3bb91`** (CPython 3.14.3, Apple M4 Pro, 10 worker processes, from the repository root):

```
worlds 1000 (seeds 100000..100999), runs 6000, events 6570230
runs that raised 0, runs that did not return within the limit 0, runs that ended with the queue not empty 0, runs with monitor violations 0, runs with a known limit 6
wall-clock limit 120 s per run, 180 s
known limits: kind: cases, runs
  parking slot kept while charging on the way to park: 6, 6
```

```
worlds 1000 (seeds 2000000..2000999), runs 6000, events 6720024
runs that raised 0, runs that did not return within the limit 0, runs that ended with the queue not empty 0, runs with monitor violations 0, runs with a known limit 8
wall-clock limit 120 s per run, 184 s
known limits: kind: cases, runs
  parking slot kept while charging on the way to park: 8, 8
```

Exit status 0 for each. The parking-slot cases are the 14 recorded under "Safety run" (the same seeds and routers).

**Mutant check** (the reviewer's scripts of `reviewer-scratch-t11/`, run on `5b3bb91` with the exemption gone): seeds
2000150..2000449 and 500000..500299, `ReservationController`, three routers, 900 runs per range.

| Mutant of `ReservationController` | Runs with `open wait with no report` (2000150.. / 500000..) | Total of 1,800 | At `ab4d430` |
|---|---|---|---|
| no mutant | 0 / 0 | 0 | 0 |
| after a member's withdrawal the controller neither detects nor reports stuck | 21 / 12 | 33 | 33 |
| every stuck report suppressed | 307 / 328 | 635 | 635 |
| every review timer dropped | 174 / 191 | 365 | 364 |

No run of any mutant ended with the withdrawn-deadlock limit, which no longer exists; the parking-slot limit appears as
in the unmutated runs (5 / 1 runs; 4 / 1 for the timer mutant). The monitor still catches a controller that goes blind.

**Spec.** The traffic spec says detection runs when a wait opens (§7.8, §15); the wording to amend is given to the
controller in the task report, not changed here. `docs/` and `CHANGELOG.md` are unchanged: 0.17.0 had no
`ReservationController` and no deadlock detection of this kind, so nothing here is visible to a user of 0.17.0.

**Fix round 1 (the second half).** The review of B1 found one more shape of the defect at `687ad66`, also present at
the base: seed 8103801, `ReservationController`, `FastestRouter`, ends with `open wait with no report ['agv-b']` at
975.7. At 844.5 agv-b's wait opens into the unresolved set `{agv-a, agv-b}`: no `on_blocked` call, no review; its claim
is also blocked by agv-e, idle since 834.4. At 873.8 agv-a's own review reroutes it: the set is forgotten, and the
re-detection finds no deadlock, since agv-b alone is none. agv-a's release scans, but the wake-up only wakes sleeping
reviews and agv-b has none; `idle(agv-e)` came before agv-b's wait opened; the stuck chain from agv-g (reported at 932.3)
runs only to the waits behind it. Nothing ever looks at agv-b again. The first half covered a remainder that is still a
deadlock; a member whose wait opened into the set and that is in no deadlock afterwards was left with nothing.

Change (`c6cb563`): a wait that opens into a set left unresolved is remembered in the order the waits open. After the
members left are looked at again (a withdrawal or a staged `on_blocked` reaction of a member, after its detection; and
after the detection of every stop, where a resolution can forget a set), each such wait that is still open, that no
remembered set holds and that had no review gets what `stopped()` gives a wait outside a deadlock, from that instant:
the `on_blocked` call and, when it keeps waiting with a delay, its first review that delay later, with the change count
taken at that instant (as `stopped()` takes it at the opening). They are taken in the order their waits opened (the
order of the `stopped()` calls, not the queue order). A wait that ended or was reviewed meanwhile (a stranded blocker
reviews the waits it blocks) gets nothing more; a timer of a wait granted later in the same instant goes with its wait
(T23). The `_reduce` guard of the first half is removed: detection after a member's end is the whole of §7.8, and the
guard changed nothing on any measured run. A grant cannot end a member's wait while its set stands; the code relies on
that and says so. The monitor test that ran the no-scan mutant on seed 2000156 `fastest` now runs it on seed 2000255
`shortest`: with the second half that run takes another course and leaves no unblocked wait.
`docs/guides/intralogistics.md` and `CHANGELOG.md` 0.18.0 now say when an unresolved deadlock is looked at again
(`ac4d837`).

Measurement, base `6ff842f` against `c6cb563` (same scripts, each tree its own monitor), on seeds 100000..100999,
2000000..2000999, 52000..53999 and 8100000..8103999: 8,000 worlds, 48,000 runs, 24,000 reservation runs paired by (seed,
router), 95 % t interval of the sum of the paired differences:

| Measure | `6ff842f` | `c6cb563` | Difference, 95 % interval | Runs that differ |
|---|---|---|---|---|
| runs that returned | 24,000 | 24,000 | 0 | 0 |
| runs with `open wait with no report` | 2 | 0 | -2 [-4.8, +0.8] | 2 |
| runs with another violation | 1 | 1 | 0 | 0 |
| runs with the withdrawn-deadlock known limit | 3 | 0 | -3 [-6.4, +0.4] | 3 |
| runs with the parking-slot known limit | 40 | 40 | 0 | 0 |
| open waits without a report | 11 in 5 runs | 0 | -11 [-21.6, -0.4] | 5 |
| `traffic.wait_stuck` | 52,897 | 52,903 | +6 [-26.8, +38.8] | 48 |
| `traffic.deadlock` resolved | 293,064 | 293,159 | +95 [-208.2, +398.2] | 39 |
| `traffic.deadlock` unresolved | 20,934 | 21,166 | +232 [+180.1, +283.9] | 198 |
| orders completed | 297,747 | 297,760 | +13 [-17.0, +43.0] | 23 |
| make-way moves | 127,239 | 127,281 | +42 [-23.2, +107.2] | 19 |
| step-aside moves | 186,674 | 186,707 | +33 [-114.8, +180.8] | 35 |
| strandings | 28,335 | 28,337 | +2 [-4.2, +8.2] | 2 |
| end time (sum, s) | 17,527,924.7 | 17,529,099.5 | +1,174.8 [-289.4, +2,639.0] | 104 |
| runs whose fingerprint changed | | | | 302 |

The other violation is `PHYSICAL opposite directions on a segment` in seed 8101904 `fastest` at 471.2, the same at both
commits; it is not this task's and stays. The withdrawn-deadlock cases at the base are 2000228 `fastest` and 8100850
`congestion` and `fastest`; the open waits without a report, those three and 52118 `shortest` and 8103801 `fastest`.
In 51 runs only the end time changed: a review of a former member fires near the end, changes nothing and sleeps.
Orders completed fall in 9 runs and rise in 14. Against `687ad66` (the first half; its code is that of `5b3bb91`),
completed orders are +13 [+0.5, +25.5] over the same 24,000 runs, 61 fingerprints change, and one run loses an order:
8103668 `congestion` (-1). There bot-x's wait opened at 45.2 into `{agv-f, bot-x}`; agv-f's wait is withdrawn at 66.0. At
`687ad66` bot-x is reported stuck at once (agv-f idle) and waits until 185.4, when agv-f leaves on a mission. With the
second half bot-x gets its review: at 96.0 the review asks agv-f, idle, to make way (§7.10), agv-f moves and bot-x goes
on 90 s earlier; the run takes another course and ends at 1,111.0 instead of 1,262.2 with one order fewer. Free flow:
24,000 of 24,000 `RunRecord`s identical to the base and to `687ad66`. The stdout of the seven transport and layout
examples is identical to the base. `PYTHONHASHSEED` 0, 1 and 7: seeds 100000..100299, 1,800 runs, every record
identical. No pin moved.

Safety run at `ac4d837` (same source as `c6cb563`), 10 workers: seeds 100000..100999, 6,000 runs, 6,570,200 events;
seeds 2000000..2000999, 6,000 runs, 6,720,030 events; in each 0 raised, 0 not returned, 0 ended with the queue not
empty, 0 with violations; the parking-slot limit in 6 and 8 runs (the 14 recorded cases); exit status 0 and 0.

Mutant check at `c6cb563` (runs with `open wait with no report`, 2000150.. / 500000..): no mutant 0 / 0; blind after a
member's withdrawal 21 / 12; every stuck report suppressed 307 / 328; every review timer dropped 174 / 191: the same
counts as at `5b3bb91`. No other violation; the parking-slot limit as in the unmutated runs.

These statements hold for the seeds run: 8,000 worlds here, the recorded safety ranges and the mutant ranges.

**Fix round 2.** No source change. Tests: the orphan review at a stop is pinned by
`test_a_former_member_left_by_a_resolution_at_a_later_stop_gets_on_blocked_before_the_stopping_wait` (`{A, B}`
unresolved, X stops behind B and the policy now reroutes A; B gets its `on_blocked` call and a review, in the order A,
B, X; the test fails without the call in `stopped()` and with it moved after the stopping wait's own call), and the
order "detection, then the waits left" in a member's withdrawal by an `on_blocked` assertion in
`test_what_is_left_of_a_set_when_a_members_wait_is_withdrawn_is_examined_again` (it fails with the two swapped). The
guide's clause on members with no review names the exception of a blocker that strands, and the changelog line is
wrapped.

Where the statements of B1 hold. "No open wait without a report" holds for the runs measured or run here: seeds
100000..100999, 2000000..2000999, 52000..53999 and 8100000..8103999 under the three routers (the paired measurement,
at `c6cb563`); the safety ranges 100000..100999 and 2000000..2000999 under both controllers (at `ac4d837`); and the
unmutated controls of 2000150..2000449 and 500000..500299. The review of B1 ran 7700000..7702999 too (18,000 runs) and
found one run that is not this defect, below; it does not extend to other seeds. The monitor's rule counts a wait
queued behind a reported wait as explained, without a report of its own (see "A sleeping wait queued behind a reported
one" under "What the monitor does not check"): this is why seeds 100281, 8102359 and 8103231, listed there, lie inside
ranges for which no open wait without a report is stated.

Found beyond this task (open, not changed here, for the maintainer): seed 7702388, `ReservationController`,
`ShortestDistanceRouter`, ends with `open wait with no report ['agv-e']` at 586.206, identical at `6ff842f`, `687ad66`
and `5b00d9f` (1,258 events, fingerprint `8a40c7e16f04`; reproduced here at the three commits). No remembered set is
involved. The reviewer's cause, which I confirmed with a probe of `_detect` (`scratch-a2-B1/probe7702388.py`): agv-e's
review at 522.235 finds blockers agv-g (stranded) and agv-b (waiting, a gate on `corridor:c01`), is not stuck, and goes
to sleep (no change since 492.2). At 522.883 agv-d stops; its detection marks agv-b's claim yielding (the gate-waiver
pass of `_detect`, `reservation.py:731` at `5b00d9f`): agv-e's blockers become agv-g alone, stranded, while the change
count stays at 379, since a waiver is neither a grant nor a release. The sleeping review is therefore never woken. agv-e
is now stuck by the rule of §7.9 and is never reported; agv-c and agv-d, behind it, are reported at their own reviews
(529.5, 552.9), and that chain does not reach forward to agv-e. A one-line scratch prototype of the reviewer, which
counts the waiver as a change, clears the run; it was not measured further. Frequency: 1 run in the 18,000 of seeds
7700000..7702999, none in the other 48,000 runs of the review.

### B2. Parking slots during a charge elsewhere

Decided by the maintainer on 2026-11-24: fix the first known limit before 0.18.0 is tagged and delete the monitor's
exemption for it. Commits `61e5b27` (`fix(intralogistics): a parking slot is released when the trip to it ends
elsewhere`) and `4bc6624` (`test(intralogistics): the monitor no longer exempts a parking slot kept during a charge`),
base `f6171ac`.

**Cause.** `FleetCoordinator._take_slot` takes the slot when an idle AGV is sent to a parking node (a repositioning, a
service-node clearance or a make-way move), and `_idle_travel` gives it back when the travel returns without arriving.
A travel whose next arc the battery cannot cover calls `_lacking_energy` (`travel.py`), which runs
`FleetCoordinator._charge_agv` inside the same travel: a travel to the charger and the charge, or the charge at once
when the AGV stands on a charger's node. Only afterwards does the travel return, with `RETRY_FROM_CURRENT_POSITION`,
and only then was the slot given back. Traced at the base: seed 619 (`FreeFlowController`, `ShortestDistanceRouter`),
agv-a enters P0 at 97.0, turns to the charger c08 at 122.0, charges from 148.6 to 220.1 and leaves P0 at 220.1; seed
100023 (`ReservationController`, `ShortestDistanceRouter`), agv-b enters P2 at 70.6, steps aside five times on the
way, charges at the charger on whose node it stands from 201.9 to 220.1 and leaves P2 at 220.1; seed 2000345
(`ReservationController`, `FastestRouter`), agv-f enters P0 at 66.6, charges at the charger on its route from 157.3 to
199.2 and leaves P0 at 199.2. 0.17.0 took no parking slots (its fleet never called `enter` or `leave`).

**What happens after the charge**, before and after the fix alike: `_lacking_energy` returns
`RETRY_FROM_CURRENT_POSITION` and no caller of `_idle_travel` retries. After a repositioning (`_run_mission`) the AGV
becomes `IDLE` where it charged; the repositioning policy is not asked again and the service node is not cleared
(clearing runs only when the policy gave no target), so it rests on the charger's node until it is dispatched or asked
to make way. A make-way move and a clearance end the same way. The trip to the parking area is never resumed: it ends
where the AGV turns to charge. In the three seeds the AGV becomes `IDLE` on the charger's node as the charge ends and
is dispatched at once to a pending order.

**Change.** `_charge_agv` gives back, before anything else of the charge (the `on_battery_low` hooks, the
`on_low_battery` callback, the travel to the charger), the slots the AGV holds in parking areas away from its own node
(`_release_slots_elsewhere`), with `parking.left`. Those are exactly the slots of a trip to park that turns to charge: a
mission takes no slot, and a slot of the node the AGV stands on (a parked AGV that leaves to charge) still goes when it
departs, as before. When an AGV charges, which charger, how long, when it strands, parking selection, repositioning,
make-way and the clearing of service nodes are unchanged; so is everything an AGV does after the charge. In seed 619
agv-a now leaves P0 at 122.0, just before it turns, and no other event of the run changes.

**Tests.** In `tests/intralogistics/test_idle_traffic.py`: a trip to park on FAR (one slot) that cannot afford its last
arc turns to a charger off its route, or charges at one on it, under both controllers: the slot goes back at the turn,
before the turn's state change, B takes it with a make-way move during the charge, and A ends idle where it charged
with no slot (`test_a_trip_to_park_that_charges_on_the_way_gives_the_slot_back_as_it_turns_to_charge`, four cases); the
same trip interrupted during its charge gave the slot back once, at the turn
(`test_a_trip_to_park_interrupted_during_its_charge_gave_the_slot_back_as_it_turned`). These five fail at the base
(`5 failed, 2 passed`). Pinned, passing at the base too: a trip to park that strands with no reachable charger gives
the slot back as it strands, and a parked AGV whose next mission turns to charge from the parking node gives it back as
it departs. Interrupted trips to park were already pinned (`test_an_interrupted_idle_move_...`). In
`tests/intralogistics/test_random_worlds.py` the pin of the known limit on seed 619 became
`test_a_trip_to_park_that_turns_to_charge_gives_its_parking_slot_back_as_it_turns` (no violation, the slot left at
122.0 just before the turn); the test with slots never given back is now
`test_a_parking_slot_held_by_an_agv_resting_elsewhere_is_a_violation`.

**Monitor.** `KNOWN_LIMIT`, `_Slot`, `_track_slots`, `Monitor.finish`, `Monitor.known_limits` and
`RunRecord.known_limits` are removed, with the known-limit lines of `benchmarks/layout_safety.py` (the exit status was
already decided by failures and violations only). A slot held by an AGV that rests elsewhere is always the violation
`parking slot of an AGV resting elsewhere`.

**Measurement.** The base `f6171ac` and the head (the source of `4bc6624`), each a copy first on `PYTHONPATH`, each
with its own monitor, on seeds 100000..100999, 2000000..2000999, 52000..53999 and 8100000..8101999 (6,000 worlds, both
controllers, three routers: 36,000 runs, paired by seed, controller and router; the interval is the 95 % t interval of
the sum of the paired differences). The measures are counted from the events by the measuring script, the same way for
both trees. "Trips to park ended in a charge" counts a `charging.started` of an AGV that holds a slot of an area whose
node it is not on.

| Measure | `f6171ac` | Head | Difference, 95 % interval | Runs that differ |
|---|---|---|---|---|
| runs that returned | 36,000 | 36,000 | 0 | 0 |
| runs with a violation | 1 | 1 | 0 | 0 |
| known-limit cases (parking slot) | 44 | 0 | -44 [-57.0, -31.0] | 44 |
| trips to park ended in a charge, slot still held | 44 | 0 | -44 [-57.0, -31.0] | 44 |
| slot time held during a charge elsewhere | 2,451.8 | 0 | -2,451.8 [-3,314.7, -1,588.9] | 44 |
| `parking.entered` | 210,448 | 210,452 | +4 [+0.1, +7.9] | 4 |
| `parking.left` | 177,156 | 177,155 | -1 [-9.5, +7.5] | 4 |
| `charging.started` | 31,205 | 31,205 | 0 | 0 |
| charging duration (sum) | 3,263,396.8 | 3,263,395.9 | -0.9 [-2.3, +0.5] | 2 |
| strandings (`agv.state_changed` to `STRANDED`) | 46,330 | 46,331 | +1 [-7.1, +9.1] | 3 |
| `traffic.wait_stuck` | 40,123 | 40,122 | -1 [-5.4, +3.4] | 2 |
| `traffic.deadlock` resolved | 222,361 | 222,363 | +2 [-4.2, +8.2] | 2 |
| `traffic.deadlock` unresolved | 15,967 | 15,969 | +2 [-0.8, +4.8] | 2 |
| orders completed | 540,028 | 540,025 | -3 [-6.4, +0.4] | 3 |
| end time (sum) | 25,311,996.4 | 25,311,996.2 | -0.2 [-0.6, +0.2] | 1 |
| runs whose fingerprint changed | | | | 51 |

The one violation is `PHYSICAL opposite directions on a segment` in seed 8101904 `fastest` at 471.2, the same at both
commits (B1; open, not this task's). The known-limit cases were 35 under `ReservationController` and 9 under
`FreeFlowController`; by range 6, 8, 12 and 18; each is one of the 44 trips counted from the events, and each is gone.

Changed runs. Of the 36,000, 51 changed (record and fingerprint), 42 under `ReservationController` and 9 under
`FreeFlowController`; the other 35,949 are identical (sha256 of the events, initial and final state, end time and
fingerprint), among them all 8,604 runs of worlds without parking areas. Each of the 51 has a trip to park that turned
to charge: at the head its first moved `parking.left` comes right before the state change of the turn. 44 of them are
the 44 trips above. The other 7 turned to charge and never charged: the travel to the charger stranded with no reachable
charger (100066 `shortest` and `fastest`, 8101405 `fastest`, 8101818 `congestion`) or stopped critical short of it and
the AGV stranded as "insufficient after charging" (52596 `congestion`, 8100301 `fastest`, 8100713 `fastest`); the base
gave the slot back at the stranding (in four of them at the same instant, later in the stream). In 46 of the 51 runs
only the parking events differ: the time of `parking.left` or its place within the instant, and in 4 of them (52396
`shortest`, 8100264 `shortest` and `fastest`, 8101926 `congestion`) also the place in the area's list of parked AGVs at
which a later `parking.entered` inserts (its delta), with the same final state. In 5 another AGV is sent to the area in
the time the slot was held at the base, which is the effect of the fix: 100800 `congestion` and 52643 `shortest`
complete the same orders; 52050 `FreeFlowController` with `congestion` and `fastest` (one run twice: the two streams are
identical) and 8101889 `congestion` complete one order fewer each, two cases in all. In 52050 agv-f, after its delivery
at 67.9, is sent to P0 on the slot agv-b gave back at 43.5 (at the base agv-b still held it and agv-f stayed idle on the
bay); dispatched from there with order-7 at 97.9, it strands at 100.8 with the order (at the base it completed order-7
and stranded at 119.7, after it). In 8101889 agv-b, parked on P0 and asked to make way at 182.9, moves to P2 on the slot
agv-g gave back at 159.6 instead of to a node of a block, and the run takes another course (5 orders completed instead
of 6). No run completes more orders.

Pins. Of the six fingerprints of `test_random_worlds.py`, seed 619 moved and is pinned again: compared event by event
with the base run, the only difference is its `parking.left` at 122.0 instead of 220.1 (same 394 events otherwise, same
final state and end time). The five safety pins, the other five fingerprints, `tests/fixtures/traces/generated/`,
`test_examples.py` and `test_traffic_scenarios.py` hold unchanged (full suite). The stdout of the seven transport and
layout examples is identical to the base, apart from the tree path in matplotlib's warnings. With `PYTHONHASHSEED` 0, 1
and 7 the 1,800 runs of seeds 100000..100299 give records identical to those of the measurement.

**Safety run at `4bc6624`** (CPython 3.14.3, Apple M4 Pro, 8 worker processes, from the repository root):

```
worlds 1000 (seeds 100000..100999), runs 6000, events 6570200
runs that raised 0, runs that did not return within the limit 0, runs that ended with the queue not empty 0, runs with monitor violations 0
wall-clock limit 120 s per run, 201 s
```

```
worlds 1000 (seeds 2000000..2000999), runs 6000, events 6720030
runs that raised 0, runs that did not return within the limit 0, runs that ended with the queue not empty 0, runs with monitor violations 0
wall-clock limit 120 s per run, 199 s
```

Exit status 0 for each. No violation, and no known limit any more: the 14 runs that had the parking-slot limit at
`ac4d837` are runs without a violation now. The event counts equal those at `ac4d837`.

**Mutant check** (`mutants.py`, adapted from `final-scratch-area2/scripts/mymutants.py`; each mutant applied in every
worker, nothing on disk; seeds 100000..100299 and 2000150..2000449, both controllers, three routers, 1,800 runs per
range; the monitor of `4bc6624`):

| Mutant of the slot bookkeeping | Runs with a violation or an exception (100000.. / 2000150..) | Kinds |
|---|---|---|
| no mutant | 0 / 0 | |
| no slot is ever given back (`ParkingArea.leave` does nothing) | 1,019 / 1,097 | resting elsewhere, held at quiescence |
| a move to park that does not arrive keeps its slot | 210 / 210 | resting elsewhere, held at quiescence; 8 / 16 raise `KeyError` |
| that slot is given back 5 time units late | 141 / 152 | resting elsewhere; 7 / 4 raise `KeyError` |
| a slot is taken whether or not the area has one free (two AGVs on one slot) | 39 / 17 | parking over capacity |
| the fix undone (`_release_slots_elsewhere` does nothing) | 3 / 5 | resting elsewhere |

"Resting elsewhere" is `parking slot of an AGV resting elsewhere`, "held at quiescence" `parking slot held away from the
node at quiescence`; a `KeyError` (a later `leave` of a slot the mutant left in an inconsistent state) is a run that
raised, which the safety runner counts as a failure. With the fix undone, the runs with a violation are exactly the runs
of these ranges that had the known limit at the base (100023 with the three routers; 2000152 `shortest`, 2000345 and
2000447 with `shortest` and `fastest`), all under `ReservationController`: with the exemption gone the monitor reports
the old behaviour as a violation.

**Docs.** `docs/guides/intralogistics.md` loses its known limit "Parking and charging", and its "Parking" paragraph
says that a move that turns to charge gives the slot back as it turns. `CHANGELOG.md` gets no line for this: 0.17.0
had `ParkingArea` but its fleet took no slots, so nothing a user of 0.17.0 saw changes. The traffic spec (§7.10, T13)
says the slot goes back "when the travel does not arrive"; the wording to amend is in the task report, not changed here.

**Fix round 1.** The review of B2 found a corner of the change: when the charger the trip turns to stands on the node of
the parking area the trip goes to, `61e5b27` gave the slot back at the turn, and the AGV then travelled to that node,
charged and rested there without a slot; the area of one slot showed free, a second AGV's make-way move to it was
accepted and both rested on the node (at `f6171ac` the move is refused). The reviewer built it by hand (a trip routed
the long way round, a charger on the parking node). Ruled by the controller: the slot is kept. Since `0d08071`
(`fix(intralogistics): a parking slot is kept when the charge is on the area's own node`) the release at the turn
keeps the slots of the charger's node as well as those of the node the AGV stands on. When the charge on that node ends
the travel returns as before and the AGV is at rest on the parking node with its slot, parked; the slot goes when it
next departs, as for any parked AGV. Test:
`test_a_trip_to_park_that_turns_to_a_charger_on_the_parking_node_keeps_its_slot` (both controllers), built from the
reviewer's case with the `Via` router of the test module: the slot is held throughout, the area never shows free, B's
make-way move to FAR is refused, A rests on FAR parked, and its next departure gives the slot back. It fails at
`881dd91` (`2 failed`, the make-way move accepted). On the 36,000 runs of the measurement above, `0d08071` against
`881dd91`: no run changed, and no turn went to a charger on the node of the trip's area (counted by a wrapper of
`_release_slots_elsewhere`); against `f6171ac` the table above holds unchanged, with no slot held during a charge
elsewhere. Full suite at `0d08071`: `4992 passed, 1 skipped`, the six fingerprints (619 as re-pinned) holding. Safety
run at `0d08071`, 8 workers: seeds 100000..100999, 6,000 runs, 6,570,200 events; seeds 2000000..2000999, 6,000 runs,
6,720,030 events; in each 0 raised, 0 not returned, 0 ended with the queue not empty, 0 with violations; exit status 0
and 0.

Open, pre-existing and not changed here, for the maintainer (counts from the review of B2):

- An AGV that charged on its way to park stays idle on the charger's node. The trip is not resumed and the
  repositioning policy is not asked again; the service node is not cleared, since clearing runs only when the policy
  returned no target (`fleet.py:766`). The review found this in 34 of 37 measured turns; in 16 the AGV stayed there a
  while, in 1 until the end of the run.
- `agv.stranded` with reason `insufficient_after_charging` names the node where the diversion started, not the node
  where the AGV stands (`travel.py:825`), and the reason is also given when the diversion never reached a charger
  (8100301 `fastest`: `node` g2_2 with the AGV on c07). The review counted 35 of 43,393 stranded events.

### C. The band of the core overhead gate

Decided by the maintainer on 2026-11-24: widen the band of the no-subscriber gate against the stripped 0.12.0 (item 8 of
"Residual findings after the fix wave"). The gate stays a gate. Decision D106 in `specs/studio-decisions.md`.

| | Before | After |
|---|---|---|
| `BUDGET_STRIPPED`, CPython (`.github/workflows/ci.yml`) | 0.065 | 0.105 |
| `NOISE_STRIPPED`, CPython | 0.02 | 0.02 |
| Limit against the stripped copy, CPython | +8.5 % | +12.5 % |
| PyPy budget and band, both bands against the released copy, released limit (+5 %) | unchanged | unchanged |

Where the 8.5 % came from (`specs/research/sp1-g3-report.md` §3 and §9, decisions D55 and D56): the budget is "the achieved
worst case plus 2 % headroom" (worst pooled overhead of the two CI workloads over interleaved rounds, 4.5 %, plus 2.0 =
6.5 %) and the noise band is the A/A spread widened (0.8 % observed, 2 %), re-checked with `calibrate_noise.py` in
the pre-0.13 hardening (0.9 and 1.21 pp; CPython kept at 2 %). The rule is applied again as written.

Measurement. Head `7736e5d` (a `git archive`, first on `PYTHONPATH`), released `simulatte==0.12.0` with the dependency
versions of the earlier gate runs as constraints (simpy 4.1.2, msgpack 1.2.4, tabulate 0.10.0, tqdm 4.70.1) and the stripped
copy that `strip_debug.py` made from it (12 statements removed; a second run of the script gives the same tree), CPython
3.14.3, Apple M4 Pro, on AC power. Each round is the gate's step: mode `none`, both CI workloads, `--warmup 1 --repeat 5
--processes 3`, and a second stripped run (S2) for the baseline's own spread; the order of the runs alternates between
rounds, as the table shows. Ten rounds, every round kept. The machine was not idle: one-minute load average 1.4 to 2.2
in rounds 1 to 4 and 8 to 10, and 2.1 to 4.6 in rounds 5 to 7 (a burst of another session during round 5).

| Round | Order of the runs | Load (1 min, after u90 / u95) | u90 vs stripped | u90 vs stripped, second run | u95 vs stripped | u95 vs stripped, second run | vs released (u90 / u95) |
|---|---|---|---|---|---|---|---|
| 1 | base, S2, S, head | 1.73 / 1.40 | +8.6 % | +9.2 % | +7.2 % | +8.1 % | -24.2 % / -23.3 % |
| 2 | head, S, S2, base | 1.61 / 1.98 | +8.0 % | +8.1 % | +8.3 % | +9.4 % | -24.9 % / -22.2 % |
| 3 | base, S2, S, head | 1.87 / 1.92 | +7.0 % | +6.8 % | +8.9 % | +8.6 % | -24.4 % / -23.0 % |
| 4 | head, S, S2, base | 1.75 / 2.04 | +9.3 % | +9.4 % | +7.0 % | +7.7 % | -24.3 % / -22.8 % |
| 5 | base, S2, S, head | 4.52 / 4.55 | +5.9 % | +9.5 % | +9.2 % | +8.7 % | -23.6 % / -22.6 % |
| 6 | head, S, S2, base | 3.55 / 2.80 | +7.4 % | +8.8 % | +8.9 % | +8.5 % | -24.4 % / -22.3 % |
| 7 | base, S2, S, head | 3.44 / 2.82 | +8.9 % | +8.5 % | +8.6 % | +9.1 % | -24.2 % / -22.6 % |
| 8 | head, S, S2, base | 2.85 / 2.42 | +9.8 % | +8.9 % | +7.8 % | +8.2 % | -24.2 % / -22.7 % |
| 9 | base, S2, S, head | 2.06 / 2.13 | +8.5 % | +9.2 % | +7.8 % | +8.5 % | -24.3 % / -22.7 % |
| 10 | head, S, S2, base | 2.21 / 1.90 | +8.3 % | +8.7 % | +9.3 % | +7.5 % | -24.6 % / -22.9 % |

Per round, head against the stripped run of the gate (S), over the ten rounds: u90 +5.9 to +9.8 % (median +8.4 %), u95
+7.0 to +9.3 % (median +8.5 %). Against S2: u90 +6.8 to +9.5 %, u95 +7.5 to +9.4 %. S2 against S: -3.3 to +0.9 % (u90;
the -3.3 % is round 5, where one of the three S processes ran at 0.525 s while the other two ran at 0.459 and 0.465 s) and
-0.9 to +1.7 % (u95). Head against released 0.12.0: -24.9 to -23.6 % and -23.3 to -22.2 %.

Pooled over the ten rounds (median of the 150 timed runs of each side and workload; the way §9 pools), head against S:
u90 0.4942 s / 0.4562 s = +8.34 %, u95 0.5315 s / 0.4901 s = +8.45 %. Worst case 8.45 % (u95).

Rule and arithmetic:

- Budget = worst pooled overhead + 2.0 points of headroom = 8.45 + 2.0 = 10.45 %. The rule states no rounding: 10.45 is written 10.5 as §9 wrote 6.5 for 6.54, and rounding to half a point gives the same.
- Noise band, CPython: kept at 2 %. The largest single-round excess of a comparison over its workload's pooled
  value is 9.81 - 8.34 = 1.47 points (u90, round 8; u95: 9.33 - 8.45 = 0.88), and S2 against S exceeds +2 % in none of
  the 20 pairs. `calibrate_noise.py out/r*` (seed 20261010, 10,000 draws, three process medians per side) gives an upper
  bound of the change of the head/stripped ratio of 2 pp or less in 15 of 20 rows (mean 1.5 pp); the five above: 5.20 pp (u90,
  round 5, the loaded round with the slow S process; the same row has a lower bound of -12.51 pp), 2.58 pp (u95, rounds 1 and
  3), 2.04 pp (u90, round 9) and 2.02 pp (u95, round 8). Ruling of the controller, 2026-11-24: the band stays 2 %. Read as
  the pre-0.13 calibration note reads it (the band covers the largest upward bound, rounded up) it would be 3 % with the
  loaded round set aside (largest 2.58), so a limit of 10.5 + 3 = 13.5 %, or 6 % with every round kept (5.20), so
  10.5 + 6 = 16.5 %. It stays 2 % because the budget already rose by the measured worst case under the procedure's own
  rule; because the same reading would move the band against the released package, which the decision of 2026-11-24 does
  not cover; and because 12.5 % covers every CI run on record (largest +10.4 %). The alternatives stay on record for the
  maintainer to revisit.
- Limit = 10.5 + 2.0 = 12.5 %. The limit is 4.05 points above the pooled worst case, as the old one (8.5) was 4.0 above
  its worst case (4.5), and 2.7 points above the highest single comparison (+9.8 %).
- For comparison, the fallback rule of the assignment (measured maximum + 1.5, up to half a point) would give 9.81 + 1.5 =
  11.31, so 11.5 %. It was not used because the procedure has its own rule.

Gate at the head with the new limit: the head passes against the released copy (limit +5 %) in 20 of 20
comparisons and against the stripped copy in 20 of 20 comparisons against S and 20 of 20 against S2; 10 of 10 whole
gates pass (both workloads, both baselines). At the old limit of +8.5 % the same rounds give 11 of 20 comparisons against S
(8 of 20 against S2) and 2 of 10 whole gates.

The gate still fails a real regression. `compare.py` on a pair whose baseline is the S result of round 1 (u90) and whose head
median is that baseline times 1.124, 1.126 and 1.100 (`--budget 0.105 --noise 0.02`): +12.4 %, limit `+12.5 % (10.5 % + 2.0 %)`,
pass, exit 0; +12.6 %, `**FAIL**`, exit 1, "mode none overhead +12.6% exceeds the budget 10.5% + noise 2.0%"; +10.0 %, pass,
exit 0. The same three pairs at the old budget (0.065) all exit 1.

CI history, if the new limit had applied (limits 12.5 % against the stripped copy; nothing was changed for it): the
runs of the brief pass: `e993ca8` +6.8 % and +7.7 %, `9cc5d64` +8.3 %, and the failed run on `main` (`ea1292c`, +9.6 % and
+10.4 %), which was a failure of noise on a commit that touched no source, would have passed with 2.1 and 2.9 points of margin; so would
the runner results quoted in the 0.13 release checks (+6.35 % and +8.17 %, and +7.67 % and +8.41 % on the first main run).
No run in the history exceeds 12.5 %. A rise of the branch's cost against the stripped copy from the pooled 8.45 % to more than 12.5 % (4.05 points) fails the gate; a smaller one passes.

Two facts of the measurement. (1) The measured cost depends on the session with identical source: a reviewer's eight rounds
at the same commit (`7736e5d`, same `src`; raw files in `reviewer-scratch-a2C/out`, recomputed here: 120 samples per side and
workload) gave pooled +7.19 % (u90, 0.4900 s / 0.4571 s) and +6.70 % (u95, 0.5247 s / 0.4917 s), every comparison passing at
8.5 % too (single rounds +6.64 to +7.72 % and +6.23 to +6.89 %), against the +8.34 % and +8.45 % above. (2) What the new
limit leaves undetected: about 4 points of overhead above today's pooled worst case (1.125 over 1.0845 is +3.7 % of run
time), which the released-package limit does not catch either, as the branch is 23 to 25 % faster than released 0.12.0.

### Open after Assignment 2, for the maintainer

Six things are not fixed, and (g) lists smaller points that were left. Each is described where the record has its
evidence; the counts below are quoted from there unless the line says that it was run for this list. Two of them end a
run of the random worlds with a violation (the first two); the others leave no violation. Nothing here changes the gates
at the final head: the recorded ranges are clean.

**(a) A stuck wait not reported after a gate waiver (seed 7702388).** **Fixed** (Assignment 3, T4): a wait with no
review left is checked for the stuck condition at every controller entry that is not a grant or a release, the stop
that waives a gate included; see "T4. Every stuck wait is reported". The run now ends without a violation.
`ReservationController`, `ShortestDistanceRouter`.
Reproduction, run for this list at `229ea61`: `uv run --all-extras python benchmarks/layout_safety.py --worlds 1 --first
7702388` gives 6 runs, 5,475 events, one violation, `seed 7702388 reservation shortest: (586.206, 'open wait with no
report', ['agv-e'])`, exit status 1. The mechanism and the evidence are under B1, "Found beyond this task" (Ruling
A2-h): agv-e's review at 522.235 finds a stranded blocker and a waiting one and goes to sleep; at 522.883 the detection
of another stop marks the waiting blocker's claim as yielding (the gate-waiver pass of §7.8), which removes it from
agv-e's blockers without a grant or a release, so the change count does not move and the sleeping review is never woken;
agv-e is then stuck by the rule of §7.9 and nobody reports it. A scratch prototype of the reviewer that counts the
waiver as a change clears the run; it was not measured further. Frequency, from B1: 1 run in the 9,000 reservation runs
of seeds 7700000..7702999 and 1 in the 33,000 reservation runs of that review in all (free-flow runs cannot show it), on
the same basis as item (b). It is residual item 9. Fixing it changes when sleeping reviews wake, a rule of the
wait-review code that Assignment 2 left alone.

**(b) A physical conflict from a direction re-claim through look-ahead (seed 8101904).** **Fixed** (Assignment 3,
T3, `adfdd30`): a segment claimed again in a new direction is held in both directions until the body has left it; see
"T3. No admission against a body after a direction re-claim". The run now ends without a violation.
`ReservationController`, `FastestRouter`. Reproduction, run for this list at `229ea61`: `uv run --all-extras python
benchmarks/layout_safety.py --worlds 1 --first 8101904` gives 6 runs, 7,018 events, one violation, `seed 8101904
reservation fastest: (471.171, 'PHYSICAL opposite directions on a segment', 'g0_2~g1_0', [('agv-c', ('g0_2',
'g1_0')), ('agv-g', ('g1_0', 'g0_2'))])`, exit status 1. It is the violation that B1 and B2 name as "not this
task's". The facts below are from the read-only investigation made at `687ad66` (Ruling A2-g; its scripts and outputs
are in `.superpowers/sdd/2026-11-23-layout-only-models/reviewer-scratch-a2-8101904/`); what I confirmed again for this
list is said as such.

- Mechanism. A vehicle at rest whose tail still lies on a single-lane segment claims that segment again in the new
  direction through look-ahead (traffic spec §7.2: the grant changes the recorded direction; §7.3: a vehicle's own
  holdings never block it). The ledger then shows one direction, and a second vehicle is admitted head-on into the lane
  that the first one's tail still occupies. In seed 8101904 agv-c reverses over `g0_2~g1_0` and re-claims it in the new
  direction and agv-g is granted the same segment in the opposite direction at the same instant (471.171); the bodies
  overlap for 1.118 s (investigation). It needs a reversal, a speed profile with limits, an extent longer than the path
  from the roomy node to the rest node, both end nodes of capacity above 1, and room for both extents.
- Present at `e993ca8`, the base of the branch (the traffic work). Confirmed for this list: the `src` of `e993ca8` (a
  `git archive`) first on `PYTHONPATH`, with the generator and monitor of `e4afae4` (the last with explicit mode, which
  `e993ca8` needs; `reviewer-scratch-a2-8101904/gen-e4`) and the investigation's `scripts/base_run.py 8101904 fastest`:
  one violation, `PHYSICAL opposite directions on a segment` at 471.1711922045716 on `g0_2~g1_0` with agv-c and agv-g,
  1,429 events. The violation is not caused by the layout work.
- Frequency, from the investigation's scans (`out/scan-8100000.jsonl`, `out/scan-9200000.jsonl`; recounted for this list
  from those files): 21,000 reservation runs of 7,000 worlds (seeds 8100000..8103999 and 9200000..9202999, three
  routers) contain this violation once (8101904 `fastest`). The exposure is common: the investigation counted 113,330
  re-claims of a segment in a new direction with the body still as it arrived, of which one let a second vehicle in.
- Minimal scenario, `reviewer-scratch-a2-8101904/scripts/minimal.py` (a line U-V-W, U~V 10 m single lane, V~W 2.5 m, two
  vehicles of 3 m with a trapezoidal profile; A drives U to V to W and back, B rests at V and asks for V to U at the
  same instant). Run for this list with `PYTHONPATH=. uv run --all-extras python <path>/minimal.py base` at `229ea61`:
  its independent body model reports `CONFLICT: bodies on U~V in opposite directions`, 22 samples from 50.05 to 51.1,
  and both vehicles arrive. Its variants (`later`, `turn`, `plain`, `deadend`, `cap1`, `fit`, `short`) show which
  conditions the case needs; they were not rerun for this list.
- The smallest fix that was stated, and not made (a change to traffic spec §7.3, and possibly to entity state): a holder
  that re-claimed a single-lane segment in a new direction counts in both directions for other claimants until its body
  has left the segment as it arrived (the investigation's `exact` prototype, `scripts/fixproto.py`: until the holder's
  own move over the segment starts, or its odometer reaches the point where the segment leaves its trail). Measured by
  the investigation and recounted for this list from its outputs: on 3,000 runs (seeds 9200000..9200999) 2 fingerprints
  change and no new violation appears; on seed 8101904 the violation is gone; its 1,753 intralogistics tests passing is
  quoted from the investigation. The conservative form (`excl`: the holder blocks every other claimant until it releases
  the segment) changes 124 of the same 3,000 fingerprints, with no violation either.
- Whether 0.18.0 ships with it is the maintainer's decision: it is a physical overlap of two bodies, in a world that
  needs the combination above, at about 1 run in 21,000 reservation runs of these random worlds.

**(c) Waits that sleep behind a reported wait and get no report of their own.** **Fixed** (Assignment 3, T4, by the
same check; the strict monitor of T2 no longer explains such a wait): see "T4. Every stuck wait is reported"; the eight
runs end without a violation. At the end of a run the monitor explains
an open wait that is blocked by a wait reported stuck or unresolved, without asking that the wait itself was reported;
§7.9's stuck chain reaches a wait behind a reported one only at the instant of that report. Eight runs at `5b00d9f` end
with such a wait, asleep and with no report: 8102359 `congestion`, 8103231 `shortest`, 100281 `shortest` and `fastest`,
7700058 with three routers, 7701490 `fastest` (listed under "What the monitor does not check"; the counts and the agvs
are quoted from there and were not rerun at the final head). The record describes it under "What the monitor does not
check" and under B1 ("Where the statements of B1 hold").

**(d) An AGV that charged on its way to park stays idle on the charger's node.** **Fixed** (Assignment 3, T5,
`186d4ab`): after such a charge the AGV is asked where to rest as an AGV that ends a mission on the charger's node;
see "T5. Two fleet items". Pre-existing, identical before and after B2. The trip to the parking area is not resumed after the charge, the repositioning policy is not asked again and
a service node is not cleared (clearing runs only when the policy returned no target, `fleet.py:766` at the time). B2's
review found it in 34 of 37 measured turns, 16 of them for a while (up to 170.5), 1 to the end of the run (quoted from
B2, "Open, pre-existing and not changed here"; not rerun). It matches the letter of traffic spec §7.10 and is at odds
with its purpose. The one exception that B2's fix round added, a charger on the area's own node, leaves the AGV parked
there with its slot.

**(e) `agv.stranded` after a diversion names the node where the diversion started.** **Fixed** (Assignment 3, T5,
`d938852`): the event names the node where the AGV stands, with `insufficient_after_charging` only after a charge and
the new reason `charger_not_reached` otherwise; see "T5. Two fleet items". With reason
`insufficient_after_charging` the event names the node where the AGV turned to charge, not the node where it stands
(`travel.py:825` at the time of B2), and the reason is also given when the diversion never reached a charger (8100301
`fastest`: `node` g2_2 with the AGV on c07). 35 of 43,393 stranded events in B2's measurement (quoted; not rerun).

**(f) The noise band of the overhead gate.** C widened the budget against the stripped copy to 10.5 % and kept the band
at 2 %, so the limit is 12.5 %. `calibrate_noise.py` gave an upward bound above 2 points in 5 of 20 rows (largest 5.20
for the loaded round, 2.58 without it); a literal application of the pre-0.13 calibration note would give a band of 3 %
(limit 13.5 %) or 6 % (limit 16.5 %), and by the same logic would move the band against the released package. The
alternatives are recorded in D106 (`specs/studio-decisions.md`) and in section C above (Ruling A2-c). A regression of
less than about 4 points of overhead passes unnoticed until the next calibration; a slow runner above 12.5 % fails the
job and needs a rerun. The overhead gate at the final head is below the limit in every comparison, with at least 3.5
points to spare against the stripped copy (below).

**(g) Smaller points recorded and left.**

- `src/simulatte/entities.py` (about line 357) reaches the private `refs._Ref` through `sys.modules` to avoid importing
  the layout, and an uncaught traceback of a handle read names `_PhysicalAttributeError` (A review, minor).
- The hand-built parking tests of B2 cover a repositioning trip only; make-way and clearance trips that turn to charge
  are covered by the random worlds. `tests/intralogistics/test_idle_traffic.py` pins the idle-after-charge behaviour of
  item (d), so that test changes if (d) is fixed (B2 review, minor). Brought up to date in T5: the pinned test now
  asserts the decision after the charge (cleared off the charger's node under reservation, kept there under free flow
  with the area full), and hand-built tests cover a repositioning, a clearance and a make-way move that turn to charge.

### Gates at the final head (Assignment 2)

Run on bigboss (Apple M4 Pro, macOS, CPython 3.14.3 unless the row says otherwise) at `229ea61`, the last commit that
changes anything but this record: `229ea61` (`docs(intralogistics): the parking slot kept for a charge on the area's own
node`) changes four texts, the parking paragraph of `docs/guides/intralogistics.md` and the docstrings of
`FleetCoordinator`, `FleetCoordinator._idle_travel` and `ParkingArea`, so that they state the exception of B2's fix
round 1 (a charger on the area's own node keeps the slot). Nothing was edited between the gates. I checked `git status
--short` (empty) before the first gate and after the PyPy and 3.11 lanes, but those outputs were not saved; what is kept
is a check made after the gates (file `scratch-a2-final/post-check.out`): the only change since `229ea61` is this
record, and `uv.lock` and `pyproject.toml` are unchanged since `4ab6572`. That the commit changes no behaviour was
checked by compiling `fleet.py` and `parking.py` of `4ab6572` and of `229ea61` with `optimize=2` (docstrings stripped)
and comparing every code object's `co_code` and constants (without docstrings): 146 and 21 code objects, identical
(`scratch-a2-final/cmp.py`, `cmp.out`). Result lines are pasted from the output; the files are under
`.superpowers/sdd/2026-11-23-layout-only-models/scratch-a2-final/`.

| # | Gate | Command | Result |
|---|---|---|---|
| 1 | Tests and coverage | `uv run --all-extras pytest` | `Required test coverage of 99% reached. Total coverage: 99.19%`; `4992 passed, 1 skipped, 3 warnings in 445.81s (0:07:25)` |
| 2 | Lint | `uv run --all-extras ruff check src tests benchmarks` | `All checks passed!` |
| 3 | Format | `uv run --all-extras ruff format --check` | `532 files already formatted` |
| 4 | Types | `uv run --all-extras ty check src` | `All checks passed!` |
| 5 | Docs | `uv run --all-extras zensical build` | `No issues found`; `Build finished in 1.52s` |
| 6 | Studio | in `studio/`: `pnpm typecheck && pnpm test && pnpm build && pnpm test:browser`, run as four commands | `pnpm typecheck` exit 0; `pnpm test` exit 0 (trace `329 passed`, playback `24`, scene `25`, export `15`, viewer `82`); `pnpm build` `built in 3.39s`, exit 0; `pnpm test:browser` `36 passed (1.2m)`, exit 0 |
| 7 | PyPy lane | `MPLBACKEND=Agg UV_PROJECT_ENVIRONMENT=<scratch>/pypy-venv uv run --python pypy-3.11 pytest tests/core tests/execution tests/intralogistics tests/layout -o addopts="-q -p no:cacheprovider"` after `UV_PROJECT_ENVIRONMENT=<scratch>/pypy-venv uv sync --dev --python pypy-3.11 --frozen` (`Checked 61 packages`) | `4861 passed, 23 skipped in 588.35s (0:09:48)` (PyPy 7.3.22, Python 3.11.15) |
| 8 | CPython 3.11 | `UV_PROJECT_ENVIRONMENT=<scratch>/py311-venv uv run --python 3.11 --all-extras pytest tests/intralogistics tests/layout --no-cov -q` | `2649 passed in 128.33s (0:02:08)` (Python 3.11.13) |
| 9 | Overhead gate | the `bench` job's step, mode `none`, `--warmup 1 --repeat 5 --processes 3`, both CI workloads, three rounds | pass in all 12 comparisons of three rounds (table below) |
| 10 | Safety run | `uv run --all-extras python benchmarks/layout_safety.py --worlds 1000 --first 100000` and `--first 2000000` | both exit 0, 0 violations, 0 raised, 0 not returned (below) |
| 11 | Seeds of Assignment 2 | `benchmarks/layout_safety.py --worlds 1 --first <seed>` | 6 clean, 2 open by decision (below) |

`uv.lock` and the checkout's `.venv` were not changed by the interpreter lanes (`uv.lock` equals the one committed: `git
diff HEAD -- uv.lock` is empty, `scratch-a2-final/post-check.out`). The coverage run's `coverage.xml` is ignored by git.

**Overhead gate (gate 9).** Run as `benchmarks/README.md` describes and the `bench` job does: mode `none` on
`jobshop10-u90-5k` and `jobshop10-u95-5k`, `--warmup 1 --repeat 5 --processes 3`; the limits are those now in
`.github/workflows/ci.yml`: against released 0.12.0 `--budget 0.03 --noise 0.02` (+5.0 %), against the stripped copy
`--budget 0.105 --noise 0.02` (+12.5 %). The branch side is the checkout's `.venv` (`simulatte.__file__` printed:
`/Users/davide/Developer/simulatte/src/simulatte/__init__.py`, CPython 3.14.3, the `src` of `229ea61`); released 0.12.0
in the environment built by Task 11 (`scratch-task-11/bench/base`, `simulatte` 0.12.0 with the checkout's dependency
versions as constraints), and the stripped copy made then by `strip_debug.py` (`scratch-task-11/bench/stripped`, printed
as the imported package), both reused as the earlier gate runs did. Nothing else of this work ran; the one-minute load
average read 1.4 to 2.0 (other sessions, two users logged in). Rounds one to three, in this order, each baseline run
just before the branch run of the same workload. "Base" is the baseline median, "Head" the branch median:

| Round | Workload | vs released: base, head, overhead (limit +5.0 %) | vs stripped: base, head, overhead (limit +12.5 %) | Verdict |
|---|---|---|---|---|
| 1 | u90 | 0.647 s, 0.489 s, -24.4 % | 0.449 s, 0.489 s, +9.0 % | pass, pass |
| 1 | u95 | 0.678 s, 0.524 s, -22.7 % | 0.486 s, 0.524 s, +7.8 % | pass, pass |
| 2 | u90 | 0.646 s, 0.487 s, -24.7 % | 0.452 s, 0.487 s, +7.8 % | pass, pass |
| 2 | u95 | 0.683 s, 0.524 s, -23.2 % | 0.485 s, 0.524 s, +8.2 % | pass, pass |
| 3 | u90 | 0.647 s, 0.485 s, -25.0 % | 0.451 s, 0.485 s, +7.5 % | pass, pass |
| 3 | u95 | 0.680 s, 0.522 s, -23.2 % | 0.485 s, 0.522 s, +7.6 % | pass, pass |

Against the stripped copy the six comparisons run from +7.5 % to +9.0 % (the highest, round 1 u90, is 3.5 points under
the limit); against released 0.12.0 from -25.0 % to -22.7 %. Every `compare.py` exit status was 0. These agree with C's
ten rounds (pooled +8.34 % and +8.45 %, single rounds +5.9 to +9.8 %) and need no new rule.

**Safety run (gate 10).** CPython 3.14.3, Apple M4 Pro, from the repository root, at `229ea61`; neither output mentions
a known limit (the mechanism is gone).

| Range | Worlds | Runs | Events | Raised | Not returned | Queue not empty | Violations | Wall clock | Exit |
|---|---|---|---|---|---|---|---|---|---|
| seeds 100000..100999 | 1,000 | 6,000 | 6,570,200 | 0 | 0 | 0 | 0 | 187 s | 0 |
| seeds 2000000..2000999 | 1,000 | 6,000 | 6,720,030 | 0 | 0 | 0 | 0 | 185 s | 0 |

Together 12,000 runs and 13,290,230 events with no violation, no exception and every run returned. The event counts
equal those of `0d08071` and `4bc6624` (B2). The feature counts (an AGV moves in 5,991 and 5,988 of 6,000 runs; a
deadlock in 2,269 and 2,362; charging in 1,794 and 1,745; stranding in 1,835 and 1,734; a zone held by two in 507 and
340; a passing segment held by two in 120 and 103; parking entered in 3,658 and 3,778) are in `g10-safety-100000.out`
and `g10-safety-2000000.out`.

**Seeds of Assignment 2 (gate 11).** Each as `uv run --all-extras python benchmarks/layout_safety.py --worlds 1 --first
<seed>` (6 runs: both controllers, three routers), at `229ea61`:

| Seed | Why it is run | Result |
|---|---|---|
| 7702388 | open by ruling A2-h | 5,475 events, 1 run with a violation, exit 1: `seed 7702388 reservation shortest: (586.206, 'open wait with no report', ['agv-e'])`. Open, item (a) above. |
| 8101904 | a recorded design gap | 7,018 events, 1 run with a violation, exit 1: `seed 8101904 reservation fastest: (471.171, 'PHYSICAL opposite directions on a segment', 'g0_2~g1_0', [('agv-c', ('g0_2', 'g1_0')), ('agv-g', ('g1_0', 'g0_2'))])`. Open, item (b) above. |
| 2000228 | formerly the second known limit (B1) | 4,194 events, 0 violations, exit 0 |
| 52118 | residual item 7 (B1) | 6,105 events, 0 violations, exit 0 |
| 8103801 | the shape the first half of B1 left open | 10,449 events, 0 violations, exit 0 |
| 9202320 | the review's open waits at `687ad66` | 14,565 events, 0 violations, exit 0 |
| 9202736 | the same | 3,237 events, 0 violations, exit 0 |
| 619 | formerly the first known limit (B2), re-pinned | 2,689 events, 0 violations, exit 0 |

For each, 0 runs raised, 0 did not return and 0 ended with the queue not empty. The two seeds expected to show a
violation show exactly one run each, in the controller and with the router that the record names, and no other run of
their six; the six others are clean in all six runs. No other violation occurred in any gate.

## Assignment 3

### T2. The strict monitor

Decided by the maintainer on 2026-11-25: before 0.18.0 is tagged, the traffic promises hold on the random worlds (no
physical conflict; every wait is granted, resolved by the policy, or reported). Two read-only audits listed the holes
(`a3-T1-i-audit.md`, the wait reviews; `a3-T1-ii-audit.md`, the direction re-claims); T3 and T4 fix them. This task
changes no library code: it makes the monitor of `tests/intralogistics/random_worlds.py` strict enough to see every
known hole and pins the runs that then fail as expected failures. Base `e387d8a`; nothing under `src/` changes, and no
run's events, fingerprint or digest changes (below).

**Before and now.** At `e387d8a` the end-of-run check of open waits read the controller's private state:
`controller._unresolved`, `controller._queue`, `wait.stopped`, `wait.timer`, `wait.stuck` and `controller._blocking`,
and it explained an open wait as soon as one of its blockers was explained, transitively ("queued behind a reported
wait"). The parking check read `ParkingArea._agv_requests`, `agv.current_node` and `agv.state`. Nothing checked what a
`traffic.wait_stuck` or a `traffic.deadlock` said. Now the waits, their reports and the parking slots are followed from
the events, the holdings from the replayed entity state, the capacities, lengths, passing flags and vehicle extents from
the world's spec and the monitor's reference graph, and positions from the monitor's own body model.

**The five changes, each with its rule.**

1. *Open waits at the end, from four event types.* A wait is open from its `traffic.wait_started` to its
   `traffic.wait_ended` (an AGV has at most one pending claim, so the AGV id names the wait). It is reported from its
   `traffic.wait_stuck` until it ends. An unresolved `traffic.deadlock` stands from its event until the first
   `traffic.wait_ended`, with any reason, of an AGV it lists. Every wait open at the end of a run carries a report of
   its own or is listed in an unresolved report that still stands; otherwise `open wait with no report`. The clause
   "queued behind a reported wait" is deleted, with no exemption in its place (Ruling A3-h). Two end checks cannot come
   from events and stay as they were: `review timer left` and `pending claim of a moving AGV at quiescence` (a
   look-ahead claim emits nothing, traffic spec §11.2); both read `controller._queue`.
2. *What a stuck report says.* At each `traffic.wait_stuck`: the AGV has an open wait (`stuck report of an AGV with no
   open wait`); the wait had no report yet (`stuck report repeated for one wait`); `blockers` is not empty (`stuck
   report with no blocker`); each blocker is, at that instant, in the disposition the report gives it, as an observer
   knows it: `STALLED` when the monitor's body of it is on a stalled arc, else `WAITING` when it has an open wait, else
   its state from the replayed `agv` entity (`IDLE`, `STRANDED`, or another state, which no report may give) (`stuck
   report gives a blocker another disposition`). And the rule of §7.9 holds as far as the holdings decide it (added in
   fix round 1): every AGV whose holding blocks an item of the reported wait, by the monitor's admission test of change
   3, is listed (`stuck report leaves out a holder that blocks the wait`); and no wait reached from the reported one
   through waiting blockers is blocked by a holder that is neither stalled, waiting, idle nor stranded (`stuck report of
   a wait that reaches a blocker not at rest`). Holders are a subset of the controller's blockers, so neither condition
   can fail for a right report. The other part of the blockers, the AGVs whose earlier claims gate the wait (§7.4),
   emits nothing and is not checked: that the list names every gating AGV, and that the waits reached through a gate
   are stuck, cannot be decided from events. A blocker that is no AGV of the world is `stuck report names an AGV the
   world does not have`.
3. *What a deadlock report says, by the monitor's own computation* (Ruling A3-j). The monitor has its own admission
   test of §7.3 (`Monitor._blocked`), over the replayed entity state: `reserved_by` of nodes, zones and corridors,
   `forward` and `backward` of segments, and the corridor's `orientation`, which counts for every holder except those
   that hold the corridor since their placement (a holder is unoriented while the only `traffic.reserved` naming the
   corridor for it is the one recorded before activation; audit (i) 5.3). Capacities, `passing`, lengths and extents
   (`length + min_gap` of the fleet's vehicle) come from the spec. A waiting AGV's claim is the `resources` of its
   `traffic.wait_started`, and the direction of each segment and the orientation of each corridor are rebuilt from the
   AGV's remaining route: the nodes of its last `agv.routed` from the node its body rests on; a segment takes the
   direction of the leg that crosses it, a corridor the orientation of the first leg inside it. The reduction of §7.8
   removes a waiting AGV whose claim is admissible given the holdings of the AGVs still in the set, until none is.
   Then:
   - at every `traffic.deadlock`, every listed AGV has an open wait, except the victim of a resolved report, whose
     `traffic.wait_ended` of the same instant, with the reason of the action, comes first (`deadlock lists an AGV with
     no open wait`); and the listed set equals the reduction over the open waits and the victim's claim at that point
     of the event sequence (`deadlock report differs from the deadlocked set`);
   - every deadlock is reported, resolved or not: at each `traffic.wait_started`, and at each `traffic.wait_ended`
     whose reason is not `granted`, the reduction over the open waits is taken; a set that is not empty and does not
     stand must be named by a `traffic.deadlock` of the same instant, or stand at its end (`deadlock not reported`).
     The instant is complete when the clock moves, and at the end of the run. And at the end of each instant in which
     a wait opened or ended, the reduction over the waits still open is empty or stands (`deadlock left at the end of
     the instant`, once per set). (Before fix round 1 the reduction was taken at the end of the instant only, which
     misses a deadlock that a victim resolved within the instant, and the listed set had only to be self-sustaining;
     fix round 1 dropped the end-of-instant reduction and fix round 2 restored it beside the reduction at the event.)
   - the victim of a resolved report has no open wait, and its wait ended in the same instant with the reason of the
     action (`deadlock report names a victim whose wait did not end`; fix round 2);
   - an unresolved report is not repeated while the same set stands (`unresolved deadlock reported again while it
     stands`).
   The second half is checked, not left out: the reduction can be made exact from events (validation below). No read
   of the controller was needed for a direction: the field `directions` beside `resources` that audit (i) proposes
   would make the rebuild unnecessary, and is not added.
4. *Admission against a body* (check M2 of audit (ii)). After every domain event of a reservation run: for every
   vehicle `X` and every single-lane segment `s` on which the monitor's body of `X` surely lies (outside the 1e-6
   band), every other holder of `s` in the replayed segment entity has the direction in which `X`'s body lies on `s`;
   reported once per `(s, X, holder)` as `ADMITTED against a body on a segment`. `passing` segments are left out (Ruling
   A3-f). Check M1 (a holder's recorded direction against its own body) is not added (Ruling A3-g; T3 adds it).
5. *A parking slot during travel* (traffic spec §7.10, "Parking (T13)"). Slots are followed from `parking.entered` and
   `parking.left` (`parking over capacity` when an area holds more than its capacity). At the end of each instant every
   held slot belongs to an AGV whose body rests on the area's node, or that is travelling (state `TRAVELING_EMPTY` or
   `TRAVELING_LOADED`) to that node: the travel's goal is the destination of the AGV's last `agv.routed` that is not a
   step aside, which is an excursion of the same travel. A slot of an AGV at rest elsewhere is `parking slot of an AGV
   resting elsewhere`; a slot of an AGV that travels to another goal is `parking slot held during a travel elsewhere`,
   at the instant that travel starts; each once for as long as it lasts. A travel that turns to charge elsewhere starts
   a travel to the charger, so a slot kept through the turn is reported then, unless the charger is on the area's own
   node (its goal is that node). The end of the run is the last instant, so the earlier check `parking slot held away
   from the node at quiescence` is gone: the same case is now one of these two kinds. The other way round (added in fix
   round 1): at the end of the instant of a `parking.left`, unless the AGV took a slot of the same area again in it,
   the AGV departed from the area's node in that instant (an `agv.move_started` from the node), or its travel there did
   not arrive: its body does not rest on the node, and it does not travel to it unless its state changed to one that is
   not travelling after the `parking.left` (a change into `TRAVELING_EMPTY` does not count: the coordinator sets it
   after taking a slot, so it would excuse a slot given back at the decision; fix round 2): a trip to park that did not
   arrive may be followed, in the same instant and after the AGV became idle, by the travel of an order to the same
   node, without a slot. Before this exception 18 runs of 4300000..4301999
   and 4310000..4310599 were flagged at this head, every one of this kind (`scratch-a3-T2/r1/left-cases.out`: the trip
   gave up in 10, was interrupted in 4 and ended at another node in 4; then `IDLE`, then an order).
   Otherwise `parking slot given back while the AGV rests on the area's node` or `parking slot given back during a
   travel to the area's node`. A `parking.left` of a slot that is not held is `parking slot given back that was not
   held`.

A wait whose `resources` name a resource the spec does not have is `wait for a resource the spec does not have` (only a
network that differs from the spec can give it; the monitor reports that difference already).

**Where the monitor still reads the controller and the live model**, each with whether a verdict depends on it:

- `_check_ledger` reads the ledger (`holders`, `direction`, `_bindings`) and compares it with the entities and with the
  admission rules: its verdicts (`LEDGER ...`, `... entity != ledger`) depend on it by design.
- The coverage checks and `_exact` compare the bodies with the ledger's holdings (`held_ids`): their verdicts depend on
  it. `_exact` also reads `controller._pending` (`pending request while not travelling`).
- `final` reads `controller._queue` for `pending claim of a moving AGV at quiescence` and `review timer left`.
- At the start of the run the monitor compares the network's capacities, zones and corridors with its own (`built ...
  differ from the spec`, `corridor derivation differs`): verdicts about the layout, from the built network.
- `controller.ledger.placed()` gates the building of the bodies (the monitor checks nothing before every AGV is placed):
  no verdict reads it, but it decides when the checks start. `env._activated` decides which `traffic.reserved` events
  are placements (the unoriented corridor holders of change 3) and that nothing is checked before activation; the
  placements run in activation initializers, so a verdict of change 3 depends on it.
- Each fleet's `fleet._executor.travel` is wrapped so that `_exact` runs at every exit of a travel (`holdings != body`
  at `exit:...`): those verdicts depend on the wrap.
- The bodies are built from the live `agv.agv_type` (length and gap) and `agv.current_node` at activation; `final` reads
  `agv.motion` (`motion left at quiescence`); `_check` reads `agv.state.name` to decide whether `_exact` runs for a
  vehicle that is not travelling (its verdicts depend on it). The replay compares the replayed state with the live
  `env.entities.snapshot()`, and the seeded replay starts from `env._initial_state` (`replay differs`, `seeded replay
  differs`: verdicts by design). Node capacities come from the spec's parking areas and chargers since fix
  round 1 (before, from the built `ParkingArea` and `ChargingStation` objects; the values are the same).

None of the five changes reads the controller.

**Validation of the monitor's own admission test.** A scratch wrapper (`scratch-a3-T2/validate.py`) compares, in the
same run, at every `traffic.wait_started` the claim the monitor rebuilt (ids, kinds, directions) with the controller's
`ledger.needed(agv, claim)`, and after every `traffic.wait_started`, `traffic.wait_ended` and `traffic.deadlock` the
monitor's reduction over its open waits with the controller's `_reduce` over its stopped waits, and the open waits, the
reported waits and the standing sets with the controller's own; at every `traffic.deadlock` it compares the listed set
with the monitor's reduction over the open waits and the victim's claim. On seeds 4300000..4301999, reservation, three
routers (6,000 runs): 217,567 claims (635,624 items), 506,344 reductions and 81,967 reports (48,570 `step_aside`, 18,699
`reroute`, 9,099 `give_up`, 5,599 `unresolved`), 0 differences of any kind; on the 42 reservation runs of the fourteen
worlds pinned below, 1,383 claims, 3,103 reductions and 500 reports, 0 differences. No direction was left unrebuilt.
After fix round 1 (the reduction taken at each wait event, the listed set compared with it), on seeds 4300000..4300999,
reservation, three routers (3,000 runs, `scratch-a3-T2/r1b/val-4300000.out`): 107,657 claims (314,678 items), 250,836
reductions and 41,042 reports (24,789 `step_aside`, 8,870 `reroute`, 4,670 `give_up`, 2,713 `unresolved`), 0
differences, and the only violation of the strict monitor in those runs is `open wait with no report` of 4300647
`fastest` (hole R, below): no report differs from the monitor's reduction at its event, and no deadlock found at a wait
event goes unreported.

**The runs that fail at this head**, each checked to fail for its stated reason only (the kinds are those of the
strict monitor, all under `ReservationController`). Each is a case of a test marked `xfail(strict=True,
raises=AssertionError)` that asserts that the run ends on its own with no violation, one test per hole, so that the
fixing task removes one mark (a run that raises is a failure, not the expected one):

- The direction re-claim (audit (ii)), fixed by T3: 8101904 `fastest`, at 471.171 `ADMITTED against a body on a
  segment` (`g0_2~g1_0`, the body of agv-c forward, holder agv-g backward), then `PHYSICAL opposite directions on a
  segment` at the same instant. Test `test_a_run_where_a_vehicle_reverses_on_a_segment_admits_no_vehicle_against_its_
  body`. A second, ordinary test, `test_the_monitor_reports_the_admission_against_a_body_before_the_physical_conflict`,
  asserts that the violations are exactly these two, in this order: T3 deletes it.
- The gate waiver (hole A), fixed by T4: 7702388 `shortest`, `open wait with no report` (agv-e) at 586.206. Test
  `test_a_run_with_a_gate_waiver_ends_with_every_open_wait_reported`.
- The waits behind a reported wait (hole R), fixed by T4: the eight known runs, 8102359 `congestion` (agv-f), 8103231
  `shortest` (agv-a), 100281 `shortest` and `fastest` (agv-f), 7700058 with the three routers (agv-e), 7701490
  `fastest` (agv-f), and the eight of audit (i), 4100399 `congestion` (agv-d), 4102230 `shortest` (agv-d, bot-y),
  4103675 `fastest` (agv-a), 4106591 `shortest` (agv-d), 4106711 `congestion` (agv-a), 4109344 `shortest` (agv-d),
  4109857 `fastest` and `congestion` (agv-e, agv-g); each has one violation, `open wait with no report`, naming those
  AGVs. Test `test_a_run_with_a_wait_behind_a_reported_wait_ends_with_every_open_wait_reported` (16 cases).

None of the pinned digests of `SAFETY` and `EXERCISED` shows a violation under the strict monitor: no mark there. Under
the monitor of `e387d8a` the 16 runs of hole R pass (`XPASS(strict)`, so the test fails) and the other two already fail.
A wrong fix must not turn these pins green: `test_a_controller_that_reports_waits_stuck_too_eagerly_does_not_clear_the_
hole_runs` applies the stuck rule to a wait's own blockers only (a wait is reported while a blocker of a blocker moves
or serves) and asserts that each of the 17 runs of holes A and R still has a violation (each gives `stuck report of a
wait that reaches a blocker not at rest`; added in fix round 1).

**Tests of the new checks**, each a monkeypatch and a seed in `test_random_worlds.py`. Under the monitor of `e387d8a`
the kind each test asks for is absent; the first six runs show no violation at all there, the last two show older kinds
(seed 58: 17 violations, `LEDGER both directions` 10 and `PHYSICAL opposite directions on a segment` 7, from 37.604;
seed 619: `parking slot of an AGV resting elsewhere` 55 times, from 149.151, once the charge has started):

- a stuck wait reported as an unresolved deadlock of its AGV alone, and remembered as one, so that the report would
  explain the wait (26 `shortest`): `deadlock report differs from the deadlocked set`;
- detection that takes every stopped wait for deadlocked, a report it should not make (26 `shortest`): the same kind;
- an unresolved deadlock neither reported nor remembered, a missed report (2000228 `fastest`): `deadlock not reported`;
- a stuck report with no blocker (26 `shortest`): `stuck report with no blocker`;
- each blocker of a stuck report given another disposition (26 `shortest`): `stuck report gives a blocker another
  disposition`;
- each stuck report emitted twice (26 `shortest`): `stuck report repeated for one wait`;
- a ledger that ignores agv-a's holdings of single-lane segments (58, default router): `ADMITTED against a body on a
  segment`;
- the parking fix undone (`_release_slots_elsewhere` does nothing; 619, `FreeFlowController`, `shortest`): `parking slot
  held during a travel elsewhere` at 122.048 (P0, agv-a, goal c08), as agv-a turns to charge.

Added in fix round 1, each kind absent under the monitor of `4758b04` (the run before the round, `scratch-a3-T2/
red1.out`):

- the stuck rule applied to a wait's own blockers only (26 `shortest`): `stuck report of a wait that reaches a blocker
  not at rest`;
- a blocker that moves or serves left out of the rule and of the report (26 `shortest`): `stuck report leaves out a
  holder that blocks the wait`;
- resolved deadlocks not reported (26 `shortest`): `deadlock not reported`;
- a resolved report of three AGVs or more that leaves out one member (26 `shortest`): `deadlock report differs from the
  deadlocked set`;
- a trip to park that gives its slot back as it arrives, or 5 later, while the AGV rests parked (619,
  `FreeFlowController`, `shortest`): `parking slot given back while the AGV rests on the area's node` at 163.078 and
  168.078 (P1, agv-b).

Added in fix round 2 (`scratch-a3-T2/red2.out`, the run under the monitor of `07db611`):

- a deadlock reported once as resolved by its first member giving up, nothing staged (26 `shortest`): `deadlock report
  names a victim whose wait did not end` and `deadlock left at the end of the instant`, both absent under `07db611`
  (which gave `deadlock not reported` on this seed);
- a slot given back as soon as it is taken, at the decision (88 `shortest`): `parking slot given back during a travel
  to the area's node`, absent under `07db611`;
- a stuck report that also names a blocker that is no AGV of the world (26 `shortest`): `stuck report names an AGV the
  world does not have`; and every `parking.left` emitted twice (88 `shortest`): `parking slot given back that was not
  held`. Both kinds existed under `07db611` but had no test.

Before fix round 1 the first two mutants of the first list gave `deadlock report lists AGVs that are not deadlocked`,
the kind that `deadlock report differs from the deadlocked set` replaces.

The seven mutant runs of `test_an_open_wait_with_no_report_is_a_violation` still end with `open wait with no report`;
the three of the mutant "blind after a member's withdrawal" also give `deadlock not reported`.

**The parking check before the parking fix.** The source of `f6171ac` (before `61e5b27`), a `git archive` copy first on
`PYTHONPATH`, with this monitor, on the seeds B2 lists, both controllers, three routers: seed 619 (`FreeFlowController`,
three routers) `parking slot held during a travel elsewhere` at 122.048 (P0, agv-a, goal c08), then `parking slot of an
AGV resting elsewhere` at 148.587 (charging); 2000447 `shortest` and `fastest` the same at 98.480 (P1, agv-b, goal
g2_0) and 106.913. In 100023 (three routers), 2000152 `shortest` and 2000345 `shortest` and `fastest` the AGV charges
on the node where it turns (on its own node, or on the charger it stands on along its route), so there is no travel
elsewhere; the slot kept through the charge is `parking slot of an AGV resting elsewhere` at 201.873, 246.548 and
157.323. At this head every one of these runs is clean (the safety ranges below hold them).

**The controller mutants.** The 25 mutants of `reviewer-scratch-t11/mutants2.py` and no mutant, each applied in every
worker (a scratch patch, nothing on disk), seeds 2000150..2000449 and 500000..500299, `ReservationController`, three
routers (900 runs per range), run on the same source once with the monitor of `e387d8a` and once with the strict
monitor as of fix round 1 (`scratch-a3-T2/mutrun.py`, `mutcmp2.py`, `r1b/mutcmp.out`; the strict column before the
round differed in one cell, `deadlock_silent` 174 / 184). The columns count the runs with a violation, an exception
or a cut run, per range (2000150.. / 500000..):

| Mutant (as named in `mutants2.py`) | Monitor of `e387d8a` | Strict monitor |
|---|---|---|
| `none` (no mutant) | 0 / 0 | 0 / 2 |
| `no_stuck` (every stuck report suppressed) | 307 / 328 | 307 / 331 |
| `no_timer` (every review timer dropped) | 174 / 191 | 226 / 243 |
| `no_report_after_member_withdraw` (blind after a member's withdrawal) | 21 / 12 | 27 / 24 |
| `stuck_some` | 176 / 194 | 233 / 263 |
| `no_deadlock_report` | 0 / 0 | 227 / 249 |
| `deadlock_silent` | 0 / 0 | 174 / 186 |
| `timer_some` | 4 / 6 | 9 / 12 |
| `no_scan_on_drop` | 0 / 1 | 5 / 12 |
| `no_scan_ever` | 2 / 0 | 64 / 57 |
| `withdraw_forgets_all` | 0 / 0 | 10 / 8 |
| `release_forgets_all` | 0 / 0 | 163 / 171 |
| `time_forgets` | 0 / 0 | 160 / 179 |
| `orphan_never_granted` | 0 / 0 | 15 / 25 |
| `orphan_never_granted_ungated` | 0 / 3 | 15 / 26 |
| `stuck_after_withdraw_suppressed` | 76 / 71 | 79 / 79 |
| `nonmember_withdraw_forgets_silently` | 1 / 0 | 3 / 3 |
| `deadlock_event_not_emitted` | 0 / 0 | 227 / 249 |
| `stuck_event_not_emitted` | 0 / 0 | 307 / 331 |
| `member_end_then_no_stuck` | 3 / 0 | 8 / 7 |
| `no_detect_after_withdraw` | 0 / 0 | 27 / 24 |
| `strict_admission` | 27 / 34 | 571 / 532 |
| `strict_after_withdraw` | 0 / 0 | 11 / 14 |
| `yield_never` | 0 / 0 | 1 / 2 |
| `interrupted_reason_for_reroute` | 0 / 0 | 476 / 456 |
| `withdraw_keeps_queue_entry_of_others` | 0 / 0 | 0 / 2 |

No run that the monitor of `e387d8a` flags passes the strict monitor, for any mutant. The three mutants of the
acceptance record give violations on both ranges: 21 / 12 and 27 / 24 (blind), 307 / 328 and 307 / 331 (stuck reports
suppressed), 174 / 191 and 226 / 243 (timers dropped); the counts of the monitor of `e387d8a` equal those recorded under
B1. The kinds the strict monitor adds: `deadlock not reported` (a set forgotten, not emitted or not detected),
`unresolved deadlock reported again while it stands` (a set forgotten silently and reported again), `deadlock report
differs from the deadlocked set` (an admission that is too strict; a wait that ends with the wrong reason), `deadlock
lists an AGV with no open wait` (a wait that ends with the wrong reason), and `stuck report with no blocker` (a wait
left admissible and unscanned, reported with an empty blocker list: the case audit (i) foresaw in section 7). The two
runs of `none` on 500000..500299, which every mutant shows as well, are 500064 `congestion` (agv-b) and 500198 `fastest`
(agv-a), each `open wait with no report`; the episode detector of audit (i) classifies both as hole R in its `idle()`
form (one episode each, to the end of the run), and `p_min` and `p_sweep2` close both while `base` does not. They are
not pinned. `withdraw_keeps_queue_entry_of_others` shows nothing beyond those two runs, under either monitor.

**Measurements**, the source of `e387d8a` with the monitor as committed at `4758b04` (the ranges rerun after fix round
1 are under "Fix round 1" below), CPython 3.14.3, Apple M4 Pro, 10 worker processes, nothing else heavy running:

| Seeds | Controllers | Runs | Events | Raised | Not returned | Runs with a violation |
|---|---|---|---|---|---|---|
| 100000..100999 | both | 6,000 | 6,570,200 | 0 | 0 | 2 |
| 2000000..2000999 | both | 6,000 | 6,720,030 | 0 | 0 | 0 |
| 4100000..4109999 | reservation | 30,000 | 40,955,436 | 0 | 0 | 8 |
| 4300000..4301999 | both | 12,000 | 13,525,559 | 0 | 0 | 2 |

Every run uses the three routers. Every violation is `open wait with no report`: 100281 `shortest` and `fastest` in the
first range (hole R, pinned); in the third exactly the eight runs of audit (i) (pinned); in the fourth 4300647
`fastest` (bot-x) and 4301217 `shortest` (agv-c).

The first two are the recorded safety ranges, run with `benchmarks/layout_safety.py` (186 s and 185 s, exit status 1
and 0); their event counts equal those of `229ea61`. On 4100000..4109999 the event counts equal the audit's (1,074,598
waits, 65,062 stuck reports, 26,172 unresolved and 375,802 resolved deadlock reports). 4300000..4301999 is fresh
(8,300,709 reservation and 5,224,850 free-flow events).

Every violation is one of the known holes. The two runs of the fresh range are hole R in its `idle()` form: the
episode detector of audit (i) (`reviewer-scratch-a3-T1i/scripts/latent2.py`, `classify.py`) classifies both as "R chain
stopped at a wait already reported stuck [idle:IDLE]" (5 episodes in the two runs, one to the end of each), and the
audit's prototypes `p_min` and `p_sweep2` each close both runs, the copy `base` does not. They are not pinned (the
brief names the runs to pin); T4 may add them.

Free flow: the strict monitor reports no violation on the 12,000 free-flow runs of 100000..100999, 2000000..2000999 and
4300000..4301999. The new checks concern the reservation controller, except the parking check, which reports nothing on
them either.

Fingerprints: the monitor observes. Seeds 100000..100199, both controllers, three routers (1,200 runs), run with the
monitor of `e387d8a` and with this one on the same source: 0 fingerprints and 0 event counts differ. The pinned
fingerprints of `test_random_worlds.py` hold (none is re-pinned).

Cost, with the monitor of fix round 1: `benchmarks/layout_safety.py --first 100000 --worlds 200 --jobs 10`, alternating
in one session, the monitor of `e387d8a` then this one, three times (`scratch-a3-T2/r1b/cost-{base,head}-{1,2,3}.time`,
with the `uptime` taken before each in `.load`, one-minute load 11.3 to 17.8, the runner's own workers): 38.38 s and
38.89 s, 38.42 s and 39.58 s, 40.52 s and 40.51 s. Summed per run over seeds 100000..100199 (1,200 runs, the monitor of
`4758b04`, `scratch-a3-T2/d-{base,head}-100000.pkl`): reservation 281.1 s against 283.9 s, free flow 60.4 s against
60.8 s. The reduction of change 3 runs at each wait that opens or ends otherwise than by a grant, and at each deadlock
report; it does not need a bound on these worlds. `tests/intralogistics/test_random_worlds.py`
(`scratch-a3-T2/r1b/tests-{base,head}.out`, one-minute load 12.9): 34 tests in 11.00 s at `e387d8a`, now 84 in 22.38 s
(66 passed, 18 expected failures); the slowest test is 1.48 s, a test that predates this task.

**What the monitor still does not check.** This replaces, for three items, the list under "What the monitor does not
check" (Task 11): the content of a `traffic.deadlock` report and that every deadlock is reported (change 3), a sleeping
wait queued behind a reported one (change 1), and a parking slot during travel (change 5) are checked now. What stays
open:

- The spacing of followers on a segment, on the bodies (as before: it holds through the coverage and ledger checks).
- The gate part of a stuck report: that it names every AGV whose earlier claim gates the wait, and that the waits
  reached through a gate are stuck. Gates emit nothing (§7.4, §11.2), so an observer cannot recompute them; the
  holding part is checked (change 2).
- That a trip to park holds a slot. The monitor had no such rule at all (corrected in T5): a mutant in which no trip
  to park takes a slot changes 558 of 900 runs and none is flagged (T2's review). What the parking exception of change
  5 lets through is one case of it: a slot given back, then in the same instant a change of the AGV's state to one
  that is not travelling, then a travel to the same node that takes no slot. The scratch mutant
  `scratch-a3-T2/mymut2.py` (after a give-back, a new trip to park on the same node in the same instant takes no slot)
  changes 6 of 1,800 runs of 4316000..4316299 (both controllers, three routers) and none is flagged
  (`scratch-a3-T2/r2/rv-slot_skipped.jsonl`). In 3 of the 6 the AGV then travels to the node, and rests there, without
  a slot (4316044 `shortest`, 4316103 `fastest`, 4316219 `congestion`, all under `ReservationController`); in the other
  3 the new trip strands in the same instant and only the parking events differ. An AGV may rest on a parking node
  without a slot (an order brings it there), so the rest is not the check; the trip is. Checked since T5 (`trip to
  park without a slot`, "T5. Two fleet items").
- `passing` segments during a reversal (Ruling A3-f): change 4 leaves them out and there is no criterion a run could
  fail.
- A holder's recorded direction against its own body (check M1 of audit (ii)), until T3 adds it with the rule.
- The review timer left and the pending claim of a moving AGV at quiescence are checked from the controller's state, not
  from events (change 1).

**Fix round 1.** The review of T2 found three gaps, each fixed in the monitor and described in the changes above: a
stuck report was not checked against the rule of §7.9 (a controller that reports a wait while a blocker of a blocker
moves passed, and would have turned every hole pin green); "every deadlock is reported" covered unresolved deadlocks
only, and a report had only to be self-sustaining; and the record said that each test mutant passed the monitor of
`e387d8a` with no violation, which was false for seeds 58 and 619 (corrected above). In the same round: a slot given
back while its AGV rests parked, or travels on to the area's node, is checked; a `parking.left` of a slot not held is
a violation; the pins carry `raises=AssertionError`; a stuck report naming an AGV the world does not have is a
violation, not an exception; node capacities come from the spec; the reads of the controller are listed in full.

Rerun with the monitor of the round (`scratch-a3-T2/r1b/`):

| Seeds | Controllers | Runs | Events | Raised | Not returned | Runs with a violation |
|---|---|---|---|---|---|---|
| 4300000..4301999 | both | 12,000 | 13,525,559 | 0 | 0 | 2 |
| 4100000..4109999 | reservation | 30,000 | 40,955,436 | 0 | 0 | 8 |
| 4310000..4310599 | both | 3,600 | 3,977,067 | 0 | 0 | 1 |

The same runs as before the round: 4300647 `fastest` and 4301217 `shortest`; the eight of audit (i); and, in the
reviewer's range, 4310551 `congestion`, which the review had already found and classified as hole R. Every violation is
`open wait with no report`; the free-flow runs have none.

The reviewer's mutants (`reviewer-scratch-a3-T2/scripts/mymut.py`, run with its `run.py` under this monitor, seeds
4315000..4315149, three routers; `scratch-a3-T2/r1b/rvsum.out`): stuck rule on direct blockers only, 139 of 450
reservation runs change and all 139 are flagged (`stuck report of a wait that reaches a blocker not at rest`); a moving
blocker left out of the report, 266 changed, 266 flagged; resolved reports not emitted, 364 changed, 364 flagged
(`deadlock not reported`); a resolved report one member short, 248 changed, 248 flagged (`deadlock report differs from
the deadlocked set`); slot given back on arrival and 5 after arrival, both controllers, 519 and 507 of 900 runs
flagged (`parking slot given back while the AGV rests on the area's node`); under the first of these, 20 runs whose
fingerprint changes are not flagged (15 under `ReservationController`, 5 under `FreeFlowController`): in each of the 20
the events are the same as without the mutant, as a multiset, and only their order within an instant moves
(`scratch-a3-T2/r2/arrival-unflagged.out`). No mutant and the
reviewer's copy of `_resolve` without a change: 1 run of 450 with a violation, 4315085 `fastest`, hole R (known).

**Fix round 2.** The re-review found two gaps in fix round 1, both fixed: a resolved `traffic.deadlock` whose victim
still waits passed, and the end-of-instant reduction had been dropped (both rules are back: the victim's wait must have
ended in the instant with the action's reason, and the deadlocked set of the waits open at the end of an instant with a
wait event must stand); and the parking exception counted any change of state, the change into `TRAVELING_EMPTY` that
follows a slot taken at the decision included (now only a change to a state that is not travelling counts). A stuck
report with an unknown blocker is checked for its other blockers too. Tests for the three kinds that had none.

Rerun with the monitor of the round (`scratch-a3-T2/r2/`): seeds 4300000..4301999, both controllers, 12,000 runs,
13,525,559 events, 0 raised, 0 cut, 0 not returned, 2 runs with a violation, `open wait with no report` in 4300647
`fastest` and 4301217 `shortest` as before; fresh seeds 4320000..4320999, both controllers, 6,000 runs, 6,505,949
events, 0 raised, 0 cut, 0 not returned, 1 run with a violation, `open wait with no report` in 4320963 `congestion`
(bot-x, agv-e, bot-y, agv-b): hole R in its stranding form by the episode detector of audit (i), four episodes that last
to the end of the run, closed by `p_min` and `p_sweep2` and not by `base` (`r2/classify-4320963.out`). Free flow: no
violation.

The reviewer's mutants on 4316000..4316299, both controllers, three routers (`r2/rvsum.out`): `fake_resolution` changes
738 runs and flags 738 (`deadlock report names a victim whose wait did not end` 738, `deadlock left at the end of the
instant` 738, `deadlock not reported` 400); `park_release_at_decision` changes 1,046 and flags 1,025 (`parking slot
given back during a travel to the area's node`); the 21 others carry the same events as without the mutant, as a
multiset, only reordered within an instant (`r2/decision-unflagged.out`); `park_left_twice` 998 of 998 (`parking slot
given back that was not held`); `stuck_names_unknown_blocker` 535 of 535 (`stuck report names an AGV the world does not
have`). No mutant: 1 run of 1,800 with a violation, 4316274 `shortest`, `open wait with no report` (agv-f): hole R in
its stranding form by the episode detector of audit (i) (one episode, to the end of the run), closed by `p_min` and
`p_sweep2` and not by `base` (`r2/classify-4316274.out`).

### T3. No admission against a body after a direction re-claim

Decided by the maintainer on 2026-11-25: the physical conflict of seed 8101904 (item (b) of "Open after Assignment 2")
is fixed before 0.18.0 is tagged, by the exact rule. Base `fcecc40` (the traffic spec amended for this task: §6.6, §7.1
`cleared`, §7.2 "Both directions", §7.3, §7.4, §7.9). Fix in `adfdd30`, the monitor's checks in `3e7e781`.

**The cause.** A vehicle whose body still lies on a single-lane segment claims that segment again in the new direction
(it reverses; the claim comes through look-ahead or a claim of several legs, at rest or moving). The grant overwrote the
recorded direction (`HoldingsLedger.take`), a holder's own holdings never block it, and the controller then admitted a
second vehicle head-on into the lane that the first one's body still occupied. Seed 8101904, `FastestRouter`: agv-c
claims `g0_2~g1_0` again at 471.171 and agv-g is granted it the other way at the same instant. It was a gap of the
design (the code did what the spec said), present since the traffic work.

**Where the exposure is** (audit (ii), `a3-T1-ii-audit.md`, sections 1 to 3, with its hand-built scenarios). Every case
passes over a single-lane segment: the segment next to a node of capacity above 1 on which the holder did not come to
rest (`base`, seed 8101904); the last segment of a corridor or of a dead-end branch next to a port or root of capacity
above 1, when the re-claim outlives a stop of the holder before the segment (`corr_stop`, `branch_stop`: the far node is
given back and the segment kept, so the exposure reaches inside corridors and branches); a zone of capacity 2 over the
segment hides nothing (`zone2`); a route staged inside a travel that starts with a turn re-claims the segment before the
turn and holds the record through the whole turn wait (`turn_staged`). A corridor's orientation alone admits nobody
against a body, and zones and branches record no direction.

**The rule** (traffic spec §7.2, "Both directions"). A single-lane segment claimed again in a new direction is, from
that grant, held in both directions by its holder: no other claimant is admissible on it, in either direction, until the
first of (a) the holder starts its own leg over the segment, (b) its odometer reaches the clear point the segment had in
the trail when it was claimed again, on whichever arc that happens (for a profile without kinematic limits, at the
arrival that ends that arc), (c) the holder comes to rest on a node of capacity above 1, (d) the holder releases the
segment. A `retain` that keeps the segment keeps the state. `passing` segments and corridor orientations have no state
(Ruling A3-f). Two rulings shaped it: the state lives in the ledger only, with no entity field, event or schema change
(Ruling A3-c: the trace cannot explain an admission on a segment today either, and a field or an event would have moved
about 78 % of the reservation digests for a rule that changes very few runs); and end (a) holds, so that the rule, the
executor's trail and the monitor's body mean the same by "the body as it arrived" (Ruling A3-d). An end that releases
nothing makes the controller scan, and counts as a change for the reviews, only when a pending claim was blocked by the
state at that instant (Ruling A3-e).

**What changed in the code.**

- `HoldingsLedger` (`traffic_holdings.py`) keeps the holders in the state per segment; `take` begins it for a
  single-lane segment that the holder already held with the other direction, `drop` ends it (d), `end_both_directions`
  ends it without a release. `blockers` reads it: on a single-lane segment a holder in the state blocks every other
  claimant, in grant order, restricted to `among` when the reduction asks. Every admission test goes through `blockers`
  (the scan, `view.admissible`, the gate, the blockers of a wait, the waits an AGV blocks, the reduction, `place`,
  `TrafficView.blockers`, hence the standard search and `CongestionAwareRouter`). The public read method is
  `HoldingsLedger.both_directions(resource)`: the holders in the state, empty for any other resource.
- The protocol has a new method, `TrafficController.cleared(agv, resources)`: the body of the AGV, as it arrived, no
  longer lies on those segments, which it still holds; nothing is released. `FreeFlowController.cleared` does nothing.
  `ReservationController.cleared` ends the state of those that have it and, when the state made a pending claim
  inadmissible at that instant, adds one to the change count of the reviews and scans the queue.
- The travel executor (`travel.py`) keeps the old clear point of a segment claimed again (as it did, in `_Trip.lifted`)
  and reports with `cleared`: (a) at the departure onto the segment; (b) at the old clear point, timed with the releases
  of the arc on which it lies (a re-claim taken at rest has its clear point classified with the trail's at each
  departure; one granted while moving reuses the release that the departure scheduled for the same clear point, which
  then finds the segment lifted); (c) at a rest on a node of capacity above 1, after the trail's releases.
- No event, entity field, schema, trace fixture, digest pin or layout hash changes.

**Timers.** End (b) inside an arc is timed by the arc's own release sleep, which the travel process waits for inside
`_move`: an interrupt or a stop request received during the arc is remembered and takes effect where the vehicle comes
to rest after it, a stalled arc schedules no timer, a travel that ends or stops before the old clear point schedules
nothing for it (the segment goes back into the trail with the state, and a later travel releases it at that point), and
a release of the segment before the instant leaves a `cleared` that the controller ignores. Every such timer lies before
the arrival of its arc, so it never advances the clock of a network in which nothing else happens, and `env.run()`
without `until` returns as before (`test_no_timer_of_the_state_outlives_the_arc_or_the_travel`). No timer is cut, since
none can outlive its arc.

**Before and after.** Base `fcecc40` against the fix, paired by run, reservation runs under the three routers, monitor
of the tree (T2's strict monitor at the base, this task's at the head); 95 % intervals are 1.96 standard errors of the
paired difference. Scratch: `.superpowers/sdd/2026-11-23-layout-only-models/scratch-a3-T3/` (`scripts/measure.py`,
`scripts/cmp.py`, `out/cmp-4200000.txt`, `out/cmp-4400000.txt`).

| | Seeds 4200000..4202999 (the audit's) | Seeds 4400000..4402999 (fresh) |
|---|---|---|
| Reservation runs, returned (base / head) | 9,000 / 9,000 of 9,000 | 9,000 / 9,000 of 9,000 |
| Events (base / head) | 12,471,644 / 12,471,671 | 12,008,590 / 12,008,590 |
| Runs with a violation (base / head) | 3 / 3, the same runs, `open wait with no report` (T4) | 7 / 7, the same runs, `open wait with no report` (T4) |
| Fingerprints changed | 12, all `congestion` | 14, all `congestion` |
| Orders completed, mean difference per run | +0 (+0 .. +0); 115,254 both | +0 (+0 .. +0); 111,126 both |
| Waits opened | +0.00067 (-0.00031 .. +0.00164); 323,631 and 323,637 | +0 (+0 .. +0); 306,592 both |
| `traffic.wait_stuck` | +0 (+0 .. +0); 19,527 both | +0 (+0 .. +0); 19,265 both |
| Deadlock reports: give up, reroute, step aside, unresolved | 12,351 / 26,075 / 70,983 / 7,940 at base; +0, +1, +2, +0 at head (every interval contains 0) | 12,843 / 25,289 / 70,043 / 7,747, the same at head |
| End time, runs that differ | 2 (mean +0.0031, interval -0.0015 .. +0.0077) | 0 |

The violating runs: 4200171 `fastest` and `shortest`, 4201442 `shortest`; 4400263 `congestion`, 4401673 `congestion`,
4402051 `fastest` and `shortest`, 4402618 `fastest`, 4402902 `fastest` and `shortest`; none of them has a changed
fingerprint. On 4200000..4202999 the 12 changed runs are exactly those the audit's prototype `exact3` changed, and each
has the prototype's fingerprint (4200169, 4200377, 4200385, 4200455, 4200509, 4200862, 4200980, 4201507, 4201841,
4202075, 4202601, 4202667, all `congestion`); in the other 8,988 runs head, base and prototype agree.

**On these two ranges every changed fingerprint has one cause, the congestion router reading the state.** (Not in
general: the review found 4412263 `fastest`, changed by an admission the state refused; see fix round 1.)
`CongestionAwareRouter` reads
`TrafficView.blockers` for its costs, and a holder in the state now counts there. With the state hidden from what that
router reads while it computes a route, and kept everywhere else (`scripts/routerblind.py`), each of the 26 changed runs
gives the base's fingerprint again; so no admission that the state refused changed the outcome of a run in these 18,000.
Seed 8101904: `fastest` now ends without a violation (28 orders before and after; the base had `ADMITTED against a body
on a segment` and `PHYSICAL opposite directions on a segment` at 471.171); `shortest` and `congestion` keep the base's
fingerprint. `benchmarks/layout_safety.py --first 8101904 --worlds 1`: exit status 1 at the base, 0 now.

**Free flow is byte-identical.** The 18,000 free-flow runs of the same 6,000 worlds: events, initial and final state and
end time equal at base and head in every run.

**How often** (head, by wrapping the ledger and the controller in the measuring process):

| | 4200000..4202999 | 4400000..4402999 |
|---|---|---|
| States begun (at rest / while moving) | 258,379 (258,221 / 158) | 249,619 (249,464 / 155) |
| Ended by (a), the holder's own leg | 233,610 | 226,786 |
| Ended by (b), the old clear point (inside an arc / at an arrival) | 24,734 (22,372 / 2,362) | 22,790 (20,911 / 1,879) |
| of which (b) for a re-claim granted while moving | 132 | 114 |
| Ended by (c), at rest with room | 0 | 0 |
| Ended by (d), a release | 15 | 25 |
| Standing when the run ended | 20 | 18 |
| Ends that blocked a pending claim (runs) | 1,118 (343) | 822 (262) |
| Timers added per run, runs with an unchanged fingerprint | +2.56 (+2.38 .. +2.74) | +2.42 (+2.26 .. +2.57) |

The 1,118 blocking ends in 343 runs match the audit's count for `exact3`. A timer added is a `simpy` timeout of a clear
point inside an arc that no release of the arc shares; none is cut. End (c) occurs in no run of these two ranges (the
review found one, 4411251 `fastest`, see fix round 1); the hand-built `roomy_stop` covers it.

**The two recorded safety ranges** (`benchmarks/layout_safety.py --worlds 1000`, six runs per world), base and head
alike: 100000..100999, 6,000 runs, 6,570,200 events, exit status 1 with two violating runs, 100281 `shortest` and
`fastest`, `open wait with no report` (agv-f at 579.996; hole R, pinned for T4); 2000000..2000999, 6,000 runs, 6,720,030
events, no violation, exit status 0. No run raised or was cut; none of the violations is physical, `ADMITTED` or a kind
of this task.

**Other checks.** `PYTHONHASHSEED` 0, 1 and 2 give the same events in every hand-built scenario
(`scripts/scen_hash.py`); 600 runs of seeds 4400000..4400099 (both controllers) under `PYTHONHASHSEED` 1 and 2 have the
fingerprints and whole records of the head run. No pinned digest, fingerprint or fixture moves. The measured tree
(`scratch-a3-T3/head1`) is not exactly the commit `adfdd30`: one sentence of the docstring of `TrafficController.cleared`
was added after the measurement; no code differs. Cost:
`benchmarks/layout_safety.py --first 100000 --worlds 200 --jobs 10`, base and head alternating in one session, three
times, one-minute load 8.9 to 10.1 before each (the runner's own workers; `out/cost/`): 38.74 s and 40.29 s, 40.11 s and
40.19 s, 39.35 s and 39.50 s (means 39.40 s and 39.99 s, +1.5 %, the monitor's new checks included);
`benchmarks/intralogistics.py --mode none --shifts 2 --warmup 1 --repeat 3` (free flow, which this change does not
touch), alternating twice: medians 0.457 s and 0.474 s, 0.472 s and 0.461 s.

**The hand-built scenarios, as tests** (`tests/intralogistics/test_travel.py`, 3 m vehicles, `TrapezoidalProfile(2.0,
0.8, 0.4)`). At the base B is granted at once and the bodies lie head-on; now: `base`, B waits from 50.0 behind A and is
granted at 51.118, A's odometer at the old clear point of `U~V` (0.5 m into `W -> V`), with no release and no other
traffic event at that instant; `zone2`, the same through a zone of capacity 2; `short_same`, end (a): B is granted at
51.581, when A starts its own leg `V -> U`; `turn_staged`, B waits through A's 4 s turn and is granted at 15.118;
`corr_stop` and `branch_stop`, B waits behind the idle A from 56.0 and is reported stuck at its first review (86.0,
`{"A": "IDLE"}`); with a fleet that makes way, A makes way at 56.0 and B follows when A's tail releases `R~b`.
`roomy_stop`, end (c): B is admitted at once at 53.0, as at the base. The other ends each on their own: (b) for a
re-claim granted while moving (ended 0.5 m into the arc on which the grant came, at 51.118); (b) for a profile without
limits (at the arrival, 51.25); (d) by `release` and by a `retain` that drops the segment, and a `retain` that keeps it
keeps the state (`tests/intralogistics/test_reservation.py`, `TestBothDirections`, with the wake-up: a wait blocked only
by the state is granted at its end; a wait blocked by the state and by an idle holder sleeps, is woken by the end,
reviewed at 70 and reported stuck; an end that blocked nothing leaves the review timer the same timer and a sleeping
wait asleep; a waiting holder in the state closes a deadlock; a wait blocked only by an idle, stranded or stalled holder
in the state is reported stuck).

**The monitor** (`tests/intralogistics/random_worlds.py`, Ruling A3-g). Three new checks, all reading the state through
`HoldingsLedger.both_directions` and the monitor's own body model:

- `LEDGER recorded direction against the body` (M1' of audit (ii)): every single-lane segment a body surely lies on and
  that its vehicle holds is recorded in the direction the body lies in, unless the vehicle is in the state there; once
  per stretch.
- `LEDGER both directions with another holder` (M2'): a holder in the state is the only holder of its segment.
- `LEDGER both directions outlived the body`: a state seen with its holder's body no longer on the segment as it
  arrived is a violation from the next instant on, and for a vehicle without kinematic limits seen so during an arc,
  from the instant after that arc's arrival; once per stretch. (As committed in `3e7e781`, "as it arrived" was read from
  the direction alone; fix round 1 reads it from the body's entry at the re-claim.)
- The monitor's own admission test (used for the deadlock and stuck checks) takes the rule in its own terms, from its
  body model and not from the ledger: on a single-lane segment, a holder whose body surely lies on the segment against
  its recorded direction blocks every other claimant (since fix round 1: a holder that claimed the segment again while
  its body lay on it, as long as the body surely lies on it by the entry it had then). Validated as T2 did
  (`scripts/val3.py`, a copy of the T2 reviewer's, seeds 4400000..4400999, 3,000 reservation runs): 0 differences in
  5,792,304 item tests (625 of them on a segment in the state, each blocked by its holder in both computations), 247,527
  reductions at wait events, 103,076 detections and 51,829 resolutions.
- `ADMITTED against a body on a segment` stays as it was. Seed 8101904 `fastest` with the rule undone (the state never
  begins) shows it at agv-g's grant, 471.171, before `PHYSICAL opposite directions on a segment`, with no older kind
  before it; the new check of recorded directions fires earlier, from the first re-claim of the run (30.619).
- Mutants, as tests on 8101904 `fastest`: a state that ends at the grant gives `LEDGER recorded direction against the
  body` first; an executor that never reports the old clear point, and a controller that ignores `cleared`, give `LEDGER
  both directions outlived the body` first; a state that admission does not read gives `ADMITTED against a body on a
  segment` first and `LEDGER both directions with another holder`. A state that never begins gives the check of recorded
  directions, then `ADMITTED`, then the physical conflict.

The pin of 8101904 `fastest` is now an ordinary test that expects no violation, and the test that asserted both kinds at
the base is replaced by the test with the rule undone. The marks of T4's holes stay. The item "a holder's recorded
direction against its own body" of T2's list "What the monitor still does not check" is checked now; the `passing` item
stays.

**What is not covered.**

- `passing` segments (Ruling A3-f): a re-claim moves the holder's extent to the other lane's count while its body lies
  in the first lane; suspected, not produced, and the monitor has no check of extents against bodies.
- A rigid body through a reversal: both body models are path models, and spec §6.6 says such a body is not tracked.
- The literal path model of `short_same`: when the rest point is nearer to the segment than half an extent, end (a)
  admits a vehicle behind the holder up to about 0.6 s before the old piece of the folded path has left the segment
  (Ruling A3-d).
- The look-ahead after a staged route (audit (ii), `turn_staged`): a route staged inside a travel keeps the look-ahead
  of the run before, so it re-claims farther segments before a turn; the rule closes the scenario without changing that.
- A holder that serves for a long time with a state left from an interrupted travel blocks without a report until it
  moves (the body is there).

**Fix round 1.** The review (seeds 4410000..4415999, a body model of its own) found the library sound and one gap of the
monitor, with five smaller points; the controller ruled on the gap (Ruling A3-n) and amended the spec (`1ebec11`).

- *A segment claimed again twice.* A holder in the state that is granted the segment again in the direction its body
  lies in (back, a stop, then forward again round a cycle) stays in the state until one of its ends: the ledger keeps
  it, and spec §7.2 now says so. The monitor read "the body as it arrived" from the direction, so it reported that state
  as outliving the body and its own admission test called a follower admissible. The monitor now takes it from the
  events: a `traffic.reserved` whose deltas move the AGV from one list of a single-lane segment to the other is a
  re-claim, remembered with the body's entry for the segment at that instant (its clear point and direction); while that
  entry stands, the holder blocks every other claimant in the monitor's admission test (if its body surely lies on the
  segment) and its state is not outliving the body (if its body lies on the segment, surely or within the band); the
  entry ends when the holder's own leg replaces it or the body clears it. `src/` does not change. The scenario is a test
  of the library (`test_travel.py`,
  `test_a_holder_granted_the_segment_again_in_the_direction_of_its_body_stays_in_the_state`: the second grant at 20.0
  leaves A in the state with the record forward, B waits from 20.5 with blocker A and is granted at 21.118, A's
  odometer at the old clear point) and of the monitor
  (`test_the_monitor_takes_the_body_as_it_arrived_from_its_entry_at_the_re_claim`: the monitor's state checks and its
  admission test for B, run on that world's events, give no violation and B's blockers equal the ledger's at every
  event; with the monitor of `1ebec11` it reports `LEDGER both directions outlived the body` at 20.5).
- *A state on a `passing` segment* is a violation now (`LEDGER both directions on a passing segment`, Ruling A3-f). The
  monitor watches every segment granted and still held (before, a segment left the watch at its grant if it had no
  state at that event, so a state begun after the event went unseen). Mutant test: a ledger that puts passing segments
  in the state, 4400003 `shortest`.
- *Two statements of this section were general and true only of my ranges* (corrected above): 4412263 `fastest` changes
  by an admission the state refused (agv-g waits behind agv-d on `g1_1~g2_2`; at the base that grant is `ADMITTED
  against a body on a segment` at 314.707, with no physical conflict after it), and 4411251 `fastest` has an end by (c).
  Both are tests: 4412263 `fastest` ends with no violation, and with the rule undone gives the check of recorded
  directions and `ADMITTED` (at 314.707 on `g1_1~g2_2`) and nothing else; 4411251 `fastest` with end (c) dropped gives
  `LEDGER both directions outlived the body`.
- The guide's end "it has travelled far enough for its tail to clear the segment" counts, for a profile without
  kinematic limits, at the arrival that ends that arc (tested at 51.25); the docstring of `TrafficController.cleared`
  follows the amended §7.1 (the call may name segments without the state).

Mutants with the monitor of the round (`scratch-a3-T3/scripts/mutants.py`, `out/r1-mutants.txt`), on 8101904 and
4411251 `fastest`: state never begins, the check of recorded directions on both (then `ADMITTED` and the physical
conflict on 8101904); ends at once, the same; no end (b), `cleared` ignored and no end (a), `LEDGER both directions
outlived the body` on both; no end (c), the same on 4411251 (8101904 has no such end); not read, `ADMITTED` and `LEDGER
both directions with another holder` on 8101904. No mutant, no violation.

Reruns with the monitor of the round: seeds 4400000..4401999, both controllers, 12,000 runs, every one returned, two
violating runs, 4400263 and 4401673 `congestion`, `open wait with no report`, the same as with the monitor of
`1ebec11`; fingerprints and whole records of all 12,000 identical to those of the measured head of T3. Seeds
4410000..4410999, reservation, 3,000 runs, 4,141,309 events, every one returned, three violating runs, 4410006
`shortest`, 4410659 `fastest` and `shortest`, `open wait with no report`, the same with the monitor of `1ebec11`, and
the same fingerprints. The monitor's reduction against the controller's (`scripts/val3.py`, seeds 4400000..4400999,
3,000 reservation runs): 0 differences in 5,792,304 item tests (625 on a segment in the state), 247,527 reductions,
103,076 detections and 51,829 resolutions. Summed wall of the 3,000 runs of 4410000..4410999: 1,537.0 s against 1,508.2
s with the monitor of `1ebec11` (+1.9 %, run one after the other).

**What the monitor still does not check (T3).**

- A state that outlasts the body by less than the gap between two events of distinct later instants: the exactness check
  sees a state only at domain events and reports it at an event of an instant later than the first event that saw it
  (the review counted 31 of 163 runs under the mutant without end (b) where this hides the outlast).
- The test with the rule undone clears the state after the grant's event, so the monitor sees the state at that event:
  the check of recorded directions reports 19 of the 20 re-claims of single-lane segments of 8101904 `fastest`.
- `passing` segments beyond the check above: the extents of a re-claim against the bodies in their lanes.

### T4. Every stuck wait is reported

Decided by the maintainer on 2026-11-25: before 0.18.0 is tagged, every wait under `ReservationController` is granted,
resolved by the policy, or reported (`traffic.wait_stuck`, or an unresolved `traffic.deadlock` that lists it). Base
`6f57185` (the traffic spec amended for this task: §7.1 `idle`, §7.9 "Waits with no review left", §15). Scratch:
`.superpowers/sdd/2026-11-23-layout-only-models/scratch-a3-T4/`.

**The cause** (audit (i), `a3-T1-i-audit.md`). The stuck rule of §7.9 was evaluated on four occasions only: the end of a
timed review, a stranding (which reviews at once), `idle()` for the waits an AGV blocks by what it holds, and the chain
of a new report. A wait can become stuck through events that reach none of them, and a sleeping wait is woken only by a
grant or a release.

**The six holes**, each now a test of `tests/intralogistics/test_deadlock.py` (`TestWaitsWithNoReviewLeft`) or of
`test_travel.py`, built from the audit's scenarios; each test failed at the base (below) and asserts the report, its
instant and its blockers, the number of `on_blocked` calls and the review timers, which are those of the base:

| Hole | Mechanism | Scenario as a test | Reported now |
|---|---|---|---|
| R | the report chain stops at a wait that is already reported, so a wait behind it that sleeps is never examined; three entries: an idle AGV that does not move, a stranding, a blocker that starts to wait | `test_behind_a_reported_wait_when_its_blocker_comes_to_rest`, `..._whose_blocker_starts_to_wait`, `..._when_its_blocker_strands` | Y at 70 (`idle`), at 65 (the stop of I), at 70 (the stranding) |
| C | the same stop at a member of an unresolved set, for a wait behind the member through a gate that detection does not waive | `test_behind_a_member_of_an_unresolved_deadlock_through_its_gate` | Y at 40, `idle(H)` |
| A | a gate waiver removes a blocker with no grant or release (seed 7702388) | `test_when_detection_waives_a_gate` | C at the stop that waives (40), and the chain reaches X and E |
| B | a gating claim ends without a grant: a withdrawal, a staged reaction | `test_when_a_gating_claim_is_withdrawn`, `test_when_a_gating_claim_ends_with_a_staged_reaction` | C at the withdrawal (40); C after the review that gives up (60) |
| E | a wait with no review (`StandardTrafficPolicy(reroute_after=None)`) that opens stuck | `test_a_wait_under_a_policy_without_reviews_that_opens_stuck` | Y at its stop (0) |
| G | a blocker stalls and no call reaches the controller | `test_a_wait_behind_a_vehicle_that_stalls_is_reported_at_the_stall` (through the executor) | B at the stall (140.208), A `STALLED` |

**The rule and why it was preferred** (Ruling A3-i; traffic spec §7.9). The audit prototyped four fixes and paired each
with the base on 6,000 reservation runs (seeds 4100000..4101999): the smallest patch for hole R alone (`p_min`, the
chain passes through reported waits) changed 1,120 fingerprints and added 566 stuck reports, because it reports waits
that have just stopped and would be granted before their first review; counting waivers, withdrawals and staged
reactions as changes (`p_wake`) moved vehicles and end times in 59 runs; a check of every sleeping wait at every entry
(`p_sweep`) brought 30 reports forward in 17 runs, 29 of them by the 30 s of a review delay; `p_sweep2`, the same check
that leaves to a scheduled review the waits it will reach, changed 1 fingerprint (the run with a hole) and nothing else.
The rule: at every controller entry that is not a plain grant or release, every open wait that carries no report, has no
review scheduled and is no member of a remembered unresolved set is checked for the stuck condition, with its blockers
recomputed, unless a wait ahead of it, through waiting blockers whose waits carry no report and are no member, has a
review scheduled. A wait found stuck is reported as every report is, with the chain of the waits behind it. It is not a
review: no policy call, no review counted, no timer, no holding changed.

**What changed in the code.**

- `ReservationController._check_asleep` (`reservation.py`) runs once at the end of `stopped`, `withdraw`, `idle` and a
  review on its timer (`_timed_review`); the staged reactions and gate waivers happen inside those entries. One pass:
  the blockers of the sleeping waits and of every wait they reach through waiting blockers are computed once; a wait
  with no blocker, or with a blocker that is none of stalled, idle, stranded or waiting, is not stuck, and the mark goes
  to the waits behind it; a second mark goes from the waits that have a review timer to the unreported waits behind
  them; the sleeping waits left are reported in queue order through `_report_stuck`, unchanged. Nothing is computed when
  no wait sleeps. Every collection is a list or an insertion-ordered dict: the order does not depend on hashing.
- `_stuck` returns None for a wait with no blocker and for a wait that reaches one (before, an empty mapping: a wait
  admissible but not granted would have been reported; no caller could pass one before this task).
- `idle` asks only an idle AGV to make way: a stalled AGV is not asked (the policy's `make_way_target` is not called for
  it) and the waits it blocks are checked at once, as for an idle AGV that does not move.
- The travel executor's `_stall` withdraws a request of the trip that is still pending and calls `controller.idle(agv)`
  (`FreeFlowController` does nothing for either). The withdrawal settles the variant of hole G that audit (i) 3.7 read
  from the code: a look-ahead claim asked for before the run stalled (here while moving towards a node where the AGV
  rests to turn) stayed pending for ever, was never a wait, and after an interrupt of the stalled AGV its next travel
  raised `RuntimeError: ... already has a pending traffic request`
  (`test_a_stalled_vehicle_withdraws_the_claim_it_had_asked_for_ahead`; reproduced at the base with
  `scripts/stall_pending.py`).
- The fairness gate (`_gating`) is as at the base. (As committed in `2d594d5` it read the earlier pending claims lazily,
  at the first segment or corridor the claim needs; the review proved the two forms equivalent and showed that the lazy
  read helps only claims of nodes, not the claims a travel makes; fix round 1 restored the base text, Ruling A3-o.)
- The docstrings of `TrafficController.idle`, `TrafficController.cleared` (the second kind of segment named without a
  state, T3's review) and `TrafficWaitStuck` (the hole-R runs contradicted "at the instant their chain becomes stuck").
- `cleared` (T3) needs no check: an end of the state changes the blockers of a pending claim only when the state made
  that claim inadmissible, and exactly then it counts as a change and scans, which wakes every sleeping review, as a
  release does.

**Before and after**, base `6f57185` against the head, `ReservationController`, the three routers, paired by seed and
router; the interval is the 95 % interval of the sum of the paired differences; each tree with its own monitor (the
head's has the one line of T3's review, below). CPython 3.14.3, 10 worker processes (`scripts/measure.py`,
`scripts/pair.py`, `out/pair-*.txt`).

| | 4100000..4101999 (audit, 6,000 runs) | 2,887 filtered worlds of 4110000..4118999 (8,661) | 4500000..4502999 (fresh, 9,000) |
|---|---|---|---|
| Runs returned, base / head | 6,000 / 6,000 | 8,661 / 8,661 | 9,000 / 9,000 |
| Runs with a violation, base / head | 1 / 0, `open wait with no report` | 11 / 0, the same kind | 6 / 0, the same kind |
| Open waits with no report at the end | 1 / 0 | 15 / 0 | 7 / 0 |
| `traffic.wait_stuck` | 13,425 / 13,426, +1 [-1.0, +3.0] | 35,560 / 35,581, +21 [+9.7, +32.3], up in 17 runs | 19,253 / 19,262, +9 [+2.5, +15.5], up in 8 |
| Orders completed | 76,271 both | 104,190 both | 114,464 both |
| Waits ended by a reroute | 21,399 both | 53,613 both | 30,893 both |
| Deadlocks give up / reroute / step aside / unresolved | 8,966 / 18,396 / 47,635 / 5,628 both | 23,967 / 45,300 / 107,535 / 17,043 both | 13,611 / 26,488 / 70,285 / 7,470 both |
| Make-way moves | 34,495 both | 73,293 both | 48,995 both |
| Strandings | 5,743 both | 8,375 both | 8,740 both |
| End times | equal in every pair | equal in every pair | equal in every pair |
| Fingerprints changed | 1 | 21 | 10 |
| Records that differ otherwise than by stuck reports and their log lines | 0 | 0 | 0 |

Every interval not shown is +0 [+0, +0]. On the audit's 6,000 runs the one changed run is the audit's: 4100399
`congestion`, one more `traffic.wait_stuck` (agv-d at 531.795, behind agv-e), as with `p_sweep2`. On the filtered sample
the 21 changed runs are exactly the 21 runs in which the audit's detector found an episode at the base (the 11 that end
with one and 10 whose episodes ended earlier); in each, the stuck reports added are the waits of the episode, at the
instant it began, and in five of them a report the base made later at a review comes earlier (4111173 `shortest` agv-d
30 s earlier, through the chain of agv-b's report; 4111267 `congestion` agv-a at 424.305 instead of 611.923; 4117253
under the three routers, agv-a at 220.748 instead of 501.899) (`out/stuckdiff-filtered.txt`). On the fresh sample the 6
violating runs at the base are 4502016 `fastest` (agv-b, agv-d), 4502163 `congestion`, 4502216 `fastest`, 4502454
`shortest`, 4502619 `fastest`, 4502628 `shortest`, all hole R by the audit's detector and `classify.py` (the `idle()`
form in the first, third and fifth, the stranding form in the others; `out/classify-freshchanged.txt`); of the 10
changed runs, 9 have an episode at the base (`out/lat-freshchanged-base.log`; in 4500084 `congestion` and 4500695
`fastest` the report comes 77 s and 98 s earlier) and one, 4500073 `shortest`, does not: there agv-g gives up every 2 s
in a loop, and at 560.174 it is idle for the instant between its give-up and its next order; `idle(agv-g)` finds agv-e's
sleeping wait stuck by the rule (agv-a and agv-d wait behind agv-g) and reports it, as the base's `idle` reports the
waits an idle AGV blocks directly at that instant. The detector looks at the end of each instant, when agv-g travels
again, so it saw no episode; the strict monitor checks the report at its event and accepts it.

**Free flow is byte-identical**: the 3,000 free-flow runs of seeds 4500000..4500999: records, initial and final state,
end time and fingerprint equal in every pair (`out/pair-free.txt`).

**The detector** of audit (i) (`latent2.py`: an open wait stuck by the rule, unreported, with nothing scheduled that
would report it, at the end of any instant), on the head: 0 episodes in the 6,000 runs of 4100000..4101999, the 8,661
runs of the filtered sample and the 9,000 runs of 4500000..4502999 (the audit counted, at `0ffc5b6`, 1 episode in 1 run
in the first and 34 episodes in 21 runs in the second; at the base, 19 episodes in 9 runs among the 30 runs of the ten
fresh worlds that changed). Every run of the three samples returned and none ended with an open wait without a report.

**The recorded safety ranges** (`benchmarks/layout_safety.py --worlds 1000 --jobs 10`, six runs per world), at the head:
100000..100999, 6,000 runs, 6,570,204 events (four more than at `229ea61`: the stuck report and its log line in 100281
`shortest` and `fastest`), 0 raised, 0 cut, 0 queue not empty, 0 violations, exit status 0 (184 s); 2000000..2000999,
6,000 runs, 6,720,030 events (equal), 0 of each, exit status 0 (185 s).

**The seeds of the ledger**, each `benchmarks/layout_safety.py --worlds 1 --first <seed>` (both controllers, three
routers), at the head, every one exit status 0 with no violation, no run raised and none cut: 2000228, 52118, 8103801,
9202320, 9202736, 7702388, 8101904, 4412263, 4411251; the worlds of the hole-R runs 8102359, 8103231, 100281, 7700058,
7701490, 4100399, 4102230, 4103675, 4106591, 4106711, 4109344, 4109857, 4105806, 4109073, 4111428, 4114572, 4115147,
4115267, 4117095, 4117325, 4118296, 4118695, 4300647, 4301217, 500064, 500198, 4310551, 4312795, 4315085, 4316274,
4320963, 4317874, 4410006, 4410659, 4411303, 4411325, 4411943, 4412497, 4414205, 4414470; and the violating worlds of
T3's ranges 4400263, 4401673, 4402051, 4402618, 4402902, 4200171, 4201442 (`out/seed-*.out`).

**The pins.** The marks `xfail(strict=True, raises=AssertionError)` of the gate waiver (7702388 `shortest`) and of the
sixteen runs of hole R are removed: they are ordinary tests that assert a run with no violation. Nineteen runs found
since are added to the second test (`FOUND_SINCE`): 4300647 `fastest`, 4301217 `shortest`, 500064 `congestion`, 500198
`fastest`, 4310551 `congestion`, 4312795 `shortest`, 4315085 `fastest`, 4316274 `shortest`, 4320963 `congestion`,
4317874 under the three routers, and the seven of T3's review on 4410000..4412999 (4410006 `shortest`, 4410659 `fastest`
and `shortest`, 4411303 `shortest`, 4411325 `congestion`, 4411943 `congestion`, 4412497 `fastest`); each ends with `open
wait with no report` at the base. T2's test of the eager mutant (the stuck rule on direct blockers only) now runs on all
36 runs, and the monitor flags every one (`stuck report of a wait that reaches a blocker not at rest`). No pinned
digest, fingerprint or fixture moves: the audit had found none moving under `p_sweep2`, and `SAFETY` and `EXERCISED`
pass unchanged.

**Mutants** (`scripts/mutants.py`, `out/mutants.out`): the check dropped at one entry at a time, against the scenario
tests and the 36 pinned runs. At `stopped`: the scenarios of the blocker that starts to wait (R), the waiver (A) and the
policy without reviews (E) fail. At `withdraw`: the withdrawal (B). At `idle`: the three `idle()` and stranding forms
(R, C) and 22 of the 36 pinned runs end with `open wait with no report` (in the other 14 a later entry reports the wait
before the run ends). At the timed review: the staged reaction (B). The stall scenario is caught by `idle`'s own check
of the waits the stalled AGV holds; without the executor's call it fails (the RED run).

**Cost** (fix round 1, Ruling A3-o; `scripts/bench3.py legs`, the reviewer's bench: n AGVs waiting one behind the
other behind a head that serves, each claiming the leg to the next node as the executor does, so that every claim names
a segment its AGV does not hold and the gate of each wait reads every earlier pending claim; the median over five fresh
worlds of the median of 20 calls of `idle()` of an AGV that blocks nobody, with every wait asleep and with every wait
awake (before the first reviews), and of the `stopped()` of one more waiter; base `6f57185` and the head of fix round 1
alternating, three rounds, the medians of the three; `out/r1-bench-*.out`):

| n (sleeping waits = blocker edges) | `idle()`, no wait asleep: base, head | `idle()`, all asleep: base, head, ratio | `stopped()`: base, head, ratio |
|---|---|---|---|
| 10 | 0.0123 ms, 0.0125 ms | 0.0123 ms, 0.076 ms, 6.2 | 0.151 ms, 0.216 ms, 1.4 |
| 20 | 0.0244 ms, 0.0247 ms | 0.0246 ms, 0.232 ms, 9.4 | 0.250 ms, 0.457 ms, 1.8 |
| 40 | 0.0486 ms, 0.0495 ms | 0.0485 ms, 0.814 ms, 16.8 | 0.458 ms, 1.27 ms, 2.8 |
| 80 | 0.101 ms, 0.101 ms | 0.101 ms, 2.94 ms, 29.3 | 1.08 ms, 3.89 ms, 3.6 |
| 160 | 0.202 ms, 0.204 ms | 0.200 ms, 11.5 ms, 57.3 | 2.30 ms, 13.5 ms, 5.9 |

With sleeping waits the check is quadratic in them for the claims a travel makes: the head's `idle()` grows x3.0, x3.5,
x3.6, x3.9 per doubling, because computing the blockers of each wait reads every earlier pending claim for its gate, as
the scan does (traffic spec §15 as amended in `e9f4b85`). With no wait asleep the check costs nothing measurable (the
head equals the base within 2 %). The rounds agree within 9 %. The one-minute load average was 7.8 to 13.2 when the
rounds started (the decay of the paired runs just before; nothing else of this task ran during them). As committed in
`2d594d5`, with the gate reading the earlier claims lazily, the audit's bench of claims of nodes measured 0.228 ms for
`idle()` at n = 80 and grew linearly; that holds only for claims of nodes, and the record said linear in general, which
was wrong (review of T4). The prototype `p_sweep2` took 92 to 102 ms at n = 80 (audit, claims of nodes, loaded machine).

On the random worlds: `benchmarks/layout_safety.py --first 100000 --worlds 200 --jobs 10` (1,200 runs, 1,361,246 events
in every round, no violation), base and head alternating in one session, three rounds: 41.33 s and 42.46 s, 42.03 s and
41.75 s, 41.13 s and 40.17 s (means 41.50 s and 41.46 s; the one-minute load 10.0 to 19.8 before each, mostly the
workers of the round before). `benchmarks/intralogistics.py --mode none --shifts 2 --warmup 1 --repeat 3` (free flow),
alternating twice: medians 0.483 s and 0.482 s, 0.477 s and 0.477 s.

**`PYTHONHASHSEED`**: the scenario tests pass under `PYTHONHASHSEED` 1 and 2, and the 300 runs of seeds 4500000..4500099
under each have the fingerprints of the measured head run (whose worker processes had random hash seeds). The check
takes the waits in queue order and keeps its marks in lists and insertion-ordered dicts.

**What remains outside the rule.**

- A wait behind an AGV that serves for ever (a pick waiting for stock) is not stuck by §7.9 and is not reported; no
  random world ends that way.
- The limit of a policy that asks for no review (Ruling A3-p; traffic spec §7.9 as amended in `e9f4b85`): such a wait
  that becomes stuck through a grant, a release or the end of a both-directions state is reported at the next stop,
  withdrawal or rest of any AGV, not at that instant. A run cut by `until` in between ends without the report, and so
  would a run in which no such entry follows (the vehicle that released then serves for ever). The review measured it:
  under `StandardTrafficPolicy(reroute_after=None)`, seeds 4510000..4510399, 88 of 1,200 runs have such a wait (stuck,
  unreported, nothing scheduled); in 70 of them a report comes late, by up to 38.8 s; in the others the wait stops
  being stuck first (the reviewer's measurement, `reviewer-scratch-a3-T4/`). The default policy reviews every wait: a
  release wakes its review. Pinned as it is by
  `test_under_a_policy_without_reviews_a_wait_made_stuck_by_a_release_is_reported_at_the_next_entry` (stuck at 10,
  reported at 20), which fails under a mutant that checks after every release.
- Requirement 7 of the brief (a former member whose own review sleeps after its set is forgotten): built as
  `test_a_former_member_asleep_when_its_set_is_forgotten`. When the set is forgotten by the withdrawal of a member that
  still travels, the waits left are not stuck; when that member rests idle, `idle` reports the former member, by the
  existing check of the waits an idle AGV blocks. It passes at the base too: covered by an existing path, no seventh
  hole. The new check covers the same wait if the member is already at rest when the set is forgotten.

**What the monitor still does not check** (T2's list, brought up to date). Checked now: the both-directions state stays
for a segment claimed again twice (T3's review): in the check of recorded directions, a holder whose body lies on the
segment as it arrived before a re-claim must be in the state (one condition in `_check`; test
`test_a_state_that_ends_when_the_segment_is_granted_again_is_a_violation`, which the monitor of `6f57185` passes with no
violation; the whole monitor now runs on that world, built by its own constructor from a spec of the world). Still not
checked: the spacing of followers on the bodies; the gate part of a stuck report; `passing` segments during a reversal; the review timer and the pending claim at quiescence from events; a wait
stuck and unreported for a while and reported later (the monitor sees the end of a run and each report; the episode
detector, which reads the controller, is the check, run for this task's samples); and a `STALLED` blocker in a random
run (no vehicle of the generator stalls; the controller and executor tests cover it).

**Fix round 1.** The review (seeds 4510000..4519999, its own rule, a fuzz of controller worlds) found the rule correct,
report-only and complete under the default policy, and three gaps, each fixed:

- The cost was stated as linear; it is quadratic for the claims a travel makes (above). The gate is back to its text at
  the base (`git diff 6f57185 -- src/simulatte/intralogistics/reservation.py` has no hunk in `_gating`). Rerun against
  the fingerprints of `cb7e520`: the 6,000 reservation runs of 4100000..4101999 and the 3,000 of 4500000..4500999 (three
  routers) have identical records, 6,000 and 3,000 of them; the 900 free-flow runs of 4500000..4500299 too
  (`out/r1-pair-*.txt`).
- Three behaviours had no test that failed without them; each has one now, failing under its mutant and passing
  without (`scripts/mutants_r1.py`, `out/mutants-r1.out`): a stalled AGV of a fleet that would accept is not asked to
  make way (`test_a_stalled_agv_is_not_asked_to_make_way`: no `make_way_target` call, no `fleet.make_way` call); a
  wait asleep behind a reported wait whose blocker stalls is reported, the stalled AGV counting as at rest in the check
  (`test_behind_a_reported_wait_whose_blocker_stalls`, Y at 70); a member's review does not cover the waits behind the
  member (`test_behind_a_member_that_keeps_its_review_timer`, Y at 55, not 60).
- The limit of a policy without reviews is stated (above), in the guide too, and pinned by a test.

The "at rest" classification of the stuck rule, coded twice before, is one helper used by `_stuck` and by the check;
the guide's sentence on `TrafficController.idle` names the executor's call at a stall.


### T5. Two fleet items

Decided by the maintainer on 2026-11-25: items (d) and (e) of "Open after Assignment 2" are fixed before 0.18.0 is
tagged. Base `3aac5c4`. Commits: `db9af3d` (the monitor), `186d4ab` (item 1), `d938852` (item 2), and the
documentation commits. Scratch: `.superpowers/sdd/2026-11-23-layout-only-models/scratch-a3-T5/` (`scripts/measure.py`,
`scripts/compare.py`, `scripts/turns.py`, `scripts/firstdiff.py`, `scripts/monitor_mutants.py`; outputs under `out/`).

**First, the monitor: a trip to park holds a slot.** Change 5 of T2 checks that a held slot belongs to an AGV at rest on
the area's node or travelling there, and that a slot is not given back too early; nothing checked that a trip to park
holds a slot at all (T2's review: a mutant in which no trip to park takes a slot changed 558 of 900 runs and none was
flagged). Item 1 makes AGVs start such trips after a charge, so the rule came first (`db9af3d`): at the end of the
instant of an `agv.routed` with reason `travel`, `make_way` or `clear_node`, an AGV of a fleet that uses the parking
areas, routed to a parking node that has none of the chargers it uses, that travels empty there at the end of the
instant and carries no live order, holds a slot of an area on that node; otherwise `trip to park without a slot`. A
charge on a parking node takes no slot, hence the nodes with a charger are left out; a second fleet without parking
areas makes way onto the first fleet's parking node without a slot, rightly, hence only the AGVs of a fleet that uses
the areas; and an AGV keeps a completed order until its mission's cleanup, which comes after the trip to park, so an
order is live only while it is not terminal and names the AGV. Tests (`test_random_worlds.py`): trips to park that
take no slot are reported at the first such trip (619 `FreeFlowController` at 86.385, g0_0, agv-e; 88
`ReservationController` at 137.397, c03, agv-e), and the residue of the give-back exception (T2's `mymut2.py`: after a
slot given back, a trip to park on the same node in the same instant takes none) is reported in 4316044 `shortest`
(305.459, g0_2, agv-c) and nothing else. All three fail under the monitor of `3aac5c4` (no violation there).

Validated before item 1, on the source of `3aac5c4` with this monitor, on the worlds of the measurement below (60,012
runs): no violation of any kind. On fresh seeds 4600000..4600149 (900 runs, both controllers, three routers, the
source of `d938852`) the rule applies 5,356 times in 580 runs, with no violation; the mutant in which no trip takes a
slot changes 610 runs and the monitor flags 577 (337 of 352 under `ReservationController`, 240 of 258 under
`FreeFlowController`). In the 33 it does not flag no trip to a parking node without a charger travels under the
mutant: the areas never fill there, so the policy sends every AGV to the area on a charger's node (in 30), or the slot
is taken and given back in one instant because the trip finds no route (in 9; some runs have both). The residue
mutant changes 4 runs and the monitor flags 3; in the fourth (4600122 `congestion`) the new trip strands in the
instant it starts, so only the parking events differ.

**Item 1, the cause.** A trip without an order (a repositioning, a clearance of a service node, a make-way move) whose
battery cannot cover the next arc turns to charge inside the travel (`_lacking_energy`), gives its slot back at the
turn (B2), charges, and then returns `RETRY_FROM_CURRENT_POSITION`; no caller retried, so the AGV became `IDLE` on the
charger's node: the trip was not resumed, the repositioning policy was not asked again, and the service node was not
cleared, because clearing runs only where the policy named no node, after a mission.

**The rule** (Ruling A3-m, as amended in fix round 1 by Rulings A3-q and A3-r). After such a charge, when the trip
ends elsewhere than its target and no order is pending, the AGV goes once through the decision of an AGV that ends a
mission on that node: the battery check, the repositioning policy asked again (a parking slot taken at the decision),
and the clearance of a service node where the policy names none and the controller controls occupancy. The trip is not
resumed. With an order pending the AGV becomes `IDLE` where it charged and the pending-queue check offers it the order,
as at the base. A trip of the decision that charges on its way again is followed by no further decision: the AGV
becomes `IDLE` where it charged. (As committed in `186d4ab` the decision ran with orders pending and could follow
itself without bound; see "Fix round 1".)

**The change** (`186d4ab`). The post-mission decision of `_run_mission` moves, unchanged, into
`FleetCoordinator._rest`, which `_run_mission` calls; `_idle_travel` calls it too when its travel returned
`RETRY_FROM_CURRENT_POSITION`, a charge was completed during the travel and the AGV does not stand on the target.
`_charge_agv` counts the charges it completes (at the station, or by the `on_low_battery` callback in its place) in
`_charges`, which `_idle_travel` reads before and after the travel. Since fix round 1 `_idle_travel` also requires an
empty pending queue and that the AGV is not already in such a decision (`_after_charge`). A make-way move enters the
decision through
`_idle_travel` and then ends as before (`IDLE`, the hooks, the pending-queue check, `idle`). Unchanged: a charger on the
area's own node (the AGV stands on the target: it keeps the slot and rests parked, not asked again), a charge during an
order (the order's travel resumes), what `idle` does for the waits the AGV blocks, charging and stranding themselves.

**Item 2, the cause.** In `_lacking_energy`, after the diversion, an AGV whose level still does not cover the arc at
`current` (the node where the diversion started) strands, and `agv.stranded` named `current` with the reason
`insufficient_after_charging` whether or not the diversion had reached the charger. The diversion stops short of the
charger when its level turns critical on the way (the travel returns at a node other than the charger) or its travel
finds no route.

**The reasons** of `agv.stranded`: `no_reachable_charger`, raised in `_lacking_energy` when no charger is reachable
from the node or a diversion cannot leave the node it started from (travel.py, the end of `_lacking_energy`; an
ordinary stranding on the way is this one, raised by the travel of the diversion at its own node); and
`insufficient_after_charging`, raised after a diversion. Traffic spec §11.2 lists neither (its table has no row for
`agv.stranded`). Neither is true of a diversion that stopped short: nothing was charged, and in two of the three runs
examined (8100301 `fastest`, agv-a on c07; 52596 `congestion`, agv-f on c04) a charger is reachable from the node
where the AGV stopped (c08 and c05), by the coordinator's own estimate; in 8100713 `fastest` none is. So a new reason,
`charger_not_reached`.

**The change** (`d938852`). The event names the node the AGV stands on (`agv.current_node` at the event), with
`insufficient_after_charging` when a charge was completed during the diversion (`_charges` before and after, so a
charge of a nested diversion counts) and `charger_not_reached` otherwise; the log line names the same node and cause.
When and where an AGV strands, what it holds, the state and the `idle` call are unchanged. `agv.stranded` carries no
deltas. Readers of the reason: the docstring of `AgvStranded` and the events table of `docs/tutorials/logging.md`, both
updated; no collector, KPI, Studio view or TypeScript module reads it (`git grep` over `src`, `studio` and `docs`).
Tests (`test_stop_requests.py`, both controllers): a diversion that stops short of the charger (`charger_not_reached`
on N0, where it stands), one that charges and still cannot cover the arc (`insufficient_after_charging` on the
charger's node), and a nested diversion that charges at a second charger (`insufficient_after_charging` on C2).

**Measurement.** Each commit against its parent: `db9af3d` (the source of `3aac5c4` and this monitor), `186d4ab` and
`d938852`, each a `git archive` copy first on `PYTHONPATH` with its own monitor (the same monitor for the three).
Worlds: the 7,002 of B2's review (seeds 3300000..3301999, 640000..641999, 911000..911999, 100000..100999,
2000000..2000999, 7702388 and 8101904) and 3,000 fresh (4600000..4602999); both controllers, three routers: 60,012 runs
per tree, paired by seed, controller and router; the interval is the 95 % interval of the sum of the paired
differences. CPython 3.14.3, Apple M4 Pro, 9 or 10 worker processes. Every run of the three trees returned with an
empty queue (`env.run()` without `until`), none raised, none was cut, and none has a violation of the strict monitor.

Item 1 as committed in `186d4ab`, before fix round 1 (the code since then is measured under "Fix round 1"):

| Measure | `db9af3d` | `186d4ab` (item 1) | Difference, 95 % interval | Runs that differ |
|---|---|---|---|---|
| runs whose fingerprint changed (reservation / free) | | | | 80 (74 / 6) |
| orders completed | 903,327 | 903,317 | -10 [-32.7, +12.7] | 29 |
| orders failed | 228,040 | 228,072 | +32 [-19.0, +83.0] | 27 |
| `parking.entered` | 348,993 | 349,038 | +45 [+26.1, +63.9] | 57 |
| `parking.left` | 293,976 | 294,002 | +26 [+8.5, +43.5] | 47 |
| time AGVs are idle on a service node of their fleet | 39,605,237.4 | 39,604,002.1 | -1,235.3 [-2,311.3, -159.3] | 48 |
| make-way moves | 162,124 | 162,119 | -5 [-32.2, +22.2] | 20 |
| clearances | 116,958 | 116,983 | +25 [+8.7, +41.3] | 23 |
| `charging.started` | 51,534 | 51,546 | +12 [+2.4, +21.6] | 21 |
| strandings (`agv.state_changed` to `STRANDED`) | 75,850 | 75,850 | 0 [-29.9, +29.9] | 27 |
| `agv.stranded`: `no_reachable_charger` / `insufficient_after_charging` | 61,719 / 52 | 61,716 / 52 | -3 [-25.9, +19.9] / 0 | 28 / 0 |
| `traffic.wait_stuck` | 65,529 | 65,496 | -33 [-51.3, -14.7] | 33 |
| `traffic.deadlock` resolved / unresolved | 363,850 / 26,129 | 363,814 / 26,135 | -36 [-152.8, +80.8] / +6 [-5.4, +17.4] | 29 / 7 |
| end time (sum) | 42,196,765.9 | 42,198,294.6 | +1,528.7 [+285.3, +2,772.2] | 40 |

Of the 80 changed runs, 49 are B2's worlds and 31 fresh. Every one has a trip without an order that charged on its way
and ended away from its target, and its first event that differs from the parent's (each run replayed on both trees,
`scripts/firstdiff.py`) comes at the end of that charge: the decision after the charge, which sends the AGV to park
(`parking.entered`, 73 runs) or clears the charger's node or travels to a node that is no parking area (`agv.state_changed`
to `TRAVELING_EMPTY`, 7). 92 runs of the parent have such a turn; in the other 12 the decision does what the AGV did
(the policy names no node and nothing is cleared). The 6 changed free-flow runs (3300052 and 4600700, three routers
each) each have such a turn; the other 29,994 free-flow runs are identical. No changed run has a turn whose charger is
on the trip's target node (no measured turn has one; the hand-built test covers it). In 27 of the 80 the parent's AGV
was taken at once, at the end of the charge, by a pending order or a make-way move; now the decision runs first and
sends it to park, as an AGV that ends a mission with an order pending is, and the order waits for the trip (seed 619,
below; 23 orders and 4 make-way moves). Orders completed and failed change within their intervals; fewer waits are
reported stuck, and the end times of the runs that differ add up to more.

Item 1's own count, the turns of a trip without an order that charged on its way (22 repositionings, 59 make-way moves
and 14 clearances at the parent, 89 under `ReservationController`; 95 at the parent and 96 after, in 92 runs), with where
the AGV is at the end of the charge's instant:

| | `db9af3d` | `186d4ab` |
|---|---|---|
| idle on the charger's node at the end of the charge's instant | 59 | 8 |
| idle on it 30 s later / 120 s later / at the end of the run | 28 / 5 / 15 | 3 / 2 / 3 |
| stayed idle there for a while (to the end of the run) | 47 (15) | 7 (2) |
| longest stay / all stays | 364.4 / 2,605.1 | 249.9 / 393.4 |

Each of the 8 after the fix is an AGV for which the decision names nothing: the policy names no node and clearing is
off in that world (640018 `congestion`, 641972 `congestion`, 100885 `fastest`) or the standard search finds no node
(911926 `congestion`, 100407 `shortest`, 4600374 `shortest`, 4602221 `congestion`, 4602476 `congestion`), as for an
AGV that ends a mission there (a scratch probe of `_rest` at that instant, `out/probe_rest.out`).

| Measure | `186d4ab` | `d938852` (item 2) | Difference, 95 % interval | Runs that differ |
|---|---|---|---|---|
| runs whose fingerprint changed (reservation / free) | | | | 51 (21 / 30) |
| `agv.stranded` `insufficient_after_charging` | 52 | 0 | -52 [-66.4, -37.6] | 51 |
| `agv.stranded` `charger_not_reached` | 0 | 52 | +52 [+37.6, +66.4] | 51 |
| `agv.stranded` naming a node other than where the AGV stands | 52 | 0 | -52 [-66.4, -37.6] | 51 |
| every other measure of the table above, and the number of events | | | 0 | 0 |

Every one of the 51 runs has a stranding after a diversion that did not charge at the parent, and, replayed on both
trees, differs from the parent only in the fields `node` and `reason` of `agv.stranded` and the message of its log line
(same events otherwise, same length). None of the 52 `insufficient_after_charging` events of the parent followed a
charge, and all 52 named the node where the diversion started.

**Termination** (since fix round 1). The decision after a charge on the way is made at most once per such charge: a
trip of it that charges on its way again ends with the AGV `IDLE` where it charged, as at the base, so a move without an
order charges on its way at most twice (in the move, and in the trip of the one decision that follows) and
`_idle_travel` and `_rest` call each other at most once. The decision does not start a trip when the policy names the node
the AGV stands on (a charger's own node included), when it names none and clearing is off, or when the standard search
finds no node: the AGV stays, as after a mission (tests
`test_after_a_charge_on_the_way_an_agv_with_nowhere_to_go_stays_on_the_charger`, three cases, one charge each). Where
the node the decision names lies beyond the reach of a full charge, the AGV charges a second time and rests on the
charger (`test_after_a_charge_on_the_way_the_decision_is_made_once`: the policy, a parking area and the clearance, under
the controllers that apply; and through two chargers, `..._also_through_two_chargers`); every such run returns. (As
committed in `186d4ab` the decision followed itself without bound there, and `env.run()` raised `RecursionError` after
about 490 rounds; the test that pinned it with `until` is replaced.) An order's travel has no such bound: an order whose
origin or destination lies beyond the reach of a full charge travels between the charger and that node without end
(the guide's known limit "Charging"), at the base as now.

**Make-way.** A make-way move that charged on its way, with no order pending, enters the decision once before it ends; then the AGV is `IDLE`, and
`idle` asks it to make way again by the ordinary rule if it still blocks a wait. Its chain of make-way moves is not
ended by the charge or by the decision's trip (neither is a mission): a move from a node it already made way from is
refused. Tests: an AGV on the charger's node X that blocks a wait, asked to make way, charges where it stands; with
clearing it then leaves X and the waiting AGV passes; without, it stays, `idle` refuses a move from X (it made way from
it in this chain) and the wait is reported stuck behind the idle AGV
(`test_a_make_way_move_that_charged_where_it_stood_leaves_the_waits_it_was_for_to_the_idle_rules`); a move that turns to
charge elsewhere ends where the decision puts it, and its chain ends there when it blocks no wait
(`test_a_make_way_move_that_charged_on_the_way_is_followed_by_the_idle_decision`, both controllers).

**Free flow.** The repositioning policy and the parking slots apply under both controllers, the clearance only when the
controller controls occupancy: item 1 changes the free-flow runs with a trip that charged on its way (6 above, each with
such a turn; 6 again in fix round 1's sample) and item 2 those with a stranding after a diversion, by the two fields (30;
25 in fix round 1's sample). All other free-flow runs are identical.

**Pins.** Seed 619 (`FreeFlowController`, shortest) was pinned again by item 1 as committed in `186d4ab`: agv-a, which
turned to charge at 122.0 on its way to park, after its charge at 220.1 went to park on P1 before it took the pending
order-6 from there, where the parent took order-6 at once from the charger (4 orders completed instead of 5, 398 events
instead of 394). Since fix round 1 an order pending at the end of the charge comes first (Ruling A3-r): agv-a takes
order-6 at once, as at `3aac5c4`, and the run and its pin are those of `3aac5c4` again
(`d48e693d…`, 394 events). The other five
fingerprints, the five safety pins, `tests/fixtures/traces/generated/`, `test_examples.py` and
`test_traffic_scenarios.py` hold under both items (full suite); the stdout of the seven transport and layout examples is
identical to `3aac5c4`. The frozen fixtures are untouched.

**The recorded safety ranges** at `d938852` (`benchmarks/layout_safety.py --worlds 1000 --jobs 10`): 100000..100999,
6,000 runs, 6,570,610 events, 0 raised, 0 not returned, 0 with the queue not empty, 0 with violations, exit status 0
(187 s); 2000000..2000999, 6,000 runs, 6,720,196 events, the same zeros, exit status 0 (190 s). The events are 406 and
166 more than at `3aac5c4` (6,570,204 and 6,720,030); the same totals come from the measurement, where 21 and 10 runs
of these ranges changed.

**`PYTHONHASHSEED`**: the 300 runs of seeds 4600000..4600049 under `PYTHONHASHSEED` 1 and 2 have the fingerprints and
whole records of the measured run of `d938852`, whose worker processes had random hash seeds. The hand-built tests use
no set or dict whose order decides anything new: `_charges` is keyed by AGV and read by key.

**What the monitor still does not check** (T4's list, brought up to date): a trip to park that holds no slot is checked
now. Still not checked: the spacing of followers on the bodies; the gate part of a stuck report; `passing` segments
during a reversal; the review timer and the pending claim at quiescence from events; a wait stuck and unreported for a
while and reported later; a `STALLED` blocker in a random run; an AGV idle on a service node after a charge (the
measurement counts it, the monitor does not: an AGV may rest on a service node when the decision names no node); and a
trip to a parking node that carries one of the fleet's chargers, which the slot rule leaves out (a charge there takes
no slot), so a world whose only parking area is on a charger's node has no check of its trips to park.

**Fix round 1.** The review (fresh seeds 4610000..4616999, its own hand-built loops and a fuzz of 4,000 small worlds)
confirmed item 2, the monitor's rule and item 1's ordinary behaviour, and found the decision after a charge recursing
without bound: when the node it names again lies beyond the reach of a full charge, each round nested two more
generator frames and `env.run()` raised `RecursionError` (about 490 rounds on CPython), with built-in parts only
(`NearestParkingPolicy` and a parking area beyond such an arc, or the clearance to such a node), in models that returned
at `3aac5c4` after one charge. It also found the test that pinned this with `until`, sentences that described the cycle
as "without end", the cost of the rule stated on the generator's worlds only, and the order with pending work left to a
ruling. Rulings of the controller: the decision after a charge is made once, and a trip of it that charges again ends
with the AGV idle where it charged (A3-q); it is not made while an order is pending: the AGV becomes idle where it
charged and the pending-queue check offers it the order, as at the base (A3-r).

The change (`fleet.py`): `_idle_travel` enters the decision only with an empty pending queue and when the AGV is not
already in one (`_after_charge`, a set entered and left around the call, also when the trip is interrupted); the
decision's own trip that charges again is therefore not followed by another. The decision's trip to the policy's node no
longer emits `agv.state_changed` with the state it already has (after a charge on the way the AGV is
`TRAVELING_EMPTY`; at a mission's end it comes from `WAITING_UNLOAD`, so missions are unchanged). The clearance keeps its
transition, which after a charge on the way repeats `TRAVELING_EMPTY`: a mission's end does the same before a clearance
at `3aac5c4` (67 times in the reservation runs of seeds 4600000..4600999), and that line is shared.

Tests (`test_idle_traffic.py`), each failing at `4ed7d6e` (`9 failed, 261 passed` with `test_random_worlds.py`, seven of
them by `RecursionError`): `test_after_a_charge_on_the_way_the_decision_is_made_once` (the policy, an area and the
clearance send A to T, out of the reach of a full charge; two charges, A idle on C, the run returns without `until`; five
cases) and `..._also_through_two_chargers` (a ring with chargers on A and B and a policy that names T1 from A and T2 from
B: charges on B then A, the vehicle idle on A; both controllers); `test_after_a_charge_on_the_way_with_an_order_pending_
the_agv_takes_the_order` (an order submitted during the charge is dispatched at the instant the charge ends, from CH);
and seed 619's pin, back to `d48e693d…`. Tests that pass at `4ed7d6e` and each fail under one source mutant
(`scratch-a3-T5/fix1/mutants.py`): a retry without a charge (critical on arrival, no charger) is not followed by the
decision, though the parking policy would act (fails when the charge condition is dropped); a charge by the
`on_low_battery` callback counts (fails when the callback's charge is not counted); a stranding after a charge on the way
is not followed by the decision (fails when any outcome but `ARRIVED` enters it). The decision repeated and the pending
queue ignored each fail the new tests above.

The reviewer's cases at the head (`scratch-a3-T5/fix1/loop.out`, `loop23.out`, `fuzz.out`): every hand-built case that
returned at `3aac5c4` returns, after two charges where the base had one (a ring through two chargers included); in its
case with uniform arcs (a full charge covers 2.5 arcs, the area 3 arcs away) the AGV sent on after its charge strands on
the way, where the base left it idle and full on the charger. The cases of an order beyond the reach of a full charge
cycle at both trees (pre-existing). The fuzz of 4,000 worlds: 3,937 return at `3aac5c4` and all 3,937 at the head; the
other 63 are cut at both (orders out of reach).

Measured again, `3aac5c4` (with this monitor) against the head of this round, each a copy first on `PYTHONPATH`, with
the reviewer's measuring script (`reviewer-scratch-a3-T5/scripts/measure_tight.py`, which counts orders by order): seeds
4600000..4602999 and the reviewer's 4610000..4614999, both controllers, three routers (48,000 runs), and small-battery
worlds, seeds 4620000..4620999 rewritten as the reviewer's 4616000..4616999 are (every fleet's battery 2.5 to 8 times
the longest arc, thresholds redrawn; 6,000 runs). Every run returned, none was cut, none has a violation. Every changed
run is attributed (`scratch-a3-T5/fix1/attrib.py`, each run replayed on both trees): its first differing event is the
decision at the end of a charge of a trip without an order that charged away from its target (item 1), or it differs
only in `node` and `reason` of `agv.stranded` and the message of its log line (item 2).

| Measure | Seeds 4600000..4602999 and 4610000..4614999 (48,000 runs) | Small batteries, 4620000..4620999 (6,000 runs) |
|---|---|---|
| changed runs: item 1 (reservation / free), item 2 | 43 (37 / 6), 39 | 55 (49 / 6), 16 |
| orders completed | 729,433 both, 0 [-19.0, +19.0] | 27,896 and 27,886, -10 [-24.9, +4.9] |
| orders failed | -4 [-27.7, +19.7] | +28 [-7.6, +63.6] |
| completion time of orders completed in both, paired by order | +656.9 [-148.9, +1,462.7] (20 runs) | -317.3 [-801.4, +166.8] (20 runs) |
| first dispatch time of orders dispatched in both, paired by order | +206.4 [-322.3, +735.1] (13 runs) | -279.6 [-671.4, +112.2] (15 runs) |
| orders completed only at the base / only at the head | 22 / 22 | 24 / 14 |
| strandings (`agv.state_changed` to `STRANDED`) | -33 [-60.9, -5.1] | +25 [-1.4, +51.4] |
| AGVs stranded at the end | -3 [-10.1, +4.1] | +7 [-2.8, +16.8] |
| `parking.entered` | +28 [+15.6, +40.4] | +32 [+15.4, +48.6] |
| time AGVs are idle on a service node of their fleet | -1,259.9 [-2,113.9, -405.8] | -3,728.7 [-5,689.8, -1,767.6] |
| `traffic.wait_stuck` | -20 [-37.3, -2.7] | -10 [-29.0, +9.0] |
| end time (sum) | +729.6 [-3.1, +1,462.4] | -136.4 [-298.0, +25.2] |
| `agv.stranded` `insufficient_after_charging` / `charger_not_reached` (base, head); naming another node | 39 / 0, 0 / 39; 39 and 0 | 20 / 0, 0 / 20; 20 and 0 |

Where the AGVs rest after a charge on the way (turns of a trip without an order that charged away from its target):
on the 48,000 runs, 87 at the base and 86 at the head, in 84 runs; idle on the charger's node at the end of the charge's
instant 51 and 13 (at the head: an order pending in 3, the decision naming nothing in 10); idle there for a while 41
(14 to the end of the run) and 12 (1), longest 364.4 and 249.9, in all 2,194.5 and 645.6; no trip of a decision turned
to charge. On the small-battery worlds: 84 and 85 turns; idle on the charger's node at the end of the instant 60 and
12; for a while 60 (4 to the end) and 12 (2), in all 5,111.5 and 1,347.3; 3 trips of a decision turned to charge again,
and the AGV then rests where it charged.

The cost of the rule, on small batteries. The review measured on its worlds 4616000..4616999 (`4ed7d6e` against
`3aac5c4`, quoted): strandings +34 [+3.8, +64.2], AGVs stranded at the end +15 [+4.1, +25.9], a second turn in 2 of
6,000 runs. With the decision made once and not with an order pending, on 4620000..4620999: strandings +25 [-1.4,
+51.4], AGVs stranded at the end +7 [-2.8, +16.8], orders completed -10 [-24.9, +4.9]. The mechanism is the one of the
reviewer's case with uniform arcs: an AGV that the base left idle with a full battery on the charger is sent on by the
decision, and with a small battery it can strand on the way.

The recorded safety ranges at the head of this round (`benchmarks/layout_safety.py --worlds 1000 --jobs 10`):
100000..100999, 6,000 runs, 6,570,403 events, 0 raised, 0 not returned, 0 with the queue not empty, 0 with violations,
exit status 0 (199 s); 2000000..2000999, 6,000 runs, 6,720,267 events, the same zeros, exit status 0 (194 s). The 300
runs of seeds 4600000..4600049 under `PYTHONHASHSEED` 1 and 2 have the fingerprints and records of the measured head
run. The focused test files (`test_idle_traffic.py`, `test_stop_requests.py`, `test_random_worlds.py`,
`test_charging.py`) pass on PyPy 3.11 (`389 passed`).
