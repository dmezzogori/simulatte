"""Studio experiment, comparison, replay and worker protocol over the real coordinator (design 7)."""

import asyncio
import json
import threading
import time

import pytest

pytest.importorskip("starlette")
from starlette.testclient import TestClient

from simulatte.execution.coordinator import Coordinator
from simulatte.execution.slots import SlotConfig
from simulatte.execution.snapshot import discover_project
from simulatte.experiments import Experiment, load_experiment
from simulatte.studio import experiments as studio_experiments
from simulatte.studio import app as studio_app
from simulatte.studio.app import StudioServer
from simulatte.studio.watcher import ProjectWatcher
from simulatte.studio.websocket import ConnectionHub, Peer
from tests.core.test_studio_live import auth, wait_state
from tests.execution.test_coordinator import MODEL

# MODEL plus a `cell/count` KPI (scalar and series, two samples per run) and a `delay` parameter.
KPI_MODEL = (
    MODEL.replace(
        "from simulatte.events import DomainEvent, Deltas, event_type\n",
        "from simulatte.events import DomainEvent, Deltas, event_type\nfrom simulatte.kpi import KPI, Collector\n",
    )
    .replace("    n: int = 2\n", "    n: int = 2\n    delay: float = 0.0\n")
    .replace(
        "def build(env, layout, params):\n    cell = Cell(env)\n",
        "def build(env, layout, params):\n    cell = Cell(env)\n    Count(cell).attach(env)\n",
    )
    .replace(
        "@dataclass(frozen=True)\nclass Params:",
        "class Count(Collector):\n"
        "    kpis = (KPI('count', unit='x', kind=('series', 'scalar')),)\n"
        "    subscribes = (Changed,)\n"
        "    def on_event(self, event):\n"
        "        self.observe('count', float(event.count))\n"
        "        self.sample('count', float(event.count))\n"
        "@dataclass(frozen=True)\nclass Params:",
    )
    .replace("        cell.count", "        __import__('time').sleep(params.delay)\n        cell.count")
    .replace(
        "        (env.artifacts_dir",
        "        env.emit(Changed(cell=cell.id, count=value + 1,"
        ' deltas=Deltas.build().set(cell.id,"count",value + 1).done()))\n'
        "        (env.artifacts_dir",
    )
)


def definition(name="sweep", **changes):
    return {
        "schema": 1,
        "name": name,
        "model": "plant.py:build",
        "replications": {"count": 2},
        "configurations": {"grid": {"n": [3, 4]}},
        **changes,
    }


def setup(tmp_path, monkeypatch, *, watch=False, local=1, model=KPI_MODEL):
    monkeypatch.setenv("SIMULATTE_WORKERS", str(tmp_path / "missing-workers.toml"))
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[tool.simulatte.bundle]\ninclude = ["plant.py", "*.experiment.json"]\nlayouts = ["variant.json"]\n'
    )
    (root / "plant.py").write_text(model)
    (root / "input.txt").write_text("5")
    spec = discover_project(root / "plant.py", root)
    coordinator = Coordinator(spec, slots=SlotConfig(local=local))
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("Studio shell")
    (static / "manifest.json").write_text(json.dumps({"assets": {"index.html": "unused"}}))
    watcher = ProjectWatcher(spec, interval=0.2, force_polling=True) if watch else None
    return coordinator, StudioServer(coordinator, 8123, static, timeout=20, watcher=watcher)


class Socket:
    """An authenticated control socket that keeps every notification it has read."""

    def __init__(self, server, client):
        self.server = server
        self.context = client.websocket_connect(
            server.origin.replace("http:", "ws:") + "/ws", headers={"Origin": server.origin}
        )
        self.socket = self.context.__enter__()
        self.socket.send_json({"v": 1, "type": "auth", "secret": server.secret})
        self.notifications = [self.socket.receive_json()]
        self.sequence = 0

    def close(self):
        self.context.__exit__(None, None, None)

    def send(self, kind, body, **envelope):
        self.sequence += 1
        identity = f"c{self.sequence}"
        self.socket.send_json({"v": 1, "type": kind, "id": identity, "body": body, **envelope})
        while True:
            message = self.socket.receive_json()
            if message.get("type") == "response" and message["id"] == identity:
                return message["body"]
            self.notifications.append(message)

    def until(self, predicate, *, timeout=30):
        """The first notification (already read or not) satisfying ``predicate``."""
        for message in self.notifications:
            if predicate(message):
                return message
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self.socket.receive_json()
            self.notifications.append(message)
            if predicate(message):
                return message
        pytest.fail("notification did not arrive")


def experiment_state(run_id, status):
    return lambda message: (
        message["type"] == "experiment_state"
        and message["body"]
        == {
            **message["body"],
            "run_id": run_id,
            "status": status,
        }
    )


def test_define_start_progress_compare_results_and_lifecycle(tmp_path, monkeypatch):
    coordinator, server = setup(tmp_path, monkeypatch)
    root = coordinator.project.root
    try:
        with TestClient(server.app, base_url=server.origin) as client:
            headers = auth(server, client)
            state = wait_state(client, headers, lambda value: bool(value["results"]))
            assert state["experiments"] == [] and state["replays"] == []
            assert [slot["name"] for slot in state["workers"]["slots"]] == ["local-1"]
            socket = Socket(server, client)
            try:
                # Experiment commands fence on store state, not on the project revision.
                defined = socket.send(
                    "experiment.define", {"path": "sweep.experiment.json", "definition": definition()}
                )
                assert defined["ok"] and defined["path"] == "sweep.experiment.json"
                assert load_experiment(root / "sweep.experiment.json") == Experiment.from_dict(definition())
                # The experiment file picker lists non-source experiment files; the model path prefills forms.
                listed_files = socket.send("state.get", {}, revision=None)["state"]
                assert listed_files["experiment_files"] == ["sweep.experiment.json"]
                assert listed_files["model"] == "plant.py"
                again = socket.send("experiment.define", {"path": "sweep.experiment.json", "definition": definition()})
                assert again["error"]["code"] == "validation_failed" and "exists" in again["error"]["message"]
                replaced = socket.send(
                    "experiment.define",
                    {"path": "sweep.experiment.json", "definition": definition(), "overwrite": True},
                    revision={"number": 999, "digest": "a" * 64},
                )
                assert replaced["ok"]
                started = socket.send("experiment.start", {"path": "sweep.experiment.json"})
                assert started["ok"]
                run_id = started["run_id"]
                socket.until(experiment_state(run_id, "running"))
                done = socket.until(experiment_state(run_id, "completed"), timeout=40)
                assert done["body"]["name"] == "sweep" and len(done["body"]["experiment_id"]) == 64
                progress = [item for item in socket.notifications if item["type"] == "experiment_progress"]
                assert progress and {item["body"]["run_id"] for item in progress} == {run_id}
                # Replication attempts never surface as the interactive run's state or progress.
                replications = {item["attempt"] for item in coordinator.store.replications(run_id)}
                assert not any(
                    item.get("attempt_id") in replications
                    for item in socket.notifications
                    if item["type"] in ("run_state", "progress")
                )

                compared = socket.send("comparison.get", {"run_id": run_id, "baseline": "n=3/base"})
                assert compared["ok"]
                report = compared["report"]
                assert report["pointwise"] is True and report["run_id"] == run_id
                row = report["rows"][0]
                assert row["kpi"] == "cell/count" and row["estimate"]["mean"] == 8.5
                assert report["paired"][0]["estimate"]["mean"] == 1.0
                # Every replication is listed with its committed trace, which the prefix endpoint serves.
                assert len(report["replications"]) == report["replications_total"] == 4
                requests = {item["attempt"]: item["request_id"] for item in coordinator.store.replications(run_id)}
                for item in report["replications"]:
                    assert item["status"] == "succeeded" and item["trace"]["attempt"] == item["attempt"]
                    assert item["request_id"] == requests[item["attempt"]]
                    assert item["trace"]["url"] == f"/api/prefix/{item['attempt']}/{item['trace']['generation']}"
                one = socket.send("comparison.get", {"run_id": run_id, "configuration": 1})["report"]
                assert {item["configuration"] for item in one["replications"]} == {1}
                assert one["replications_total"] == 2
                assert client.get(f"/api/experiments/{run_id}/results?configuration=1", headers=headers).json() == one
                for value in ("x", "-1", "01", "5"):
                    query = f"/api/experiments/{run_id}/results?configuration={value}"
                    assert client.get(query, headers=headers).status_code == 400
                for value in (True, "1", 5):
                    rejected = socket.send("comparison.get", {"run_id": run_id, "configuration": value})
                    assert rejected["error"]["code"] == "validation_failed"
                trace = report["replications"][0]["trace"]
                served = client.get(trace["url"], headers=headers)
                assert served.status_code == 200 and len(served.content) == trace["length"]
                adjusted = socket.send("comparison.get", {"run_id": run_id, "bonferroni": True, "level": 0.9})
                # The same definition on unchanged inputs is the same run (D103), restartable by id.
                again = socket.send("experiment.start", {"run_id": run_id})
                assert again["ok"] and again["run_id"] == run_id and again["status"] == "completed"
                assert socket.send("experiment.start", {"path": "sweep.experiment.json", "workers": "local"})["error"][
                    "code"
                ] == ("validation_failed")
                assert socket.send("experiment.start", {"path": "sweep.experiment.json", "run_id": run_id})["error"][
                    "code"
                ] == ("validation_failed")
                assert adjusted["report"]["pointwise"] is False
                assert socket.send("comparison.get", {"run_id": "f" * 64})["error"]["code"] == "validation_failed"
                assert socket.send("comparison.get", {"run_id": run_id, "level": True})["error"]["code"] == (
                    "validation_failed"
                )

                url = f"/api/experiments/{run_id}/results"
                assert client.get(url).status_code == 403
                fetched = client.get(url + "?baseline=n%3D3%2Fbase", headers=headers)
                assert fetched.status_code == 200 and fetched.json() == report
                bonferroni = client.get(url + "?level=0.9&bonferroni=true", headers=headers).json()
                assert bonferroni == adjusted["report"]
                assert client.get(f"/api/experiments/{'f' * 64}/results", headers=headers).status_code == 404
                assert client.get("/api/experiments/nope/results", headers=headers).status_code == 404
                assert client.get(url + "?level=2", headers=headers).status_code == 400
                assert client.get(url + "?other=1", headers=headers).status_code == 400
                for query in ("?level=abc", "?level=nan", "?bonferroni=yes", "?level=0.9&level=0.8"):
                    assert client.get(url + query, headers=headers).status_code == 400
                for body in ({"baseline": 3}, {"bonferroni": "yes"}, {"baseline": "unknown"}):
                    rejected = socket.send("comparison.get", {"run_id": run_id, **body})
                    assert rejected["error"]["code"] == "validation_failed"

                listed = wait_state(client, headers, lambda value: bool(value["experiments"]))["experiments"]
                assert listed[0]["run_id"] == run_id and listed[0]["counts"]["succeeded"] == 4

                # A slow run: pause it as a restart would, resume it, cancel it, delete it.
                slow = definition(
                    "slow", replications={"count": 3}, configurations={"explicit": [{"parameters": {"delay": 1.0}}]}
                )
                assert socket.send("experiment.define", {"path": "slow.experiment.json", "definition": slow})["ok"]
                slow_id = socket.send("experiment.start", {"path": "slow.experiment.json", "workers": ["local"]})[
                    "run_id"
                ]
                assert socket.send("experiment.resume", {"run_id": slow_id})["error"]["code"] == "validation_failed"
                assert socket.send("experiment.delete", {"run_id": slow_id})["error"]["code"] == "validation_failed"
                with coordinator._condition:
                    coordinator._scheduler.pause()
                resumed = socket.send("experiment.resume", {"run_id": slow_id})
                assert resumed["ok"] and resumed["status"] == "running"
                cancelled = socket.send("experiment.cancel", {"run_id": slow_id})
                assert cancelled["ok"] and cancelled["status"] == "cancelled"
                socket.until(experiment_state(slow_id, "cancelled"))
                assert socket.send("experiment.cancel", {"run_id": slow_id})["error"]["code"] == "validation_failed"
                deleted = socket.send("experiment.delete", {"run_id": slow_id})
                assert deleted["ok"]
                socket.until(experiment_state(slow_id, "deleted"))
                assert socket.send("experiment.delete", {"run_id": slow_id})["error"]["code"] == "validation_failed"
                assert [
                    run["run_id"] for run in socket.send("state.get", {}, revision=None)["state"]["experiments"]
                ] == [run_id]
            finally:
                socket.close()
    finally:
        coordinator.close()


def test_define_rejections_and_external_edits_notify(tmp_path, monkeypatch):
    coordinator, server = setup(tmp_path, monkeypatch, watch=True)
    root = coordinator.project.root
    (tmp_path / "outside").mkdir()
    (root / "linked").symlink_to(tmp_path / "outside")
    try:
        with TestClient(server.app, base_url=server.origin) as client:
            wait_state(client, auth(server, client), lambda value: bool(value["results"]))
            socket = Socket(server, client)
            try:
                for path in (
                    "../escape.experiment.json",
                    str(root / "absolute.experiment.json"),
                    "plain.json",
                    "linked/inside.experiment.json",
                    "missing/inside.experiment.json",
                    ".simulatte/store.experiment.json",
                    "./sweep.experiment.json",
                    "plant.py/inside.experiment.json",
                    7,
                ):
                    rejected = socket.send("experiment.define", {"path": path, "definition": definition()})
                    assert rejected["error"]["code"] == "validation_failed", path
                for invalid in (
                    definition(schema=2),
                    definition(configurations={"grid": {"unknown": [1]}}),
                    definition(model="other.py:build"),
                    definition(layouts=[{"label": "v", "file": "unlisted.json"}]),
                    definition(layouts=[{"label": "v", "file": "variant.json"}]),
                    [],
                ):
                    rejected = socket.send("experiment.define", {"path": "bad.experiment.json", "definition": invalid})
                    assert rejected["error"]["code"] == "validation_failed"
                assert socket.send("experiment.define", {"definition": definition()})["error"]["code"] == (
                    "validation_failed"
                )
                assert not list(tmp_path.rglob("*.experiment.json"))
                (root / "folder.experiment.json").mkdir()
                rejected = socket.send(
                    "experiment.define",
                    {"path": "folder.experiment.json", "definition": definition(), "overwrite": True},
                )
                assert rejected["error"]["code"] == "validation_failed"
                assert socket.send(
                    "experiment.define", {"path": "x.experiment.json", "definition": definition(), "overwrite": 1}
                )["error"]["code"] == ("validation_failed")
                # A listed layout variant validates once its file exists.
                (root / "variant.json").write_text(
                    json.dumps({"schema_version": "1.0", "placements": {"cell": {"kind": "server", "visual": {}}}})
                )
                variants = definition(
                    layouts=[{"label": "base", "file": "layout.json"}, {"label": "v", "file": "variant.json"}]
                )
                written = socket.send("experiment.define", {"path": "variants.experiment.json", "definition": variants})
                assert written.get("ok"), written
                assert len(load_experiment(root / "variants.experiment.json").layouts) == 2
                (root / "variants.experiment.json").unlink()
                assert not (tmp_path / "outside" / "inside.experiment.json").exists()
                assert socket.send("experiment.start", {"path": "absent.experiment.json"})["error"]["code"] == (
                    "validation_failed"
                )
                assert socket.send("experiment.start", {})["error"]["code"] == "validation_failed"

                # An external edit of a watched experiment file is announced and never starts a run.
                (root / "edited.experiment.json").write_text(json.dumps(definition("edited")))
                changed = socket.until(
                    lambda message: (
                        message["type"] == "experiments_changed"
                        and "edited.experiment.json" in message["body"]["paths"]
                    )
                )
                # Only experiment files and layout variants are reported (here also earlier, batched edits).
                assert all(
                    name.endswith(".experiment.json") or name == "variant.json" for name in changed["body"]["paths"]
                )
                assert coordinator.store.slots().get("local-1") is None
                assert server.snapshot()["experiments"] == []
            finally:
                socket.close()
    finally:
        coordinator.close()


def test_unknown_worker_replay_series_and_interactive_filtering(tmp_path, monkeypatch):
    coordinator, server = setup(tmp_path, monkeypatch)
    root = coordinator.project.root
    (root / "one.experiment.json").write_text(
        json.dumps(definition("one", replications={"count": 1}, configurations={"grid": {"n": [3]}}))
    )
    try:
        with TestClient(server.app, base_url=server.origin) as client:
            headers = auth(server, client)
            state = wait_state(client, headers, lambda value: bool(value["results"]))
            interactive = state["results"][0]["key"]
            socket = Socket(server, client)
            try:
                unknown = socket.send("experiment.start", {"path": "one.experiment.json", "workers": ["nowhere"]})
                assert unknown["error"]["code"] == "validation_failed" and "nowhere" in unknown["error"]["message"]
                assert not coordinator.store.slots().get("nowhere-1")
                started = socket.send("experiment.start", {"path": "one.experiment.json", "level": "full"})
                run_id = started["run_id"]
                socket.until(experiment_state(run_id, "completed"), timeout=30)
                (replication,) = coordinator.store.replications(run_id)

                # The interactive view keeps the interactive run; the replication stays openable.
                current = client.get("/api/state", headers=headers).json()
                assert [item["key"] for item in current["results"]] == [interactive]
                assert current["latest_attempt"]["key"] == interactive
                trace = coordinator.store.result(replication["attempt"])
                with coordinator.store._transaction() as db:
                    url = server._prefix(db, replication["attempt"])["url"]
                assert client.get(url, headers=headers).content.startswith(b"SIMTRACE")
                assert trace.manifest["request"]["level"] == "full"

                workers = socket.send("workers.get", {})
                assert workers["ok"] and workers["workers"]["local"] == 1
                assert workers["workers"]["workers"] == [] and workers["workers"]["error"] is None
                (slot,) = workers["workers"]["slots"]
                assert slot["name"] == "local-1" and slot["kind"] == "local" and slot["last_status"] == "succeeded"

                replay = socket.send("replay.start", {"request_id": replication["request_id"]})
                assert replay["ok"] and replay["original"] == replication["request_id"]
                verified = socket.until(
                    lambda message: message["type"] == "replay_state" and message["body"]["status"] == "succeeded"
                )
                assert verified["body"]["verified"] is True
                assert verified["body"]["request_id"] == replay["request_id"]
                for bad in (
                    {"request_id": "e" * 64},
                    {"request_id": 1},
                    {"request_id": replay["request_id"], "worker": 2},
                ):
                    assert socket.send("replay.start", bad)["error"]["code"] == "validation_failed"
                # A failure before scheduling arrives as the replay's state, like a failed replay attempt.
                elsewhere = socket.send("replay.start", {"request_id": replay["request_id"], "worker": "nowhere"})
                assert elsewhere["request_id"] == replay["request_id"]
                assert elsewhere["original"] == replication["request_id"]
                failed = socket.until(
                    lambda message: (
                        message["type"] == "replay_state" and message["body"]["status"] == "no_compatible_worker"
                    )
                )
                assert failed["body"]["request_id"] == replay["request_id"] and failed["body"]["verified"] is None
                assert socket.until(lambda message: message["type"] == "workers_state")["body"]["slots"]
                replays = wait_state(client, headers, lambda value: bool(value["replays"]))["replays"]
                assert replays[0]["request_id"] == replay["request_id"] and replays[0]["verified"] is True
                assert replays[0]["trace"]["url"].startswith("/api/prefix/")
                current = client.get("/api/state", headers=headers).json()
                assert [item["key"] for item in current["results"]] == [interactive]

                series = socket.send("series.get", {"attempts": [replication["attempt"]], "keys": ["cell/count"]})
                ((only, points),) = [(item["attempt"], item["series"]) for item in series["series"]]
                assert only == replication["attempt"]
                assert points == {"cell/count": {"points": [[1.0, 2, 8.0], [1.0, 4, 9.0]], "truncated": False}}
                missing = socket.send("series.get", {"attempts": [replication["attempt"]], "keys": ["cell/none"]})
                assert missing["series"][0]["series"] == {"cell/none": {"points": [], "truncated": False}}
                monkeypatch.setattr(studio_experiments, "MAX_SERIES_POINTS", 1)
                truncated = socket.send(
                    "series.get", {"attempts": [replication["attempt"]], "keys": ["cell/count"], "x": 1}
                )
                assert truncated["error"]["code"] == "validation_failed"
                truncated = socket.send("series.get", {"attempts": [replication["attempt"]], "keys": ["cell/count"]})
                assert truncated["series"][0]["series"]["cell/count"] == {"points": [[1.0, 2, 8.0]], "truncated": True}
                nine = socket.send("series.get", {"attempts": [replication["attempt"]] * 9, "keys": ["cell/count"]})
                assert nine["error"]["code"] == "validation_failed"
                for body in (
                    {"attempts": ["0" * 32], "keys": ["cell/count"]},
                    {"attempts": [], "keys": ["cell/count"]},
                    {"attempts": [replication["attempt"]], "keys": []},
                    {"attempts": [replication["attempt"]], "keys": [3]},
                    {"attempts": [replication["attempt"]], "keys": ["cell/count"] * 5},
                ):
                    assert socket.send("series.get", body)["error"]["code"] == "validation_failed"
            finally:
                socket.close()
    finally:
        coordinator.close()


def test_duplicate_replay_start_returns_the_running_replay(tmp_path, monkeypatch):
    # Two idle slots: without the guard a second start would schedule a second attempt. The
    # one-second run keeps the replay's attempt live while the repeated starts arrive.
    slow = KPI_MODEL.replace("delay: float = 0.0", "delay: float = 1.0")
    coordinator, server = setup(tmp_path, monkeypatch, local=2, model=slow)
    try:
        with TestClient(server.app, base_url=server.origin) as client:
            headers = auth(server, client)
            original = wait_state(client, headers, lambda value: bool(value["results"]))["results"][0]["key"]
            release = threading.Event()
            wait = coordinator.wait

            def held(key, **options):
                release.wait(20)
                return wait(key, **options)

            monkeypatch.setattr(coordinator, "wait", held)
            body = {"request_id": original["request_id"]}

            def attempts():
                with coordinator.store._transaction() as db:
                    query = "SELECT COUNT(*) FROM attempts WHERE request_id=?"
                    return db.execute(query, (original["request_id"],)).fetchone()[0]

            # The interactive original is already full, so its replay shares its request id.
            before = attempts()
            first = server.experiments("replay.start", body)
            replay = first["request_id"]
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    if coordinator.store.replay(replay)["status"] == "running":
                        break
                except KeyError:
                    pass
                time.sleep(0.02)
            assert coordinator.store.replay(replay)["status"] == "running"
            second = server.experiments("replay.start", body)
            # Another command owner has no in-flight entry; the running replay row still guards it.
            third = studio_experiments.ExperimentCommands(coordinator)("replay.start", {"request_id": replay})
            assert first == second == third == {"ok": True, "request_id": replay, "original": original["request_id"]}
            release.set()
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and (
                coordinator.store.replay(replay)["status"] == "running" or server.experiments._replaying
            ):
                time.sleep(0.02)
            assert coordinator.store.replay(replay)["status"] == "succeeded"
            assert not server.experiments._replaying
            with coordinator.store._transaction() as db:
                rows = db.execute("SELECT request_id FROM replays").fetchall()
            assert [row[0] for row in rows] == [replay] and attempts() == before + 1

            class Unstartable:
                def __init__(self, **options):
                    pass

                def start(self):
                    raise RuntimeError("can't start new thread")

            # A replay that never started leaves no in-flight entry behind.
            monkeypatch.setattr(studio_experiments.threading, "Thread", Unstartable)
            with pytest.raises(RuntimeError, match="start new thread"):
                server.experiments("replay.start", body)
            assert not server.experiments._replaying
    finally:
        coordinator.close()


def test_state_lists_newest_twenty_runs(tmp_path, monkeypatch):
    from tests.execution.test_store_slots import DEFINITION, configurations, replications

    coordinator, server = setup(tmp_path, monkeypatch)
    try:
        store = coordinator.store
        for index in range(21):
            identity = store.put_experiment({**DEFINITION, "name": f"run-{index}"})
            store.create_experiment_run(identity, {}, configurations(), replications())
        listed = server.snapshot()["experiments"]
        assert len(listed) == 20 and len({item["run_id"] for item in listed}) == 20
        assert {"run_id", "name", "status", "counts", "created", "experiment_id"} <= set(listed[0])
        assert server.snapshot()["workers"]["slots"][0]["state"] == "idle"

        # An unreadable membership (here a Git conflict) lists no experiment files; the watcher reports it.
        def conflict(project):
            raise ValueError("Resolve Git merge conflicts before capture")

        monkeypatch.setattr(studio_app, "non_source_members", conflict)
        assert server.snapshot()["experiment_files"] == []
        with pytest.raises(ValueError, match="Wait for model metadata"):
            server.experiments("experiment.define", {"path": "a.experiment.json", "definition": definition()})
    finally:
        coordinator.close()


def test_experiment_notifications_coalesce_per_run_and_workers_state():
    async def run():
        hub = ConnectionHub(None, None, None)
        peer = Peer(None)
        hub.connections.add(peer)
        for run_id, done in (("a", 1), ("b", 1), ("a", 2), ("a", 3)):
            hub.notify("experiment_progress", {"run_id": run_id, "counts": {"succeeded": done}})
        hub.notify("workers_state", {"slots": [1]})
        hub.notify("workers_state", {"slots": [2]})
        hub.notify("experiment_state", {"run_id": "a", "status": "running"})
        hub.notify("experiment_state", {"run_id": "a", "status": "completed"})
        messages = [json.loads(await peer.outbox.get()) for _ in range(peer.outbox.count)]
        return [(item["type"], item["body"]) for item in messages]

    assert asyncio.run(run()) == [
        ("experiment_progress", {"run_id": "b", "counts": {"succeeded": 1}}),
        ("experiment_progress", {"run_id": "a", "counts": {"succeeded": 3}}),
        ("workers_state", {"slots": [2]}),
        ("experiment_state", {"run_id": "a", "status": "running"}),
        ("experiment_state", {"run_id": "a", "status": "completed"}),
    ]
