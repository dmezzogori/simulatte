"""Map workloads of the Studio bench: a fleet that really moves on a declared transport network.

``--workload fleet100``: 100 vehicles on a 20 x 20 ``Lattice`` (5 m spacing) under ``ReservationController``,
carrying crates between 12 warehouses placed on lattice nodes. Every vehicle type has a ``length``, which the
controller requires; the floor is held, vehicles wait for each other and some are idle.

``--workload lanes1000``: 1,000 vehicles on 50 parallel ``Lanes`` of 100 m with the default controller
(``FreeFlowController``). Reservation control would allow one vehicle per node at a time, and 20 vehicles share each
lane; the workload is for the renderer, not a transport-throughput model.

Both write a recording at ``--output`` (default under ``/tmp/studio-map-bench``), check and verify it, and print the
path, size and entity count. Run from the repository root with ``uv run python studio/bench/glyphs.py``.
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from simulatte import Environment, Trace, TraceRecorder
from simulatte.intralogistics import (
    AGV,
    SKU,
    AGVType,
    FleetCoordinator,
    NearestIdleStrategy,
    ReservationController,
    TrapezoidalProfile,
    Warehouse,
)
from simulatte.layout import Address, Grid, Lanes, Lattice

OUTPUT = Path("/tmp/studio-map-bench")


def lattice_node(env: Environment, i: int, j: int):
    return env.layout.node_at(Address({"kind": "lattice", "i": i, "j": j}))


def fleet100(path: Path, until: float, seed: int) -> None:
    """100 vehicles on a 20 x 20 lattice, under reservation control."""
    side, vehicles, houses = 20, 100, 12
    with Environment(seed=seed) as env:
        recorder = TraceRecorder(env, path)
        layout = env.layout
        layout.set_grid(Grid(5, 5))
        layout.set_network(Lattice((0, 0, 5 * (side - 1), 5 * (side - 1)), neighbors=4, capacity=2))
        rnd = random.Random(seed)
        crate = SKU("crate", weight=10.0, volume=0.2)
        cells = [(i, j) for i in range(1, side - 1) for j in range(1, side - 1)]
        spots = rnd.sample(cells, houses)
        warehouses = [
            Warehouse(
                env=env,
                name=f"WH-{k:02d}",
                input_bays=[lattice_node(env, i, j)],
                output_bays=[lattice_node(env, i, j)],
                n_slots=4,
                products=[crate],
                initial_inventory={crate: 100_000},
                pick_time=6.0,
                put_time=4.0,
            )
            for k, (i, j) in enumerate(spots)
        ]
        for house in warehouses:
            env.layout.place(house, obstacle=False)
        kind = AGVType(
            name="tugger",
            speed_profile=TrapezoidalProfile(
                max_speed=1.5, acceleration=0.5, deceleration=0.5, battery_degradation_fn=lambda level: 1.0
            ),
            battery_capacity=100_000.0,
            weight_capacity=100.0,
            volume_capacity=2.0,
            length=1.2,
            min_gap=0.3,
            load_time=3.0,
            unload_time=3.0,
        )
        starts = rnd.sample([(i, j) for i in range(side) for j in range(side)], vehicles)
        agvs = [
            AGV(env=env, agv_type=kind, agv_id=f"AGV-{k:03d}", initial_node=lattice_node(env, i, j))
            for k, (i, j) in enumerate(starts)
        ]
        fleet = FleetCoordinator(
            env=env,
            graph=layout.graph(),
            fleet=agvs,
            warehouses=warehouses,
            charging_stations=[],
            parking_areas=[],
            traffic_controller=ReservationController(env=env, graph=layout.graph()),
            dispatch_strategy=NearestIdleStrategy(),
            name="fleet",
        )

        def orders():
            while True:
                origin, destination = rnd.sample(warehouses, 2)
                fleet.submit(fleet.create_order(sku=crate, quantity=1, origin=origin, destination=destination))
                yield env.timeout(rnd.expovariate(1 / 2.0))

        env.process(orders())
        env.run(until=until)
        recorder.close()


def lanes1000(path: Path, until: float, seed: int) -> None:
    """1,000 vehicles on 50 parallel lanes, default traffic control, orders between the two ends of every lane."""
    lanes, per_lane, length = 50, 20, 100.0
    with Environment(seed=seed) as env:
        recorder = TraceRecorder(env, path)
        layout = env.layout
        layout.set_network(
            Lanes(
                {
                    f"lane-{n:02d}": {
                        "vertices": [
                            {"id": "a", "position": [0, 2 * n, 0]},
                            {"id": "b", "position": [length, 2 * n, 0]},
                        ]
                    }
                    for n in range(lanes)
                }
            )
        )

        def end(n: int, vertex: str):
            return layout.node_at(Address({"kind": "lane_vertex", "lane": f"lane-{n:02d}", "vertex": vertex}))

        crate = SKU("crate", weight=1.0, volume=0.01)
        kind = AGVType(
            name="shuttle",
            speed_profile=TrapezoidalProfile(
                max_speed=10.0, acceleration=2.0, deceleration=2.0, battery_degradation_fn=lambda level: 1.0
            ),
            battery_capacity=1_000_000.0,
            weight_capacity=10.0,
            volume_capacity=1.0,
            length=0.8,
            min_gap=0.2,
            load_time=1.0,
            unload_time=1.0,
        )
        fleets = []
        for n in range(lanes):
            houses = [
                Warehouse(
                    env=env,
                    name=f"WH-{n:02d}-{side}",
                    input_bays=[end(n, vertex)],
                    output_bays=[end(n, vertex)],
                    n_slots=per_lane,
                    products=[crate],
                    initial_inventory={crate: 1_000_000},
                    pick_time=0.5,
                    put_time=0.5,
                )
                for side, vertex in (("W", "a"), ("E", "b"))
            ]
            for house in houses:
                layout.place(house, obstacle=False)
            agvs = [
                AGV(
                    env=env,
                    agv_type=kind,
                    agv_id=f"AGV-{n:02d}-{k:02d}",
                    initial_node=end(n, "a" if k % 2 == 0 else "b"),
                )
                for k in range(per_lane)
            ]
            fleets.append(
                FleetCoordinator(
                    env=env,
                    graph=layout.graph(),
                    fleet=agvs,
                    warehouses=houses,
                    charging_stations=[],
                    parking_areas=[],
                    dispatch_strategy=NearestIdleStrategy(),
                    name=f"lane{n:02d}",
                )
            )

        def orders(fleet: FleetCoordinator, west: Warehouse, east: Warehouse):
            flip = False
            while True:
                origin, destination = (east, west) if flip else (west, east)
                fleet.submit(fleet.create_order(sku=crate, quantity=1, origin=origin, destination=destination))
                flip = not flip
                yield env.timeout(1.0)

        for fleet in fleets:
            env.process(orders(fleet, fleet.warehouses[0], fleet.warehouses[1]))
        env.run(until=until)
        recorder.close()


WORKLOADS = {"fleet100": (fleet100, 300.0, 100), "lanes1000": (lanes1000, 120.0, 312)}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workload", choices=sorted(WORKLOADS), required=True)
    parser.add_argument("--until", type=float, help="simulated seconds")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    build, until, seed = WORKLOADS[args.workload]
    output = args.output or OUTPUT / f"{args.workload}.simtrace"
    output.parent.mkdir(parents=True, exist_ok=True)
    build(output, args.until or until, args.seed or seed)
    trace = Trace.open(output)
    trace.check()
    assert trace.verify()
    print(output, output.stat().st_size)
