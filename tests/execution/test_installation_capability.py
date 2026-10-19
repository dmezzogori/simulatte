"""The new runtime contract is refused before any model-import command reaches an old peer."""

from dataclasses import replace

import pytest

from simulatte.execution.coordinator import Coordinator, ExecutionFailure
from simulatte.execution.schema import InspectionRequest
from simulatte.execution.slots import SlotConfig
from tests.execution.test_coordinator import project
from tests.execution.test_scheduler import Peer, until


@pytest.mark.parametrize("inspection", [False, True])
@pytest.mark.parametrize("reused", [False, True])
def test_incapable_new_or_reused_worker_never_receives_import_command(tmp_path, inspection, reused):
    with Coordinator(project(tmp_path), slots=SlotConfig(local=1)) as co:
        prepared = co.prepare(timeout=10)
        runtime = {**prepared.runtime, "kind": "local-installation-v1"}
        peer = Peer()
        sent = []
        peer.send = lambda kind, body, **kw: sent.append(kind)
        slot = "interactive" if inspection else "local-1"
        actor = co._actors[slot]
        until(lambda: actor.current is None and actor.pending is None)
        if actor.worker is not None:
            actor.worker.close()
            actor.worker = None
        actor.transport_factory = lambda: peer
        if reused:
            actor._start_worker()
        if inspection:
            request = InspectionRequest(
                model=prepared.model, source_hash=prepared.snapshot.source.content_hash, runtime=runtime
            )
            key = co._schedule(request, prepared.revision, prepared.snapshot, prepared.documents, 10, slot=slot)
        else:
            request = co.make_request(replace(prepared, runtime=runtime))
            key = co.submit(request, prepared.revision, timeout=10, slot=slot)
        with pytest.raises(ExecutionFailure, match="capabilit"):
            co.wait(key, timeout=15)
        assert not {"run", "inspect"} & set(sent)


def test_experiment_local_children_require_new_capability(tmp_path, monkeypatch):
    from tests.execution.test_scheduler import write_experiment
    from simulatte.experiments import load_experiment
    from simulatte.execution import coordinator as module

    peers = []

    def start(*args, **kwargs):
        peer = Peer()
        peers.append(peer)
        return peer

    spec = project(tmp_path)
    with Coordinator(spec, slots=SlotConfig(local=1)) as co:
        prepared = co.prepare(timeout=10)
        co.inputs.bind(replace(prepared, runtime={**prepared.runtime, "kind": "local-installation-v1"}))
        monkeypatch.setattr(module.LocalTransport, "start", start)
        run = co.start_experiment(load_experiment(write_experiment(spec.root, replications={"count": 1})))
        assert co.wait_experiment(run, timeout=15)["status"] == "failed"
        assert peers and not any(peer.ran for peer in peers)
        for row in co.store.replications(run):
            assert row["worker_kind"] == "local"
            assert row["status"] == "failed"


@pytest.mark.parametrize("inspection", [False, True])
def test_new_installation_handoff_keeps_tags_raw_evidence_and_relocated_bindings(tmp_path, inspection):
    from pathlib import Path
    from types import SimpleNamespace
    from simulatte.execution.references import read_document
    from simulatte.execution.hashing import canonical_hash

    with Coordinator(project(tmp_path)) as co:
        prepared = co.prepare(timeout=10)
        until(lambda: co._current is None)
        actor = co._actors["interactive"]
        actor.worker.close()
        actor.worker = None
        identity = {
            "format": "installation-v1",
            "facts": dict(prepared.runtime["facts"]),
            "dependencies": {"packages": {}},
            "warnings": [],
        }
        captured_bindings = {"file:///deleted-staging/wheel.whl": "a" * 64}
        relocated_bindings = {"file:///rebuilt-staging/wheel.whl": "a" * 64}
        generation = SimpleNamespace(
            id="g" * 64,
            interpreter=tmp_path / "relocated/bin/python",
            evidence={"spec": {"runtime_format": "installation-v1"}},
        )
        released = []
        co.environments = SimpleNamespace(
            selected=generation,
            installation_probe=lambda value: {
                "installation": identity,
                "runtime": {"raw": "retained"},
                "bindings": captured_bindings,
            },
            installation_bindings=lambda value: relocated_bindings,
            acquire=lambda identity, owner: None,
            release=released.append,
        )
        peer = Peer()
        receive = peer.receive
        sent = []

        def hello(timeout):
            message = receive(timeout)
            message.body["capabilities"].append("installation-runtime-v1")
            return message

        peer.receive = hello
        peer.send = lambda kind, body, **kw: sent.append((kind, body))
        actor.transport_factory = lambda: peer
        if inspection:
            with pytest.raises(ExecutionFailure):
                co.prepare(timeout=10)
        else:
            runtime = {
                "kind": "local-installation-v1",
                "facts": identity["facts"],
                "dependencies_hash": canonical_hash("installation-dependencies-v1", identity["dependencies"]),
                "trusted_runtime_hash": co.runtime.content_hash,
            }
            evidence = {
                "format": "installation-v1",
                "identity": identity,
                "raw": {"raw": "retained"},
                "bindings": captured_bindings,
            }
            documents = {**prepared.documents, "runtime_ref": co._document("runtime", evidence)}
            request = co.make_request(replace(prepared, runtime=runtime, documents=documents))
            key = co.submit(request, prepared.revision, timeout=10)
            with pytest.raises(ExecutionFailure):
                co.wait(key, timeout=15)
        kind, command = next(item for item in sent if item[0] in ("inspect", "run"))
        assert kind == ("inspect" if inspection else "run")
        config = command["config"]
        assert Path(config["installation_prefix"]) == generation.interpreter.parent.parent
        assert config["installation_bindings"] == relocated_bindings
        assert config["trusted_runtime_hash"] == co.runtime.content_hash
        captured_request = read_document(command["inspection_ref" if inspection else "request_ref"], "request")
        assert captured_request["runtime"]["kind"] == "local-installation-v1"
        evidence = read_document(config["runtime_ref"], "runtime")
        assert evidence == {
            "format": "installation-v1",
            "identity": identity,
            "raw": {"raw": "retained"},
            "bindings": captured_bindings,
        }
        until(lambda: bool(released))
