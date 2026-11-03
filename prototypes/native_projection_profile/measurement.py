"""Separate uninstrumented latency, deterministic profiles and traced Python peaks."""

import cProfile
import gc
import hashlib
import json
from pathlib import Path
import pstats
import random
import time
import tracemalloc
from types import SimpleNamespace

from simulatte.experimental import native

if __package__:
    from . import projection, reference_projection as reference
else:
    import projection
    import reference_projection as reference


def run_calls(function, count):
    value = None
    for _ in range(count):
        value = function()
    return value


def timed(function, count):
    wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
    value = run_calls(function, count)
    cpu = time.thread_time_ns() - cpu
    wall = time.perf_counter_ns() - wall
    del value
    return dict(calls=count, wall_ns=wall, cpu_ns=cpu)


def category(filename, name):
    path = filename.replace("\\", "/")
    if name in ("_detached", "plain") or "_collections_abc" in path:
        return "detachment_access"
    if path.endswith("/simulatte/_wire.py"):
        if name in ("__getitem__", "__iter__", "__len__"):
            return "detachment_access"
        if name in ("canonical_pack", "_packb", "_pack_default"):
            return "messagepack"
        return "wire_preparation"
    if "msgpack" in path or "msgpack" in name:
        return "messagepack"
    if "state_projection" in name and "built-in" in name:
        return "native_atomic"
    if any(word in name for word in ("openssl_sha256", "_hashlib", "HASH object")):
        return "sha256"
    if name == "state" and Path(path).name in ("fixed_case.py", "coupled_case.py") or name == "synthetic_inputs":
        return "callback"
    if path.endswith("measurement.py") and name == "run_calls" or "Profiler" in name:
        return "harness"
    return "other_glue"


def profile(function, count, target):
    instrument = cProfile.Profile()
    started = time.perf_counter_ns()
    instrument.enable()
    value = run_calls(function, count)
    instrument.disable()
    elapsed = time.perf_counter_ns() - started
    del value
    instrument.dump_stats(str(target))
    stats = pstats.Stats(instrument)
    rows, groups = [], {}
    for (filename, line, name), (primitive, total, own, cumulative, callers) in stats.stats.items():
        group = category(filename, name)
        groups[group] = groups.get(group, 0.0) + own
        rows.append(
            dict(
                file=filename,
                line=line,
                name=name,
                category=group,
                primitive_calls=primitive,
                total_calls=total,
                self_seconds=own,
                cumulative_seconds=cumulative,
            )
        )
    assert abs(sum(groups.values()) - stats.total_tt) < 1e-7
    return dict(
        calls=count,
        elapsed_wall_ns=elapsed,
        accounted_seconds=stats.total_tt,
        self_categories=groups,
        functions=sorted(rows, key=lambda r: (-r["self_seconds"], r["file"], r["line"])),
    )


def allocations(function, calls=8):
    gc.collect()
    tracemalloc.start(8)
    tracemalloc.reset_peak()
    value = run_calls(function, calls)
    current, peak = tracemalloc.get_traced_memory()
    del value
    gc.collect()
    after = tracemalloc.get_traced_memory()[0]
    sites = tracemalloc.take_snapshot().statistics("lineno")[:12]
    result = dict(
        calls=calls,
        current_with_last_result=current,
        peak_bytes=peak,
        after_release_bytes=after,
        retained_sites=[dict(trace=str(s.traceback), bytes=s.size, blocks=s.count) for s in sites],
    )
    tracemalloc.stop()
    return result


def capture_state(backend, input_state):
    stream = backend._stream
    return dict(
        native=hashlib.sha256(stream.state_bytes()).hexdigest(),
        inputs=reference.digest(input_state()),
        authority=backend.state_digest(),
        generation=stream._generation,
    )


def prepare_operations(backend, input_state, actions):
    before = capture_state(backend, input_state)
    view = backend.state_projection()
    expected, size = projection.hook_capture(backend, actions)
    oracle = reference.capture(backend, input_state, actions)
    assert (expected, size) == oracle
    reference.validate(expected)
    stream = backend._stream
    raw_query = getattr(
        stream._module, "retention_state_projection" if stream.retention == "bounded" else "state_projection"
    )

    def raw_native():
        with stream._lock:
            return raw_query(stream._engine)

    def full_pipeline():
        result = projection.hook_capture(backend, actions)
        reference.validate(result[0])
        return result

    def parser_pipeline():
        result = reference.capture(backend, input_state, actions)
        reference.validate(result[0])
        return result

    replay = SimpleNamespace(state_projection=lambda: view)
    frozen_inputs, frozen_view = native.freeze(input_state()), native.freeze(view)
    operations = dict(
        noop=lambda: None,
        native_atomic=raw_native,
        stream_hook=stream.state_projection,
        managed_hook=backend.state_projection,
        composition_only=lambda: projection.hook_capture(replay, actions),
        validation_only=lambda: reference.validate(expected),
        hook_identity=lambda: projection.hook_capture(backend, actions),
        pipeline=full_pipeline,
        parser_pipeline=parser_pipeline,
        callback_only=input_state,
        callback_freeze=lambda: native.freeze(input_state()),
        callback_freeze_pack=lambda: native.canonical_pack(native.freeze(input_state())),
        callback_prepared_pack=lambda: native.canonical_pack(frozen_inputs),
        detach_view=lambda: native._detached(frozen_view),
        freeze_view=lambda: native.freeze(view),
        prepared_pack_view=lambda: native.canonical_pack(frozen_view),
        identity_size_pack=lambda: reference.pack(expected),
    )
    assert operations["composition_only"]() == (expected, size)
    assert before == capture_state(backend, input_state)
    context = dict(
        state=before,
        native_bytes=view["native_bytes"],
        callback_bytes=len(reference.pack(input_state())),
        managed_view_bytes=len(reference.pack(view)),
        identity_bytes=len(reference.pack(expected)),
        identity_sha256=reference.digest(expected),
        actions=actions,
        snapshot=view["reporting"]["snapshot"],
    )
    return operations, context


def measure(backend, input_state, actions, output, *, seed, blocks=5, target_ns=25_000_000):
    output.mkdir(parents=True, exist_ok=False)
    operations, context = prepare_operations(backend, input_state, actions)
    batches, counts, estimates = {key: [] for key in operations}, {}, {}
    for key, function in operations.items():
        run_calls(function, 8)
        calibration = timed(function, 16)
        per_call = max(1.0, calibration["wall_ns"] / 16)
        counts[key] = max(1, min(32768, round(target_ns / per_call)))
        estimates[key] = per_call
    rng = random.Random(seed)
    for block in range(blocks):
        keys = list(operations)
        rng.shuffle(keys)
        gc.collect()
        for key in keys:
            value = timed(operations[key], counts[key])
            value.update(block=block, order=keys.index(key))
            batches[key].append(value)
    assert context["state"] == capture_state(backend, input_state)
    profiles, memory = {}, {}
    for key in ("managed_hook", "composition_only", "pipeline"):
        count = max(4, min(128, round(10_000_000 / estimates[key])))
        profiles[key] = profile(operations[key], count, output / (key + ".prof"))
        memory[key] = allocations(operations[key])
    assert context["state"] == capture_state(backend, input_state)
    result = dict(
        context=context,
        batches=batches,
        calibrations_ns=estimates,
        profiles=profiles,
        traced_python_memory=memory,
        gc_enabled=gc.isenabled(),
    )
    (output / "query.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
