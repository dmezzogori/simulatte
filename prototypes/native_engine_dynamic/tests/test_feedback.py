"""The next job is constructed only after a verified state observation."""

from copy import deepcopy

import pytest

from prototypes.native_engine.model import Workload
from prototypes.native_engine.workloads import generate
from prototypes.native_engine_dynamic.compiled import CompiledStream
from prototypes.native_engine_dynamic.feedback import feed, validate_feed
from prototypes.native_engine_dynamic.native import NativeStream
from prototypes.native_engine_dynamic.reference import ReferenceStream
from prototypes.native_engine_dynamic.tuned import TunedStream
from prototypes.native_engine_dynamic.validation import validate_result, validate_recording


@pytest.mark.parametrize("mode", ["none", "kpi", "record"])
@pytest.mark.parametrize("policy", ["fifo", "spt"])
def test_feedback_exact_workload_decisions_results_and_records(tmp_path, mode, policy):
    templates = generate(300, 0.95)
    expected = None
    observed = set()
    for cls in (ReferenceStream, TunedStream, NativeStream, CompiledStream):
        path = tmp_path / cls.__name__
        stream = cls(
            templates.servers, wip_cap=12, policy=policy, mode=mode, trace_path=path if mode == "record" else None
        )
        try:
            jobs, log = feed(stream, templates, "feedback")
            stream.run()
            result = stream.result()
            data = Workload(templates.servers, jobs)
            validate_result(data, result, wip_cap=12)
            validate_feed(templates, jobs, log, result)
            observed.update(row[4] for row in log[1:])
            record = None
            if mode == "record":
                validate_recording(data, result, path, wip_cap=12)
                record = path.read_bytes()
            actual = jobs, log, result, record
            expected = actual if expected is None else expected
            assert actual == expected
        finally:
            stream.close()
    assert len(observed) >= 2
    assert jobs != templates.jobs
    broken = list(log)
    broken[1] = (*broken[1][:4], "finished", "normal")
    with pytest.raises(AssertionError):
        validate_feed(templates, jobs, tuple(broken), result)
    wrong = deepcopy(result)
    wrong["finished"][0] = -1
    with pytest.raises(AssertionError):
        validate_feed(templates, jobs, log, wrong)
