"""Opt-in owned-buffer callbacks preserve grant semantics and lifetime safety."""

import pytest

from prototypes.native_engine.model import Job, Workload
from prototypes.native_engine.native import NativeRunner
from prototypes.native_engine.reference import ReferenceRunner


def sample():
    return Workload(1, (Job(0, (0,), (3,), 2), Job(1, (0,), (2,), 4), Job(1, (0,), (1,), 6)))


@pytest.mark.parametrize("policy", ["fifo", "spt"])
@pytest.mark.parametrize("mode", ["none", "kpi", "record"])
def test_buffer_hook_preserves_stateful_grant_stream(tmp_path, policy, mode):
    from prototypes.native_engine.hooks import BufferReferenceRunner

    calls, results, files = [], [], []
    for index, (factory, style) in enumerate(
        (
            (NativeRunner, "tuple"),
            (NativeRunner, "buffers"),
            (ReferenceRunner, "tuple"),
            (BufferReferenceRunner, "buffers"),
        )
    ):
        observed = []

        def hook(now, server, *data):
            candidates = data[0] if style == "tuple" else tuple(zip(*data))
            observed.append((now, server, candidates))
            return candidates[0][0] if policy == "fifo" else min(candidates, key=lambda item: item[1])[0]

        path = tmp_path / f"{index}.bin" if mode == "record" else None
        options = dict(policy=policy, mode=mode, hook=hook, trace_path=path)
        if factory is NativeRunner:
            options["hook_style"] = style
        runner = factory(sample(), **options)
        try:
            runner.run()
            results.append(runner.result())
        finally:
            runner.close()
        calls.append(observed)
        if path is not None:
            files.append(path.read_bytes())
    assert all(call == calls[0] for call in calls)
    assert len(calls[0]) == 3  # includes both singleton grants
    assert calls[0][1] == (3.0, 0, ((1, 2.0), (2, 1.0)))
    assert all(result == results[0] for result in results)
    assert all(data == files[0] for data in files)


def test_buffer_candidates_are_owned_readonly_and_survive_close():
    retained = []

    def hook(now, server, ids, durations):
        assert ids.readonly and durations.readonly
        retained.append((ids, durations))
        with pytest.raises(TypeError):
            ids[0] = 0
        return ids[0]

    runner = NativeRunner(sample(), hook=hook, hook_style="buffers")
    try:
        runner.run()
    finally:
        runner.close()
    assert [(list(ids), list(durations)) for ids, durations in retained] == [
        ([0], [3.0]),
        ([1, 2], [2.0, 1.0]),
        ([2], [1.0]),
    ]


@pytest.mark.parametrize("choice", [True, None, 1.0, -1, 999, 2**100])
def test_invalid_buffer_choice_fails_cleanly(choice):
    runner = NativeRunner(sample(), hook=lambda *args: choice, hook_style="buffers")
    try:
        with pytest.raises((TypeError, ValueError)):
            runner.run()
        with pytest.raises(RuntimeError):
            runner.result()
    finally:
        runner.close()


def test_buffer_hook_preserves_exception_and_lifecycle_guards():
    error = LookupError("buffer failure")
    runner = None

    def hook(*args):
        for method in (runner.result, runner.close, runner.run):
            with pytest.raises(RuntimeError):
                method()
        raise error

    runner = NativeRunner(sample(), hook=hook, hook_style="buffers")
    try:
        with pytest.raises(LookupError) as caught:
            runner.run()
        assert caught.value is error
    finally:
        runner.close()


def test_invalid_hook_style():
    with pytest.raises(ValueError):
        NativeRunner(sample(), hook_style="borrowed")
