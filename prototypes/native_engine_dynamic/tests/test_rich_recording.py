"""Rich source records preserve reservation/departure and registration ordering."""

import pytest

from prototypes.native_engine.model import Job
from prototypes.native_engine_dynamic.compiled import CompiledStream
from prototypes.native_engine_dynamic.native import NativeStream
from prototypes.native_engine_dynamic.reference import ReferenceStream
from prototypes.native_engine_dynamic.tuned import TunedStream
from prototypes.native_engine_dynamic.rich_recording import read_records, REGISTER, GRANT, DEPART


@pytest.mark.parametrize("policy", ["fifo", "spt"])
def test_rich_records_match_across_backends_and_preserve_atomic_boundaries(tmp_path, policy):
    expected = result = None
    for cls in (ReferenceStream, TunedStream, NativeStream, CompiledStream):
        path = tmp_path / cls.__name__
        stream = cls(2, wip_cap=2, policy=policy, mode="studio", trace_path=path)
        try:
            for index in range(100):
                stream.advance(index // 3)
                stream.add(Job(index // 3, (0, 1), (1, 1), index % 7))
            stream.run()
            actual = stream.result()
        finally:
            stream.close()
        rows = list(read_records(path))
        assert len(rows) == actual["records"] == 4 * 100 + 5 * 200
        assert sum(r[2] == REGISTER for r in rows) == 100
        assert sum(r[2] == GRANT for r in rows) == 200
        assert sum(r[2] == DEPART for r in rows) == 200
        if expected is None:
            expected, result = path.read_bytes(), actual
        assert path.read_bytes() == expected
        assert actual == result
        first_grant = next(i for i, r in enumerate(rows) if r[2] == GRANT)
        first_start = next(i for i, r in enumerate(rows) if r[2] == 3)
        assert first_grant < first_start
        assert any(r[2] == REGISTER for r in rows[first_grant:first_start]) is False


def test_rich_bulk_registration_is_preserved_but_full_projection_stays_scalar(tmp_path):
    path = tmp_path / "bulk"
    stream = NativeStream(1, mode="studio", trace_path=path)
    stream.add_many((Job(0, (0,), (1,), 5), Job(5, (0,), (1,), 10)))
    stream.run()
    stream.close()
    registered = [r for r in read_records(path) if r[2] == REGISTER]
    assert [(r[0], r[3]) for r in registered] == [(0, 0), (0, 1)]
