"""Mode ratios on the advanced intralogistics example, scaled up (spec §14, ruling R28). Branch only, never gated.

The layout, SKUs, warehouses, AGV type, parking, charging, strategies and the reorder-point replenishment are those of
``examples/intralogistics_advanced.py`` (16 nodes, 16 arcs, 5 SKUs, 3 warehouses). The scale-up is in three numbers:
more AGVs (the example's five starting nodes are reused cyclically), a shorter outbound order interval and a longer
horizon; defaults are 20 AGVs, orders every 30 to 60 time units and 20 shifts of 28,800 time units. The run is
deterministic (``Environment(seed=42)``); there is no 0.12.0 comparison because its intralogistics API differs from
the branch's, so the results are ratios to the branch's own mode ``none``.

Modes, as in ``feeder.py`` (the fleet coordinator replaces the shop floor):

- ``none``: ``default_metrics=False`` and no collector.
- ``default``: the coordinator's default ``OrderEMACollector``.
- ``default_logging``: ``none`` with the default log sinks checked active (they are in every mode, see ``feeder.py``).
- ``bare``: ``none`` with the default log sinks closed (diagnostic floor for the logging cost).
- ``kpi``: ``TraceRecorder(level="kpi")``, the default ``OrderEMACollector`` and a ``FleetKPIs`` collector.
- ``digest`` and ``full``: ``none`` plus ``env.enable_digest()`` or a ``TraceRecorder`` (default limits).

Usage::

    python benchmarks/intralogistics.py --mode none --warmup 1 --repeat 5 --processes 3 --json il-none.json

The JSON has the keys of ``run.py`` that ``compare.py`` and ``summarize.py`` read, with workload path
``intralogistics-advanced``.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import feeder
from run import _max_rss_mb, _version, provenance, quartiles
from simulatte.distributions import Uniform
from simulatte.environment import Environment
from simulatte.intralogistics import (
    AGV,
    SKU,
    AGVType,
    Arc,
    ChargingStation,
    FleetCoordinator,
    FleetKPIs,
    LayoutGraph,
    NearestParkingPolicy,
    Node,
    OrderStatus,
    ParkingArea,
    ReorderPointPolicy,
    ReturnToOrigin,
    RoundRobinStrategy,
    TrapezoidalProfile,
    Warehouse,
)

MODES = ("none", "default", "default_logging", "bare", "kpi", "digest", "full")
SHIFT = 28800.0
SEED = 42


def _layout() -> tuple[LayoutGraph, dict[str, Node]]:
    coordinates = {
        "RCV_IN": (0, 30),
        "RCV_OUT": (20, 30),
        "R1": (50, 30),
        "R2": (80, 30),
        "BULK_IN": (110, 30),
        "CHRG": (80, 15),
        "PARK": (100, 15),
        "BULK_OUT": (20, 0),
        "B1": (50, 0),
        "B2": (80, 0),
        "B3": (110, 0),
        "DSP_IN": (140, 0),
        "DSP_OUT": (160, 0),
        "B4": (80, -20),
        "B5": (100, -20),
        "B6": (110, -20),
    }
    nodes = {name: Node(id=name, x=x, y=y) for name, (x, y) in coordinates.items()}
    pairs = [
        ("RCV_IN", "RCV_OUT"),
        ("DSP_IN", "DSP_OUT"),
        ("RCV_OUT", "R1"),
        ("R1", "R2"),
        ("R2", "BULK_IN"),
        ("BULK_OUT", "B1"),
        ("B1", "B2"),
        ("B2", "B3"),
        ("B3", "DSP_IN"),
        ("R2", "B2"),
        ("R2", "CHRG"),
        ("CHRG", "PARK"),
    ]
    arcs = [Arc(nodes[a], nodes[b], bidirectional=True) for a, b in pairs]
    arcs += [
        Arc(nodes[a], nodes[b], bidirectional=False)
        for a, b in (("B2", "B4"), ("B4", "B5"), ("B5", "B6"), ("B6", "B3"))
    ]
    return LayoutGraph(list(nodes.values()), arcs), nodes


def _order_stream(
    env: Any, coordinator: Any, source: Any, sink: Any, skus: list[SKU], agv_type: Any, interval: tuple[float, float]
):
    rng = env.rng("outbound-orders")
    gap = env.bind(Uniform(*interval), kind="scalar", stream="outbound-orders/interval", owner="outbound-orders")
    due = env.bind(Uniform(1800, 3600), kind="scalar", stream="outbound-orders/due", owner="outbound-orders")
    while True:
        yield env.timeout(gap())
        sku = rng.choice(skus)
        max_qty = min(
            max(1, int(agv_type.weight_capacity // sku.weight)), max(1, int(agv_type.volume_capacity // sku.volume))
        )
        order = coordinator.create_order(
            sku=sku,
            quantity=rng.randint(1, min(3, max_qty)),
            origin=source,
            destination=sink,
            due_date=env.now + due(),
        )
        coordinator.submit(order)


def run(args: argparse.Namespace, *, mode: str, trace_path: Path | None) -> dict[str, Any]:
    """Build the scaled-up advanced example, run it to the horizon and close the environment, timing all of it."""
    gc.collect()
    horizon = args.shifts * SHIFT
    start = time.perf_counter()
    env = Environment(seed=SEED)
    if mode == "digest":
        env.enable_digest()
    elif mode in ("full", "kpi"):
        from simulatte.trace import TraceRecorder

        assert trace_path is not None
        TraceRecorder(env, trace_path, level="kpi" if mode == "kpi" else "full")
    elif mode == "bare":
        for sink in env.sinks:
            sink.close()
    graph, nodes = _layout()

    skus = [
        SKU(id="Pallet-A-Heavy", weight=120.0, volume=0.5),
        SKU(id="Pallet-B-Medium", weight=50.0, volume=0.8),
        SKU(id="Pallet-C-Light", weight=10.0, volume=0.3),
        SKU(id="Pallet-D-Bulky", weight=30.0, volume=1.2),
        SKU(id="Pallet-E-Small", weight=5.0, volume=0.1),
    ]

    def times(pick: tuple[float, float], put: tuple[float, float]):
        return (lambda sku, qty: pick[0] + qty * pick[1]), (lambda sku, qty: put[0] + qty * put[1])

    def warehouse(name: str, bays: tuple[str, str], slots: int, stock: int, pick, put) -> Warehouse:
        pick_time, put_time = times(pick, put)
        return Warehouse(
            env=env,
            name=name,
            input_bays=[nodes[bays[0]]],
            output_bays=[nodes[bays[1]]],
            n_slots=slots,
            products=skus,
            initial_inventory={sku: stock for sku in skus},
            pick_time=pick_time,
            put_time=put_time,
        )

    receiving = warehouse("Receiving", ("RCV_IN", "RCV_OUT"), 3, 200, (20.0, 3.0), (10.0, 2.0))
    bulk = warehouse("Bulk Storage", ("BULK_IN", "BULK_OUT"), 4, 30, (25.0, 4.0), (15.0, 3.0))
    dispatch = warehouse("Dispatch", ("DSP_IN", "DSP_OUT"), 3, 0, (15.0, 2.0), (10.0, 2.0))

    agv_type = AGVType(
        name="heavy-duty",
        speed_profile=TrapezoidalProfile(
            max_speed=2.0,
            acceleration=0.8,
            deceleration=1.0,
            battery_degradation_fn=lambda level: 1.0 if level >= 0.3 else 0.7 + level,
            load_speed_factor_fn=lambda weight: max(0.5, 1.0 - weight / 300),
        ),
        battery_capacity=100.0,
        weight_capacity=150.0,
        volume_capacity=1.5,
        depletion_fn=lambda distance, load_weight, speed: distance * 0.02 * (1.0 + load_weight / 200),
        low_battery_threshold=0.2,
        critical_battery_threshold=0.05,
        load_time=12.0,
        unload_time=10.0,
    )
    starting = [nodes[n] for n in ("PARK", "BULK_OUT", "B1", "R1", "B3")]
    agvs = [
        AGV(env=env, agv_type=agv_type, agv_id=f"AGV-{i + 1}", initial_node=starting[i % len(starting)])
        for i in range(args.agvs)
    ]
    coordinator = FleetCoordinator(
        env=env,
        graph=graph,
        fleet=agvs,
        warehouses=[receiving, bulk, dispatch],
        charging_stations=[ChargingStation(env=env, name="Charger", node=nodes["CHRG"], n_slots=2)],
        parking_areas=[ParkingArea(env=env, name="Parking", node=nodes["PARK"], capacity=3)],
        dispatch_strategy=RoundRobinStrategy(),
        repositioning_policy=NearestParkingPolicy(),
        load_recovery_strategy=ReturnToOrigin(),
        default_metrics=mode in ("default", "kpi"),
    )
    if mode == "kpi":
        FleetKPIs(coordinator).attach(env)
    coordinator.add_replenishment_policy(
        ReorderPointPolicy(
            thresholds={sku: 10 for sku in skus},
            reorder_quantity=dict(zip(skus, (1, 1, 5, 1, 10), strict=True)),
        ),
        bulk,
    )
    orders: list[Any] = []
    coordinator.on_order_submitted(orders.append)
    env.process(_order_stream(env, coordinator, bulk, dispatch, skus, agv_type, (args.interval_min, args.interval_max)))
    live = [sink for sink in env.sinks if not sink.closed]
    if bool(live) != (mode != "bare"):
        raise RuntimeError(f"mode {mode!r}: unexpected log sinks {live}")
    if mode in ("none", "default_logging", "bare", "digest", "full") and coordinator.metrics is not None:
        raise RuntimeError(f"mode {mode!r} must run without the default metrics")
    env.run(until=horizon)
    env.close()
    wall = time.perf_counter() - start

    counts = {status.name: sum(1 for o in orders if o.status is status) for status in OrderStatus}
    trajectory = hashlib.sha256()
    for o in orders:
        trajectory.update(f"{o.id}|{o.status.name}|{o.created_at!r}|{o.delivered_at!r}\n".encode())
    digest = env.fingerprint().digest if mode in ("digest", "full", "kpi") else None
    return {
        "wall_s": wall,
        "counts": {"orders": len(orders), "by_status": counts, "trajectory": trajectory.hexdigest()},
        "digest": digest,
    }


def _in_process(args: argparse.Namespace) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        trace_path = Path(tmp) / "il.simtrace"
        samples, results = [], []
        for i in range(args.warmup + args.repeat):
            trace_path.unlink(missing_ok=True)
            result = run(args, mode=args.mode, trace_path=trace_path)
            if i >= args.warmup:
                samples.append(result["wall_s"])
                results.append(result)
            print(f"{'warmup' if i < args.warmup else 'run'} {i + 1}: {result['wall_s']:.4f} s", file=sys.stderr)
        if len({(json.dumps(r["counts"], sort_keys=True), r["digest"]) for r in results}) != 1:
            raise RuntimeError("repeated runs gave different digests or trajectories")
        trace_bytes = os.path.getsize(trace_path) if args.mode in ("kpi", "full") else None
    q1, median, q3 = quartiles(samples)
    interval = f"{args.interval_min}-{args.interval_max}"
    spec = f"intralogistics-advanced agvs={args.agvs} shifts={args.shifts} interval={interval}"
    return {
        "label": args.label or feeder.default_label(),
        "simulatte_version": _version(),
        "has_trace": feeder.has_trace(),
        "mode": args.mode,
        "python": {
            "implementation": sys.implementation.name,
            "version": platform.python_version(),
            "build": platform.python_build()[0],
        },
        "platform": {"system": platform.system(), "machine": platform.machine(), "node": platform.node()},
        **provenance(),
        "workload": {
            "path": "intralogistics-advanced",
            "sha256": hashlib.sha256(spec.encode()).hexdigest(),
            "params": {
                "agvs": args.agvs,
                "shifts": args.shifts,
                "interval": [args.interval_min, args.interval_max],
                "seed": SEED,
            },
            "horizon": args.shifts * SHIFT,
        },
        "counts": results[-1]["counts"],
        "subscribers": None,
        "digest": results[-1]["digest"],
        "warmup": args.warmup,
        "repeat": args.repeat,
        "processes": 1,
        "process_medians_s": [median],
        "samples_s": samples,
        "median_s": median,
        "iqr_s": q3 - q1,
        "min_s": min(samples),
        "max_s": max(samples),
        "peak_mb": None,
        "rss_before_mb": None,
        "trace_bytes": trace_bytes,
    }


def _flags(args: argparse.Namespace) -> list[str]:
    return [
        *("--mode", args.mode),
        *("--agvs", str(args.agvs)),
        *("--shifts", str(args.shifts)),
        *("--interval-min", str(args.interval_min)),
        *("--interval-max", str(args.interval_max)),
    ]


def _multi_process(args: argparse.Namespace) -> dict[str, Any]:
    outs = []
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(args.processes):
            path = Path(tmp) / f"child{i}.json"
            command = [sys.executable, __file__, *_flags(args), "--warmup", str(args.warmup)]
            command += ["--repeat", str(args.repeat), "--processes", "1", "--no-memory", "--json", str(path)]
            if args.label:
                command += ["--label", args.label]
            subprocess.run(command, check=True)
            outs.append(json.loads(path.read_text()))
    if len({(json.dumps(o["counts"], sort_keys=True), o["digest"]) for o in outs}) != 1:
        raise RuntimeError("processes gave different digests or trajectories")
    out = outs[0]
    samples = [s for o in outs for s in o["samples_s"]]
    q1, median, q3 = quartiles(samples)
    out.update(
        processes=args.processes,
        process_medians_s=[o["median_s"] for o in outs],
        samples_s=samples,
        median_s=median,
        iqr_s=q3 - q1,
        min_s=min(samples),
        max_s=max(samples),
    )
    return out


def memory_probe(args: argparse.Namespace) -> dict[str, float]:
    before = _max_rss_mb()
    with tempfile.TemporaryDirectory() as tmp:
        run(args, mode=args.mode, trace_path=Path(tmp) / "probe.simtrace")
    return {"rss_before_mb": before, "peak_mb": _max_rss_mb()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=MODES, default="none")
    parser.add_argument("--agvs", type=int, default=20)
    parser.add_argument("--shifts", type=float, default=20, help="horizon in shifts of 28,800 time units")
    parser.add_argument("--interval-min", type=float, default=30.0, help="outbound order interval, lower bound")
    parser.add_argument("--interval-max", type=float, default=60.0, help="outbound order interval, upper bound")
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--repeat", type=int, default=5, help="timed runs (per process)")
    parser.add_argument("--processes", type=int, default=1)
    parser.add_argument("--json", help="write the results here (default: stdout)")
    parser.add_argument("--label", help="name of the measured version in reports")
    parser.add_argument("--memory", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--memory-probe", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not feeder.has_trace():
        parser.error("this benchmark needs the branch (SP1 events and traces)")
    if args.memory_probe:
        json.dump(memory_probe(args), sys.stdout)
        return 0
    out = _in_process(args) if args.processes == 1 else _multi_process(args)
    if args.memory:
        completed = subprocess.run(
            [sys.executable, __file__, *_flags(args), "--memory-probe"], capture_output=True, text=True, check=True
        )
        out.update(json.loads(completed.stdout))
    text = json.dumps(out, indent=2)
    if args.json:
        Path(args.json).write_text(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
