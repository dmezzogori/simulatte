"""Deterministic renderer stress recording: 1,000 real AGV entities on 50 lanes.

Motion is installed before activation to isolate projection/render cost from fleet dispatch.
This is a renderer workload, not a transport-throughput model.
"""

from __future__ import annotations
from pathlib import Path
from typing import cast
from simulatte import Environment, TraceRecorder, Trace
from simulatte._wire import freeze, FrozenMap
from simulatte.intralogistics.agv import AGV, AGVType, AGVState
from simulatte.intralogistics.graph import Node


class Speed:
    def travel_time(self, distance, load_weight=0, battery_level=1, speed_limit=None):
        return distance


path = Path("/tmp/sp3-glyphs1000.simtrace")
with Environment(seed=312) as env:
    recorder = TraceRecorder(env, path)
    kind = AGVType("renderer", Speed(), 100, 100, 100)
    for lane in range(50):
        a, b = Node(f"lane-{lane}-a", 0, lane), Node(f"lane-{lane}-b", 100, lane)
        env.entities.bind_node(a)
        env.entities.bind_node(b)
        for i in range(20):
            agv = AGV(env=env, agv_type=kind, agv_id=f"agv-{lane}-{i}", initial_node=a)
            agv.transition_to(AGVState.TRAVELING_EMPTY)
            agv.motion = cast(
                FrozenMap,
                freeze(
                    {
                        "from": a.id,
                        "to": b.id,
                        "t_start": -i * 4,
                        "t_end": 100 - i * 4,
                        "description": {"curve": "linear"},
                    }
                ),
            )
    env.run(until=100)
    recorder.close()
trace = Trace.open(path)
trace.check()
assert trace.verify()
print(path, path.stat().st_size)
