"""Fresh-process lazy input with recording enabled; compare observation budgets."""

import argparse
import json
from pathlib import Path
import resource
import sys
import time

from simulatte import Environment, Trace, TraceRecorder
from simulatte.experimental.native import Job, NativeObservation, NativeProduction
from simulatte.stopping import Horizon, execute_until


def main(args):
    assert args.jobs > 0 and args.jobs % 16 == 0
    cursor, next_tick, queries = 0, 0.0, 0

    class Backend(NativeProduction):
        def kpi_values(self):
            nonlocal queries
            queries += 1
            return super().kpi_values()

    started = time.perf_counter()
    with Environment(seed=0) as env:
        policy = Horizon(args.jobs // 16 * 32)
        env._execution_policy = policy
        recorder = TraceRecorder(env, args.output.with_suffix(".simtrace"), level="kpi")
        backend = Backend(
            3,
            capacities=(3, 1, 2),
            wip_cap=5,
            retention=args.retention,
            input_state=lambda: dict(cursor=cursor, next_tick=next_tick, jobs=args.jobs),
            observation=NativeObservation(interval=0.1, max_samples=args.limit) if args.limit else None,
        )
        env.attach_execution_backend(backend)

        def source():
            nonlocal cursor, next_tick
            while cursor < args.jobs:
                yield env.timeout(next_tick - env.now)
                for i in range(16):
                    backend.add(Job(env.now, (i % 3, (i + 1) % 3), (0.5, 0.25), env.now + 6))
                    cursor += 1
                next_tick += 32

        env.process(source())
        execute_until(env, policy)
        stats = backend._stream.storage_stats()
        assert stats["completed"] == args.jobs
        if args.retention == "bounded":
            assert stats["retained_jobs"] == 0
        recorder.close()
        fingerprint = env.fingerprint()
        value = dict(
            retention=args.retention,
            jobs=args.jobs,
            limit=args.limit,
            queries=queries,
            wall_seconds=time.perf_counter() - started,
            storage=stats,
            native_state=backend.state_digest(),
            fingerprint=dict(digest=fingerprint.digest, kpis=dict(fingerprint.kpis)),
            trace_bytes=args.output.with_suffix(".simtrace").stat().st_size,
            peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            * (1 if sys.platform == "darwin" else 1024),
        )
    trace = Trace.open(args.output.with_suffix(".simtrace"))
    value["samples"] = {key: len(points) for key, points in trace.kpi_series().items()}
    assert not value["samples"] if args.limit == 0 else all(n == args.limit for n in value["samples"].values())
    args.output.write_text(json.dumps(value, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retention", choices=("full", "bounded"), required=True)
    parser.add_argument("--limit", type=int, required=True)
    parser.add_argument("--jobs", type=int, default=65536)
    parser.add_argument("output", type=Path)
    main(parser.parse_args())
