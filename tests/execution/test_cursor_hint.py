import io

import pytest

from simulatte._wire import pack
from simulatte.execution.cursor_hint import committed_cursor
from simulatte.trace.format import RecordType, write_record, write_preamble, write_trailer


def segment(tmp_path, records, *, first=False, trailer=False):
    file = io.BytesIO()
    if first:
        write_preamble(file)
    for kind, body in records:
        write_record(file, kind, pack(body))
    if trailer:
        write_trailer(file, 0)
    path = tmp_path / "segment"
    path.write_bytes(file.getvalue())
    return path


def test_cursor_hint_scans_only_new_records_and_ignores_preparation(tmp_path):
    path = segment(
        tmp_path,
        [
            (RecordType.HEADER, {}),
            (RecordType.PRELUDE, {"last": [0, 99]}),
            (RecordType.OBSERVERS, {"phase": "prelude", "last": [0, 100]}),
        ],
        first=True,
    )
    assert committed_cursor(path, first=True) is None
    path = segment(
        tmp_path,
        [
            (RecordType.INITIAL, {"cursor": [0, -1]}),
            (RecordType.CHUNK, {}),
            (RecordType.INDEX, {"last": [1, 2]}),
            (RecordType.OBSERVERS, {"phase": "simulation", "last": [1, 3]}),
            (RecordType.KPI, {"samples": [[4, 1, "value", 3]]}),
            (RecordType.KPI, {"scalars": {}}),
        ],
    )
    assert committed_cursor(path) == (1, 4)
    path = segment(tmp_path, [(RecordType.FOOTER, {"cursor": [2, 3]})], trailer=True)
    assert committed_cursor(path, previous=(1, 4)) == (2, 3)


@pytest.mark.parametrize("cursor", [None, [], [0], [True, 1], [-1, 0], [float("nan"), 0], [0, True], [0, -2], [0, 1.5]])
def test_invalid_cursor_hint_is_rejected(tmp_path, cursor):
    path = segment(tmp_path, [(RecordType.INITIAL, {"cursor": cursor})])
    with pytest.raises(ValueError, match="cursor"):
        committed_cursor(path)


@pytest.mark.parametrize(
    "records,match",
    [
        ([(RecordType.KPI, {"samples": [None]})], "sample"),
        ([(RecordType.KPI, [])], "map"),
        ([(RecordType.FOOTER, {"cursor": [0, -1]})], "trailer"),
    ],
)
def test_invalid_committed_records_are_rejected(tmp_path, records, match):
    with pytest.raises(ValueError, match=match):
        committed_cursor(segment(tmp_path, records))


def test_corrupt_or_partial_record_is_rejected(tmp_path):
    path = segment(tmp_path, [(RecordType.INITIAL, {"cursor": [0, -1]})])
    raw = path.read_bytes()
    for data, match in ((raw[:3], "header"), (raw[:-1], "segment"), (raw[:-1] + bytes([raw[-1] ^ 1]), "checksum")):
        path.write_bytes(data)
        with pytest.raises(ValueError, match=match):
            committed_cursor(path)
