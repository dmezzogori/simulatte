from simulatte import Environment, Layout, Trace, TraceRecorder
from simulatte.trace.format import PREAMBLE
import inspect
import io
import pytest
from simulatte._wire import freeze, pack
from simulatte.layout import LayoutError, Point
from simulatte.server import Server
from simulatte.trace import ReaderLimits, TraceCorrupted
from simulatte.trace.format import RecordType, write_preamble, write_record
from tests.core.test_trace_writer import _read
from tests.layout.test_binding import line
from tests.layout.test_hashes import prepared


def test_layout_initial_and_version(tmp_path):
    env = Environment()
    path = tmp_path / "layout.simtrace"
    TraceRecorder(env, path)
    layout = Layout(env)
    assert env.manifest().requested["layout"] == {"schema_version": "1.0", "status": "unresolved"}
    env.run()
    env.close()
    assert PREAMBLE.unpack(path.read_bytes()[: PREAMBLE.size])[1:] == (1, 1)
    trace = Trace.open(path)
    assert trace.layout.full_hash == layout.prepare().full_hash
    trace.check()


@pytest.mark.parametrize("level", ["full", "kpi"])
def test_recorder_before_stages_and_bind_dependencies(tmp_path, level):
    env = Environment(seed=1)
    path = tmp_path / level
    TraceRecorder(env, path, level=level)
    layout = Layout(env)

    @layout.stage("make", reads=[])
    def make(ctx):
        server = Server(env=env, capacity=1, name="late")
        layout.place(server, footprint={"width": 3}, position=Point(0, 0))

    ref = layout.ref("placements", "late", "footprint", "width")
    layout.on_bind("size", lambda ctx: ctx.physical(ref), reads=[ref])
    env.run()
    env.close()
    trace = Trace.open(path)
    assert trace.layout == layout.prepare().bundle
    assert any(q["descriptor"]["path"] == ref.path for q in trace.layout["dependencies"]["queries"])
    assert trace.manifest["layout"] == trace.layout.manifest()
    records = _read(path)
    assert "layout" not in records[0].body["manifest"]
    trace.check()
    assert trace.verify() == (True if level == "full" else "not_verifiable")


def test_full_kpi_layout_equality_and_recorder_after_prepare(tmp_path):
    bundles = []
    for level in ("full", "kpi"):
        env = Environment(seed=1)
        layout = line(env)
        result = layout.prepare()
        path = tmp_path / level
        TraceRecorder(env, path, level=level)
        env.run()
        env.close()
        trace = Trace.open(path)
        assert trace.layout == result.bundle
        assert not any(record.type == RecordType.PRELUDE for record in _read(path))
        trace.check()
        bundles.append(trace.layout)
    assert bundles[0] == bundles[1]


def test_failed_prepare_has_no_layout_initial(tmp_path):
    env = Environment()
    path = tmp_path / "failed"
    recorder = TraceRecorder(env, path)
    layout = Layout(env)

    @layout.stage("fail", reads=[])
    def fail(ctx):
        raise LayoutError("fail")

    with pytest.raises(LayoutError):
        env.run()
    recorder.close(outcome="failed")
    trace = Trace.open(path)
    assert trace.layout is None and not any(r.type == RecordType.INITIAL for r in _read(path))


def test_no_layout_metadata_and_default_limit(tmp_path):
    assert inspect.signature(TraceRecorder).parameters["max_initial_bytes"].default == 64 << 20
    env = Environment()
    path = tmp_path / "none"
    TraceRecorder(env, path)
    env.run()
    env.close()
    trace = Trace.open(path)
    assert trace.layout is None and trace.manifest["layout"] is None
    assert PREAMBLE.unpack(path.read_bytes()[: PREAMBLE.size])[1:] == (1, 1)
    trace.check()


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_initial_limit_validation(tmp_path, limit):
    with pytest.raises(ValueError, match="max_initial_bytes"):
        TraceRecorder(Environment(), tmp_path / "bad", max_initial_bytes=limit)


def test_initial_limit_is_independent_of_queue_backpressure(tmp_path):
    env = Environment()
    layout = line(env)
    path = tmp_path / "small"
    recorder = TraceRecorder(env, path, max_pending_bytes=1, max_initial_bytes=10)
    with pytest.raises(ValueError, match="INITIAL/layout payload size"):
        env.run()
    recorder.close(outcome="failed")
    assert not any(r.type == RecordType.INITIAL for r in _read(path))
    assert Trace.open(path).layout is None
    env = Environment()
    layout = line(env)
    path = tmp_path / "large"
    TraceRecorder(env, path, max_pending_bytes=1, max_initial_bytes=100_000)
    env.run()
    env.close()
    assert Trace.open(path, limits=ReaderLimits(max_record=100_000)).layout == layout.prepare().bundle
    with pytest.raises(TraceCorrupted):
        Trace.open(path, limits=ReaderLimits(max_record=100))


def initial_trace(tmp_path, bundle, *, manifest=None):
    out = io.BytesIO()
    write_preamble(out)
    write_record(
        out,
        RecordType.HEADER,
        pack(freeze({"features": {"required": []}, "level": "kpi", "manifest": {}, "kinds": {}, "catalog": {}})),
    )
    initial = {"state": {}, "cursor": (0.0, -1), "manifest": {} if manifest is None else {"layout": manifest}}
    if bundle is not None:
        initial["layout"] = bundle
    write_record(out, RecordType.INITIAL, pack(freeze(initial)))
    path = tmp_path / "custom"
    path.write_bytes(out.getvalue())
    return path


@pytest.mark.parametrize("name", ["full_hash", "physical_hash", "layers_hash"])
def test_layout_hash_tamper_with_valid_record_crc(tmp_path, name):
    _, result = prepared()
    data = result.bundle.to_dict()
    data[name] = "0" * 64
    trace = Trace.open(initial_trace(tmp_path, data, manifest=result.bundle.manifest()))
    with pytest.raises(TraceCorrupted, match=name):
        trace.check()


def test_manifest_agreement_and_unknown_layout(tmp_path):
    _, result = prepared()
    for data, manifest in [(result.bundle.to_dict(), {}), (None, result.bundle.manifest())]:
        trace = Trace.open(initial_trace(tmp_path, data, manifest=manifest))
        with pytest.raises(TraceCorrupted, match="manifest"):
            trace.check()
    trace = Trace.open(initial_trace(tmp_path, {"schema_version": "7.0", "future": [1, 2]}))
    assert not trace.layout.reusable
    trace.check()
    with pytest.raises(LayoutError, match="unsupported"):
        trace.layout.check()


# Mutations exercise corruption handling for all known record families, without
# relying on frame CRC failures or running model callbacks during decode.
MALFORMED = [
    ((), None),
    (("schema_version",), 1),
    (("algorithms", "hash"), "unknown"),
    (("full_hash",), "bad"),
    (("stages",), []),
    (("aliases",), {}),
    (("layers", "code", 0, 0), 3),
    (("targets", "machine", "explicitly_named"), 1),
    (("dependencies", "sealed"), False),
    (("dependencies", "queries", 0, "descriptor", "kind"), "bad"),
    (("dependencies", "queries", 0, "descriptor", "path"), [2]),
    (("dependencies", "queries", 0, "code_count"), -1),
    (("dependencies", "queries", 0, "observed"), []),
    (("dependencies", "queries", 0, "origin"), "bad"),
    (("dependencies", "queries", 0, "observed", "state"), "bad"),
    (("dependencies", "queries", 0, "targets", "machine"), 4),
    (("layers", "extra"), 1),
    (("stages", "binders", 0, "name"), 1),
    (("stages", "binders", 0, "after"), 2),
    (("stages", "binders", 0, "reads", 0, "kind"), "bad"),
    (("stages", "binders", 0, "reads", 0, "path"), [1]),
    (("auto", "algorithm"), "bad"),
    (("bindings", "fake"), 1),
    (("leaves", 0, "state"), "bad"),
    (("leaves", 0, "source", "kind"), "bad"),
]


@pytest.mark.parametrize("path,value", MALFORMED)
def test_malformed_known_bundle(tmp_path, path, value):
    _, result = prepared(read=True)
    data = result.bundle.to_dict()
    if path:
        parent = data
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = value
    else:
        del data["resolved"]
    with pytest.raises(TraceCorrupted, match="malformed"):
        Trace.open(initial_trace(tmp_path, data))


@pytest.mark.parametrize(
    "path,value",
    [
        (("nodes",), {}),
        (("graph",), []),
        (("nodes", 0, "enabled"), 1),
        (("edges", 0, "direction"), "bad"),
        (("graph", "nodes"), {}),
        (("nodes", 0, "position"), [0, 0, 1]),
        (("edges", 0, "enabled"), 1),
    ],
)
def test_malformed_known_network(tmp_path, path, value):
    data = line(Environment()).prepare().bundle.to_dict()
    parent = data["network"]
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    with pytest.raises(TraceCorrupted, match="malformed"):
        Trace.open(initial_trace(tmp_path, data))


@pytest.mark.parametrize(
    "predicate,field,value",
    [
        ("obstacle", "flags", {"machine": 42}),
        ("obstacle", "members", [42]),
        ("obstacle", "flags", {"machine": {"state": "present", "value": 1}}),
        ("obstacle", "flags", {"machine": {"state": "bad", "value": None}}),
        ("obstacle", "flags", {"machine": {"state": "absent", "value": False}}),
        ("connector", "flags", {"machine": {"state": "present", "ports": []}}),
        ("connector", "flags", {"machine": {"state": "present", "ports": {"p": 42}}}),
        ("connector", "flags", {"machine": {"state": "bad", "ports": {}}}),
        ("obstacle", "selected", ["absent-member"]),
    ],
)
def test_known_selection_observation_nested_corruption(tmp_path, predicate, field, value):
    _, result = prepared()
    data = result.bundle.to_dict()
    query = next(q for q in data["dependencies"]["queries"] if q["descriptor"].get("predicate") == predicate)
    query["observed"][field] = value
    with pytest.raises(TraceCorrupted):
        Trace.open(initial_trace(tmp_path, data))
