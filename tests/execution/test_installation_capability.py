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
