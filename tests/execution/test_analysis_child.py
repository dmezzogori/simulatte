"""In-process analysis child entrypoint, context inputs/limits and output verification."""

import importlib.util
import os
from pathlib import Path
import runpy
import sqlite3
import sys
import warnings
from contextlib import contextmanager

import pytest

from simulatte.analysis import child, codec, context as context_module, publication
from simulatte.analysis.context import AnalysisContext, artifact, write_data
from simulatte.analysis.datasets import DatasetWriter
from simulatte.analysis.outputs import Figure, Image, Number
from simulatte.analysis.publication import publish, verify
from simulatte.analysis.types import DatasetSchema, Field

REPORT = """from simulatte.analysis.outputs import Number

def analyze(context, parameters):
    rows = list(context.rows('jobs'))
    context.emit('lateness', Number(sum(row['lateness'] for row in rows) * parameters['scale']))
    context.emit('replications', Number(len(list(context.scalars()))))
    assert list(context.rows('extra')) == [] and list(context.rows('other')) == []
"""


def context(tmp_path):
    return AnalysisContext(
        {"replications": [], "available": 0, "planned": 1, "partial": True},
        input_dir=tmp_path / "inputs",
        output_dir=tmp_path / "outputs",
    )


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Undo the child's process-global import policy, cwd and loaded captured modules.

    The real child is a fresh ``-I`` process, so modules other tests loaded from their own temporary
    directories are hidden from the import audit for the duration of the test.
    """
    original = set(sys.modules)
    roots = tuple(Path(p).resolve() for p in sys.path if p and Path(p).exists())
    with monkeypatch.context() as patch:
        for name, module in tuple(sys.modules.items()):
            spec = getattr(module, "__spec__", None)
            paths = [*(getattr(spec, "submodule_search_locations", None) or ())]
            if getattr(spec, "origin", None) not in (None, "built-in", "frozen"):
                paths.append(spec.origin)
            if any(not any(Path(p).resolve().is_relative_to(r) for r in roots) for p in paths):
                patch.delitem(sys.modules, name)
        patch.setattr(sys, "path", list(sys.path))
        patch.setattr(sys, "meta_path", list(sys.meta_path))
        patch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
        patch.chdir(Path.cwd())
        yield tmp_path
    for name in set(sys.modules) - original:
        origin = getattr(getattr(sys.modules[name], "__spec__", None), "origin", None) or ""
        if name.startswith(("_simulatte_analysis", "stray_")) or origin.startswith(str(tmp_path)):
            sys.modules.pop(name, None)


def dataset(root, name, fields, rows):
    writer = DatasetWriter(root, DatasetSchema(name, tuple(Field(f, "float64") for f in fields)))
    for row in rows:
        writer.append(row)
    return writer.finish().to_dict()


def replication(index, *, available=True, datasets=None, **extra):
    row = {
        "run": "run",
        "configuration": 0,
        "index": index,
        "seed": index,
        "attempt": f"attempt-{index}",
        "status": "succeeded" if available else "failed",
        "available": available,
        "scalars": {"lateness": index},
    }
    if datasets is not None:
        row["datasets"] = datasets
    return {**row, **extra}


def snapshot(tmp_path):
    inputs = tmp_path / "inputs"
    jobs = dataset(inputs / "attempt-0/datasets/jobs", "jobs", ("lateness",), [{"lateness": 2.0}, {"lateness": 3.0}])
    extra = dataset(inputs / "attempt-0/datasets/extra", "extra", ("value",), [{"value": 1.0}])
    rows = [
        replication(
            0,
            datasets={
                "jobs": {"path": "datasets/jobs", "manifest": jobs},
                "extra": {"path": "datasets/extra", "manifest": extra},
            },
        ),
        replication(1, datasets={}),
        replication(2, available=False, reason="failed attempt"),
    ]
    return {"replications": rows, "available": 2, "planned": 3, "partial": True}


def write_config(tmp_path, text=REPORT, *, entrypoint="report.py:analyze", **extra):
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    (source / "report.py").write_text(text)
    (tmp_path / "project").mkdir(exist_ok=True)
    config = {
        "source": str(source),
        "python_path": ["."],
        "project_root": str(tmp_path / "project"),
        "entrypoint": entrypoint,
        "output": str(tmp_path / "output"),
        **extra,
    }
    path = tmp_path / "config.json"
    path.unlink(missing_ok=True)
    write_data(path, config)
    return path


def run_config(tmp_path, text=REPORT, **extra):
    return write_config(
        tmp_path,
        text,
        inputs=snapshot(tmp_path),
        input_dir=str(tmp_path / "inputs"),
        execution_id="execution",
        parameters={"scale": 10},
        requirements=[{"dataset": "jobs", "fields": ["lateness"]}, {"dataset": "extra", "fields": ["absent"]}],
        provenance={"source_hash": "hash"},
        **extra,
    )


def test_run_mode_reads_inputs_and_writes_verified_outputs(isolated):
    assert child.main(run_config(isolated)) == 0
    manifest = verify(isolated / "output", "execution")
    assert manifest["provenance"] == {"source_hash": "hash"}
    values = {
        o["name"]: codec.loads((isolated / "output" / o["name"] / "number.json").read_bytes())
        for o in manifest["outputs"]
    }
    assert values["lateness"]["value"] == 50.0 and values["replications"]["value"] == 2
    availability = values["lateness"]["metadata"]["input_availability"]
    assert availability["jobs"]["valid"] == 1
    assert [(m["index"], m["reason"]) for m in availability["jobs"]["missing"]] == [
        (1, "dataset unavailable"),
        (2, "failed attempt"),
    ]
    assert availability["extra"]["missing"][0] == {
        "run": "run",
        "configuration": 0,
        "index": 0,
        "status": "succeeded",
        "missing_fields": ["absent"],
        "reason": "fields not retained",
    }
    assert sys.dont_write_bytecode and Path.cwd() == (isolated / "source").resolve()


def test_run_mode_failures_never_write_a_manifest(isolated):
    failing = "def analyze(context, parameters):\n    raise RuntimeError('broken analysis')\n"
    with pytest.raises(RuntimeError, match="broken analysis"):
        child.main(run_config(isolated, failing))
    assert not (isolated / "output/manifest.json").exists()


def test_run_mode_audit_rejects_modules_outside_the_snapshot(isolated):
    stray = """import importlib.machinery, sys, types

def analyze(context, parameters):
    module = types.ModuleType('stray_editable')
    module.__spec__ = importlib.machinery.ModuleSpec('stray_editable', None, origin=parameters['origin'])
    sys.modules['stray_editable'] = module
"""
    config = run_config(isolated, stray)
    data = codec.loads(config.read_bytes())
    data["parameters"] = {"origin": str(isolated / "project/stray.py")}
    config.unlink()
    write_data(config, data)
    with pytest.raises(ImportError, match="editable project"):
        child.main(config)
    assert not (isolated / "output/manifest.json").exists()


def test_run_mode_refuses_existing_manifest(isolated):
    (isolated / "output").mkdir()
    (isolated / "output/manifest.json").write_bytes(b"stale")
    with pytest.raises(FileExistsError):
        child.main(run_config(isolated))


def inspect_config(tmp_path, text):
    return write_config(tmp_path, text, inspect=True, definition_id="definition", source_hash="hash")


def test_inspect_mode_writes_definition_document(isolated):
    text = """from simulatte.analysis.definition import DatasetRequirement, analysis
from simulatte.model import ParameterSchema

@analysis(parameters=ParameterSchema(()), requires=(DatasetRequirement('jobs', ('lateness',)),))
def analyze(context, parameters):
    pass
"""
    assert child.main(inspect_config(isolated, text)) == 0
    definition = codec.loads((isolated / "output").read_bytes())
    assert definition["id"] == "definition" and definition["source_hash"] == "hash"
    assert definition["entrypoint"] == "report.py:analyze"
    assert definition["requirements"][0]["dataset"] == "jobs"


def test_inspect_mode_rejects_undecorated_function(isolated):
    with pytest.raises(ValueError, match="not a decorated analysis"):
        child.main(inspect_config(isolated, "def analyze(context, parameters):\n    pass\n"))
    assert not (isolated / "output").exists()


def test_bad_config_and_missing_entrypoints_fail(isolated):
    bad = isolated / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ValueError):
        child.main(bad)
    with pytest.raises(AttributeError, match="missing"):
        child.main(write_config(isolated, "x = 1\n", entrypoint="report.py:missing", inspect=True))
    with pytest.raises(ValueError, match="not callable"):
        child.main(write_config(isolated, "analyze = 1\n", inspect=True))
    with pytest.raises(FileNotFoundError):
        child.main(isolated / "absent.json")


@pytest.mark.parametrize(
    "reference", ["report.py", "report.py:not-a-name", "../report.py:analyze", "notes.txt:analyze"]
)
def test_load_rejects_references_outside_captured_python(tmp_path, reference):
    with pytest.raises(ValueError, match="captured Python source"):
        child.load(reference, tmp_path.resolve(), (tmp_path.resolve(),))


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("src/pkg/report.py", "pkg.report"),
        ("src/pkg/__init__.py", "pkg"),
        ("src/top.py", "_simulatte_analysis"),
        ("src/my-pkg/report.py", "_simulatte_analysis"),
        ("other/report.py", "_simulatte_analysis"),
    ],
)
def test_load_names_modules_by_their_import_root(isolated, relative, expected):
    source = (isolated / "source").resolve()
    path = source / relative
    path.parent.mkdir(parents=True)
    path.write_text("def analyze(context, parameters):\n    return __name__\n")
    function = child.load(relative + ":analyze", source, (source / "src",))
    assert function(None, None) == expected
    assert sys.modules[expected].__file__ == str(path)
    sys.modules.pop(expected)


def test_load_rejects_files_without_a_loader(tmp_path, monkeypatch):
    (tmp_path / "report.py").write_text("def analyze(context, parameters):\n    pass\n")
    monkeypatch.setattr(importlib.util, "spec_from_file_location", lambda *args: None)
    with pytest.raises(ValueError, match="Cannot load"):
        child.load("report.py:analyze", tmp_path.resolve(), ())


def test_module_entrypoint_exits_with_main_status(isolated, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["child", str(run_config(isolated))])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with pytest.raises(SystemExit) as exited:
            runpy.run_module("simulatte.analysis.child", run_name="__main__")
    assert exited.value.code == 0
    assert verify(isolated / "output", "execution")["execution_id"] == "execution"


def test_context_artifact_media_kind_and_budget_limits(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"data")
    (root / "link").symlink_to(outside)
    for path in (outside, root / "link", root / "absent", root):
        with pytest.raises(ValueError, match="contained regular file"):
            artifact(path, root)
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg")
    with pytest.raises(ValueError, match="media type"):
        context(tmp_path / "jpeg").emit("image", Image(image, "image/jpeg"))
    with pytest.raises(ValueError, match="adapter"):
        context(tmp_path / "kind").emit("figure", Figure("bitmap", image))
    svg = tmp_path / "figure.svg"
    svg.write_text("<svg/>")
    ctx = context(tmp_path / "figures")
    ctx.emit("svg", Figure("svg", svg))
    ctx.emit("png", Figure("png", image))
    assert sorted(p.name for p in (tmp_path / "figures/outputs").rglob("image.*")) == ["image.png", "image.svg"]
    monkeypatch.setattr(context_module, "MAX_OUTPUT_FILES", 0)
    with pytest.raises(ValueError, match="budget"):
        context(tmp_path / "files").emit("value", Number(1))
    monkeypatch.setattr(context_module, "MAX_OUTPUT_BYTES", 4)
    with pytest.raises(ValueError, match="byte limit"):
        context(tmp_path / "bytes").emit("value", Number(1))


def outputs(tmp_path, provenance=None):
    ctx = context(tmp_path)
    ctx.emit("value", Number(3))
    ctx.finish("execution", provenance or {})
    return tmp_path / "outputs"


def rewrite(directory, change):
    manifest = codec.loads((directory / "manifest.json").read_bytes())
    change(manifest)
    for output in manifest["outputs"]:
        output["id"] = codec.identity({k: v for k, v in output.items() if k != "id"})
    (directory / "manifest.json").write_bytes(codec.dumps(manifest))


def test_verify_rejects_manifest_identity_and_descriptor_changes(tmp_path, monkeypatch):
    directory = outputs(tmp_path / "identity")
    with pytest.raises(ValueError, match="identity"):
        verify(directory, "other")
    monkeypatch.setattr(publication, "MAX_MANIFEST_BYTES", 1)
    with pytest.raises(ValueError, match="Invalid analysis manifest"):
        verify(directory, "execution")
    monkeypatch.undo()
    link = outputs(tmp_path / "link")
    (link / "manifest.json").rename(link / "real.json")
    (link / "manifest.json").symlink_to(link / "real.json")
    with pytest.raises(ValueError, match="Invalid analysis manifest"):
        verify(link, "execution")
    changed = outputs(tmp_path / "changed")
    manifest = codec.loads((changed / "manifest.json").read_bytes())
    manifest["outputs"][0]["kind"] = "table"
    (changed / "manifest.json").write_bytes(codec.dumps(manifest))
    with pytest.raises(ValueError, match="changed output descriptor"):
        verify(changed, "execution")
    duplicate = outputs(tmp_path / "duplicate")
    rewrite(duplicate, lambda m: m["outputs"].append(dict(m["outputs"][0])))
    with pytest.raises(ValueError, match="Duplicate"):
        verify(duplicate, "execution")


@pytest.mark.parametrize("path", ["/value/number.json", "../value/number.json", "value//number.json", "manifest.json"])
def test_verify_rejects_unsafe_or_duplicate_artifact_paths(tmp_path, path):
    directory = outputs(tmp_path)

    def change(manifest):
        manifest["outputs"][0]["artifacts"][0]["path"] = path

    rewrite(directory, change)
    with pytest.raises(ValueError, match="Unsafe or duplicate"):
        verify(directory, "execution")


def test_verify_rejects_changed_unlisted_and_linked_files(tmp_path, monkeypatch):
    changed = outputs(tmp_path / "hash")
    with (changed / "value/number.json").open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ValueError, match="hash/length"):
        verify(changed, "execution")
    unlisted = outputs(tmp_path / "unlisted")
    (unlisted / "value/extra.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="Unlisted"):
        verify(unlisted, "execution")
    linked = outputs(tmp_path / "linked")
    (linked / "alias").symlink_to(linked / "value")
    with pytest.raises(ValueError, match="symlinks"):
        verify(linked, "execution")
    monkeypatch.setattr(publication, "MAX_OUTPUT_FILES", 0)
    with pytest.raises(ValueError, match="budget"):
        verify(outputs(tmp_path / "budget"), "execution")


class Store:
    def __init__(self, root, state):
        self.root = root
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE analysis_executions (id TEXT PRIMARY KEY, state TEXT)")
        self.db.execute("CREATE TABLE analysis_outputs (id TEXT PRIMARY KEY, manifest BLOB)")
        if state is not None:
            self.db.execute("INSERT INTO analysis_executions VALUES ('execution', ?)", (state,))
        self.db.commit()
        (root / "analysis-results").mkdir(parents=True)

    @contextmanager
    def _transaction(self):
        with self.db:
            yield self.db


@pytest.fixture
def stores(tmp_path):
    opened = []

    def open_store(name, state):
        opened.append(Store(tmp_path / name, state))
        return opened[-1]

    yield open_store
    for store in opened:
        store.db.close()


def test_publish_commits_and_refuses_changed_or_superseded_executions(tmp_path, stores):
    provenance = {"source_hash": "hash"}
    stages = []
    store = stores("store", "finalizing")
    manifest = publish(store, "execution", outputs(tmp_path / "ok", provenance), provenance, checkpoint=stages.append)
    assert stages == ["verified", "renamed", "before_commit", "committed"]
    assert store.db.execute("SELECT state FROM analysis_executions").fetchone() == ("succeeded",)
    assert codec.loads(store.db.execute("SELECT manifest FROM analysis_outputs").fetchone()[0]) == manifest
    assert (store.root / "analysis-results/execution/value/number.json").is_file()
    with pytest.raises(ValueError, match="provenance changed"):
        publish(store, "execution", outputs(tmp_path / "provenance", provenance), {"source_hash": "other"})
    for state in (None, "cancelled"):
        with pytest.raises(ValueError, match="cancelled or superseded"):
            publish(stores(f"store-{state}", state), "execution", outputs(tmp_path / f"{state}"), {})
    exists = stores("exists", "finalizing")
    (exists.root / "analysis-results/execution").mkdir()
    directory = outputs(tmp_path / "late")
    with pytest.raises(ValueError, match="already exists"):
        publish(exists, "execution", directory, {})
    assert directory.is_dir() and exists.db.execute("SELECT state FROM analysis_executions").fetchone() == (
        "finalizing",
    )
    assert os.listdir(exists.root / "analysis-results/execution") == []


def test_context_rows_skip_available_replications_without_datasets(tmp_path):
    ctx = AnalysisContext(
        {"replications": [replication(0)], "available": 1, "planned": 1, "partial": False},
        input_dir=tmp_path / "inputs",
        output_dir=tmp_path / "outputs",
    )
    assert list(ctx.rows("jobs")) == []


def test_child_input_document_has_its_own_bound(tmp_path, monkeypatch):
    from simulatte.analysis.context import write_input

    large = {"padding": "x" * (5 << 20)}
    with pytest.raises(ValueError, match="exceeds 4 MiB"):
        write_data(tmp_path / "document.json", large)
    write_input(tmp_path / "config.json", large)
    assert codec.loads((tmp_path / "config.json").read_bytes()) == large
    monkeypatch.setattr(context_module, "MAX_INPUT_DOCUMENT_BYTES", 8)
    with pytest.raises(ValueError, match="input document exceeds 64 MiB"):
        write_input(tmp_path / "small.json", large)
