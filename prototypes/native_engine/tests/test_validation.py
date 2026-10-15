from copy import deepcopy

import pytest

from prototypes.native_engine.baseline import SimulatteRunner
from prototypes.native_engine.native import NativeRunner
from prototypes.native_engine.reference import ReferenceRunner
from prototypes.native_engine.recording import MAGIC, RECORD, read_records
from prototypes.native_engine.validation import compare_results, validate_recording, validate_result
from prototypes.native_engine.workloads import generate, read, save


@pytest.mark.parametrize("policy", ["fifo", "spt"])
def test_fractional_workload_independently_validated(tmp_path, policy):
    workload = generate(200, 0.95)
    outputs = []
    for backend in (ReferenceRunner, SimulatteRunner, NativeRunner):
        path = tmp_path / backend.__name__
        runner = backend(workload, policy=policy, mode="record", trace_path=path)
        runner.run()
        result = runner.result()
        runner.close()
        validate_result(workload, result)
        validate_recording(workload, result, path)
        outputs.append(result)
    for result in outputs[1:]:
        compare_results(outputs[0], result)
    broken = deepcopy(result)
    broken["starts"][0] = -1
    with pytest.raises(AssertionError):
        validate_result(workload, broken)
    broken = deepcopy(result)
    broken["dispatch"][0] = broken["dispatch"][1]
    with pytest.raises(AssertionError, match="conservation"):
        validate_result(workload, broken)
    # Valid binary layout, invalid lifecycle: announce END instead of START.
    data = bytearray(path.read_bytes())
    for offset in range(8, len(data), RECORD.size):
        row = list(RECORD.unpack_from(data, offset))
        if row[2] == 3:
            row[2] = 4
            RECORD.pack_into(data, offset, *row)
            break
    path.write_bytes(data)
    with pytest.raises(AssertionError):
        validate_recording(workload, result, path)


@pytest.mark.parametrize("data", [b"", b"badhead!", MAGIC + b"bad", MAGIC + RECORD.pack(0, 2, 1, 0, 0, 0)])
def test_corrupt_records_rejected(tmp_path, data):
    path = tmp_path / "bad.nevt"
    path.write_bytes(data)
    with pytest.raises(ValueError):
        list(read_records(path))


def test_input_roundtrip(tmp_path):
    workload = generate(20, 0.5)
    path = tmp_path / "input.json"
    save(workload, path)
    assert read(path) == workload
