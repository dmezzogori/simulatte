"""Public-result overhead and fresh query attribution; independent of live timing."""

import gc
import hashlib
import json
import random
from types import SimpleNamespace

from simulatte._wire import canonical_pack, freeze
from simulatte.experimental.native_identity import compose_native_identity

if __package__:
    from . import (
        prepared_projection as prepared,
        reference_projection as reference,
        unprepared_projection as unprepared,
    )
    from . import reference_measurement as original
else:
    import prepared_projection as prepared
    import reference_projection as reference
    import unprepared_projection as unprepared
    import reference_measurement as original


def category(filename, name):
    path = filename.replace("\\", "/")
    if path.endswith("experimental/native_identity.py") and name in ("_plain", "to_dict"):
        return "detachment_access"
    if "state_bytes" in name and "built-in" in name:
        return "native_atomic"
    return original.category(filename, name)


def profile(function, count, target):
    value = original.profile(function, count, target)
    groups = {}
    for row in value["functions"]:
        row["category"] = category(row["file"], row["name"])
        groups[row["category"]] = groups.get(row["category"], 0) + row["self_seconds"]
    value["self_categories"] = groups
    return value


def prepare_operations(backend, inputs, actions):
    before = original.capture_state(backend, inputs)
    view = backend.state_projection()
    replay = SimpleNamespace(state_projection=lambda: view)
    identity = compose_native_identity(view, actions=actions)
    expected, sizes = prepared.hook_capture(replay, actions)
    assert identity.canonical_bytes == reference.pack(expected) == canonical_pack(prepared.prepare(view, actions))
    assert identity.canonical_bytes == reference.pack(unprepared.hook_capture(replay, actions)[0])
    assert identity.canonical_bytes == reference.pack(reference.capture(backend, inputs, actions)[0])
    assert sizes == dict(raw_bytes=identity.native_bytes, view_bytes=identity.size_bytes)
    reference.validate(expected)

    def helper(backend):
        result = compose_native_identity(backend.state_projection(), actions=actions)
        return result.to_dict(), dict(raw_bytes=result.native_bytes, view_bytes=result.size_bytes)

    def pipeline(capture):
        result = capture(backend)
        reference.validate(result[0])
        return result

    operations = dict(
        helper_object=lambda: compose_native_identity(view, actions=actions),
        helper_composition=lambda: helper(replay),
        prepared_composition=lambda: prepared.hook_capture(replay, actions),
        unprepared_composition=lambda: unprepared.hook_capture(replay, actions),
        detach_only=identity.to_dict,
        managed_hook=backend.state_projection,
        validation_only=lambda: reference.validate(expected),
        native_state_hash=lambda: hashlib.sha256(backend._stream.state_bytes()).digest(),
        callback_freeze_pack=lambda: canonical_pack(freeze(inputs())),
        helper_pipeline=lambda: pipeline(helper),
        prepared_pipeline=lambda: pipeline(lambda b: prepared.hook_capture(b, actions)),
        unprepared_pipeline=lambda: pipeline(lambda b: unprepared.hook_capture(b, actions)),
    )
    assert original.capture_state(backend, inputs) == before
    return operations, dict(
        state=before,
        native_bytes=identity.native_bytes,
        identity_bytes=identity.size_bytes,
        callback_bytes=len(reference.pack(inputs())),
        canonical_sha256=hashlib.sha256(identity.canonical_bytes).hexdigest(),
        snapshot=view["reporting"]["snapshot"],
    )


def measure(backend, inputs, actions, output, *, seed):
    output.mkdir(parents=True, exist_ok=False)
    operations, context = prepare_operations(backend, inputs, actions)
    counts, estimates = {}, {}
    for name, function in operations.items():
        original.run_calls(function, 8)
        sample = original.timed(function, 16)
        estimates[name] = max(1, sample["wall_ns"] / 16)
        counts[name] = max(1, min(32768, round(25_000_000 / estimates[name])))
    rng = random.Random(seed)
    batches = {name: [] for name in operations}
    for block in range(5):
        keys = list(operations)
        rng.shuffle(keys)
        gc.collect()
        for order, name in enumerate(keys):
            value = original.timed(operations[name], counts[name])
            batches[name].append(dict(block=block, order=order, **value))
    assert context["state"] == original.capture_state(backend, inputs)
    profiles, memory = {}, {}
    for name in ("helper_object", "helper_composition", "prepared_composition", "helper_pipeline", "prepared_pipeline"):
        count = max(4, min(128, round(10_000_000 / estimates[name])))
        profiles[name] = profile(operations[name], count, output / (name + ".prof"))
        memory[name] = original.allocations(operations[name])
    assert context["state"] == original.capture_state(backend, inputs)
    result = dict(
        context=context, batches=batches, profiles=profiles, traced_python_memory=memory, gc_enabled=gc.isenabled()
    )
    (output / "query.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
