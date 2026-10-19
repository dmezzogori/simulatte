"""Windows generated launchers: paths vary, code/entrypoint payload must not."""

import importlib
import io
import struct
import zipfile

import pytest


def archive(script):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as output:
        output.writestr(zipfile.ZipInfo("__main__.py"), script)
    return stream.getvalue()


def pe_launcher(python, body=b"print(1)\n", *, code=b"launcher code", extra=None):
    # Valid minimal PE32+ layout with uv 0.13 named RT_RCDATA resources.
    names = ["UV_PYTHON_PATH", "UV_SCRIPT_DATA", "UV_TRAMPOLINE_KIND"]
    values = [python.encode(), archive(b"#!" + python.encode() + b"\n" + body), b"\x01"]
    if extra is not None:
        names.append("ZZ_UNRELATED")
        values.append(extra)
    count = len(names)
    resource = bytearray(24 + 16 + 8 * count + 24 * count + 16 * count)
    struct.pack_into("<HHII", resource, 12, 0, 1, 10, 0x80000018)
    struct.pack_into("<HH", resource, 24 + 12, count, 0)
    for i, (name, value) in enumerate(zip(names, values)):
        table = 40 + count * 8 + i * 24
        entry = 40 + count * 32 + i * 16
        offset = len(resource)
        encoded = name.encode("utf-16le")
        resource += struct.pack("<H", len(name)) + encoded
        struct.pack_into("<II", resource, 40 + i * 8, 0x80000000 | offset, 0x80000000 | table)
        struct.pack_into("<HHII", resource, table + 12, 0, 1, 0, entry)
        while len(resource) % 4:
            resource += b"\0"
        offset = len(resource)
        resource += value
        struct.pack_into("<IIII", resource, entry, 0x2000 + offset, len(value), 0, 0)
    header = bytearray(512)
    header[:2] = b"MZ"
    struct.pack_into("<I", header, 60, 128)
    header[128:132] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", header, 132, 0x8664, 2, 0, 0, 0, 240, 0x22)
    optional = 152
    struct.pack_into("<H", header, optional, 0x20B)
    struct.pack_into("<I", header, optional + 32, 4096)
    struct.pack_into("<I", header, optional + 36, 512)
    struct.pack_into("<I", header, optional + 56, 0x3000)
    struct.pack_into("<I", header, optional + 60, 512)
    struct.pack_into("<I", header, optional + 108, 16)
    struct.pack_into("<II", header, optional + 112 + 16, 0x2000, len(resource))
    size = (len(resource) + 511) // 512 * 512
    struct.pack_into("<I", header, optional + 8, size)
    for offset, name, virtual, rawlen, pointer, flags in [
        (392, b".text", 0x1000, 512, 512, 0x60000020),
        (432, b".rsrc", 0x2000, size, 1024, 0x40000040),
    ]:
        header[offset : offset + 8] = name.ljust(8, b"\0")
        struct.pack_into(
            "<IIIIIIHHI",
            header,
            offset + 8,
            len(code) if name == b".text" else len(resource),
            virtual,
            rawlen,
            pointer,
            0,
            0,
            0,
            0,
            flags,
        )
    return bytes(header) + code.ljust(512, b"\0") + bytes(resource).ljust(size, b"\0")


def identity(raw, python):
    return importlib.import_module("simulatte.execution.launchers").windows_identity(raw, python)


@pytest.mark.parametrize("gui", [False, True])
def test_uv_resource_launcher_relocation_preserves_code_and_entrypoint(gui):
    first = r"C:\old\Scripts\python.exe"
    second = r"C:\a much longer copied project\Scripts\python.exe"
    a = first.replace("python.exe", "pythonw.exe") if gui else first
    b = second.replace("python.exe", "pythonw.exe") if gui else second
    before = identity(pe_launcher(a), first)
    assert before == identity(pe_launcher(b), second)
    assert before != identity(pe_launcher(b, b"print(2)\n"), second)
    assert before != identity(pe_launcher(b, code=b"changed code"), second)
    with pytest.raises(ValueError, match="selector"):
        identity(pe_launcher(r"C:\different\python.exe"), second)


def test_distlib_launcher_relocation_retains_binary_and_body():
    a = r"C:\old\Scripts\python.exe"
    b = r"C:\new location\Scripts\python.exe"

    def launcher(python, body=b"print(1)\n"):
        return b"MZbinary launcher" + b"#!" + python.encode() + b"\n" + archive(body)

    assert identity(launcher(a), a) == identity(launcher(b), b)
    assert identity(launcher(a), a) != identity(launcher(b, b"print(2)\n"), b)
    with pytest.raises(ValueError):
        identity(b"MZbroken", a)


@pytest.mark.parametrize(
    "python",
    [
        r"C:\p\python.exe",
        r"C:\Projects with spaces\Vénétó\Scripts\python.exe",
        "C:\\" + ("long directory\\" * 30) + "python.exe",
    ],
)
def test_uv_paths_lengths_spaces_and_non_ascii(python):
    assert identity(pe_launcher(python), python) == identity(
        pe_launcher(r"D:\other\python.exe"), r"D:\other\python.exe"
    )


@pytest.mark.parametrize(
    "offset,value",
    [
        (60, 0xFFFFFFF0),
        (128, 0),
        (152, 0),
        (132 + 2, 33),
        (152 + 108, 2),
        (152 + 60, 200),
        (152 + 112 + 16, 0xDEAD),
        (152 + 8, 1),
        (152 + 56, 1),
    ],
)
def test_pe_malformed_bounds_and_derived_sizes_refuse(offset, value):
    python = r"C:\a\python.exe"
    raw = bytearray(pe_launcher(python))
    struct.pack_into("<I", raw, offset, value)
    with pytest.raises(ValueError):
        identity(bytes(raw), python)


def test_uv_body_crc_unrelated_resource_and_mode_are_not_discarded():
    python = r"C:\a\python.exe"
    raw = pe_launcher(python)
    changed = raw.replace(b"print(1)", b"print(2)")
    with pytest.raises(ValueError):
        identity(changed, python)  # CRC no longer matches.
    # The console/GUI subsystem is executable behavior, not generated bookkeeping.
    gui = bytearray(raw)
    struct.pack_into("<H", gui, 152 + 68, 2)
    assert identity(bytes(gui), python) != identity(raw, python)
    assert identity(pe_launcher(python, extra=b"original"), python) != identity(
        pe_launcher(python, extra=b"tampered"), python
    )
    # Changing the executable kind is unsupported, not a path normalization.
    name = "UV_TRAMPOLINE_KIND".encode("utf-16le")
    assert name in raw
    kind = bytearray(raw)
    offset = 136 + 2 * 16 + 1024
    rva = struct.unpack_from("<I", kind, offset)[0]
    kind[1024 + rva - 0x2000] = 2
    with pytest.raises(ValueError, match="kind"):
        identity(bytes(kind), python)


def test_nonzero_pe_checksum_must_be_valid_before_normalization():
    python = r"C:\a\python.exe"
    raw = bytearray(pe_launcher(python))
    struct.pack_into("<I", raw, 152 + 64, 42)
    with pytest.raises(ValueError, match="checksum"):
        identity(bytes(raw), python)


def test_resource_payload_cannot_alias_another_resource():
    python = r"C:\a\python.exe"
    raw = bytearray(pe_launcher(python))
    # Make script data alias the interpreter resource instead of its own storage.
    raw[1024 + 152 : 1024 + 160] = raw[1024 + 136 : 1024 + 144]
    with pytest.raises(ValueError):
        identity(bytes(raw), python)


def test_recognized_optional_pe_checksum_is_verified_not_part_of_identity():
    python = r"C:\a\python.exe"
    raw = bytearray(pe_launcher(python))
    # PE checksum for this fixed fixture, independently calculated by DWORD folding.
    struct.pack_into("<I", raw, 216, 13915)
    assert identity(bytes(raw), python) == identity(pe_launcher(python), python)


@pytest.mark.parametrize(
    "change", ["overlap", "resource-size", "padding", "unreferenced", "cycle", "duplicate", "missing", "language"]
)
def test_uv_resource_manifest_refuses_ambiguous_or_unknown_layout(change):
    python = r"C:\a\python.exe"
    raw = bytearray(pe_launcher(python))
    if change == "overlap":
        struct.pack_into("<I", raw, 432 + 20, 512)
    elif change == "resource-size":
        struct.pack_into("<I", raw, 152 + 112 + 20, 999999)
    elif change == "padding":
        raw[-1] = 1
    elif change == "unreferenced":
        entry = 1024 + 136
        size = struct.unpack_from("<I", raw, entry + 4)[0]
        struct.pack_into("<I", raw, entry + 4, size - 1)
    elif change == "cycle":
        struct.pack_into("<I", raw, 1024 + 20, 0x80000000)
    elif change == "duplicate":
        raw[1024 + 48 : 1024 + 52] = raw[1024 + 40 : 1024 + 44]
    elif change == "missing":
        raw = raw.replace("UV_SCRIPT_DATA".encode("utf-16le"), "XY_SCRIPT_DATA".encode("utf-16le"))
    else:
        struct.pack_into("<I", raw, 1024 + 64 + 16, 1)
    with pytest.raises(ValueError):
        identity(bytes(raw), python)


def test_unknown_archive_and_interposed_launcher_code_refuse():
    python = r"C:\a\python.exe"
    for zipped in (archive(b"print(1)\n").replace(b"__main__.py", b"not_main.py"), archive(b"print(1)\n")):
        raw = b"MZcode#!" + python.encode() + b"\n" + (b"code\n" if b"not_main.py" not in zipped else b"") + zipped
        with pytest.raises(ValueError):
            identity(raw, python)
