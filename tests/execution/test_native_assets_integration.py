"""Real managed children consume context assets; no compiler is required."""

import os

import pytest

from simulatte.execution.coordinator import Coordinator, ExecutionFailure
from simulatte.execution.references import read_document
from simulatte.execution.slots import SlotConfig
from simulatte.execution.snapshot import discover_project
from simulatte.execution.store import RunStore
from simulatte.experiments import Experiment, Explicit, Replications
from simulatte.stopping import Horizon

pytestmark = pytest.mark.skipif(os.name != "posix", reason="native assets initially support local POSIX execution")

MODEL = '''from dataclasses import dataclass
import hashlib
from simulatte import model, KPI
from simulatte.execution.backend import ExecutionBackend, BackendCapabilities
from simulatte.execution.native_assets import NativeAssetBundle
@dataclass(frozen=True)
class Params:
    corrupt: bool = False
class Backend(ExecutionBackend):
    capabilities = BackendCapabilities("asset-fixture", "asset-state-v1")
    kpis = (KPI("bytes", unit="bytes"),)
    def __init__(self, corrupt):
        self.corrupt = corrupt
        self.data, self.t = b"", 0.0
    def prepare(self, context):
        self.context = context
        self.bundle = context.native_assets["engine"]
        self.data = self.bundle.path("engine.so").read_bytes()
    def advance(self, until):
        self.t = until
        if self.corrupt:
            path = self.bundle.path("engine.so")
            path.chmod(0o644)
            path.write_bytes(b"x" * len(self.data))
            path.chmod(0o444)
    def kpi_values(self):
        return {"bytes": len(self.data)}
    def state_digest(self):
        return hashlib.sha256(self.data + str(self.t).encode()).hexdigest()
    def finish(self):
        (self.context.env.artifacts_dir / "value.txt").write_text(str(len(self.data)))
    def close(self):
        pass
@model(params=Params, horizon=2, native_assets=(NativeAssetBundle("engine", ("engine.so",)),))
def build(env, layout, params):
    env.attach_execution_backend(Backend(params.corrupt))
'''


def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "plant.py").write_text(MODEL)
    (root / "engine.so").write_bytes(b"captured native bytes")
    (root / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude = ["plant.py", "engine.so"]\n')
    return discover_project(root / "plant.py", root)


def test_two_slots_eviction_and_reproduction_use_captured_assets(tmp_path):
    spec = project(tmp_path)
    with Coordinator(spec, slots=SlotConfig(local=2)) as coordinator:
        experiment = Experiment(name="assets", model="plant.py:build", stopping=Horizon(2),
                                configurations=Explicit([dict(label="one", parameters={})]),
                                replications=Replications(count=4, base_seed=100), level="kpi")
        identity = coordinator.start_experiment(experiment, timeout=15)
        assert coordinator.wait_experiment(identity)["counts"]["succeeded"] == 4
        rows = coordinator.store.replications(identity)
        result = coordinator.store.result(rows[0]["attempt"])
        assert result.fingerprint["kpis"]["shopfloor/bytes"] == 21
        assert (result.path / "artifacts/value.txt").read_text() == "21"
        assert len(list((coordinator.store.root / "native-assets").iterdir())) == 1
        request_id = result.key.request_id
        fingerprint = result.fingerprint
    with RunStore.open(spec.root / ".simulatte") as store:
        assert len(store.prune()["native_assets"]) == 1
    (spec.root / "engine.so").unlink()
    (spec.root / "plant.py").write_text("raise RuntimeError('live source must not execute')")
    with Coordinator.from_store(spec.root, request_id) as coordinator:
        result = coordinator.reproduce(request_id, timeout=15)
        assert result.fingerprint == fingerprint
        assert len(list((coordinator.store.root / "native-assets").iterdir())) == 1


def test_corrupt_native_asset_cannot_publish_and_explicit_pruning_repairs(tmp_path):
    spec = project(tmp_path)
    with Coordinator(spec) as coordinator:
        prepared = coordinator.prepare(timeout=15)
        request = coordinator.make_request(prepared, parameters={"corrupt": True}, level="kpi")
        key = coordinator.submit(request, prepared.revision, timeout=15)
        with pytest.raises(ExecutionFailure):
            coordinator.wait(key, timeout=20)
        with coordinator.store._transaction() as db:
            assert db.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 0
    # The changed entry is not repaired until explicitly pruned. A good request
    # can then rebuild identical bytes from the retained snapshot.
    with Coordinator(spec) as coordinator:
        assert len(coordinator.store.prune()["native_assets"]) == 1
        prepared = coordinator.prepare(timeout=15)
        request = coordinator.make_request(prepared, level="kpi")
        result = coordinator.wait(coordinator.submit(request, prepared.revision, timeout=15), timeout=20)
        assert result.fingerprint["kpis"]["shopfloor/bytes"] == 21


def test_declared_assets_preview_and_invalidation(tmp_path):
    spec = project(tmp_path)
    with Coordinator(spec) as coordinator:
        first = coordinator.prepare(timeout=15)
        old = read_document(first.documents["native_assets_ref"], "native-assets")["bundles"][0]["address"]
        request = coordinator.make_request(first, level="kpi")
        bundle = coordinator.preview(first, request, first.layout, timeout=15)
        assert bundle.reusable
        with coordinator.store._transaction() as db:
            assert db.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 0
        (spec.root / "engine.so").write_bytes(b"new native bytes")
        second = coordinator.prepare(timeout=15)
        new = read_document(second.documents["native_assets_ref"], "native-assets")["bundles"][0]["address"]
        assert new != old
