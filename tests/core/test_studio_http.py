"""HTTP references expose immutable data, never user-selected filesystem paths."""

import hashlib
import json

import pytest

from simulatte.studio.http import ImmutableFile, RangeSource, References, parse_range


def test_reference_cache_is_content_addressed_bounded_and_round_trips(monkeypatch):
    refs = References(max_bytes=200, max_items=2)
    a = refs.put({"x": 1})
    assert refs.put({"x": 1}) == a
    assert json.loads(refs.get(a["id"])) == {"x": 1}
    refs.put({"x": 2})
    refs.put({"x": 3})
    with pytest.raises(KeyError):
        refs.get(a["id"])
    with pytest.raises(ValueError):
        refs.put({"x": "z" * 200})
    assert refs.bytes <= 200


@pytest.mark.parametrize(
    "raw,expected",
    [(None, (0, 9, 200)), ("bytes=2-4", (2, 4, 206)), ("bytes=-2", (8, 9, 206)), ("bytes=8-", (8, 9, 206))],
)
def test_range_syntax(raw, expected):
    assert parse_range(raw, 10) == expected


@pytest.mark.parametrize("raw", ["bytes=10-", "bytes=4-2", "bytes=-0", "bytes=1-2,3-4", "other=2-3", "bytes=a-b"])
def test_bad_ranges(raw):
    with pytest.raises(ValueError):
        parse_range(raw, 10)


def test_range_source_reads_across_immutable_segments_and_detects_mutation(tmp_path):
    files = []
    for i, data in enumerate((b"01234", b"56789")):
        path = tmp_path / str(i)
        path.write_bytes(data)
        files.append(ImmutableFile(path, len(data), hashlib.sha256(data).hexdigest()))
    source = RangeSource(files, 8)
    source.validate(2, 7)
    assert b"".join(source.chunks(2, 7)) == b"234567"
    (tmp_path / "0").write_bytes(b"xxxxx")
    with pytest.raises(ValueError, match="hash"):
        source.validate(0, 4)


def test_sources_reject_symlinks_wrong_lengths_and_gaps(tmp_path):
    path = tmp_path / "a"
    path.write_bytes(b"abc")
    digest = hashlib.sha256(b"abc").hexdigest()
    link = tmp_path / "link"
    link.symlink_to(path)
    for file in (ImmutableFile(link, 3, digest), ImmutableFile(path, 4, digest)):
        with pytest.raises(ValueError):
            RangeSource([file], 3).validate(0, 2)
    with pytest.raises(ValueError):
        RangeSource([ImmutableFile(path, 3, digest)], 4)


@pytest.mark.parametrize("bounds", [{"max_bytes": 0}, {"max_items": 0}, {"max_items": True}])
def test_reference_bounds_are_positive_integers(bounds):
    with pytest.raises(ValueError):
        References(**bounds)


def test_range_reads_reject_midstream_replacement_and_invalid_bounds(tmp_path):
    import os

    data = b"x" * ((1 << 20) + 3)
    path = tmp_path / "large"
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    with pytest.raises(ValueError):
        ImmutableFile(path, -1, digest)
    source = RangeSource([ImmutableFile(path, len(data), digest)], len(data))
    with pytest.raises(ValueError):
        source.validate(-1, 2)
    stream = source.chunks(0, len(data) - 1)
    assert len(next(stream)) == 1 << 20
    other = tmp_path / "replacement"
    other.write_bytes(data)
    if os.name == "nt":
        # Windows refuses replacing a file while the response holds its handle.
        with pytest.raises(PermissionError) as denied:
            os.replace(other, path)
        assert denied.value.winerror in (5, 32)
        assert b"".join(stream) == data[1 << 20 :]
        assert path.read_bytes() == data
        return
    os.replace(other, path)
    with pytest.raises(ValueError, match="changed"):
        next(stream)


def test_descriptor_mutation_and_short_reads_fail_closed(tmp_path, monkeypatch):
    import os
    from simulatte.execution.references import _identity

    data = b"abc"
    path = tmp_path / "data"
    path.write_bytes(data)
    descriptor = ImmutableFile(path, 3, hashlib.sha256(data).hexdigest())
    with descriptor.open() as (file, before):
        original = os.fstat
        monkeypatch.setattr(os, "fstat", lambda fd: tmp_path.stat() if fd == file.fileno() else original(fd))
        with pytest.raises(ValueError, match="during read"):
            descriptor._unchanged(file, before)
        monkeypatch.setattr(os, "fstat", original)
    # A lower-level short read cannot silently shorten a range response.
    from contextlib import contextmanager
    import io

    @contextmanager
    def short(self):
        yield io.BytesIO(b""), _identity(path.stat())

    monkeypatch.setattr(ImmutableFile, "open", short)
    monkeypatch.setattr(ImmutableFile, "_unchanged", lambda *args: None)
    with pytest.raises(ValueError, match="Short"):
        list(RangeSource([descriptor], 3).chunks(0, 2))


@pytest.mark.parametrize("phase", ["during", "between"])
def test_descriptor_change_time_guards_reads_and_verified_cache(tmp_path, monkeypatch, phase):
    import errno
    import os
    from simulatte.studio import http

    path = tmp_path / "data"
    path.write_bytes(b"abc")
    descriptor = ImmutableFile(path, 3, hashlib.sha256(b"abc").hexdigest())
    before = path.stat()
    # Model the Windows cross-API identity, whose creation time does not change.
    monkeypatch.setattr(http, "_identity", lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns))

    def mutate():
        path.write_bytes(b"xyz")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    if phase == "during":
        if os.name == "nt":
            # HTTP uses read-only sharing, so the OS denies mutation even when
            # the held handle's timestamps would not expose it immediately.
            with descriptor.open() as (file, _):
                with pytest.raises(PermissionError) as denied:
                    mutate()
                # Python's CRT-backed open reports EACCES rather than retaining
                # CreateFileW's original ERROR_SHARING_VIOLATION value.
                assert denied.value.errno == errno.EACCES
                assert file.read() == b"abc"
            assert path.read_bytes() == b"abc"
            path.write_bytes(b"xyz")  # Closing the response releases the sharing restriction.
            return
        with pytest.raises(ValueError, match="during read"):
            with descriptor.open():
                mutate()
    else:
        with descriptor.open():
            pass
        mutate()
        with pytest.raises(ValueError, match="hash mismatch"):
            with descriptor.open():
                pass


@pytest.mark.parametrize("failure", [None, "open", "descriptor"])
def test_windows_immutable_handle_sharing_and_ownership(monkeypatch, failure):
    import ctypes as c
    import os
    import sys
    from types import SimpleNamespace
    from simulatte.studio.http import _open_windows_immutable

    opened, transferred, closed = [], [], []

    def create(*args):
        opened.append(args)
        return c.c_void_p(-1).value if failure == "open" else 42

    def close(handle):
        closed.append(handle)
        return 1

    def descriptor(handle, flags):
        transferred.append((handle, flags))
        if failure == "descriptor":
            raise OSError("descriptor failed")
        return 7

    api = SimpleNamespace(CreateFileW=create, CloseHandle=close)
    monkeypatch.setattr(c, "WinDLL", lambda *a, **kw: api, raising=False)
    monkeypatch.setattr(c, "WinError", lambda: OSError("open failed"), raising=False)
    monkeypatch.setattr(os, "O_BINARY", 0x8000, raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(open_osfhandle=descriptor))
    if failure:
        with pytest.raises(OSError, match=failure):
            _open_windows_immutable("immutable.segment")
    else:
        assert _open_windows_immutable("immutable.segment") == 7
    assert opened == [("immutable.segment", 0x80000000, 1, None, 3, 0x80, None)]
    assert transferred == ([] if failure == "open" else [(42, os.O_RDONLY | 0x8000)])
    assert closed == ([42] if failure == "descriptor" else [])


def test_verified_identity_cache_and_segment_iteration_are_bounded(tmp_path, monkeypatch):
    from collections import OrderedDict
    from simulatte.studio import http

    cache = OrderedDict((("old", i), True) for i in range(1024))
    monkeypatch.setattr(http, "_verified", cache)
    files = []
    for index in range(3):
        path = tmp_path / str(index)
        path.write_bytes(bytes([index]))
        files.append(ImmutableFile(path, 1, hashlib.sha256(bytes([index])).hexdigest()))
    source = RangeSource(files, 3)
    assert b"".join(source.chunks(1, 1)) == b"\x01"
    assert len(cache) == 1024 and ("old", 0) not in cache


def test_app_policy_only_gains_sandboxed_frames():
    from simulatte.studio.auth import CSP, response_headers

    assert CSP == (
        "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; worker-src 'self'; "
        "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; frame-src 'self'"
    )
    assert response_headers()["Content-Security-Policy"] == CSP


def _connection(*origins, host="127.0.0.1:8123"):
    from types import SimpleNamespace

    from starlette.datastructures import Headers

    pairs = [(b"host", host.encode())] + [(b"origin", value.encode()) for value in origins]
    return SimpleNamespace(headers=Headers(raw=pairs))


def test_null_origin_is_accepted_only_when_the_caller_allows_it():
    from simulatte.studio.auth import SessionAuth

    auth = SessionAuth(8123, mode="studio")
    assert not auth.headers_valid(_connection("null"))
    assert not auth.headers_valid(_connection("null"), require_origin=True)
    assert auth.headers_valid(_connection("null"), allow_null_origin=True)
    assert auth.headers_valid(_connection(), allow_null_origin=True)
    assert auth.headers_valid(_connection(auth.origin), allow_null_origin=True)
    # Any other value, alone or beside null, is refused.
    assert not auth.headers_valid(_connection("https://example.com"), allow_null_origin=True)
    assert not auth.headers_valid(_connection("null", "null"), allow_null_origin=True)
    assert not auth.headers_valid(_connection("null", auth.origin), allow_null_origin=True)
    assert not auth.headers_valid(_connection("null", host="example.com"), allow_null_origin=True)
    # A required origin is still required.
    assert not auth.headers_valid(_connection(), require_origin=True, allow_null_origin=True)
