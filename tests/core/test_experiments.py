"""Experiment definitions expand to deterministic ordinary execution requests (D92, D96, D103, D104)."""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
import subprocess

from jsonschema import Draft202012Validator
import pytest

from simulatte.execution.schema import ExecutionRequest, Revision, content_id, request_id
from simulatte.execution.snapshot import ProjectSpec, Snapshot, SourceSnapshot, capture_source, discover_project
from simulatte.layout.identity import layout_hash
from simulatte.model import ParameterField, ParameterSchema
from simulatte.stopping import FinitePopulation, Horizon

DESIGN_EXAMPLE = {
    "schema": 1,
    "name": "servers-sweep",
    "model": "plant.py:build",
    "replications": {"count": 10, "base_seed": 0},
    "configurations": {"grid": {"servers": [2, 3, 4], "wip_norm": [10, 20]}},
    "layouts": [{"label": "base", "file": "layout.json"}, {"label": "wide", "file": "layouts/wide.json"}],
    "stopping": None,
    "level": "kpi",
    "kpis": [],
    "workers": ["local", "strangelove"],
}

UV = {
    "kind": "uv",
    "python": "3.14.1",
    "lock_hash": "1" * 64,
    "pyproject_hash": "2" * 64,
    "extras": [],
    "groups": ["dev"],
    "local_packages": ["helpers"],
    "trusted_runtime_hash": "e" * 64,
}


LOCAL = {"facts": {"implementation": "CPython"}, "dependencies_hash": "d" * 64, "trusted_runtime_hash": "e" * 64}


def api():
    assert importlib.util.find_spec("simulatte.experiments"), "experiments package missing"
    return importlib.import_module("simulatte.experiments")


def schema_document():
    path = Path(api().__file__).parent / "schema" / "experiment-v1.schema.json"
    return json.loads(path.read_text())


def parameter_schema():
    return ParameterSchema(
        (
            ParameterField("servers", "int", False, default=1),
            ParameterField("wip_norm", "int", False, default=5),
            ParameterField("rate", "float", False, default=1.0),
            ParameterField("fast", "bool", False, default=False),
            ParameterField("rule", "enum", False, default="FIFO", choices=("FIFO", "EDD")),
        )
    )


def prepared(tmp_path, *, layouts=(), revision=3, runtime=None):
    from simulatte.execution.coordinator import PreparedModel

    project = ProjectSpec(tmp_path, "plant.py", "layout.json", (), (), (".",), False, layouts=tuple(layouts))
    source = SourceSnapshot(project, (), tmp_path / "source.tar", "a" * 64, "0" * 64)
    inputs = SourceSnapshot(project, (), tmp_path / "inputs.tar", "b" * 64, "0" * 64)
    metadata = {
        "selected": "build",
        "models": [
            {
                "name": "build",
                "parameters": parameter_schema().to_dict(),
                "inputs": [],
                "stopping": {"type": "horizon", "horizon": 100.0, "warmup": 10.0},
            }
        ],
    }
    return PreparedModel(
        Snapshot(source, inputs),
        Revision(revision, "f" * 64),
        "plant.py:build",
        metadata,
        {"schema_version": "1.0"},
        runtime or LOCAL,
        {"layout_ref": {"kind": "layout", "sha256": "9" * 64}},
    )


def definition(**changes):
    return {
        "schema": 1,
        "name": "sweep",
        "model": "plant.py:build",
        "replications": {"count": 3, "base_seed": 100},
        "configurations": {"grid": {"servers": [2, 3], "wip_norm": [10, 20]}},
    } | changes


def expand(m, experiment, base, layouts=None, kinds=None):
    return m.expand(
        experiment,
        base,
        {} if layouts is None else layouts,
        {"local": base.runtime} if kinds is None else kinds,
    )


def test_grid_order_and_labels_follow_sorted_parameter_names(tmp_path):
    m = api()
    experiment = m.Experiment.from_dict(definition())
    run = expand(m, experiment, prepared(tmp_path))
    assert [c.label for c in run.configurations] == [
        "servers=2,wip_norm=10/base",
        "servers=2,wip_norm=20/base",
        "servers=3,wip_norm=10/base",
        "servers=3,wip_norm=20/base",
    ]
    assert run.configurations[0].parameters == {
        "servers": 2,
        "wip_norm": 10,
        "rate": 1.0,
        "fast": False,
        "rule": "FIFO",
    }
    reordered = m.Experiment.from_dict(definition(configurations={"grid": {"wip_norm": [10, 20], "servers": [2, 3]}}))
    assert reordered == experiment and reordered.to_dict() == experiment.to_dict()
    again = expand(m, reordered, prepared(tmp_path))
    assert again.configurations == run.configurations
    assert [(c.index, c.label) for c in again.configurations] == [(c.index, c.label) for c in run.configurations]
    assert (again.experiment_id, again.run_id, again.replications) == (run.experiment_id, run.run_id, run.replications)
    assert again.requests == run.requests
    values = m.Experiment.from_dict(definition(configurations={"grid": {"wip_norm": [20, 10], "servers": [2, 3]}}))
    assert [c.label for c in expand(m, values, prepared(tmp_path)).configurations][:2] == [
        "servers=2,wip_norm=20/base",
        "servers=2,wip_norm=10/base",
    ]


def test_label_formats_booleans_floats_and_enum_member_names(tmp_path):
    m = api()
    grid = {"fast": [True, False], "rate": [0.1, 2], "rule": ["EDD"]}
    run = expand(m, m.Experiment.from_dict(definition(configurations={"grid": grid})), prepared(tmp_path))
    assert [c.label for c in run.configurations] == [
        "fast=true,rate=0.1,rule=EDD/base",
        "fast=true,rate=2.0,rule=EDD/base",
        "fast=false,rate=0.1,rule=EDD/base",
        "fast=false,rate=2.0,rule=EDD/base",
    ]
    empty = expand(m, m.Experiment.from_dict(definition(configurations={"grid": {}})), prepared(tmp_path))
    assert [c.label for c in empty.configurations] == ["defaults/base"]


def test_explicit_labels_and_layout_variants(tmp_path):
    m = api()
    experiment = m.Experiment.from_dict(
        definition(
            configurations={
                "explicit": [
                    {"label": "small", "parameters": {"servers": 2}},
                    {"parameters": {"servers": 4, "fast": True}},
                ]
            },
            layouts=[{"label": "base", "file": "layout.json"}, {"label": "wide", "file": "layouts/wide.json"}],
        )
    )
    base = prepared(tmp_path, layouts=("layouts/wide.json",))
    wide = {"schema_version": "1.0", "grid": {"dx": 2.0}}
    layouts = {"wide": (layout_hash("file", wide), {"kind": "layout", "sha256": "8" * 64})}
    run = expand(m, experiment, base, layouts)
    assert [c.label for c in run.configurations] == [
        "small/base",
        "small/wide",
        "servers=4,fast=true/base",
        "servers=4,fast=true/wide",
    ]
    assert run.configurations[0].layout_hash == layout_hash("file", base.layout)
    assert run.configurations[0].layout_ref == base.documents["layout_ref"]
    assert run.configurations[1].layout_hash == layout_hash("file", wide)
    assert run.configurations[1].to_dict() == {
        "label": "small/wide",
        "parameters": {"servers": 2, "wip_norm": 5, "rate": 1.0, "fast": False, "rule": "FIFO"},
        "layout_hash": layout_hash("file", wide),
        "layout_ref": {"kind": "layout", "sha256": "8" * 64},
    }
    assert run.requests[(1, 0, "local")].layout_hash == layout_hash("file", wide)
    with pytest.raises(ValueError, match="not loaded"):
        expand(m, experiment, base)
    with pytest.raises(ValueError, match="Unknown layout"):
        expand(m, experiment, base, layouts | {"narrow": layouts["wide"]})
    with pytest.raises(ValueError, match="Experiment"):
        expand(m, experiment.to_dict(), base, layouts)
    with pytest.raises(ValueError, match="bundle"):
        expand(m, experiment, prepared(tmp_path), layouts)


@pytest.mark.parametrize(
    "configurations",
    [
        {"grid": {"servers": ["two"]}},
        {"grid": {"unknown": [1]}},
        {"grid": {"rule": ["LIFO"]}},
        {"grid": {"rate": [1, 1.0]}},
        {"explicit": [{"parameters": {"servers": 1.5}}]},
    ],
)
def test_parameters_are_validated_against_the_model_schema(tmp_path, configurations):
    m = api()
    with pytest.raises(ValueError):
        expand(m, m.Experiment.from_dict(definition(configurations=configurations)), prepared(tmp_path))


def test_model_and_runtime_kinds_must_match_the_preparation(tmp_path):
    m = api()
    base = prepared(tmp_path)
    with pytest.raises(ValueError, match="model"):
        expand(m, m.Experiment.from_dict(definition(model="other.py:build")), base)
    assert expand(m, m.Experiment.from_dict(definition(model="plant.py")), base).requests
    for kinds in ({}, {"remote": UV}, {"local": UV}, {"uv": base.runtime}):
        with pytest.raises(ValueError):
            expand(m, m.Experiment.from_dict(definition()), base, kinds=kinds)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"configurations": {"grid": {f"p{i}": list(range(10)) for i in range(5)}}},
            "Experiment exceeds 10000 configurations",
        ),
        (
            {"configurations": {"explicit": [{"parameters": {"servers": i}} for i in range(10_001)]}},
            "Experiment exceeds 10000 configurations",
        ),
        (
            {
                "configurations": {"grid": {"servers": list(range(5001))}},
                "layouts": [{"label": "a", "file": "a.json"}, {"label": "b", "file": "b.json"}],
            },
            "Experiment exceeds 10000 configurations",
        ),
        ({"replications": {"count": 250_001}}, "Experiment exceeds 1000000 replications"),
        ({"configurations": {"grid": {f"p{i}": [1] for i in range(65)}}}, "Grid exceeds 64 parameters"),
        ({"replications": {"count": 2, "base_seed": str(2**63 - 1)}}, "Replication seeds exceed 2\\*\\*63"),
    ],
)
def test_product_limits_are_validation_errors(changes, message):
    m = api()
    with pytest.raises(ValueError, match=message):
        m.Experiment.from_dict(definition(**changes))


def test_largest_seed_and_limits_inside_bounds_are_accepted():
    m = api()
    last = m.Experiment.from_dict(definition(replications={"count": 1, "base_seed": str(2**63 - 1)}))
    assert last.replications.base_seed == 2**63 - 1
    assert last.to_dict()["replications"] == {"count": 1, "base_seed": str(2**63 - 1)}
    assert m.Experiment.from_dict(definition(configurations={"grid": {f"p{i}": [1] for i in range(64)}}))
    assert m.Experiment.from_dict(definition(replications={"count": 250_000}))


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": 2},
        {"name": ""},
        {"model": "../plant.py"},
        {"replications": {"count": 0}},
        {"replications": {"count": True}},
        {"replications": {"count": 1, "base_seed": -1}},
        {"replications": {"count": 1, "base_seed": "01"}},
        {"replications": {"count": 1, "base_seed": 2**53}},
        {"replications": {"count": 1, "base_seed": "5"}},
        {"replications": {"count": 1, "seed": 0}},
        {"configurations": {"grid": {"servers": []}}},
        {"configurations": {"grid": {"servers": [1, 1]}}},
        {"configurations": {"grid": {"servers": [float("nan")]}}},
        {"configurations": {"grid": {"1x": [1]}}},
        {"configurations": {"grid": {"servers": [[1]]}}},
        {"configurations": {"grid": {}, "explicit": []}},
        {"configurations": {"explicit": []}},
        {"configurations": {"explicit": [{"label": "a/b", "parameters": {}}]}},
        {"configurations": {"explicit": [{"label": "a", "parameters": {}}, {"label": "a", "parameters": {}}]}},
        {"configurations": {"explicit": [{"parameters": {}, "extra": 1}]}},
        {"layouts": [{"label": "base", "file": "/abs.json"}]},
        {"layouts": [{"label": "a", "file": "x.json"}, {"label": "a", "file": "y.json"}]},
        {"layouts": [{"label": "a", "file": "x.json"}, {"label": "b", "file": "x.json"}]},
        {"layouts": [{"label": "a b", "file": "x.json"}]},
        {"stopping": {"type": "horizon", "horizon": 10.0}},
        {"level": "summary"},
        {"kpis": ["a", "a"]},
        {"workers": ["local", "local"]},
        {"workers": ["bad host"]},
        {"unknown": True},
        {"configurations": {"grid": {"servers": [2**53]}}},
        {"configurations": {"grid": []}},
        {"configurations": {"other": {}}},
        {"configurations": {"explicit": [{"parameters": []}]}},
        {"configurations": {"explicit": [{"parameters": {"1x": 1}}]}},
        {"configurations": {"explicit": {"parameters": {}}}},
        {"kpis": "shop/wip"},
        {"layouts": "layout.json"},
        {"layouts": [{"label": "a"}]},
        {"stopping": "horizon"},
    ],
)
def test_invalid_definitions_are_rejected(changes):
    m = api()
    with pytest.raises(ValueError):
        m.Experiment.from_dict(definition(**changes))


def test_python_api_rejects_invalid_parts():
    m = api()
    replications, grid = m.Replications(1), m.Grid({"servers": [1]})
    for kwargs in (
        {"replications": 3},
        {"configurations": {"servers": [1]}},
        {"layouts": ("layout.json",)},
        {"stopping": "horizon"},
    ):
        with pytest.raises(ValueError):
            m.Experiment(
                **({"name": "x", "model": "plant.py", "replications": replications, "configurations": grid} | kwargs)
            )
    with pytest.raises(ValueError, match="unique"):
        m.Grid((("servers", [1]), ("servers", [2])))
    built = m.Experiment(
        name="x", model="plant.py", replications=replications, configurations=grid, stopping=Horizon(5, 1).to_dict()
    )
    assert built.stopping == Horizon(5, 1)


def test_definition_roundtrip_identity_and_python_api(tmp_path, monkeypatch):
    m = api()
    experiment = m.Experiment.from_dict(DESIGN_EXAMPLE)
    assert experiment.to_dict() == DESIGN_EXAMPLE
    assert m.Experiment.from_dict(experiment.to_dict()) == experiment
    built = m.Experiment(
        name="servers-sweep",
        model="plant.py:build",
        replications=m.Replications(10),
        configurations=m.Grid({"servers": [2, 3, 4], "wip_norm": [10, 20]}),
        layouts=(m.LayoutVariant("base", "layout.json"), m.LayoutVariant("wide", "layouts/wide.json")),
        workers=("local", "strangelove"),
    )
    assert built == experiment
    from simulatte.execution.hashing import canonical_hash

    assert m.experiment_id(experiment) == canonical_hash("experiment-v1", DESIGN_EXAMPLE)
    assert m.experiment_id(DESIGN_EXAMPLE) == m.experiment_id(experiment)
    minimal = {key: DESIGN_EXAMPLE[key] for key in ("schema", "name", "model", "replications", "configurations")}
    assert m.experiment_id(minimal) != m.experiment_id(experiment)
    assert m.Experiment.from_dict(minimal).to_dict() == minimal | {
        "layouts": [],
        "stopping": None,
        "level": "kpi",
        "kpis": [],
        "workers": [],
    }
    stopped = m.Experiment.from_dict(definition(stopping=Horizon(50, 5).to_dict(), kpis=["b", "a"], level="full"))
    assert stopped.to_dict()["kpis"] == ["a", "b"]
    assert stopped.stopping == Horizon(50, 5)
    explicit = definition(
        configurations={"explicit": [{"label": "x", "parameters": {"servers": 2}}, {"parameters": {}}]}
    )
    assert m.Experiment.from_dict(explicit).to_dict()["configurations"] == explicit["configurations"]
    path = tmp_path / "sweep.experiment.json"
    path.write_text(json.dumps(DESIGN_EXAMPLE))
    assert m.load_experiment(path) == experiment
    path.write_text('{"schema": 1, "schema": 1}')
    with pytest.raises(ValueError, match="Duplicate"):
        m.load_experiment(path)
    path.write_text('{"schema": NaN}')
    with pytest.raises(ValueError):
        m.load_experiment(path)
    path.write_text("[]")
    with pytest.raises(ValueError):
        m.load_experiment(path)
    path.write_bytes(b'{"name": "\xff"}')
    with pytest.raises(ValueError, match="UTF-8"):
        m.load_experiment(path)
    path.write_text(json.dumps(DESIGN_EXAMPLE))
    monkeypatch.setattr(importlib.import_module("simulatte.experiments.definition"), "MAX_FILE_BYTES", 64)
    with pytest.raises(ValueError, match="16 MiB"):
        m.load_experiment(path)


def test_json_schema_validates_the_design_example_and_rejects_unknown_keys():
    m = api()
    schema = schema_document()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    assert validator.is_valid(DESIGN_EXAMPLE)
    assert validator.is_valid(m.Experiment.from_dict(DESIGN_EXAMPLE).to_dict())
    assert validator.is_valid(definition())
    assert validator.is_valid(definition(configurations={"explicit": [{"label": "x", "parameters": {"servers": 2}}]}))
    assert validator.is_valid(definition(stopping=FinitePopulation(arrivals=10, safety_limit=100).to_dict()))
    assert validator.is_valid(definition(replications={"count": 1, "base_seed": str(2**63 - 1)}))
    assert not validator.is_valid(DESIGN_EXAMPLE | {"unknown": 1})
    assert not validator.is_valid(definition(replications={"count": 1, "seed": 0}))
    assert not validator.is_valid(definition(configurations={"grid": {}, "explicit": []}))
    assert not validator.is_valid(definition(level="summary"))


def test_seeds_are_base_plus_index_shared_by_every_configuration(tmp_path):
    m = api()
    base = prepared(tmp_path)
    run = expand(m, m.Experiment.from_dict(definition()), base, kinds={"local": base.runtime, "uv": UV})
    assert len(run.requests) == 4 * 3 * 2
    for (configuration, index, kind), request in run.requests.items():
        assert request.seed == 100 + index
        assert request.layout_revision == 3
        assert request.parameters == run.configurations[configuration].parameters
        assert request.to_dict()["runtime"] == (LOCAL if kind == "local" else UV)
    assert run.replications[:5] == tuple(
        {
            "configuration": configuration,
            "index": index,
            "seed": 100 + index,
            "content_ids": [content_id(run.requests[(configuration, index, kind)]) for kind in ("local", "uv")],
        }
        for index, configuration in [(0, 0), (0, 1), (0, 2), (0, 3), (1, 0)]
    )
    from simulatte.execution.store import experiment_run_id

    identities = [identity for item in run.replications for identity in item["content_ids"]]
    assert len(set(identities)) == len(identities)
    assert run.experiment_id == m.experiment_id(m.Experiment.from_dict(definition()))
    assert run.run_id == experiment_run_id(run.experiment_id, identities)


def test_local_requests_keep_coordinator_identity_and_uv_differs(tmp_path):
    from simulatte.execution.coordinator import Coordinator

    m = api()
    base = prepared(tmp_path)
    stopping = Horizon(60, 0).to_dict()
    experiment = m.Experiment.from_dict(definition(stopping=stopping, kpis=["shop/wip"]))
    run = expand(m, experiment, base, kinds={"local": base.runtime, "uv": UV})
    for (configuration, index, kind), request in run.requests.items():
        if kind != "local":
            continue
        expected = Coordinator._build_request(
            base,
            parameters=run.configurations[configuration].parameters,
            seed=100 + index,
            stopping=stopping,
            level="kpi",
            kpis=("shop/wip",),
        )
        assert request == expected
        assert request_id(request) == request_id(expected)
    local, remote = run.requests[(0, 0, "local")], run.requests[(0, 0, "uv")]
    assert request_id(local) != request_id(remote) and content_id(local) != content_id(remote)
    assert ExecutionRequest.from_dict(remote.to_dict()) == remote
    assert remote.to_dict()["runtime"] == UV
    defaulted = expand(m, m.Experiment.from_dict(definition()), base)
    assert defaulted.requests[(0, 0, "local")].stopping == base.metadata["models"][0]["stopping"]


def test_experiment_type_is_derived_from_the_stopping_policy():
    m = api()
    assert m.experiment_type(Horizon(100, 10)) == "steady_state"
    assert m.experiment_type(Horizon(100, 0)) == "terminating_horizon"
    assert m.experiment_type(FinitePopulation(arrivals=50, safety_limit=1000)) == "terminating_finite"
    assert m.experiment_type(FinitePopulation(cutoff=5.0, safety_limit=1000).to_dict()) == "terminating_finite"
    assert m.experiment_type({"type": "horizon", "horizon": 10.0, "warmup": 1.0}) == "steady_state"


def git_project(tmp_path):
    root = tmp_path / "project"
    (root / "layouts").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[tool.simulatte.bundle]\nlayouts = ["layouts/wide.json"]\n')
    (root / "plant.py").write_text("VALUE = 1\n")
    (root / "sweep.experiment.json").write_text(json.dumps(definition()))
    (root / "nested").mkdir()
    (root / "nested/other.experiment.json").write_text("{}")
    (root / "layouts/wide.json").write_text('{"schema_version": "1.0"}')
    (root / "layouts/other.json").write_text('{"schema_version": "1.0"}')
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    return root


def test_experiment_files_and_layout_variants_are_not_source(tmp_path):
    root = git_project(tmp_path)
    spec = discover_project(root / "plant.py")
    assert spec.layouts == ("layouts/wide.json",)
    source = capture_source(spec)
    assert {entry.path for entry in source.entries} == {"plant.py", "pyproject.toml", "layouts/other.json"}
    (root / "sweep.experiment.json").write_text(json.dumps(definition(name="edited")))
    (root / "nested/other.experiment.json").write_text('{"x": 1}')
    (root / "layouts/wide.json").write_text('{"schema_version": "1.0", "grid": {"dx": 3.0}}')
    assert capture_source(spec).content_hash == source.content_hash
    (root / "layouts/other.json").write_text('{"schema_version": "1.0", "grid": {"dx": 3.0}}')
    assert capture_source(spec).content_hash != source.content_hash


def test_run_id_is_stable_across_separate_preparations(tmp_path):
    from simulatte.execution.coordinator import Coordinator
    from tests.execution.test_coordinator import project

    m = api()
    spec = project(tmp_path)
    experiment = m.Experiment.from_dict(
        definition(configurations={"grid": {"n": [1, 2]}}, replications={"count": 2, "base_seed": 7})
    )
    with Coordinator(spec) as coordinator:
        first = coordinator.prepare(timeout=30)
        second = coordinator.prepare(timeout=30)
        assert first.revision != second.revision
        one = expand(m, experiment, first)
        two = expand(m, experiment, second)
    assert one.run_id == two.run_id
    assert one.replications == two.replications
    assert [request_id(r) for r in one.requests.values()] != [request_id(r) for r in two.requests.values()]
