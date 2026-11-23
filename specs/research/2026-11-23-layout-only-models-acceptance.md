# Layout-only models, acceptance

Branch `feature/layout-only-models`, spec `specs/2026-11-23-layout-only-models-design.md`. The work makes the layout
the only way to build a model. This file records the evidence each task produces for the spec's verification (§8) and
acceptance (§10): the parity run that allows explicit mode to be removed (L8), and the measurements and gates of the
later tasks.

## Parity of explicit and layout models (Task 2)

### Result

The same 1,000 random worlds, each built once with concrete graph objects (explicit mode) and once through
`Explicit` and layout handles (layout mode), run alike under both traffic controllers and the three routers: 6,000
compared pairs, 6,000 equal, no difference, no exception, no run left at the horizon, no monitor violation. The known
difference of spec §8.1 (`Explicit` dropping `Arc.length`) is gone since Task 1 (`15a52de`, `8f54323`); the same
comparison against the source before Task 1 finds it (see "The comparison finds a difference").

### The run

Made at commit `ae1be68` (the source is that of `8f54323`; the commit adds the generator, its tests and the script),
CPython 3.14.3 on an Apple M4 Pro, ten worker processes:

```
uv run --all-extras python benchmarks/layout_parity.py --worlds 1000
```

```
worlds 1000 (seeds 0..999), runs 12000, pairs 6000
equal 6000, differing 0, events compared 13053112, failed in both modes 0, ended at the horizon 0, with monitor violations 0
until 20000.0, wall-clock limit 120 s per run, 455 s
```

Each world (`world_spec(seed)` in `tests/intralogistics/random_worlds.py`, seeds 0 to 999) runs twelve times: two
modes, `ReservationController` and `FreeFlowController`, and `ShortestDistanceRouter`, `FastestRouter` and
`CongestionAwareRouter`. A pair is the two modes under one controller and one router. Each run is bounded by a
simulated horizon of 20,000 (orders arrive before 600) and a wall-clock limit of 120 s; every run ended on its own,
with an empty event queue, before the horizon, so the bound never cut a run short. "Events compared" counts the
events of both streams of every pair.

### What is compared

Two runs are equal when their `RunRecord`s are:

- every event emitted after the prelude, domain and observer events alike, as its type and all its fields (time,
  deltas and payload) except `seq` and `ordinal`. The prelude, what is emitted before the initial state is captured at
  activation, is left out because its attachment counts depend on when nodes are bound. These are the exclusions of
  the two existing parity tests (`tests/layout/test_binding.py::test_explicit_layout_semantic_parity_and_speed_limited_transport`,
  `tests/layout/test_builders.py::test_prefixed_explicit_and_layout_trajectories_and_kpis_match`), and no other;
- the initial state, the final state of every entity, the end time, whether the run ended on its own;
- the monitor's violations.

### The worlds

The generator ports the worlds of the traffic reviews (`final-scratch-control/world.py`) to a pure-data description
(`WorldSpec`), so that one description builds both modes. Over the 1,000 worlds (21,829 nodes, 3,164 AGVs, 22,049
orders): grids of 2 to 5 by 2 to 4 nodes with removed links, chains and spurs; one-way arcs in 371 worlds, passing
segments in 607, speed limits in 941, arcs with a recorded length (1.3 times the chord) in 540; a block length in 387
(212 of them together with recorded lengths); nodes of capacity 2 in 748; zones in 657 (with hold-back in 422,
without in 404, with pairs under a block length in 190); parking in 727, two chargers in 516; low batteries in 480;
cancellations in 384 and interruptions in 383 (449 worlds have either); a second fleet on the same controller in
291; plain linear profiles in 408 and trapezoidal ones in the rest; corner speeds or turn times in 383. No world has
two nodes at one position.

In explicit mode a world with a block length uses `LayoutGraph.subdivide(block_length)`; in layout mode
`Explicit(graph, block_length=...)`. Per-node capacities go to `ReservationController(capacities=)` in both modes (the
controller matches nodes by value) until Task 5 moves them to the layout. Zones go to the controller in explicit mode
and to `Explicit(zones=...)` in layout mode.

### Where the generator equalizes the modes

The modes differ in two places that spec L7 and L13 settle for layout mode. The generator builds both sides so that
neither shows:

- **Ids.** AGVs are constructed with `agv_id=`, and the fleets, warehouses, chargers and parking areas are named, as
  the brief prescribes.
- **The order of a zone's members.** A controller zone keeps its members in the order given; the layout resolves a
  zone to its nodes in id order, then its node pairs in id order (`resolve_zones` in `layout/network.py`). The order
  is the `members` field of the zone entity, so it shows in the initial state. In explicit mode the generator passes
  the members in the layout's order. Checked: with the explicit members reversed, 21 of the 26 worlds with zones among
  seeds 0 to 39 differ, first in the zone's `members`. `ReservationController(zones=)` is removed with L13.

The generator also starts no more AGVs, of all fleets, in a zone than its capacity, since placement would raise at
activation in both modes (`cannot place ...`); before this rule seed 33 raised alike in both modes under
`ReservationController`.

### The comparison finds a difference

- Against the source before Task 1 (`2129469`, extracted with `git archive` and put first on `PYTHONPATH`),
  `tests/intralogistics/test_random_worlds.py` fails the four of its five parity seeds whose worlds have a recorded
  arc length (45, 54, 88, 110) and passes seed 82, which has none.
- With `Explicit` patched to drop every recorded length again, 35 of seeds 0 to 59 differ under
  `ReservationController` and `ShortestDistanceRouter`: exactly the 35 worlds with a recorded length. The first
  difference is an `agv.routed` event with another route or cost.

### The monitor

`Monitor` in `random_worlds.py` is the control review's monitor (`final-scratch-control/monitor.py`), ported to built
worlds. After every domain event it replays the deltas from the first event and from the initial state and compares
them with the live entities. With a `ReservationController` it also keeps its own body model of every vehicle, from
the motion events only, and checks node, segment, zone and corridor admission against it, that the holdings cover the
bodies (exactly for a vehicle at rest and at the end of every travel), its own derivation of the corridors, and the
entities against the holdings ledger; under either controller, parking capacity and that moves start and end where
the body is. At the end of a run that ended on its own: no claim of a moving AGV, no review timer, every open wait
explained by a stuck report or an unresolved deadlock, no motion left, no parking slot held away from its node. It
records violations instead of raising. `test_the_monitor_reports_an_admission_that_ignores_node_capacity` shows that
it detects a seeded violation (node capacity ignored at admission: physical and ledger over-capacity reported).

### Tests

`tests/intralogistics/test_random_worlds.py`: `world_spec` is equal for equal seeds, also in processes with other
`PYTHONHASHSEED` values; no world of seeds 0 to 199 has two nodes at one position; seeds 45, 54, 82, 88 and 110 run
with equal `RunRecord`s in both modes under `ReservationController` and the default router, end on their own and
show no violation; the seeded admission violation is reported.
