"""Copied immutable inputs and bounded output spooling for one analysis child."""

from __future__ import annotations
from collections.abc import Mapping
import hashlib
import os
from pathlib import Path
import shutil

from . import codec
from .datasets import DatasetManifest, DatasetReader, DatasetWriter
from .outputs import Number, Table, Distribution, Figure, Image, OutputDescriptor, OutputManifest
from .types import Observation, identifier

MAX_OUTPUT_BYTES = 1 << 30
MAX_OUTPUT_FILES = 100_000
MAX_MANIFEST_BYTES = 4 << 20


def artifact(path: Path, root: Path):
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Output artifact is not a contained regular file")
    digest = hashlib.sha256()
    length = 0
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            length += len(block)
            if length > MAX_OUTPUT_BYTES:
                raise ValueError("Output artifact exceeds byte limit")
            digest.update(block)
    return {"path": path.relative_to(root).as_posix(), "length": length, "sha256": digest.hexdigest()}


def write_data(path, data):
    raw = codec.dumps(data)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("Analytical document exceeds 4 MiB; use a streaming Table")
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


class AnalysisContext:
    def __init__(self, inputs: Mapping, *, input_dir: Path, output_dir: Path, requirements=()):
        self.inputs = Observation(inputs)
        self._input_dir, self._output_dir = Path(input_dir), Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._requirements = {r["dataset"]: set(r["fields"]) for r in requirements}
        self._outputs = []
        self._names = set()
        self._bytes = self._files = 0

    def rows(self, dataset: str):
        identifier(dataset)
        for row in self.inputs["replications"]:
            if not row["available"] or dataset not in row["datasets"]:
                continue
            captured = row["datasets"][dataset]
            manifest = DatasetManifest.from_dict(captured["manifest"])
            if not self._requirements.get(dataset, set()) <= {f.name for f in manifest.schema.fields}:
                continue
            root = self._input_dir / row["attempt"] / captured["path"]
            yield from DatasetReader(root, manifest).iter_rows()

    def scalars(self):
        for row in self.inputs["replications"]:
            if row["available"]:
                yield Observation({k: row[k] for k in ("run", "configuration", "index", "seed", "attempt", "scalars")})

    def emit(self, name, output):
        identifier(name)
        if name in self._names:
            raise ValueError("Output names must be unique")
        if any(old.casefold() == name.casefold() for old in self._names):
            raise ValueError("Output names collide on a case-insensitive filesystem")
        self._names.add(name)
        directory = self._output_dir / name
        directory.mkdir()
        metadata = {
            **dict(output.metadata),
            "partial_snapshot": self.inputs["partial"],
            "planned": self.inputs["planned"],
            "available": self.inputs["available"],
            "inferential_convention": output.metadata.get("inferential_convention", "descriptive"),
        }
        renderer = None
        if isinstance(output, Number):
            if type(output.value) not in (int, float, type(None)):
                raise ValueError("Number requires an int64, float64 or missing value")
            kind = "number"
            metadata["unit"] = output.unit
            write_data(directory / "number.json", {"value": output.value, "metadata": metadata})
        elif isinstance(output, Table):
            kind = "table"
            writer = DatasetWriter(directory / "table", output.schema)
            for row in output.rows:
                writer.append(row)
                if self._bytes + writer.encoded_size > MAX_OUTPUT_BYTES:
                    raise ValueError("Analysis output budget exceeded")
            manifest = writer.finish()
            metadata["schema"] = output.schema.to_dict()
            metadata["row_count"] = manifest.row_count
        elif isinstance(output, Distribution):
            kind = "distribution"
            if output.summary.get("representation") not in ("exact", "binned", "approximate"):
                raise ValueError("Distribution must declare its retained representation")
            write_data(directory / "distribution.json", {"summary": output.summary, "dataset_ref": output.dataset_ref})
        elif isinstance(output, Figure) and output.kind == "plotly":
            kind, renderer = "figure", output.renderer
            if not renderer or renderer.get("name") != "plotly" or not renderer.get("version"):
                raise ValueError("Plotly figure needs renderer name/version")
            write_data(directory / "figure.json", output.payload)
        elif isinstance(output, Image) or isinstance(output, Figure) and output.kind in ("png", "svg"):
            kind = "image"
            media_type = (
                output.media_type
                if isinstance(output, Image)
                else "image/" + ("svg+xml" if output.kind == "svg" else "png")
            )
            if media_type not in ("image/png", "image/svg+xml"):
                raise ValueError("Unsupported image media type")
            source = Path(output.path if isinstance(output, Image) else output.payload)
            if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_OUTPUT_BYTES:
                raise ValueError("Invalid image file")
            destination = directory / ("image.svg" if media_type.endswith("svg+xml") else "image.png")
            with source.open("rb") as src, destination.open("xb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
                dst.flush()
                os.fsync(dst.fileno())
            metadata["media_type"] = media_type
        else:
            raise ValueError("Unsupported analysis output; use a typed output or optional figure adapter")
        artifacts = tuple(artifact(p, self._output_dir) for p in sorted(directory.rglob("*")) if not p.is_dir())
        self._files += len(artifacts)
        self._bytes += sum(a["length"] for a in artifacts)
        if self._files > MAX_OUTPUT_FILES or self._bytes > MAX_OUTPUT_BYTES:
            raise ValueError("Analysis output budget exceeded")
        descriptor = {
            "name": name,
            "kind": kind,
            "artifacts": list(artifacts),
            "metadata": metadata,
            "renderer": renderer,
        }
        self._outputs.append(
            OutputDescriptor(codec.identity(descriptor), name, kind, artifacts, Observation(metadata), renderer)
        )

    def finish(self, execution_id, provenance):
        manifest = OutputManifest(execution_id, tuple(self._outputs), Observation(provenance))
        write_data(self._output_dir / "manifest.json", manifest.to_dict())
        return manifest
