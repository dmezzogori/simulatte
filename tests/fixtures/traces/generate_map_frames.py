"""Frame fixtures for the Studio map: ``python tests/fixtures/traces/generate_map_frames.py [--out DIR]``.

Each fixture is one JSON file ``{time, cursor, state, initialState, layout}``, the parts of a ``PreparedFrame`` that
come from a recording. The TypeScript tests (``studio/packages/scene/test/fixtures.ts``) and the render harness
(``studio/harness``) load them, so the map is developed and looked at without a simulation run.

The four intralogistics examples are recorded as ``generate.record_example`` records them (the corridor with seed 19,
the advanced one with its own seed 42 and ``main(plot=False)``, the simple and the intermediate one with seed 0). The
state is the trace's ``state_at`` and the layout is the trace's bundle, cut down to what the scene reads:
``schema_version``, ``resolved``, ``network.graph``, ``bindings`` and ``targets``. Every other key of the bundle
(hashes, layers, leaves, dependencies, auto, aliases, algorithms, stages, topology, diagnostics, required_paths, the
other ``network`` keys) is dropped.

Everything the map reads (spec §6: the vehicle size, the facilities' ``node_ids`` and positions, ``wait``, ``route``,
the capacities and the fleet counters) is recorded; nothing is added by hand except in ``two-fleets`` (a second fleet)
and ``sprites``, which is built by hand. The generator fails when an example no longer gives the measured facts the
tests rely on (:func:`check_corridor`, :func:`check_advanced`, :func:`check_moving`). ``bare-layout`` stands for a
trace recorded before Stage 2: the corridor example is recorded for it once more with the facilities' ``node_ids``
withheld (:func:`without_node_ids`, which leaves them in the grid), and the vehicle size, ``wait``, ``route``,
capacities and counters it records are deleted from it (:func:`unsized`, :func:`unrouted`, :func:`uncounted`).
"""

from __future__ import annotations

import argparse
import io
import json
import math
import runpy
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate
from simulatte import Trace, TraceRecorder
from simulatte.environment import Environment
from simulatte.layout.layout import Layout
from simulatte.trace import ChunkLimits

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "studio" / "fixtures" / "map"
LARGE_SEQ = 10**9
"""A sequence number above every event: ``state_at((t, LARGE_SEQ))`` is the state after everything at time `t`."""

Plain = dict[str, Any]


# ---------------------------------------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------------------------------------


def record_main(path: Path, example: str, *, seed: int, **kwargs: Any) -> None:
    """Run ``main(**kwargs)`` of ``examples/<example>.py`` with its environment recording to `path`.

    As ``generate.record_example``, which cannot pass arguments to ``main``: the script's ``Environment`` is replaced,
    in its namespace only, by a subclass that fixes the seed and the provenance and attaches the recorder.
    """
    limits = ChunkLimits(max_events=200, max_bytes=1 << 30, max_latency_s=3600.0)

    class Recorded(Environment):
        def __init__(self, **_: Any) -> None:  # the script's own seed is replaced by `seed`
            super().__init__(seed=seed, provenance=generate.PROVENANCE)
            TraceRecorder(self, path, chunk_limits=limits)

    main = runpy.run_path(str(generate.EXAMPLES / f"{example}.py"), run_name="__headless__")["main"]
    main.__globals__["Environment"] = Recorded
    with generate.fixed_volatile_metadata(), redirect_stdout(io.StringIO()):
        main(**kwargs)


# ---------------------------------------------------------------------------------------------------------
# What a frame holds
# ---------------------------------------------------------------------------------------------------------


def reduce_layout(bundle: Plain | None) -> Plain | None:
    """The layout of a fixture: only what the scene reads (see the module docstring)."""
    if bundle is None:
        return None
    network = bundle["network"]
    return {
        "bindings": bundle["bindings"],
        "network": None if network is None else {"graph": network["graph"]},
        "resolved": bundle["resolved"],
        "schema_version": bundle["schema_version"],
        "targets": bundle["targets"],
    }


class Recording:
    """An opened trace with its events read once, as plain data."""

    def __init__(self, path: Path) -> None:
        self.trace = Trace.open(path)
        assert self.trace.cursor_range is not None
        self.events = [(e.t, e.seq, e.type, generate.plain(e.payload)) for e in self.trace.events()]
        self.layout = reduce_layout(generate.plain(self.trace.layout.to_dict()) if self.trace.layout else None)

    def state(self, cursor: tuple[float, int]) -> Plain:
        return generate.plain(self.trace.state_at(cursor))

    def cursor_at(self, t: float) -> tuple[float, int]:
        """The cursor of the last event at or before time `t`, or the activation cursor."""
        assert self.trace.cursor_range is not None
        found = self.trace.cursor_range[0]
        for time, seq, _type, _payload in self.events:
            if time > t:
                break
            found = (time, seq)
        return found


# ---------------------------------------------------------------------------------------------------------
# A trace recorded before Stage 2, for ``bare-layout``
# ---------------------------------------------------------------------------------------------------------


def unsized(layout: Plain) -> Plain:
    """`layout` as a trace recorded before the vehicle size: no ``size`` in the targets, vehicles 1 x 1 m (§6.1).

    ``bare-layout`` stands for such a trace; the corridor example records its vehicles' size since Task 11."""
    layout = json.loads(json.dumps(layout))
    for name, target in layout["targets"].items():
        if target.pop("size", None) is not None:
            layout["resolved"]["placements"][name]["footprint"].update(width=1.0, depth=1.0)
    return layout


def unrouted(frame: Plain) -> Plain:
    """`frame` as a trace recorded before Task 13: no vehicle has a ``wait`` or a ``route`` field (spec §6.2)."""
    for state in (frame["state"], frame["initialState"]):
        for fields in state.values():
            if fields["$kind"] == "agv":
                del fields["wait"], fields["route"]
    return frame


RECORDED_SINCE_TASK_14 = {
    "agv": ("battery_capacity",),
    "parking_area": ("capacity",),
    "charging_station": ("slots",),
    "fleet": ("waits", "stuck_waits", "deadlocks"),
}
"""The capacities and the fleet counters (spec §6.4, §6.6), by entity kind."""


def uncounted(frame: Plain) -> Plain:
    """`frame` as a trace recorded before Task 14: no capacity and no fleet counter (spec §6.4, §6.6)."""
    for state in (frame["state"], frame["initialState"]):
        for fields in state.values():
            for name in RECORDED_SINCE_TASK_14.get(fields["$kind"], ()):
                del fields[name]
    return frame


@contextmanager
def without_node_ids() -> Iterator[None]:
    """Record as before Task 12: no facility target carries ``node_ids``, so the placement packs them in the grid."""
    bound_nodes = Layout._bound_nodes
    Layout._bound_nodes = lambda self, refs: None
    try:
        yield
    finally:
        Layout._bound_nodes = bound_nodes


def frame_of(recording: Recording, t: float) -> Plain:
    """The fixture of `recording` at time `t`: the state after every event at `t`."""
    cursor = recording.cursor_at(t)
    bounds = recording.trace.cursor_range
    assert bounds is not None
    return {
        "cursor": [cursor[0], cursor[1]],
        "initialState": recording.state(bounds[0]),
        "layout": recording.layout,
        "state": recording.state((t, LARGE_SEQ)),
        "time": t,
    }


# ---------------------------------------------------------------------------------------------------------
# Measured facts (globals.md): the generator fails when the examples no longer give them
# ---------------------------------------------------------------------------------------------------------


def _strips(layout: Plain) -> dict[frozenset[str], set[tuple[str, str]]]:
    strips: dict[frozenset[str], set[tuple[str, str]]] = {}
    for arc in layout["network"]["graph"]["arcs"]:
        strips.setdefault(frozenset((arc["source"], arc["target"])), set()).add((arc["source"], arc["target"]))
    return strips


def check_corridor(frame: Plain) -> None:
    state, layout = frame["state"], frame["layout"]
    graph = layout["network"]["graph"]
    assert (len(graph["nodes"]), len(graph["arcs"])) == (13, 22), "corridor graph"
    assert not any(arc.get("passing") for arc in graph["arcs"]), "no arc has passing"
    strips = _strips(layout)
    assert len(strips) == 15, "15 strips"
    one_way = {tuple(sorted(s)) for s, arcs in strips.items() if len(arcs) == 1}
    assert one_way == {
        ("J1", "W_N"), ("W", "W_N"), ("W", "W_S"), ("J1", "W_S"),
        ("E_S", "J2"), ("E", "E_S"), ("E", "E_N"), ("E_N", "J2"),
    }, one_way  # fmt: skip
    a1, a2, a3 = state["AGV-1"], state["AGV-2"], state["AGV-3"]
    assert (a1["node"], a1["state"], a1["motion"], a1["load"], a1["order"]) == (
        "C", "TRAVELING_LOADED", None, {"crate": 1}, "order-0"
    )  # fmt: skip
    assert a1["heading"] == 0.0
    motion = a2["motion"]
    assert (motion["from"], motion["to"], a2["state"], a2["order"]) == ("M2", "D", "TRAVELING_LOADED", "order-1")
    assert math.isclose(a2["heading"], math.pi) and a2["load"] == {"crate": 1}
    # position along the piecewise description: 8.5 s at 1.5 m/s, then a deceleration of 0.5 m/s2
    elapsed = frame["time"] - motion["t_start"]
    run = 1.5 * elapsed if elapsed <= 8.5 else 12.75 + 1.5 * (elapsed - 8.5) - 0.25 * (elapsed - 8.5) ** 2
    assert math.isclose(70.0 - run, 57.53, abs_tol=0.01), "AGV-2 at x = 57.53"
    assert (a3["node"], a3["state"], a3["motion"]) == ("W", "WAITING_LOAD", None)
    assert math.isclose(a3["heading"], -2.356, abs_tol=1e-3)
    # held (§5.5): reserved by a vehicle itself, or a member of a zone that is
    members = {m for v in state.values() if v["$kind"] == "zone" and v["reserved_by"] for m in v["members"]}
    held = {k for k, v in state.items() if v["$kind"] == "segment" and (v["forward"] or v["backward"])} | {
        k for k, v in state.items() if v["$kind"] == "node" and v["reserved_by"]
    }
    held |= members
    held_strips = {k for k in held if state[k]["$kind"] == "segment"}
    assert held_strips == {"C~M1", "J1~M1", "D~M2", "J2~M2", "W~W_N"}, held_strips
    held_nodes = {k for k in held if state[k]["$kind"] == "node"}
    assert held_nodes == {"C", "D", "W", "M1", "M2"}, held_nodes
    assert not state["C~D"]["forward"] and not state["C~D"]["backward"]
    assert (state["East"]["inventory"], state["West"]["inventory"]) == ({"crate": 49.0}, {"crate": 48.0})
    pending = {k: v["origin"] for k, v in state.items() if v["$kind"] == "order" and v["status"] == "PENDING"}
    assert pending == {"order-3": "East", "order-4": "West"}, pending
    assert state["Siding"]["parked"] == []
    wait = a1["wait"]
    assert math.isclose(wait["since"], 121.5426, abs_tol=1e-4) and wait["node"] == "D", wait
    assert wait["resources"] == ["corridor:M2", "C~D", "D"] and wait["blockers"] == ["AGV-2"], wait
    assert a1["route"]["nodes"] == ["W", "W_S", "J1", "M1", "C", "D", "M2", "J2", "E_S", "E"]
    assert a2["wait"] is None and a3["wait"] is None
    assert a3["route"] is None, "AGV-3 arrived at 103.757"
    fleet = state["tuggers"]
    assert (fleet["waits"], fleet["deadlocks"], fleet["stuck_waits"]) == (2, 0, 0), fleet
    assert state["Siding"]["capacity"] == 3
    # the type has length 1.2 and no width: size [1.2, 0.6] and the footprint 1.2 x 0.6 (spec §6.1)
    for name in ("AGV-1", "AGV-2", "AGV-3"):
        footprint = layout["resolved"]["placements"][name]["footprint"]
        assert layout["targets"][name]["size"] == [1.2, 0.6], name
        assert (footprint["width"], footprint["depth"]) == (1.2, 0.6), name


def check_moving(frame: Plain, expected: dict[str, tuple[str, str, str]]) -> None:
    """Every vehicle of `frame` is loaded and moves: its recorded state and the arc under way are `expected`."""
    agvs = {k: v for k, v in frame["state"].items() if v["$kind"] == "agv"}
    found = {k: (v["state"], v["motion"]["from"], v["motion"]["to"]) for k, v in agvs.items() if v["motion"]}
    assert found == expected, found
    assert all(v["load"] for v in agvs.values()), "every vehicle is loaded"


def check_advanced(frame: Plain) -> None:
    state = frame["state"]
    assert not any(v["$kind"] in {"segment", "zone"} for v in state.values()), "no ReservationController"
    layout = frame["layout"]
    for name, placement in layout["resolved"]["placements"].items():
        if layout["targets"][name]["kind"] == "agv":
            assert placement["footprint"]["width"] == placement["footprint"]["depth"] == 1.0, "vehicles stay 1 x 1"
            assert "size" not in layout["targets"][name]
    agv3 = state["AGV-3"]
    assert (agv3["state"], agv3["node"]) == ("CHARGING", "CHRG"), agv3
    assert state["Charger"]["slots_in_use"] == 1
    assert state["Parking"]["parked"] == ["AGV-1", "AGV-2", "AGV-5"], state["Parking"]


# ---------------------------------------------------------------------------------------------------------
# The sprites fixture: hand-built, no simulation
# ---------------------------------------------------------------------------------------------------------


def sprites_frame() -> Plain:
    """Every sprite and every unusual case of spec §5.4 in one frame (time 50), on a row of 12 nodes 7 m apart."""
    step, ids = 7.0, [f"n{i}" for i in range(12)]
    nodes = {n: (i * step, 0.0) for i, n in enumerate(ids)}

    def arcs(a: str, b: str, *, both: bool, passing: bool = False) -> list[Plain]:
        length = step
        pairs = [(a, b), (b, a)] if both else [(a, b)]
        return [{"length": length, "passing": passing, "source": s, "speed_limit": None, "target": t} for s, t in pairs]

    graph_arcs = [
        *arcs("n0", "n1", both=False),  # one-way
        *arcs("n1", "n2", both=True),  # two-way
        *arcs("n2", "n3", both=True, passing=True),  # two-way and passing
        *arcs("n3", "n4", both=False),  # one-way
        *arcs("n4", "n5", both=False, passing=True),  # one-way and passing
        *arcs("n5", "n6", both=True),
        *arcs("n6", "n7", both=True),
        *arcs("n7", "n8", both=True),
        *arcs("n8", "n9", both=False),
        *arcs("n9", "n10", both=True),
        *arcs("n10", "n11", both=True),
    ]
    hostile = "</script><img src=x onerror=alert(1)>"
    long_label = "0123456789" * 50
    rtl = "‮evil‏ label שלום"

    def vehicle(
        label: str,
        node: str,
        status: str,
        *,
        heading: float | None = 0.0,
        load: Plain | None = None,
        wait: Plain | None = None,
        route: Plain | None = None,
        motion: Plain | None = None,
        order: str | None = None,
    ) -> Plain:
        return {
            "$kind": "agv",
            "battery": 80.0,
            "battery_capacity": 100.0,
            "fleet": "fleet",
            "heading": heading,
            "label": label,
            "load": load,
            "motion": motion,
            "node": node,
            "order": order,
            "route": route,
            "state": status,
            "wait": wait,
        }

    vehicles: dict[str, Plain] = {
        # one vehicle for each row of the §5.4 table, then the unusual cases
        "A1": vehicle(
            "A1", "n1", "STRANDED", wait={"blockers": ["A2"], "node": "n2", "resources": ["n1~n2"], "since": 40.0}
        ),
        "A2": vehicle(
            "A2",
            "n2",
            "TRAVELING_EMPTY",
            wait={"blockers": ["A3"], "node": "n3", "resources": ["n2~n3", "n3"], "since": 44.5},
        ),
        "A3": vehicle(
            "A3",
            "n3",
            "TRAVELING_LOADED",
            load={"box": 2},
            order="order-3",
            motion={"description": {"curve": "linear"}, "from": "n3", "t_end": 60.0, "t_start": 45.0, "to": "n4"},
            route={
                "destination": "n11",
                "nodes": ["n3", "n4", "n5", "n6", "n7", "n8", "n9", "n10", "n11"],
                "reason": "travel",
            },
        ),  # fmt: skip
        "A4": vehicle("A4", "n9", "WAITING_LOAD", heading=0.0),
        "A5": vehicle("A5", "n0", "IDLE", heading=None),
        "A6": vehicle("A6", "n8", "CHARGING", heading=1.5707963267948966),
        "A7": vehicle(
            "A7",
            "n5",
            "TRAVELING_EMPTY",
            heading=0.0,
            route={"destination": "n7", "nodes": ["n5", "n6", "n7"], "reason": "travel"},
        ),  # fmt: skip
        "A8": vehicle("A8", "n4", "REBOOTING"),  # a state the table does not know
        "P1": vehicle("P1", "n6", "IDLE"),
        "P2": vehicle("P2", "n6", "IDLE"),
        "P3": vehicle("P3", "n6", "IDLE"),
        "H1": vehicle(hostile, "n11", "IDLE"),
        "H2": vehicle(long_label, "n7", "IDLE"),
        "H3": vehicle(rtl, "n10", "WAITING_UNLOAD", heading=3.141592653589793),
    }
    node_states = {
        n: {"$kind": "node", "agvs": [], "label": n, "reserved_by": [], "x": x, "y": y} for n, (x, y) in nodes.items()
    }
    for vid, v in vehicles.items():
        node_states[v["node"]]["agvs"].append(vid)
    node_states["n2"]["reserved_by"] = ["A2"]
    segments = {
        "n1~n2": {
            "$kind": "segment",
            "backward": [],
            "forward": ["A1"],
            "label": "n1~n2",
            "length": step,
            "source": "n1",
            "target": "n2",
        },  # fmt: skip
        "n3~n4": {
            "$kind": "segment",
            "backward": [],
            "forward": ["A3"],
            "label": "n3~n4",
            "length": step,
            "source": "n3",
            "target": "n4",
        },  # fmt: skip
    }
    orders = {
        f"order-{i}": {
            "$kind": "order",
            "agv": agv,
            "created_at": 10.0 * i,
            "delivered_at": None,
            "destination": "Dock",
            "dispatched_at": None,
            "fleet": "fleet",
            "label": f"order-{i}",
            "origin": "Store",
            "picked_at": None,
            "quantity": 1,
            "sku": "bolts",
            "status": status,
        }  # fmt: skip
        for i, (agv, status) in {1: (None, "PENDING"), 2: (None, "PENDING"), 3: ("A3", "IN_TRANSIT")}.items()
    }
    facilities = {
        "Store": {
            "$kind": "warehouse",
            "inventory": {"bolts": 40.0, "nuts": 12.0, "washers": 7.0},
            "label": "Store",
            "slots_in_use": 0,
        },  # fmt: skip
        "Dock": {"$kind": "warehouse", "inventory": {"bolts": 3.0}, "label": "Dock", "slots_in_use": 0},
        "Lot": {"$kind": "parking_area", "capacity": 4, "label": "Lot", "parked": ["P1", "P2", "P3"]},
        "Charger-A": {
            "$kind": "charging_station",
            "label": "Charger-A",
            "slots": 2,
            "slots_in_use": 1,
            "swap_pool": None,
        },
        "Charger-B": {
            "$kind": "charging_station",
            "label": "Charger-B",
            "slots": 1,
            "slots_in_use": 0,
            "swap_pool": None,
        },
        "Belt": {"$kind": "conveyor", "label": "Belt", "speed": 0.5},
        "fleet": {
            "$kind": "fleet",
            "deadlocks": 0,
            "label": "fleet",
            "pending": ["order-1", "order-2"],
            "stuck_waits": 0,
            "waits": 2,
        },  # fmt: skip
    }
    state: Plain = {**node_states, **segments, **vehicles, **orders, **facilities}

    initial: Plain = {k: json.loads(json.dumps(v)) for k, v in state.items() if v["$kind"] != "order"}
    for fields in initial.values():
        if fields["$kind"] == "agv":
            fields.update(heading=None, load=None, motion=None, order=None, route=None, state="IDLE", wait=None)
        elif fields["$kind"] in {"segment"}:
            fields.update(backward=[], forward=[])
        elif fields["$kind"] == "node":
            fields["reserved_by"] = []
        elif fields["$kind"] == "parking_area":
            fields["parked"] = []
        elif fields["$kind"] == "fleet":
            fields.update(pending=[], waits=0)
    for name in ("Charger-A",):
        initial[name]["slots_in_use"] = 0

    # layout: nodes at their graph position, the vehicles on their first node, facilities beside their bays
    def placement(kind: str, position: tuple[float, float], width: float, depth: float) -> Plain:
        return {
            "footprint": {"depth": depth, "height": 0.0, "width": width},
            "kind": kind,
            "obstacle": False,
            "ports": {},
            "position": [position[0], position[1], 0.0],
            "rotation": 0.0,
            "visual": {},
        }

    placements: Plain = {n: placement("node", xy, 0.1, 0.1) for n, xy in nodes.items()}
    targets: Plain = {
        n: {"explicitly_named": True, "explicitly_placed": False, "graph_position": [*xy, 0.0], "kind": "node"}
        for n, xy in nodes.items()
    }
    for vid, v in vehicles.items():
        placements[vid] = placement("agv", nodes[v["node"]], 1.2, 0.6)
        targets[vid] = {
            "explicitly_named": True, "explicitly_placed": False, "kind": "agv", "node_id": v["node"],
            "size": [1.2, 0.6],
        }  # fmt: skip
    bindings: Plain = {}

    def bind(key: str, node: str) -> None:
        bindings[key] = {"address": {"id": node, "kind": "explicit"}, "endpoint": node}

    # Store: first input bay n9 (neighbours n8 one-way in and n10), direction +y when the mean is zero; here the mean of
    # the unit vectors (-1, 0) and (1, 0) is zero: (63, 0) + 1.5 * (0, 1) = (63, 1.5). Dock: bay n11, neighbour n10:
    # direction (1, 0): (77, 0) + 1.5 * (1, 0) = (78.5, 0). Lot is centred on n6, both chargers on n8.
    facility_places = {
        "Store": ("warehouse", (63.0, 1.5), 1.0, 1.0, ["n9", "n10"]),
        "Dock": ("warehouse", (78.5, 0.0), 1.0, 1.0, ["n11"]),
        "Lot": ("parking_area", (42.0, 0.0), 3.0, 3.0, ["n6"]),
        "Charger-A": ("charging_station", (56.0, 0.0), 3.0, 3.0, ["n8"]),
        "Charger-B": ("charging_station", (56.0, 0.0), 3.0, 3.0, ["n8"]),
    }
    for name, (kind, xy, width, depth, node_ids) in facility_places.items():
        placements[name] = placement(kind, xy, width, depth)
        targets[name] = {"explicitly_named": True, "explicitly_placed": False, "kind": kind, "node_ids": node_ids}
    bind("Store:input_bays/0", "n9")
    bind("Store:output_bays/0", "n10")
    bind("Dock:input_bays/0", "n11")
    bind("Dock:output_bays/0", "n11")
    bind("Lot:node", "n6")
    bind("Charger-A:node", "n8")
    bind("Charger-B:node", "n8")
    placements["Belt"] = placement("conveyor", (20.0, -6.0), 6.0, 1.0)
    targets["Belt"] = {"explicitly_named": True, "explicitly_placed": True, "kind": "conveyor"}

    layout = {
        "bindings": bindings,
        "network": {
            "graph": {
                "arcs": graph_arcs,
                "nodes": [{"capacity": None, "id": n, "position": [*xy, 0.0]} for n, xy in nodes.items()],
            }
        },
        "resolved": {"placements": placements, "schema_version": "1.1"},
        "schema_version": "1.1",
        "targets": targets,
    }
    return {"cursor": [50.0, 100], "initialState": initial, "layout": layout, "state": state, "time": 50.0}


# ---------------------------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------------------------


def waiting_moving_idle(state: Plain) -> bool:
    """One vehicle waiting, one moving and one idle (the first row of the §5.4 table that applies decides)."""
    shown: list[str] = []
    for fields in state.values():
        if fields["$kind"] != "agv":
            continue
        if fields["wait"] is not None:
            shown.append("waiting")
        elif fields["motion"] is not None:
            shown.append("moving")
        elif fields["state"] == "IDLE":
            shown.append("idle")
        else:
            shown.append("other")
    return sorted(shown) == ["idle", "moving", "waiting"]


def dump(document: Plain) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the frame fixtures of the Studio map.")
    parser.add_argument("--out", type=Path, default=OUT, help="output directory")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    frames: dict[str, Plain] = {}

    with tempfile.TemporaryDirectory() as tmp:
        corridor = Path(tmp) / "corridor.simtrace"
        advanced = Path(tmp) / "advanced.simtrace"
        production = Path(tmp) / "production.simtrace"
        bare = Path(tmp) / "bare.simtrace"
        simple = Path(tmp) / "simple.simtrace"
        intermediate = Path(tmp) / "intermediate.simtrace"
        record_main(corridor, "intralogistics_traffic", seed=19)
        with without_node_ids():
            record_main(bare, "intralogistics_traffic", seed=19)
        record_main(advanced, "intralogistics_advanced", seed=42, plot=False)
        record_main(simple, "intralogistics_simple", seed=0)
        record_main(intermediate, "intralogistics_intermediate", seed=0, plot=False)
        sys.path.insert(0, str(generate.EXAMPLES))
        import studio_recording

        studio_recording.record(production, "shop")

        c, a, p = Recording(corridor), Recording(advanced), Recording(production)
        assert c.layout is not None and a.layout is not None

        corridor_wait = frame_of(c, 128.0)
        check_corridor(corridor_wait)
        frames["corridor-wait"] = corridor_wait

        # the first whole second in 1000..1015 with one vehicle waiting, one moving and one idle
        found: float | None = None
        for second in range(1000, 1016):
            if waiting_moving_idle(frame_of(c, float(second))["state"]):
                found = float(second)
                break
        if found is None:
            raise SystemExit("no whole second in 1000..1015 has one vehicle waiting, one moving and one idle")
        print(f"corridor-idle: t = {found}")
        frames["corridor-idle"] = frame_of(c, found)

        advanced_frame = frame_of(a, 22960.0)
        check_advanced(advanced_frame)
        frames["advanced-charging"] = advanced_frame

        # the two other examples, at an instant with every vehicle loaded and moving (acceptance 1 of the spec)
        simple_frame = frame_of(Recording(simple), 12.0)
        check_moving(simple_frame, {v: ("TRAVELING_LOADED", "N1", "N2") for v in ("vehicle-0", "vehicle-1")})
        frames["simple-moving"] = simple_frame
        intermediate_frame = frame_of(Recording(intermediate), 78.0)
        check_moving(
            intermediate_frame,
            {
                "AGV-1": ("TRAVELING_LOADED", "C3", "FG_IN"),
                "AGV-2": ("TRAVELING_LOADED", "C1", "C2"),
                "AGV-3": ("TRAVELING_LOADED", "RM_OUT", "C1"),
            },
        )
        frames["intermediate-moving"] = intermediate_frame

        two_fleets = json.loads(json.dumps(corridor_wait))
        two_fleets["state"]["AGV-3"]["fleet"] = "shuttles"
        frames["two-fleets"] = two_fleets

        frames["bare-layout"] = uncounted(unrouted(frame_of(Recording(bare), 128.0)))
        frames["bare-layout"]["layout"] = unsized(frames["bare-layout"]["layout"])
        no_layout = json.loads(json.dumps(corridor_wait))
        no_layout["layout"] = None
        frames["no-layout"] = no_layout
        frames["production"] = frame_of(p, 60.0)
        frames["sprites"] = sprites_frame()

        for name in ("corridor-wait", "corridor-idle"):
            for vid, fields in sorted(frames[name]["state"].items()):
                if fields["$kind"] == "agv":
                    route = fields.get("route")
                    shown = None if route is None else {"destination": route["destination"], "nodes": route["nodes"]}
                    print(f"{name} {vid}: node={fields['node']} state={fields['state']} route={shown}")

    for name, frame in frames.items():
        (args.out / f"{name}.json").write_text(dump(frame), encoding="utf-8")
        print(args.out / f"{name}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
