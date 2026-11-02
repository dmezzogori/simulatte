import json
import threading

import pytest

from prototypes.native_engine_dynamic.phase_probe import Recorder
from prototypes.native_engine_dynamic.phase_instrument import transform
from prototypes.native_engine_dynamic import phase_probe as probe


def test_nested_inclusive_exclusive_and_exception(tmp_path):
    ticks = iter((0, 10, 20, 40, 70, 80))
    recorder = Recorder(tmp_path / "probe.json", clock=lambda: next(ticks))
    with recorder.scope("outer"):
        with pytest.raises(ValueError), recorder.scope("inner", span=False):
            raise ValueError("failure")
    data = recorder.flush()
    totals = data["threads"][0]["totals"]
    assert totals == {
        "inner": dict(count=1, inclusive_ns=20, exclusive_ns=20),
        "outer": dict(count=1, inclusive_ns=60, exclusive_ns=40),
    }
    assert data == json.loads((tmp_path / "probe.json").read_text())
    assert data["threads"][0]["spans"] == [dict(name="outer", start_ns=10, end_ns=70)]


def test_threads_have_independent_stacks(tmp_path):
    recorder = Recorder(tmp_path / "probe.json")
    barrier = threading.Barrier(2)

    def work():
        with recorder.scope("work"):
            barrier.wait()

    threads = [threading.Thread(target=work) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    data = recorder.flush()
    assert len(data["threads"]) == 2
    assert all(d["totals"]["work"]["count"] == 1 for d in data["threads"])


def test_transform_matches_exact_targets_and_preserves_decorators():
    source = '''"""doc"""
from __future__ import annotations
class C:
    @staticmethod
    def method():
        return 7
'''
    measured = transform(source, [("C.method", "method", True)])
    assert "@staticmethod\n    @_phase_timed" in measured
    assert "from __future__ import annotations\nfrom simulatte._phase_probe" in measured
    with pytest.raises(AssertionError):
        transform(source, [("C.missing", "method", True)])


def test_switch_preserves_results_and_writes_only_diagnostic_file(tmp_path):
    values = []

    @probe.timed("call", False)
    def function(value):
        values.append(value)
        return value * 2

    assert function(3) == 6 and list(tmp_path.iterdir()) == []
    probe.configure(dict(output=str(tmp_path / "diagnostic.json")))
    try:
        assert function(4) == 8
    finally:
        document = probe.finish()
    assert values == [3, 4]
    assert document["threads"][0]["totals"]["call"]["count"] == 1
    assert function(5) == 10 and probe.finish() is None
