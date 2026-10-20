"""Presentation recordings cloned from a captured study request (design 9, P4 Task 1)."""

from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from simulatte import Trace
from simulatte.analysis import DatasetSchema, Field, MeasurementPlan
from simulatte.analysis.measurements import select_measurements
from simulatte.cli import main
from simulatte.execution import coordinator as m
from simulatte.execution import store as store_module
from simulatte.execution.replay import Verification
from simulatte.execution.schema import ExecutionRequest, request_id
from simulatte.execution.slots import SlotConfig
from simulatte.execution.store import _ACTIVE, _blob, _read
from simulatte.experiments import load_experiment
from simulatte.model import ParameterField, ParameterSchema
from simulatte.presentations import recording
from simulatte.presentations.recording import (
    make_presentation_request,
    preview_recording,
    presentation_diff,
    record_presentation,
    recordings,
)
from tests.execution.test_coordinator import MODEL, project
from tests.execution.test_replay import kpi_original
from tests.execution.test_scheduler import Peer, until, write_experiment

PLAN = MeasurementPlan("numbers", (DatasetSchema("numbers", (Field("x", "int64"),)),))
# The current model defaults: horizon 1,000 without warm-up; the captured study differs from both.
RECORDED = MODEL.replace(
    '@simulatte.model(params=Params, horizon=2, inputs=["input.txt"])',
    """from simulatte.analysis import DatasetSchema, Field, MeasurementPlan, ObservationCollector
plan = MeasurementPlan('numbers', (DatasetSchema('numbers', (Field('x', 'int64'),)),))
@simulatte.model(params=Params, horizon=1000, inputs=["input.txt"], measurement_plans=(plan,))""",
).replace(
    "    def process():",
    """    collector = ObservationCollector(plan.datasets[0], fields={}).attach(env)
    def process():
        collector.record(None, observed_at='observation', values={'x': params.n})""",
)
STUDY = {"type": "horizon", "horizon": 10_000.0, "warmup": 1_000.0}


def captured(**changes):
    """A study request: horizon 10,000 with warm-up 1,000 and a nondefault measurement selection."""
    return ExecutionRequest(
        **{
            "model": "plant.py:build",
            "source_hash": "a" * 64,
            "input_hash": "b" * 64,
            "layout_hash": "c" * 64,
            "layout_revision": 3,
            "parameters": {"n": 3},
            "seed": 7,
            "stopping": STUDY,
            "level": "kpi",
            "kpis": ("shop/wip",),
            "measurements": select_measurements(PLAN, {"limit": 3}),
            "runtime": {"implementation": "CPython", "version": "3.14.1"},
        }
        | changes
    )


def metadata(stopping=None):
    """Captured model-info: model defaults are horizon 1,000 and zero warm-up."""
    return {
        "selected": "build",
        "models": [
            {
                "name": "build",
                "parameters": ParameterSchema((ParameterField("n", "int", False, 2),)).to_dict(),
                "inputs": [],
                "stopping": stopping or {"type": "horizon", "horizon": 1_000.0, "warmup": 0.0},
                "measurement_plans": [PLAN.to_dict()],
            }
        ],
    }


def states(coordinator):
    return [
        (event["body"]["attempt_id"], event["body"]["mode"], event["body"]["status"], event["body"]["verified"])
        for event in coordinator.events()
        if event["type"] == "recording_state"
    ]


def idle(coordinator):
    """Wait until every slot finished its last job: a result is visible shortly before its slot is idle."""
    until(lambda: all(a.current is None and a.pending is None for a in coordinator._actors.values()))


def recording_rows(store):
    with store._transaction() as db:
        return recordings(db)


def test_presentation_request_clones_complete_capture():
    original = captured()
    target = make_presentation_request(original, seed=11, metadata=metadata())
    assert target.stopping == original.stopping
    assert dict(target.stopping) == {"type": "horizon", "horizon": 10_000.0, "warmup": 1_000.0}
    assert target.measurements == original.measurements
    assert target.source_hash == original.source_hash
    assert target.runtime == original.runtime
    before, after = original.to_dict(), target.to_dict()
    assert {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)} == {"seed", "level"}
    assert (target.seed, target.level) == (11, "full")
    assert presentation_diff(original, target) == [
        {"field": "level", "original": "kpi", "target": "full"},
        {"field": "seed", "original": "7", "target": "11"},
    ]
    assert recording.recording_mode(original, target) == "new_seed"
    same = make_presentation_request(original, seed=7)
    assert recording.recording_mode(original, same) == "replay"
    assert presentation_diff(original, same) == [{"field": "level", "original": "kpi", "target": "full"}]
    assert presentation_diff(replace(original, level="full"), same) == []


def test_finite_population_stopping_and_unmeasured_requests_are_cloned():
    population = {"type": "finite_population", "arrivals": 500, "cutoff": None, "safety_limit": 1e6}
    original = captured(stopping=population, measurements=None)
    target = make_presentation_request(original, seed=1, metadata=metadata())
    assert dict(target.stopping) == population and target.measurements is None
    assert "measurements" not in target.to_dict()


def test_explicit_edits_are_validated_against_the_captured_schemas():
    original = captured()
    info = metadata()
    edited = make_presentation_request(original, seed=7, overrides={"parameters": {"n": 5}}, metadata=info)
    assert dict(edited.parameters) == {"n": 5}
    assert recording.recording_mode(original, edited) == "edited"
    assert presentation_diff(original, edited) == [
        {"field": "level", "original": "kpi", "target": "full"},
        {"field": "parameters.n", "original": 3, "target": 5},
    ]
    stopping = {"type": "horizon", "horizon": 500.0, "warmup": 50.0}
    measured = make_presentation_request(
        original,
        seed=7,
        overrides={"stopping": stopping, "kpis": ["shop/leadtime"], "measurements": {"plan": "numbers"}},
        metadata=info,
    )
    assert dict(measured.stopping) == stopping and measured.kpis == ("shop/leadtime",)
    assert dict(measured.measurements) == select_measurements(PLAN)
    assert [item["field"] for item in presentation_diff(original, measured)] == [
        "kpis",
        "level",
        "measurements",
        "stopping",
    ]
    removed = make_presentation_request(original, seed=7, overrides={"measurements": None})
    assert removed.measurements is None
    assert presentation_diff(original, removed)[1] == {
        "field": "measurements",
        "original": original.to_dict()["measurements"],
        "target": None,
    }
    full = select_measurements(PLAN, {"limit": 9})
    assert dict(make_presentation_request(original, seed=7, overrides={"measurements": full}).measurements) == full
    declared = make_presentation_request(original, seed=7, overrides={"measurements": full}, metadata=info)
    assert dict(declared.measurements) == full
    # An explicit value equal to the capture changes nothing: the request is the original's.
    assert make_presentation_request(original, seed=7, overrides={"parameters": {"n": 3}}) == same(original)
    # Without captured metadata only the request schema applies.
    assert dict(make_presentation_request(original, seed=7, overrides={"parameters": {"m": 1}}).parameters) == {
        "n": 3,
        "m": 1,
    }
    refusals = [
        ({"source_hash": "d" * 64}, "new preparation"),
        ({"runtime": {}}, "new preparation"),
        ({"model": "other.py:build"}, "new preparation"),
        ({"seed": 3}, "Unknown presentation override"),
        ({"level": "kpi"}, "Unknown presentation override"),
        ({"parameters": {"m": 1}}, "Unknown parameter"),
        ({"parameters": {"n": "five"}}, "n"),
        ({"parameters": [1]}, "parameters"),
        ({"stopping": {"type": "horizon", "horizon": 1.0, "warmup": 2.0}}, "horizon"),
        ({"stopping": "long"}, "stopping"),
        ({"kpis": "shop/wip"}, "kpis"),
        ({"kpis": ["a", "a"]}, "unique"),
        ({"measurements": {"plan": "absent"}}, "Unknown measurement plan"),
        ({"measurements": {**full, "definition_hash": "e" * 64}}, "differs from the captured declaration"),
        ({"measurements": {**full, "plan": "other"}}, "Unknown measurement plan"),
        ({"measurements": {"schema": 2}}, "Invalid measurement selection"),
    ]
    for overrides, message in refusals:
        with pytest.raises(ValueError, match=message):
            make_presentation_request(original, seed=7, overrides=overrides, metadata=info)
    with pytest.raises(ValueError, match="mapping"):
        make_presentation_request(original, seed=7, overrides=[("parameters", {})])
    with pytest.raises(ValueError, match="captured model metadata"):
        make_presentation_request(original, seed=7, overrides={"measurements": {"plan": "numbers"}})


def same(original):
    return replace(original, level="full")


@pytest.mark.parametrize("seed", [-1, 2**63, True, "3", 1.0, None])
def test_invalid_seed_is_refused(seed):
    with pytest.raises(ValueError, match="seed"):
        make_presentation_request(captured(), seed=seed)


def test_experiment_replication_records_a_new_seed_and_a_verified_replay(tmp_path, monkeypatch):
    spec = project(tmp_path, RECORDED)
    selection = select_measurements(PLAN, {"limit": 3})
    path = write_experiment(
        spec.root,
        replications={"count": 1},
        configurations={"grid": {"n": [3]}},
        stopping=STUDY,
        measurements=selection,
    )
    calls = []
    verify = recording.verify_replay
    monkeypatch.setattr(recording, "verify_replay", lambda *args: calls.append(args) or verify(*args))
    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        run_id = coordinator.start_experiment(load_experiment(path))
        assert coordinator.wait_experiment(run_id, timeout=30)["status"] == "completed"
        (replication,) = coordinator.store.replications(run_id)
        attempt = replication["attempt"]
        original = coordinator.store.request(replication["request_id"])
        assert dict(original.stopping) == STUDY and dict(original.measurements) == selection
        coordinator.events()
        fresh = record_presentation(coordinator, attempt, seed=original.seed + 100, timeout=20)
        assert (fresh.mode, fresh.verification, fresh.original_attempt) == ("new_seed", None, attempt)
        assert calls == []
        target = coordinator.store.request(fresh.key.request_id)
        assert target.stopping == original.stopping and target.measurements == original.measurements
        assert target.source_hash == original.source_hash and target.runtime == original.runtime
        assert target == replace(original, seed=original.seed + 100, level="full")
        assert [item["field"] for item in fresh.diff] == ["level", "seed"]
        result = coordinator.store.result(fresh.key.attempt_id)
        assert Trace.open(result.path / "run.simtrace").verify() is True
        assert (result.path / "artifacts/datasets/numbers/manifest.json").is_file()
        assert coordinator.store.attempt(fresh.key)["slot"] == "local-1"
        idle(coordinator)
        replayed = record_presentation(coordinator, attempt, seed=original.seed, timeout=20)
        assert (replayed.mode, replayed.verification) == ("replay", Verification(True, None))
        assert len(calls) == 1 and replayed.key.request_id == request_id(replace(original, level="full"))
        assert states(coordinator) == [
            (fresh.key.attempt_id, "new_seed", "running", None),
            (fresh.key.attempt_id, "new_seed", "succeeded", None),
            (replayed.key.attempt_id, "replay", "running", None),
            (replayed.key.attempt_id, "replay", "succeeded", True),
        ]
        listed = recording_rows(coordinator.store)
        assert [(item["attempt_id"], item["mode"], item["status"], item["verified"]) for item in listed] == [
            (replayed.key.attempt_id, "replay", "succeeded", True),
            (fresh.key.attempt_id, "new_seed", "succeeded", None),
        ]
        assert listed[1] == {
            "attempt_id": fresh.key.attempt_id,
            "request_id": fresh.key.request_id,
            "original_attempt": attempt,
            "mode": "new_seed",
            "seed": original.seed + 100,
            "status": "succeeded",
            "verified": None,
            "created": listed[1]["created"],
        }
        with coordinator.store._transaction() as db:
            assert recordings(db, original_attempt="f" * 32) == []
            assert len(recordings(db, original_attempt=attempt)) == 2
            owned = db.execute(
                'SELECT target_kind,target_id FROM "references" WHERE owner_kind=? AND owner_id=? ORDER BY 1',
                ("presentation-recording", fresh.key.attempt_id),
            ).fetchall()
            row = db.execute("SELECT manifest FROM snapshots WHERE id=?", (replayed.key.attempt_id,)).fetchone()
        assert sorted(map(tuple, owned)) == sorted(
            [("attempt", fresh.key.attempt_id), ("result", attempt), ("result", fresh.key.attempt_id)]
        )
        assert _read(row[0])["verification"] == {"verified": True, "reason": None}
        # The diff command's preview names the same target before anything runs.
        assert preview_recording(coordinator.store, attempt, seed=original.seed + 100) == {
            "mode": "new_seed",
            "diff": list(fresh.diff),
            "target_request_id": fresh.key.request_id,
        }
        # The original result and replication are untouched.
        assert coordinator.store.replications(run_id) == [replication]


def test_explicit_edits_create_distinct_presentation_run(tmp_path, monkeypatch):
    spec = project(tmp_path, RECORDED)
    calls = []
    monkeypatch.setattr(recording, "verify_replay", lambda *args: calls.append(args))
    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        original = kpi_original(coordinator, n=3)
        attempt = original.key.attempt_id
        request = coordinator.store.request(original.key.request_id)
        edited = record_presentation(
            coordinator, attempt, seed=request.seed, overrides={"parameters": {"n": 6}}, timeout=20
        )
        # The same seed does not make an edited run a reproduction of the original.
        assert (edited.mode, edited.verification) == ("edited", None) and calls == []
        assert edited.key.request_id not in (request_id(replace(request, level="full")), original.key.request_id)
        assert coordinator.store.request(edited.key.request_id) == replace(request, level="full", parameters={"n": 6})
        assert [item["field"] for item in edited.diff] == ["level", "parameters.n"]
        assert recording_rows(coordinator.store)[0]["verified"] is None
        answer = coordinator.store.result(edited.key.attempt_id).path / "artifacts/answer.txt"
        assert answer.read_text() == "11"


def test_recording_refuses_an_insufficient_historical_environment(tmp_path, monkeypatch):
    from simulatte.projects.environments import EnvironmentPool

    spec = project(tmp_path)
    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        original = kpi_original(coordinator, n=3)
        with coordinator.store._transaction() as db:
            before = db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]

        def reconstruct(*args, **kwargs):
            pytest.fail("recording never reconstructs a historical environment")

        monkeypatch.setattr(EnvironmentPool, "reconstruct", reconstruct)
        monkeypatch.setattr(coordinator, "runtime", SimpleNamespace(content_hash="f" * 64))
        with pytest.raises(ValueError, match="Requested runtime differs from this installation"):
            record_presentation(coordinator, original.key.attempt_id, seed=1)
        with coordinator.store._transaction() as db:
            assert db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == before
        assert recording_rows(coordinator.store) == []


def test_recording_resolves_only_committed_captured_attempts(tmp_path):
    spec = project(tmp_path)
    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        original = kpi_original(coordinator, n=3)
        attempt = original.key.attempt_id
        store = coordinator.store
        with pytest.raises(ValueError, match="attempt id"):
            record_presentation(coordinator, "x", seed=1)
        with pytest.raises(KeyError):
            record_presentation(coordinator, "0" * 32, seed=1)
        with pytest.raises(ValueError, match="Timeout"):
            record_presentation(coordinator, attempt, seed=1, timeout=0)
        with pytest.raises(ValueError, match="worker"):
            record_presentation(coordinator, attempt, seed=1, worker=3)
        prepared = coordinator.prepare(timeout=10)
        pending = coordinator.store.assign(coordinator.make_request(prepared, parameters={"n": 4}), prepared.revision)
        with pytest.raises(ValueError, match="No committed result"):
            record_presentation(coordinator, pending.attempt_id, seed=1)
        with store._transaction() as db:
            row = db.execute(
                "SELECT manifest FROM snapshots WHERE id=? AND kind='preparation'", (original.key.request_id,)
            ).fetchone()
            manifest = _read(row[0])
            del manifest["documents"]["expected_metadata_ref"]
            db.execute(
                "UPDATE snapshots SET manifest=? WHERE id=? AND kind='preparation'",
                (_blob(manifest), original.key.request_id),
            )
        # Without captured metadata an unedited recording still works; explicit edits are refused.
        assert preview_recording(store, attempt, seed=1)["mode"] == "new_seed"
        with pytest.raises(ValueError, match="captured model metadata"):
            preview_recording(store, attempt, seed=1, overrides={"parameters": {"n": 1}})
        with store._transaction() as db:
            db.execute("DELETE FROM snapshots WHERE id=? AND kind='preparation'", (original.key.request_id,))
        with pytest.raises(ValueError, match="preparation is unavailable"):
            record_presentation(coordinator, attempt, seed=1)
        assert recording_rows(store) == []


def test_busy_slots_refuse_and_an_interactive_manual_run_keeps_ownership(tmp_path, monkeypatch):
    spec = project(tmp_path)
    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        original = kpi_original(coordinator, n=3)

        def busy(*args, slot, **kwargs):
            raise RuntimeError(f"Slot {slot} is busy")

        with monkeypatch.context() as patch:
            patch.setattr(coordinator, "_schedule", busy)
            with pytest.raises(RuntimeError, match="Every compatible slot is busy; record again"):
                record_presentation(coordinator, original.key.attempt_id, seed=1)

            def closed(*args, **kwargs):
                raise RuntimeError("Coordinator is closed")

            patch.setattr(coordinator, "_schedule", closed)
            with pytest.raises(RuntimeError, match="closed"):
                record_presentation(coordinator, original.key.attempt_id, seed=1)
        assert recording_rows(coordinator.store) == []
    root = tmp_path / "interactive"
    root.mkdir()
    release = tmp_path / "release"
    # A manual run with n == 4 holds the interactive slot until the test releases it.
    held = MODEL.replace(
        "    def process():",
        f"""    import time
    while params.n == 4 and not Path({str(release)!r}).exists():
        time.sleep(0.01)
    def process():""",
    )
    spec = project(root, held)
    with m.Coordinator(spec, slots=SlotConfig(local=0)) as coordinator:
        original = kpi_original(coordinator, n=3)
        prepared = coordinator.prepare(timeout=10)
        manual = coordinator.submit(
            coordinator.make_request(prepared, parameters={"n": 4}), prepared.revision, timeout=20
        )
        # The manual run owns the interactive slot: the recording neither supersedes nor queues behind it.
        with pytest.raises(RuntimeError, match="Every compatible slot is busy"):
            record_presentation(coordinator, original.key.attempt_id, seed=1)
        assert coordinator.store.attempt(manual)["status"] in _ACTIVE
        assert coordinator.store.slots()["interactive"]["active"] == manual.attempt_id
        assert recording_rows(coordinator.store) == []
        release.write_text("")
        coordinator.wait(manual, timeout=20)
        until(lambda: coordinator._owner() is None)
        job = record_presentation(coordinator, original.key.attempt_id, seed=1, timeout=20)
        assert job.mode == "new_seed" and coordinator.store.attempt(job.key)["slot"] == "interactive"


def test_remote_captured_request_follows_replay_worker_rules(tmp_path, monkeypatch):
    spec = project(tmp_path)
    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        original = kpi_original(coordinator, n=3)
        attempt = original.key.attempt_id
        with coordinator.store._transaction() as db:
            db.execute("UPDATE attempts SET worker='alpha' WHERE id=?", (attempt,))
        tried = []

        def busy(*args, slot, **kwargs):
            tried.append(slot)
            raise RuntimeError(f"Slot {slot} is busy")

        with monkeypatch.context() as patch:
            patch.setattr(coordinator, "_worker_specs", lambda names: {name: object() for name in names})
            patch.setattr(coordinator, "_add_workers", lambda specs: None)
            for name, host in (("alpha-1", "alpha"), ("beta-1", "beta")):
                patch.setitem(coordinator._actors, name, SimpleNamespace(kind="uv", host=host))
            patch.setattr(coordinator, "_schedule", busy)
            # A request captured on host alpha never moves to beta implicitly.
            with pytest.raises(RuntimeError, match="Every compatible slot is busy"):
                record_presentation(coordinator, attempt, seed=1)
            assert tried == ["alpha-1"]
            patch.delitem(coordinator._actors, "alpha-1")
            with pytest.raises(m.ExecutionFailure, match="alpha") as failure:
                record_presentation(coordinator, attempt, seed=1)
            assert failure.value.status == "no_compatible_worker" and tried == ["alpha-1"]
            with pytest.raises(RuntimeError, match="busy"):
                record_presentation(coordinator, attempt, seed=1, worker="alpha")
            assert tried == ["alpha-1", "beta-1"]
        with pytest.raises(m.ExecutionFailure, match="alpha"):
            record_presentation(coordinator, attempt, seed=1)
        # An explicit local worker runs the captured request on a compatible local slot.
        job = record_presentation(coordinator, attempt, seed=1, worker="local", timeout=20)
        assert job.mode == "new_seed" and coordinator.store.attempt(job.key)["slot"] == "local-1"
        assert coordinator.store.request(job.key.request_id) == replace(
            coordinator.store.request(original.key.request_id), seed=1, level="full"
        )


def test_failed_and_interrupted_recordings_are_announced(tmp_path, monkeypatch):
    spec = project(tmp_path)
    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        original = kpi_original(coordinator, n=3)
        attempt = original.key.attempt_id
        coordinator.events()
        with monkeypatch.context() as patch:
            patch.setattr(m.LocalTransport, "start", lambda *args, **kwargs: Peer())
            with pytest.raises(m.ExecutionFailure) as lost:
                record_presentation(coordinator, attempt, seed=9, timeout=20)
        assert lost.value.status == "lost"
        key = lost.value.key
        assert states(coordinator) == [
            (key.attempt_id, "new_seed", "running", None),
            (key.attempt_id, "new_seed", "lost", None),
        ]
        assert [(item["attempt_id"], item["status"]) for item in recording_rows(coordinator.store)] == [
            (key.attempt_id, "lost")
        ]

        idle(coordinator)

        def interrupted(key, *, timeout=None):
            raise KeyboardInterrupt

        monkeypatch.setattr(coordinator, "wait", interrupted)
        with pytest.raises(KeyboardInterrupt):
            record_presentation(coordinator, attempt, seed=10, timeout=20)
        (cancelled, _) = recording_rows(coordinator.store)
        assert cancelled["status"] == "cancelled"
        assert [state[2] for state in states(coordinator)] == ["running", "cancelled"]


def test_prune_keeps_unadopted_recordings_until_recordings_are_dropped(tmp_path, monkeypatch, capsys):
    spec = project(tmp_path)
    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        original = kpi_original(coordinator, n=3)
        jobs = []
        for seed in (1, 2, 3):
            idle(coordinator)
            jobs.append(record_presentation(coordinator, original.key.attempt_id, seed=seed, timeout=20))
        oldest, middle, newest = (job.key.attempt_id for job in jobs)
        store = coordinator.store
        # A presentation revision adopted the oldest recording.
        with store._transaction() as db:
            db.execute('INSERT INTO "references" VALUES (?,?,?,?)', ("presentation-revision", "p:1", "result", oldest))
        assert store.prune()["results"] == []
        assert len(recording_rows(store)) == 3
        monkeypatch.setattr(store_module, "KEEP_RECORDINGS", 1)
        dry = store.prune(dry_run=True, recordings=True)
        assert (dry["results"], dry["recordings"]) == ([middle], [middle])
        assert len(recording_rows(store)) == 3 and store.result(middle)
        assert store.prune(recordings=True) == dry
        assert [item["attempt_id"] for item in recording_rows(store)] == [newest, oldest]
        with pytest.raises(KeyError):
            store.result(middle)
        assert store.result(oldest) and store.result(newest) and store.result(original.key.attempt_id)
        with store._transaction() as db:
            assert (
                db.execute(
                    'SELECT COUNT(*) FROM "references" WHERE owner_kind=? AND owner_id=?',
                    ("presentation-recording", middle),
                ).fetchone()[0]
                == 0
            )
    monkeypatch.setattr(store_module, "KEEP_RECORDINGS", 0)
    root = str(spec.root)
    assert main(["store", "prune", "--recordings", "--dry-run", "--project", root, "--json"]) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["recordings"] == [newest] and envelope["results"] == [newest]
    assert main(["store", "prune", "--recordings", "--project", root]) == 0
    assert capsys.readouterr().out.startswith("removed 1 results, 0 snapshots and 1 recordings")
    assert main(["store", "prune", "--project", root, "--json"]) == 0
    assert "recordings" not in json.loads(capsys.readouterr().out)
