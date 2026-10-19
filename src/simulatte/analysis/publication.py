"""Verify, durably rename, then expose all outputs in one fenced transaction."""

from pathlib import Path, PurePosixPath
import os
from simulatte.execution.durability import replace_durable, sync_directory
from . import codec
from .context import artifact, MAX_MANIFEST_BYTES, MAX_OUTPUT_BYTES, MAX_OUTPUT_FILES
from .types import identifier


def verify(directory: Path, execution_id: str):
    path = directory / "manifest.json"
    if path.is_symlink() or path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("Invalid analysis manifest")
    manifest = codec.loads(path.read_bytes())
    if (
        set(manifest) != {"schema", "execution_id", "outputs", "provenance"}
        or manifest["schema"] != "analysis-output-v1"
        or manifest["execution_id"] != execution_id
    ):
        raise ValueError("Invalid analysis output identity")
    expected, names, total = {"manifest.json"}, set(), 0
    for output in manifest["outputs"]:
        identifier(output["name"])
        if output["name"] in names or output["id"] != codec.identity({k: v for k, v in output.items() if k != "id"}):
            raise ValueError("Duplicate or changed output descriptor")
        names.add(output["name"])
        for descriptor in output["artifacts"]:
            relative = PurePosixPath(descriptor["path"])
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or relative.as_posix() != descriptor["path"]
                or descriptor["path"] in expected
            ):
                raise ValueError("Unsafe or duplicate output path")
            expected.add(descriptor["path"])
            actual = artifact(directory.joinpath(*relative.parts), directory)
            if actual != descriptor:
                raise ValueError("Output artifact hash/length mismatch")
            total += actual["length"]
    if total > MAX_OUTPUT_BYTES or len(expected) > MAX_OUTPUT_FILES + 1:
        raise ValueError("Analysis output budget exceeded")
    actual = set()
    for file in directory.rglob("*"):
        if file.is_symlink():
            raise ValueError("Output symlinks are forbidden")
        if not file.is_dir():
            actual.add(file.relative_to(directory).as_posix())
            with file.open("rb") as stream:
                os.fsync(stream.fileno())
    if actual != expected:
        raise ValueError("Unlisted or missing output file")
    for folder in sorted((p for p in directory.rglob("*") if p.is_dir()), reverse=True):
        sync_directory(folder)
    sync_directory(directory)
    return manifest


def publish(store, identity, directory, provenance, *, checkpoint=lambda stage: None):
    manifest = verify(directory, identity)
    if codec.identity(manifest["provenance"]) != codec.identity(provenance):
        raise ValueError("Analysis provenance changed")
    checkpoint("verified")
    target = store.root / "analysis-results" / identity
    # Rename and index share the writer fence. Cancellation cannot interleave visibility.
    with store._transaction() as db:
        row = db.execute("SELECT state FROM analysis_executions WHERE id=?", (identity,)).fetchone()
        if row is None or row[0] != "finalizing":
            raise ValueError("Analysis publication was cancelled or superseded")
        if target.exists():
            raise ValueError("Analysis output already exists")
        replace_durable(directory, target)
        checkpoint("renamed")
        db.execute("INSERT INTO analysis_outputs VALUES (?,?)", (identity, codec.dumps(manifest)))
        checkpoint("before_commit")
        db.execute("UPDATE analysis_executions SET state='succeeded' WHERE id=?", (identity,))
    checkpoint("committed")
    return manifest
