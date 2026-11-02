"""Production is the oracle for native simultaneous-event ordering."""

import pytest

from prototypes.native_engine_dynamic.compiled import CompiledStream
from prototypes.native_engine_dynamic.feedback import feed
from prototypes.native_engine_dynamic.native import NativeStream
from prototypes.native_engine_dynamic.production import ProductionStream
from prototypes.native_engine_dynamic.reference import ReferenceStream
from prototypes.native_engine_dynamic.semantics_gate import workloads, gate
from prototypes.native_engine_dynamic.simultaneous import check, quantized, certify_targeted
from prototypes.native_engine_dynamic.tuned import TunedStream
from prototypes.native_engine_dynamic.validation import fingerprint


def execute(cls, templates, *, cap=2, policy="spt", producer="independent"):
    stream = cls(templates.servers, wip_cap=cap, policy=policy)
    try:
        jobs, decisions = feed(stream, templates, producer)
        stream.run()
        result = stream.result()
        return fingerprint(result), jobs, decisions, result
    finally:
        stream.close()


@pytest.mark.parametrize("factory", [ReferenceStream, TunedStream, NativeStream, CompiledStream])
@pytest.mark.parametrize("policy", ["fifo", "spt"])
def test_four_job_case_agrees_with_production(factory, policy):
    templates = workloads()["reduced"]
    expected = execute(ProductionStream, templates, policy=policy)
    assert expected[3]["finished"] == [9, 10, 11, 12]
    actual = execute(factory, templates, policy=policy)
    assert actual[:3] == expected[:3]


@pytest.mark.parametrize("seed", range(8))
def test_quantized_phase_order_matches_independent_production(seed):
    for cap in (None, 1, 2, 4, 12):
        for policy in ("fifo", "spt"):
            for producer in ("independent", "feedback"):
                check(quantized(seed, jobs=24), cap=cap, policy=policy, producer=producer)


def test_recorded_gate_requires_semantics_and_isolates_known_peak_export():
    data = gate()
    assert certify_targeted(data) == dict(comparisons=288, scheduling_mismatches=0, known_export_artifacts=4)
    data["rows"][0]["results"]["direct"]["trajectory"] = "corrupt"
    with pytest.raises(AssertionError):
        certify_targeted(data)
