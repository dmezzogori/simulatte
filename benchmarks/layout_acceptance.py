"""Reproducible SP2 fixtures and preparation measurements (core dependencies only).

Run with an explicitly chosen installed interpreter, e.g.:
  python benchmarks/layout_acceptance.py fixture --rotation 90
  python benchmarks/layout_acceptance.py prepare --nodes 10000 --memory
Timing excludes declarations, activation and encoding; memory is a separate prepare run.
Preparation probes use CPython tracemalloc; fixture mode also runs on PyPy.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import gc
import json
import importlib.resources
import importlib.util
import math
from pathlib import Path
import platform
import re
import resource
import sys
import tempfile
import time

from simulatte import Environment, Layout, Trace, TraceRecorder
from simulatte.intralogistics.builders import build_simple_system
from simulatte.intralogistics.events import AgvMoveEnded
from simulatte.intralogistics.metrics import FleetKPIs
from simulatte.intralogistics.sku import SKU
from simulatte.layout import Address, Footprint, Grid, Lattice, Point
from simulatte.layout.geometry import point_distance_squared
from simulatte.server import Server
from simulatte.trace.format import PREAMBLE, RECORD_HEADER, RecordType


def model(*, rotation=90, width=6, file=None, record=None):
    """A generated network plus staged count, late overrides and an orphan."""
    env = Environment(seed=17)
    env.enable_digest()
    if record is not None:
        TraceRecorder(env, record)
    patch = {
        "schema_version": "1.0",
        "placements": {
            "hall": {"kind": "server", "footprint": {"width": width}},
            "machine-01": {"kind": "server", "visual": {"color": "blue"}},
            "removed": {"kind": "server", "position": [99, 99, 0]},
        },
    }
    if file is None:
        layout = Layout.from_dict(env, patch)
    else:
        file.write_text(json.dumps(patch))
        layout = Layout(env, file=file)
    hall = Server(env=env, capacity=1, name="hall")
    # Remote obstacle tests transformed geometry without changing the transport path.
    layout.place(hall, position=Point(30, 30), rotation=rotation, footprint=Footprint(6, 2), obstacle=True)
    width_ref = layout.ref("placements", "hall", "footprint", "width")
    machines = []

    @layout.stage("machines", reads=[width_ref])
    def machines_stage(ctx):
        for i in range(int(ctx.physical(width_ref) // 3)):
            machine = Server(env=ctx.env, capacity=1, name=f"machine-{i:02}")
            layout.place(machine, position=Point(i * 3, 10))
            machines.append(machine)

    layout.set_grid(Grid(5, 5))
    layout.set_network(Lattice((0, 0, 20, 0)))
    roles = {
        role: layout.node_at(Address({"kind": "lattice", "i": i, "j": 0}))
        for i, role in enumerate(("WH_A_OUT", "N1", "N2", "N3", "WH_B_IN"))
    }
    sku = SKU("part", 1, 1)
    fleet, carts, origin, destination, _ = build_simple_system(
        env, layout=layout, nodes=roles, n_agvs=1, products=[sku], initial_inventory_a={sku: 10}
    )
    FleetKPIs(fleet).attach(env)
    layout.require_path(roles["WH_A_OUT"], roles["WH_B_IN"])
    order = fleet.create_order(sku=sku, quantity=1, origin=origin, destination=destination)
    fleet.submit(order)
    trajectory = []
    env.bus.subscribe(lambda event: trajectory.append([event.t, event.agv, event.node]), (AgvMoveEnded,))
    return env, layout, machines, order, trajectory


def fixture(*, rotation=90, width=6, file=None, record=None):
    env, layout, machines, order, trajectory = model(rotation=rotation, width=width, file=file, record=record)
    result = layout.prepare()
    # Geometric validity applies also to arbitrary rotations: nondegenerate rectangle,
    # expected side lengths, finite points, and all required directed paths resolved.
    obstacle = result.network.definitions.obstacles[0]
    vertices = obstacle.vertices
    distances = [float(point_distance_squared(vertices[i], vertices[(i + 1) % 4])) for i in range(4)]
    assert all(math.isfinite(p.x) and math.isfinite(p.y) for p in vertices)
    assert all(math.isclose(a, b, rel_tol=1e-12) for a, b in zip(sorted(distances), [4, 4, width**2, width**2]))
    assert result.required_paths
    env.run()
    assert order.status.name == "COMPLETED"
    fingerprint = asdict(env.fingerprint())
    env.close()
    if record is not None:
        trace = Trace.open(record)
        trace.check()
        assert trace.verify() is True and trace.layout == result.bundle
    return {
        "hashes": result.bundle.manifest(),
        "geometry": result.bundle.to_dict()["network"],
        "trajectory": trajectory,
        "fingerprint": fingerprint,
        "machine_ids": [machine.id for machine in machines],
        "diagnostics": [asdict(d) for d in result.diagnostics],
    }


def preparation(nodes, memory):
    """Exactly 1k/10k nodes in a 4-neighbor grid, no obstacles or consumers."""
    import tracemalloc  # CPython-only probe; PyPy fixtures need no tracemalloc extension.

    assert nodes in (1000, 10000)
    nx, ny = (40, 25) if nodes == 1000 else (100, 100)
    env = Environment(seed=17)
    layout = Layout(env)
    layout.set_grid(Grid(1, 1))
    layout.set_network(Lattice((0, 0, nx - 1, ny - 1)))
    gc.collect()
    if memory:
        tracemalloc.start()
    start = time.perf_counter()
    result = layout.prepare()
    elapsed = time.perf_counter() - start
    peak = tracemalloc.get_traced_memory()[1] if memory else None
    if memory:
        tracemalloc.stop()
    assert len(result.network.graph.nodes) == nodes
    # Read actual uncompressed wire payload lengths, not JSON or total trace sizes.
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "initial.simtrace"
        TraceRecorder(env, path)
        env.activate()
        env.close()
        with path.open("rb") as stream:
            stream.seek(PREAMBLE.size)
            while True:
                length, kind, _ = RECORD_HEADER.unpack(stream.read(RECORD_HEADER.size))
                if kind == RecordType.INITIAL:
                    initial_bytes = length
                    break
                stream.seek(length, 1)
    from simulatte._wire import pack

    return {
        "nodes": nodes,
        "grid": [nx, ny],
        "directed_arcs": sum(len(result.network.graph.neighbors(node)) for node in result.network.graph.nodes),
        "prepare_seconds": elapsed,
        "memory_probe": memory,
        "tracemalloc_peak_bytes": peak,
        "process_maxrss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "maxrss_units": "bytes" if sys.platform == "darwin" else "KiB",
        "initial_payload_bytes": initial_bytes,
        "layout_bundle_encoded_bytes": len(pack(result.bundle.to_dict())),
        "hashes": result.bundle.manifest(),
    }


def package_smoke(guide):
    import simulatte
    import simulatte.intralogistics
    import simulatte.trace

    for name in ("matplotlib", "numpy", "gymnasium", "jsonschema"):
        assert importlib.util.find_spec(name) is None, f"unexpected optional package: {name}"
    assert str(Path(simulatte.__file__).resolve()).startswith(str(Path(sys.prefix).resolve()))
    schema = json.loads(
        importlib.resources.files("simulatte.layout.schema").joinpath("layout-v1.schema.json").read_text()
    )
    assert schema["$schema"]
    with tempfile.TemporaryDirectory() as directory:
        result = fixture(record=Path(directory) / "roundtrip.simtrace")
    blocks = re.findall(r"```python(?: \{ \.run \})?\n(.*?)\n```", guide.read_text(), re.DOTALL)
    assert len(blocks) == 8
    for block in blocks:
        exec(compile(block, str(guide), "exec"), {"__name__": "__main__"})
    return {"package": simulatte.__file__, "prefix": sys.prefix, "snippets": len(blocks), "fixture": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("fixture", "prepare", "package"))
    parser.add_argument("--rotation", type=float, default=90)
    parser.add_argument("--nodes", type=int, default=1000)
    parser.add_argument("--memory", action="store_true")
    parser.add_argument("--guide", type=Path)
    args = parser.parse_args()
    if args.mode == "fixture":
        data = fixture(rotation=args.rotation)
    elif args.mode == "prepare":
        data = preparation(args.nodes, args.memory)
    else:
        data = package_smoke(args.guide)
    print(json.dumps({"runtime": sys.version, "platform": platform.platform(), "result": data}, sort_keys=True))


if __name__ == "__main__":
    main()
