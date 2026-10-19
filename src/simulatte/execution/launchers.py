"""Recognized Windows installer launchers, without executing their code.

uv 0.13.1 uses named PE resources; distlib uses a shebang + ZIP overlay.
Only validated interpreter selectors and derived layout/CRC sizes are normalized.
Reference: astral-sh/uv, tag 0.13.1, uv-trampoline-builder/src/lib.rs.
"""

from __future__ import annotations

import io
import struct
import zipfile

from simulatte.execution.installation import digest, interpreter_selector, script_identity


def _archive(raw, interpreter, *, shebang):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) != 1 or entries[0].filename != "__main__.py" or archive.comment:
            raise ValueError("Unsupported launcher archive")
        entry = entries[0]
        # read checks the CRC; ZIP offsets/sizes and CRC are derived from the script.
        body = archive.read(entry)
        content = script_identity(body, interpreter) if shebang else {"body": digest(body)}
        return {
            "content": content,
            "compression": entry.compress_type,
            "flags": entry.flag_bits,
            "date": list(entry.date_time),
            "attributes": entry.external_attr,
            "extra": entry.extra.hex(),
            "comment": entry.comment.hex(),
            "create_system": entry.create_system,
        }


class _PE:
    def __init__(self, raw):
        self.raw = raw

    def bytes(self, offset, size):
        if offset < 0 or size < 0 or offset + size > len(self.raw):
            raise ValueError("Windows launcher bounds are invalid")
        return self.raw[offset : offset + size]

    def unpack(self, fmt, offset):
        return struct.unpack(fmt, self.bytes(offset, struct.calcsize(fmt)))

    def identity(self, interpreter):
        pe = self.unpack("<I", 60)[0]
        if self.bytes(pe, 4) != b"PE\0\0":
            raise ValueError("Unsupported Windows launcher PE signature")
        _, count, _, _, _, optional_size, _ = self.unpack("<HHIIIHH", pe + 4)
        optional = pe + 24
        checksum_offset = optional + 64
        checksum = self.unpack("<I", checksum_offset)[0]
        if checksum:
            total = 0
            for offset in range(0, len(self.raw), 2):
                if checksum_offset <= offset < checksum_offset + 4:
                    continue
                total += int.from_bytes(self.raw[offset : offset + 2], "little")
                total = (total & 0xFFFF) + (total >> 16)
            total = ((total & 0xFFFF) + (total >> 16)) & 0xFFFF
            if checksum != total + len(self.raw):
                raise ValueError("Invalid Windows launcher checksum")
        magic = self.unpack("<H", optional)[0]
        if (magic, optional_size) not in ((0x10B, 224), (0x20B, 240)) or not 1 <= count <= 32:
            raise ValueError("Unsupported Windows launcher PE layout")
        directories = optional + (96 if magic == 0x10B else 112)
        if self.unpack("<I", directories - 4)[0] != 16:
            raise ValueError("Unsupported Windows launcher directories")
        resource_rva, resource_size = self.unpack("<II", directories + 16)
        headers_size = self.unpack("<I", optional + 60)[0]
        table = optional + optional_size
        if table + count * 40 > headers_size:
            raise ValueError("Windows launcher section table escapes headers")
        header = bytearray(self.bytes(0, headers_size))
        sections = []
        resource = None
        occupied = []
        initialized = 0
        image_end = 0
        for i in range(count):
            offset = table + i * 40
            virtual_size, address, size, start, _, _, _, _, flags = self.unpack("<IIIIIIHHI", offset + 8)
            payload = self.bytes(start, size)
            if flags & 0x40:
                initialized += size
            image_end = max(image_end, address + virtual_size)
            if size and (start < headers_size or any(start < end and begin < start + size for begin, end in occupied)):
                raise ValueError("Overlapping Windows launcher sections")
            occupied.append((start, start + size))
            if address == resource_rva:
                if resource is not None or not resource_size or resource_size > virtual_size or resource_size > size:
                    raise ValueError("Invalid Windows launcher resource section")
                resource = (start, size, address, offset)
                # These sizes change with the length of the generated selector/archive.
                struct.pack_into("<I", header, offset + 8, 0)
                struct.pack_into("<I", header, offset + 16, 0)
            else:
                sections.append({"header": self.bytes(offset, 40).hex(), "payload": digest(payload)})
        if resource is None:
            raise ValueError("Windows launcher has no uv resources")
        start, size, rva, resource_header = resource
        if start + size != max(end for _, end in occupied):
            raise ValueError("Unsupported non-final uv resource section")
        alignment = self.unpack("<I", optional + 32)[0]
        initialized_header = self.unpack("<I", optional + 8)[0]
        # uv0.13.1/editpe0.2.4 appends .pedata without updating the original
        # StandardHeader. That unchanged base value is meaningful evidence:
        # validate it against the retained sections and keep it byte-for-byte.
        preserved_initialized = (
            self.bytes(resource_header, 8) == b".pedata\0" and initialized_header == initialized - size
        )
        if (
            not alignment
            or alignment & (alignment - 1)
            or (initialized_header != initialized and not preserved_initialized)
            or self.unpack("<I", optional + 56)[0] != (image_end + alignment - 1) // alignment * alignment
        ):
            raise ValueError("Invalid derived Windows launcher sizes")
        if any(self.bytes(start + resource_size, size - resource_size)):
            raise ValueError("Unsupported nonzero Windows resource padding")
        if not preserved_initialized:
            struct.pack_into("<I", header, optional + 8, 0)
        for offset in (optional + 56, optional + 64, directories + 20):
            struct.pack_into("<I", header, offset, 0)
        # Parse logical resources, retaining unrelated names, metadata and content.
        resources = _Resources(self.bytes(start, resource_size), rva)
        tree = resources.read(0, 0)
        resources.validate_padding()
        rcdata = tree["entries"].get("id:10", {}).get("entries", {})
        required = ["name:UV_TRAMPOLINE_KIND", "name:UV_PYTHON_PATH", "name:UV_SCRIPT_DATA"]
        if not all(name in rcdata for name in required):
            raise ValueError("Unsupported Windows launcher resources")
        values = []
        for name in required:
            node = rcdata[name]
            if set(node.get("entries", {})) != {"id:0"}:
                raise ValueError("Unsupported Windows launcher resource language")
            values.append(node["entries"]["id:0"])
        kind, path, script = values
        if kind["data"] != b"\x01":
            raise ValueError("Unsupported Windows launcher kind")
        path["data"] = {"interpreter": interpreter_selector(path["data"].decode("utf8"), interpreter)}
        script["data"] = _archive(script["data"], interpreter, shebang=True)
        resources.hash_bytes(tree)
        # Uninterpreted overlay bytes are retained; resource offsets/padding are derived layout.
        return {
            "format": "uv-pe-resources-v1",
            "headers": digest(bytes(header)),
            "sections": sections,
            "resources": tree,
            "overlay": digest(self.raw[start + size :]),
        }


class _Resources:
    def __init__(self, raw, rva):
        self.pe = _PE(raw)
        self.rva = rva
        self.seen = set()
        self.covered = bytearray(len(raw))

    def bytes(self, offset, size):
        raw = self.pe.bytes(offset, size)
        if any(self.covered[offset : offset + size]):
            raise ValueError("Overlapping Windows resource data")
        self.covered[offset : offset + size] = b"\x01" * size
        return raw

    def unpack(self, fmt, offset):
        return struct.unpack(fmt, self.bytes(offset, struct.calcsize(fmt)))

    def validate_padding(self):
        if any(value for index, value in enumerate(self.pe.raw) if not self.covered[index]):
            raise ValueError("Unsupported unreferenced Windows resource data")

    def read(self, offset, depth):
        if depth > 3 or offset in self.seen:
            raise ValueError("Cyclic/aliased Windows resource directory")
        self.seen.add(offset)
        if depth == 3:
            address, size, page, reserved = self.unpack("<IIII", offset)
            return {"codepage": page, "reserved": reserved, "data": self.bytes(address - self.rva, size)}
        named, ids = self.unpack("<HH", offset + 12)
        entries = {}
        for i in range(named + ids):
            name, child = self.unpack("<II", offset + 16 + i * 8)
            if name & 0x80000000:
                position = name & 0x7FFFFFFF
                length = self.unpack("<H", position)[0]
                key = "name:" + self.bytes(position + 2, length * 2).decode("utf-16le")
            else:
                key = "id:" + str(name)
            if key in entries or bool(child & 0x80000000) != (depth < 2):
                raise ValueError("Duplicate/invalid Windows resource directory")
            entries[key] = self.read(child & 0x7FFFFFFF, depth + 1)
        return {"metadata": self.bytes(offset, 12).hex(), "entries": entries}

    def hash_bytes(self, node):
        if "data" in node:
            if isinstance(node["data"], bytes):
                node["data"] = {"sha256": digest(node["data"])}
        else:
            for child in node["entries"].values():
                self.hash_bytes(child)


def windows_identity(raw: bytes, interpreter: str) -> dict:
    try:
        # distlib's appended ZIP starts after a single generated interpreter shebang.
        if zipfile.is_zipfile(io.BytesIO(raw)):
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                start = min(entry.header_offset for entry in archive.infolist())
            marker = raw.rfind(b"#!", 0, start)
            if marker >= 0 and raw[marker:start].endswith(b"\n"):
                selected = script_identity(raw[marker:start], interpreter)
                if selected["body"] != digest(b""):
                    raise ValueError("Unsupported code between launcher selector and archive")
                return {
                    "format": "distlib-v1",
                    "launcher": digest(raw[:marker]),
                    "interpreter": selected["interpreter"],
                    "archive": _archive(raw[start:], interpreter, shebang=False),
                }
        return _PE(raw).identity(interpreter)
    except (struct.error, UnicodeError, zipfile.BadZipFile, KeyError, IndexError) as error:
        raise ValueError("Malformed Windows generated launcher") from error
