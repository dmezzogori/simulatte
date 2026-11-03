"""Serial cold-process whole-memory gate; timings stay outside semantic state."""

import argparse
from datetime import datetime, UTC
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import resource
import statistics
import subprocess
import sys
import time

from .build import ROOT, REPO, sources
from .producer import Producer, encoded
from .stream import Stream, verify


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rss():
    return int(subprocess.check_output(["ps", "-o", "rss=", "-p", str(os.getpid())], text=True)) * 1024


def peak_rss():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def worker(args):
    before = verify()
    producer = Producer(args.seed, args.spacing)
    input_hash = hashlib.sha256()
    t0 = time.perf_counter()

    def controller_state():
        return dict(producer.state(), limit=args.jobs, window=args.window)

    env = None
    if args.execution == "environment":
        from simulatte import Environment
        from simulatte.stopping import Horizon, execute_until
        from .backend import StreamingProduction

        backend = StreamingProduction(3, input_state=controller_state, retention=args.retention, wip_cap=12)
        env = Environment(seed=0)
        env.enable_digest()
        env._execution_policy = Horizon(args.jobs * args.spacing, warmup=100)
        env.configure_kpis(warmup=100)
        env.attach_execution_backend(backend, scope="shopfloor")
        stream = backend._stream
    else:
        stream = Stream(3, wip_cap=12, policy="spt", warmup=100, retention=args.retention, input_state=controller_state)
    create = time.perf_counter() - t0
    samples, query_times = [], []
    max_jobs = max_ops = max_native = 0

    def observe():
        nonlocal max_jobs, max_ops, max_native
        started = time.perf_counter()
        state = stream.state_bytes()
        query_times.append(time.perf_counter() - started)
        stats = stream.stats()
        max_jobs = max(max_jobs, stats["retained_jobs"])
        max_ops = max(max_ops, stats["retained_operations"])
        samples.append(
            dict(
                submitted=producer.cursor,
                native_time=stream.now,
                current_rss=rss(),
                peak_rss=peak_rss(),
                stats=stats,
                state_bytes=len(state),
                state_sha256=hashlib.sha256(state).hexdigest(),
                query_seconds=query_times[-1],
            )
        )
        max_native = max(max_native, stats["native_bytes"])

    observe()
    milestones = {64, 1024, 8192, 16384, 65536, 131072, 262144, 524288, args.jobs}
    started = time.perf_counter()
    loop = 0.0

    def admit_window():
        end = min(producer.cursor + args.window, args.jobs)
        while producer.cursor < end:
            job = producer.next()
            input_hash.update(encoded(job))
            stream.add(job)  # No retained Python handle or completed-job list.
        pending = stream.stats()
        return pending

    def track(stats):
        nonlocal max_jobs, max_ops, max_native
        max_jobs = max(max_jobs, stats["retained_jobs"])
        max_ops = max(max_ops, stats["retained_operations"])
        max_native = max(max_native, stats["native_bytes"])

    if env is None:
        while producer.cursor < args.jobs:
            t0 = time.perf_counter()
            track(admit_window())
            stream.advance(producer.cursor * args.spacing)
            loop += time.perf_counter() - t0
            track(stream.stats())
            if producer.cursor in milestones:
                observe()
    else:

        def controller():
            while producer.cursor < args.jobs:
                track(admit_window())
                if producer.cursor in milestones:
                    observe()
                yield env.timeout(producer.cursor * args.spacing - env.now)

        env.process(controller())
        execute_until(env, env._execution_policy)
        loop = time.perf_counter() - started
    instrumented_loop = time.perf_counter() - started
    before_finish = stream.stats()
    t0 = time.perf_counter()
    if env is None and not args.horizon:
        stream.run()
    finish = time.perf_counter() - t0
    observe()
    snapshot, history, state = stream.snapshot(), stream.history_digest, stream.state_bytes()
    final = stream.stats()
    environment = (
        None
        if env is None
        else dict(
            entities=len(env.entities.live()),
            collectors=len(env.collectors),
            pending_events=len(env._queue),
            fingerprint=env.fingerprint().digest,
        )
    )
    t0 = time.perf_counter()
    if env is None:
        stream.close()
    else:
        env.close()
    close = time.perf_counter() - t0
    assert before == verify()
    result = dict(
        retention=args.retention,
        execution=args.execution,
        environment=environment,
        jobs=args.jobs,
        seed=args.seed,
        spacing=args.spacing,
        window=args.window,
        horizon=args.horizon,
        input_sha256=input_hash.hexdigest(),
        snapshot=snapshot,
        history_sha256=history,
        state_sha256=hashlib.sha256(state).hexdigest(),
        before_finish=before_finish,
        final=final,
        max_retained_jobs=max_jobs,
        max_retained_operations=max_ops,
        max_native_bytes=max_native,
        samples=samples,
        create_seconds=create,
        loop_seconds=loop,
        instrumented_loop_seconds=instrumented_loop,
        finish_seconds=finish,
        close_seconds=close,
        peak_rss=peak_rss(),
        query_seconds_median=statistics.median(query_times),
    )
    print(json.dumps(result), flush=True)


def matrix(args):
    output = args.output.resolve()
    output.mkdir(exist_ok=False, parents=True)
    frozen = sources()
    manifest = verify()
    metadata = dict(
        base=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        source=frozen,
        source_sha256=hashlib.sha256(json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        build_manifest=manifest,
        python=sys.version,
        platform=platform.platform(),
        machine=platform.machine(),
        hostname=platform.node(),
        recorded_at=datetime.now(UTC).isoformat(),
        compiler=subprocess.check_output([os.environ.get("CC", "cc"), "--version"], text=True),
        jobs=args.sizes,
        repeats=args.repeats,
        window=64,
        timing_scope=(
            "Create/load + lazy input generation/validation/hash + admission/advance/completion hashing "
            "+ finish/close. Direct loop excludes RSS/state polling; environment loop includes it. "
            "instrumented_loop_seconds includes polling for both paths."
        ),
    )
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    cases = [
        (mode, n, 1.5, False, 41 + r, "direct")
        for mode in ("full", "bounded")
        for n in args.sizes
        for r in range(args.repeats)
    ]
    if not args.quick:
        cases += [(mode, n, 1.0, False, 41, "direct") for mode in ("full", "bounded") for n in args.sizes]
        cases += [(mode, n, 0.125, True, 41, "direct") for mode in ("full", "bounded") for n in (16384, 65536)]
        cases += [
            (mode, n, 1.5, True, 41, execution)
            for mode in ("full", "bounded")
            for n in args.sizes[-2:]
            for execution in ("direct", "environment")
        ]
    random.Random(20261103).shuffle(cases)
    rows = []
    for i, (mode, n, spacing, horizon, seed, execution) in enumerate(cases):
        assert sources() == frozen and verify() == manifest
        command = [
            sys.executable,
            "-m",
            "prototypes.native_engine_streaming.experiment",
            "--worker",
            "--retention",
            mode,
            "--execution",
            execution,
            "--jobs",
            str(n),
            "--spacing",
            str(spacing),
            "--seed",
            str(seed),
            "--window",
            "64",
        ]
        if horizon:
            command.append("--horizon")
        started = time.perf_counter()
        proc = subprocess.run(command, cwd=REPO, text=True, capture_output=True, timeout=240)
        (output / f"{i:02d}-stderr.log").write_text(proc.stderr)
        assert proc.returncode == 0, proc.stderr
        row = json.loads(proc.stdout)
        row["cold_process_seconds"] = time.perf_counter() - started
        rows.append(row)
        (output / "rows.json").write_text(json.dumps(rows, indent=2) + "\n")
        print(
            json.dumps(
                dict(
                    case=i + 1,
                    of=len(cases),
                    mode=mode,
                    execution=execution,
                    n=n,
                    spacing=spacing,
                    seconds=round(row["loop_seconds"], 3),
                    rss_mib=round(row["peak_rss"] / 2**20, 2),
                )
            ),
            flush=True,
        )
    assert sources() == frozen and verify() == manifest
    (output / "verification.json").write_text(
        json.dumps(
            dict(
                source_sha256=metadata["source_sha256"],
                files={p.name: sha(p) for p in sorted(output.iterdir()) if p.is_file()},
            ),
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--worker", action="store_true")
    p.add_argument("--execution", choices=["direct", "environment"], default="direct")
    p.add_argument("--jobs", type=int, default=16384)
    p.add_argument("--spacing", type=float, default=1.5)
    p.add_argument("--retention", choices=["full", "bounded"], default="bounded")
    p.add_argument("--seed", type=int, default=41)
    p.add_argument("--window", type=int, default=64)
    p.add_argument("--horizon", action="store_true")
    p.add_argument("--output", type=Path, default=ROOT / "results/gate")
    p.add_argument("--sizes", type=int, nargs="+", default=[16384, 131072, 1048576])
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--quick", action="store_true")
    args = p.parse_args()
    if args.execution == "environment":
        args.horizon = True
    if args.worker:
        worker(args)
    else:
        matrix(args)
