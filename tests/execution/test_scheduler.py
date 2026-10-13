"""Multi-slot scheduling of experiment replications through the local coordinator (design 4.3, D96, D97)."""

import json
import threading
import time
from types import SimpleNamespace

import pytest

from simulatte.execution import coordinator as m
from simulatte.execution.schema import AttemptKey
from simulatte.execution.slots import SlotConfig
from simulatte.execution.store import StaleAttempt, _blob, _read
from simulatte.experiments import load_experiment
from tests.execution.test_coordinator import MODEL, project

# n == 0 raises inside the simulation, n == 1 never finishes, delay slows a replication down.
SLOW = MODEL.replace("    n: int = 2\n", "    n: int = 2\n    delay: float = 0.0\n").replace(
    "        yield env.timeout(1)\n",
    "        yield env.timeout(1)\n"
    "        import time\n"
    "        time.sleep(params.delay)\n"
    "        if params.n == 0:\n"
    "            raise RuntimeError('model failure')\n"
    "        if params.n == 1:\n"
    "            while True: pass\n",
)


def write_experiment(root, name="sweep", **changes):
    data = {
        "schema": 1,
        "name": name,
        "model": "plant.py:build",
        "replications": {"count": 3},
        "configurations": {"grid": {"n": [3, 4]}},
        **changes,
    }
    path = root / f"{name}.experiment.json"
    path.write_text(json.dumps(data))
    return path


def until(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.02)


def attempts(store, *, prefix="local-"):
    with store._transaction() as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT id,request_id,generation,status,slot,created FROM attempts WHERE slot LIKE ? ORDER BY created",
                (prefix + "%",),
            )
        ]


def key_of(row):
    return AttemptKey(row["request_id"], row["id"], row["generation"])


def active_key(store, slot):
    active = store.slots()[slot]["active"]
    if active is None:
        return None
    (row,) = [row for row in attempts(store, prefix=slot) if row["id"] == active]
    return key_of(row)


def test_experiment_runs_replication_major_on_two_local_slots_and_reuses_by_content(tmp_path):
    spec = project(tmp_path)
    path = write_experiment(spec.root)
    with m.Coordinator(spec, slots=SlotConfig(local=2)) as coordinator:
        run_id = coordinator.start_experiment(load_experiment(path))
        report = coordinator.wait_experiment(run_id, timeout=60)
        events = coordinator.events()
        assert report["run_id"] == run_id and report["status"] == "completed"
        assert report["counts"]["succeeded"] == 6
        assert [item["counts"]["succeeded"] for item in report["configurations"]] == [3, 3]
        assert not any(item["mixed_runtime"] for item in report["configurations"])
        run = coordinator.store.experiment_run(run_id)
        dispatched = attempts(coordinator.store)
        assert {row["slot"] for row in dispatched} == {"local-1", "local-2"}
        order = {row["id"]: index for index, row in enumerate(dispatched)}
        replications = coordinator.store.replications(run_id)
        by_dispatch = sorted(replications, key=lambda item: order[item["attempt"]])
        assert [(item["index"], item["configuration"]) for item in by_dispatch] == [
            (0, 0),
            (0, 1),
            (1, 0),
            (1, 1),
            (2, 0),
            (2, 1),
        ]
        for item in replications:
            assert item["status"] == "succeeded" and item["dispatches"] == 1
            result = coordinator.store.result(item["attempt"])
            n = run["configurations"][item["configuration"]]["parameters"]["n"]
            assert (result.path / "artifacts/answer.txt").read_text() == str(n + 5)
            assert result.manifest["request"]["seed"] == str(item["index"])
        progress = [event for event in events if event["type"] == "experiment_progress"]
        assert progress and len({event["body"]["run_id"] for event in progress}) == len(progress)
        assert progress[-1]["body"]["counts"]["succeeded"] == 6
        executed = len(dispatched)
    # The same file after a fresh prepare() is the same run (content ids exclude the revision): nothing runs.
    with m.Coordinator(spec, slots=SlotConfig(local=2)) as coordinator:
        assert coordinator.start_experiment(load_experiment(path)) == run_id
        assert coordinator.wait_experiment(run_id, timeout=10)["status"] == "completed"
        # A different definition with equal requests reuses every committed replication by content id.
        copy = coordinator.start_experiment(load_experiment(write_experiment(spec.root, "copy")))
        assert copy != run_id
        reused = coordinator.wait_experiment(copy, timeout=10)
        assert reused["status"] == "completed" and reused["counts"]["reused"] == 6
        assert len(attempts(coordinator.store)) == executed
        # Two replications of one configuration resolved different dependencies.
        first = coordinator.store.replications(copy)[0]
        with coordinator.store._transaction() as db:
            blob = db.execute("SELECT manifest FROM results WHERE attempt=?", (first["attempt"],)).fetchone()[0]
            manifest = _read(blob)
            manifest["simulation"]["dependencies"]["hash"] = "0" * 64
            db.execute("UPDATE results SET manifest=? WHERE attempt=?", (_blob(manifest), first["attempt"]))
        flags = [item["mixed_runtime"] for item in coordinator.wait_experiment(copy)["configurations"]]
        assert flags[first["configuration"]] is True and flags.count(True) == 1


def test_interactive_edit_does_not_cancel_experiment(tmp_path):
    spec = project(tmp_path, SLOW)
    path = write_experiment(
        spec.root, replications={"count": 2}, configurations={"grid": {"delay": [2.0]}}, level="full"
    )
    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        state = coordinator.inputs.bind(coordinator.prepare(timeout=10))
        run_id = coordinator.start_experiment(load_experiment(path))
        # The bound preparation is reused: starting the experiment creates no revision.
        assert coordinator.store.current_revision() == state.revision
        state = coordinator.inputs.apply(state.revision, {"parameters": {"n": 2, "delay": 5.0}})
        interactive = active_key(coordinator.store, "interactive")
        assert interactive is not None
        until(lambda: coordinator.store.attempt(interactive)["status"] == "running")
        experiment = active_key(coordinator.store, "local-1")
        assert experiment is not None
        state = coordinator.inputs.apply(state.revision, {"parameters": {"n": 3, "delay": 0.0}})
        assert coordinator.store.attempt(interactive)["status"] == "superseded"
        coordinator.store.require_active(experiment)
        report = coordinator.wait_experiment(run_id, timeout=60)
        assert report["status"] == "completed" and report["counts"]["succeeded"] == 2
        assert {row["status"] for row in attempts(coordinator.store)} == {"succeeded"}
        replacement = active_key(coordinator.store, "interactive")
        if replacement is not None:
            assert coordinator.wait(replacement, timeout=20).key == replacement


class Peer:
    """A worker transport whose stream ends after `run` (EOF), optionally only once `gate` is set."""

    def __init__(self, gate=None):
        self.gate = gate
        self.hello = True
        self.ran = False
        self.closed = False
        self.channel = SimpleNamespace(last_heartbeat=time.monotonic())

    def send(self, kind, body, *, key):
        self.ran = self.ran or kind == "run"

    def receive(self, timeout):
        self.channel.last_heartbeat = time.monotonic()
        if self.hello:
            self.hello = False
            return SimpleNamespace(type="hello", body={"protocol": [1, 0], "capabilities": ["metadata-artifacts"]})
        if self.gate is not None and not self.gate.wait(timeout):
            raise TimeoutError()
        raise OSError("transport closed")

    def close(self):
        self.closed = True


def test_lost_redispatch_fences_old_attempt_and_stops_after_two(tmp_path, monkeypatch):
    spec = project(tmp_path)
    path = write_experiment(spec.root, replications={"count": 1}, configurations={"grid": {"n": [3]}})
    gate = threading.Event()
    peers = []

    def start(*args, **kwargs):
        peers.append(Peer(gate if len(peers) == 1 else None))
        return peers[-1]

    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        coordinator.inputs.bind(coordinator.prepare(timeout=10))
        monkeypatch.setattr(m.LocalTransport, "start", start)
        run_id = coordinator.start_experiment(load_experiment(path))
        until(lambda: coordinator.store.replications(run_id)[0]["dispatches"] == 2)
        first, second = attempts(coordinator.store)
        assert first["status"] == "lost"
        old = key_of(first)
        # A late result of the lost attempt is fenced: it is no slot's assignment any more.
        assert coordinator._active(old) is False
        with pytest.raises(StaleAttempt):
            coordinator.store.require_active(old)
        with pytest.raises(StaleAttempt):
            coordinator.store.set_replication_status(run_id, 0, 0, "succeeded", attempt=old.attempt_id)
        coordinator._scheduler.on_terminal(old, "succeeded")
        (replication,) = coordinator.store.replications(run_id)
        assert replication["status"] == "dispatched" and replication["attempt"] == second["id"]
        gate.set()
        report = coordinator.wait_experiment(run_id, timeout=20)
        (replication,) = coordinator.store.replications(run_id)
        assert (replication["status"], replication["dispatches"]) == ("lost", 3)
        assert report["status"] == "failed" and report["counts"]["lost"] == 1
        assert [row["status"] for row in attempts(coordinator.store)] == ["lost", "lost", "lost"]
        assert len({row["request_id"] for row in attempts(coordinator.store)}) == 1
        assert len(peers) == 3 and all(peer.ran and peer.closed for peer in peers)


def test_model_failure_is_terminal_and_not_redispatched(tmp_path):
    from simulatte.experiments import run

    spec = project(tmp_path, SLOW)
    path = write_experiment(spec.root, replications={"count": 2}, configurations={"grid": {"n": [0, 3]}})
    report = run(path, project=spec.root, slots=SlotConfig(local=2), timeout=30)
    assert report["status"] == "completed"
    counts = {item["label"]: item["counts"] for item in report["configurations"]}
    assert counts["n=0/base"]["failed"] == 2 and counts["n=3/base"]["succeeded"] == 2
    with m.RunStore.open(spec.root / ".simulatte") as store:
        failed = store.replications(report["run_id"], status="failed")
        assert len(failed) == 2 and all(item["dispatches"] == 1 for item in failed)


def test_cancel_stops_active_attempts_and_cancels_pending_within_five_seconds(tmp_path):
    spec = project(tmp_path, SLOW)
    path = write_experiment(spec.root, configurations={"grid": {"n": [1]}})
    with m.Coordinator(spec, slots=SlotConfig(local=2)) as coordinator:
        run_id = coordinator.start_experiment(load_experiment(path))
        until(lambda: [row["status"] for row in attempts(coordinator.store)] == ["running", "running"])
        started = time.monotonic()
        coordinator.cancel_experiment(run_id)
        until(lambda: all(actor.current is None for actor in coordinator._actors.values()), timeout=5)
        report = coordinator.wait_experiment(run_id, timeout=5)
        assert time.monotonic() - started < 5
        assert report["status"] == "cancelled" and report["counts"]["cancelled"] == 3
        assert [row["status"] for row in attempts(coordinator.store)] == ["cancelled", "cancelled"]
        with coordinator.store._transaction() as db:
            assert db.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 0
        with pytest.raises(ValueError, match="paused"):
            coordinator.resume_experiment(run_id)


def test_restart_pauses_run_and_resume_finishes_it(tmp_path):
    spec = project(tmp_path, SLOW)
    path = write_experiment(spec.root, configurations={"grid": {"delay": [1.0]}})
    with m.Coordinator(spec, slots=SlotConfig(local=2)) as coordinator:
        run_id = coordinator.start_experiment(load_experiment(path))

        def mid_run():
            statuses = [row["status"] for row in attempts(coordinator.store)]
            return "succeeded" in statuses and "running" in statuses

        until(mid_run, timeout=30)
    with m.Coordinator(spec, slots=SlotConfig(local=2)) as coordinator:
        assert coordinator.store.experiment_run(run_id)["status"] == "paused"
        interrupted = len(attempts(coordinator.store))
        time.sleep(0.3)
        assert len(attempts(coordinator.store)) == interrupted  # no user code runs on open
        assert {item["status"] for item in coordinator.store.replications(run_id)} == {"pending", "succeeded"}
        assert coordinator.wait_experiment(run_id)["status"] == "paused"
        coordinator.resume_experiment(run_id)
        report = coordinator.wait_experiment(run_id, timeout=60)
        assert report["status"] == "completed" and report["counts"]["succeeded"] == 3
        # The replication interrupted by the shutdown ran again; its first attempt is lost, not failed,
        # and the orderly close did not spend a dispatch of the D97 worker-loss budget.
        assert max(item["dispatches"] for item in coordinator.store.replications(run_id)) == 1
        assert "lost" in {row["status"] for row in attempts(coordinator.store)}


def test_orderly_close_returns_in_flight_replication_to_pending_without_spending_a_dispatch(tmp_path, monkeypatch):
    spec = project(tmp_path, SLOW)
    path = write_experiment(spec.root, replications={"count": 1}, configurations={"grid": {"n": [1]}})

    def in_flight(coordinator, run_id, dispatches=1):
        until(lambda: [row["status"] for row in attempts(coordinator.store)][-1:] == ["running"])
        (replication,) = coordinator.store.replications(run_id)
        assert (replication["status"], replication["dispatches"]) == ("dispatched", dispatches)

    def paused(run_id, dispatches):
        with m.RunStore.open(spec.root / ".simulatte") as store:
            assert store.experiment_run(run_id)["status"] == "paused"
            (replication,) = store.replications(run_id)
            assert (replication["status"], replication["dispatches"]) == ("pending", dispatches)

    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        run_id = coordinator.start_experiment(load_experiment(path))
        in_flight(coordinator, run_id)
    paused(run_id, 0)
    for _ in range(2):
        with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
            coordinator.resume_experiment(run_id)
            in_flight(coordinator, run_id)
        paused(run_id, 0)
    # Three open/close cycles: every attempt ended lost (attempt bookkeeping), no dispatch was spent.
    with m.RunStore.open(spec.root / ".simulatte") as store:
        assert [row["status"] for row in attempts(store)] == ["lost", "lost", "lost"]
    # A transport EOF still counts: the first dispatch is lost, the second one is in flight.
    real = m.LocalTransport.start
    peers = []

    def start(*args, **kwargs):
        if peers:
            return real(*args, **kwargs)
        peers.append(Peer())
        return peers[0]

    monkeypatch.setattr(m.LocalTransport, "start", start)
    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        coordinator.resume_experiment(run_id)
        in_flight(coordinator, run_id, dispatches=2)
    paused(run_id, 1)
    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        coordinator.resume_experiment(run_id)
        in_flight(coordinator, run_id, dispatches=2)
        coordinator.cancel_experiment(run_id)
        assert coordinator.wait_experiment(run_id, timeout=10)["status"] == "cancelled"


def idle(coordinator, monkeypatch):
    """Keep the slot actors from dispatching; return the real `Scheduler.next` for direct calls."""
    from simulatte.execution.scheduler import Scheduler

    monkeypatch.setattr(coordinator._scheduler, "next", lambda actor: None)
    return lambda actor: Scheduler.next(coordinator._scheduler, actor)


def test_dispatch_skips_stale_mismatched_and_unprepared_replications(tmp_path, monkeypatch):
    spec = project(tmp_path)
    path = write_experiment(spec.root, replications={"count": 3}, configurations={"grid": {"n": [3]}})
    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        dispatch = idle(coordinator, monkeypatch)
        run_id = coordinator.start_experiment(load_experiment(path))
        with pytest.raises(TimeoutError, match="experiment"):
            coordinator.wait_experiment(run_id, timeout=0.05)
        queue = coordinator._scheduler._runs[run_id].queue
        # A slot of another worker kind gets nothing, and the queue is unchanged.
        assert dispatch(SimpleNamespace(slot="strangelove-1", kind="uv", host="strangelove")) is None
        assert sorted(queue) == [(0, 0), (1, 0), (2, 0)]
        actor = coordinator._actors["local-1"]
        job = dispatch(actor)
        assert job.experiment == run_id and coordinator.store.attempt(job.key)["slot"] == "local-1"
        # A replication changed behind the scheduler's back is skipped; a missing preparation fails it.
        coordinator.store.set_replication_status(run_id, 0, 1, "cancelled")
        with coordinator.store._transaction() as db:
            db.execute("DELETE FROM snapshots WHERE kind='preparation'")
        assert dispatch(SimpleNamespace(slot="local-2", kind="local", host=None)) is None
        states = {
            item["index"]: (item["status"], item["dispatches"]) for item in coordinator.store.replications(run_id)
        }
        assert states == {0: ("dispatched", 1), 1: ("cancelled", 0), 2: ("failed", 1)}
        # Cancelling a run whose dispatched attempt already ended leaves it to the attempt's outcome.
        coordinator.store.revoke(job.key, "failed")
        coordinator.cancel_experiment(run_id)
        assert coordinator.store.replications(run_id)[0]["status"] == "dispatched"
        coordinator._scheduler.on_terminal(job.key, "failed")
        report = coordinator.wait_experiment(run_id)
        assert report["status"] == "cancelled" and report["counts"]["failed"] == 2
        assert coordinator.delete_experiment(run_id) == {"results": []}
        with pytest.raises(KeyError):
            coordinator.wait_experiment(run_id)


def test_scheduler_failures_close_admission(tmp_path, monkeypatch):
    spec = project(tmp_path)
    path = write_experiment(spec.root, replications={"count": 1}, configurations={"grid": {"n": [3]}})
    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        coordinator.inputs.bind(coordinator.prepare(timeout=10))
        with monkeypatch.context() as patch:
            idle(coordinator, patch)
            run_id = coordinator.start_experiment(load_experiment(path))
            # The replication is bound to another request: binding refuses and the new attempt is revoked.
            with coordinator.store._transaction() as db:
                db.execute("UPDATE replications SET request_id=(SELECT id FROM requests LIMIT 1)")
        until(lambda: coordinator._fatal is not None)
        (attempt,) = attempts(coordinator.store)
        assert attempt["status"] == "cancelled"
        with pytest.raises(m.ExecutionFailure, match="another request"):
            coordinator.wait_experiment(run_id)
        with pytest.raises(RuntimeError, match="storage failure"):
            coordinator.start_experiment(load_experiment(path))
        with pytest.raises(RuntimeError, match="storage failure"):
            coordinator.resume_experiment(run_id)


def test_failed_status_update_closes_admission(tmp_path, monkeypatch):
    spec = project(tmp_path)
    path = write_experiment(spec.root, replications={"count": 1}, configurations={"grid": {"n": [3]}})
    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        coordinator.inputs.bind(coordinator.prepare(timeout=10))
        monkeypatch.setattr(m.LocalTransport, "start", lambda *args, **kwargs: Peer())

        def broken(key, status):
            raise ValueError("status update failed")

        monkeypatch.setattr(coordinator._scheduler, "on_terminal", broken)
        coordinator.start_experiment(load_experiment(path))
        until(lambda: coordinator._fatal is not None)
        assert coordinator._fatal.details == {"message": "status update failed"}

        def unwritable():
            raise m.sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(coordinator._scheduler, "pause", unwritable)
    assert all(not actor.thread.is_alive() for actor in coordinator._actors.values())


def test_resume_refuses_other_runtimes_and_tampered_runs(tmp_path, monkeypatch):
    spec = project(tmp_path)
    path = write_experiment(spec.root, replications={"count": 1}, configurations={"grid": {"n": [3]}})
    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        idle(coordinator, monkeypatch)
        run_id = coordinator.start_experiment(load_experiment(path))
        runtime = coordinator.runtime
    other = SimpleNamespace(root=runtime.root, content_hash="0" * 64)
    with m.Coordinator(spec, slots=SlotConfig(local=1), _runtime=other) as coordinator:
        with pytest.raises(ValueError, match="differs from this installation"):
            coordinator.resume_experiment(run_id)
        with coordinator.store._transaction() as db:
            db.execute("UPDATE configurations SET parameters=? WHERE run=?", (_blob({"n": 4}), run_id))
    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        with pytest.raises(ValueError, match="does not reproduce"):
            coordinator.resume_experiment(run_id)
        assert coordinator.store.experiment_run(run_id)["status"] == "paused"


def test_experiment_and_slot_arguments_are_validated(tmp_path):
    from simulatte.execution.schema import InspectionRequest
    from simulatte.experiments.runner import project_root

    spec = project(tmp_path)
    path = write_experiment(spec.root)
    assert project_root(path, None) == spec.root.resolve()
    assert project_root(None, spec.root) == spec.root.resolve()
    for slots in (-1, True, 65):
        with pytest.raises(ValueError, match="Local slots"):
            SlotConfig(local=slots)
    with pytest.raises(ValueError, match="SlotConfig"):
        m.Coordinator(spec, slots=2)  # ty: ignore[invalid-argument-type]
    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        with pytest.raises(ValueError, match="Experiment"):
            coordinator.start_experiment(path)
        with pytest.raises(ValueError, match="list"):
            coordinator.start_experiment(load_experiment(path), workers="local")
        prepared = coordinator.prepare(timeout=10)
        request = coordinator.make_request(prepared, parameters={"n": 3})
        with pytest.raises(ValueError, match="slot"):
            coordinator.submit(request, prepared.revision, slot="local-9")
        inspection = InspectionRequest(model="plant.py", source_hash=request.source_hash, runtime=request.runtime)
        with pytest.raises(ValueError, match="slot"):
            coordinator._schedule(inspection, prepared.revision, None, {}, None, slot="local-1")
        # An idle experiment slot runs one request at its pinned revision, without a presentation view.
        key = coordinator.submit(request, prepared.revision, timeout=20, slot="local-1")
        with pytest.raises(RuntimeError, match="busy"):
            coordinator.submit(request, prepared.revision, slot="local-1")
        result = coordinator.wait(key, timeout=20)
        assert (result.path / "artifacts/answer.txt").read_text() == "8"
        assert coordinator.store.attempt(key)["slot"] == "local-1"
        view = coordinator.store.presentation_view()
        assert view is None or view.key != key


def test_layout_variants_are_loaded_documented_and_pinned(tmp_path):
    from simulatte.execution.snapshot import discover_project
    from simulatte.layout.identity import layout_hash

    spec = project(tmp_path)
    (spec.root / "pyproject.toml").write_text(
        '[tool.simulatte.bundle]\ninclude = ["plant.py"]\nlayouts = ["layouts/wide.json", "layouts/gone.json"]\n'
    )
    (spec.root / "layouts").mkdir()
    wide = {"schema_version": "1.0", "grid": {"dx": 3.0}}
    (spec.root / "layouts/wide.json").write_text(json.dumps(wide))
    spec = discover_project(spec.root / "plant.py", spec.root)
    variants = [{"label": "base", "file": "layout.json"}, {"label": "wide", "file": "layouts/wide.json"}]
    path = write_experiment(spec.root, replications={"count": 1}, configurations={"grid": {"n": [3]}}, layouts=variants)
    with m.Coordinator(spec, slots=SlotConfig(local=2)) as coordinator:
        run_id = coordinator.start_experiment(load_experiment(path))
        report = coordinator.wait_experiment(run_id, timeout=60)
        assert report["status"] == "completed"
        stored = {item["label"]: item for item in coordinator.store.experiment_run(run_id)["configurations"]}
        assert set(stored) == {"n=3/base", "n=3/wide"}
        assert stored["n=3/wide"]["layout_hash"] == layout_hash("file", wide)
        assert stored["n=3/base"]["layout_hash"] != stored["n=3/wide"]["layout_hash"]
        for item in coordinator.store.replications(run_id):
            manifest = coordinator.store.result(item["attempt"]).manifest
            label = report["configurations"][item["configuration"]]["label"]
            assert manifest["request"]["layout_hash"] == stored[label]["layout_hash"]
        gone = write_experiment(
            spec.root, "gone", layouts=[{"label": "gone", "file": "layouts/gone.json"}], replications={"count": 1}
        )
        with pytest.raises(ValueError, match="does not exist"):
            coordinator.start_experiment(load_experiment(gone))


def test_progress_coalesces_per_run_and_storage_failures_are_not_durable(tmp_path):
    from simulatte.execution.snapshot import ProjectSpec

    spec = ProjectSpec(tmp_path, "plant.py", "layout.json", (), (), (".",), False)
    with m.Coordinator(spec, slots=SlotConfig(local=0)) as coordinator:
        for run_id, count in (("a", 1), ("b", 1), ("a", 2)):
            coordinator._emit("experiment_progress", None, {"run_id": run_id, "counts": count})
        coordinator._emit("queued", None, {})
        events = coordinator.events()
        assert [(event["body"].get("run_id"), event["body"].get("counts")) for event in events] == [
            ("b", 1),
            ("a", 2),
            (None, None),
        ]
        coordinator._fail_admission(m.sqlite3.OperationalError("database or disk is full"))
        assert coordinator._fatal.status == "disk_full"
        assert coordinator._fatal.details["index_terminal_state"] == "not_durable"
        with pytest.raises(ValueError, match="No local experiment slots"):
            coordinator.start_experiment(load_experiment(write_experiment(tmp_path)))
