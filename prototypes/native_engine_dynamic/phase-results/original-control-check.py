"""Independent original-runtime comparison against the frozen probe gate."""
import json
from pathlib import Path
import tempfile

from simulatte.experiments import run
from simulatte.execution.slots import SlotConfig
from simulatte.stopping import Horizon
from prototypes.native_engine_dynamic import backend_experiment as baseline
from prototypes.native_engine_dynamic.execution_project import create_project


if __name__ == "__main__":
    output = Path("prototypes/native_engine_dynamic/phase-results")
    gate = json.loads((output / "gate/runs.json").read_text())
    records = []
    with tempfile.TemporaryDirectory(prefix="phase-original-controls-") as tmp:
        for producer in ("independent", "feedback"):
            project = create_project(Path(tmp) / producer)
            definition = baseline.prior.definition(
                [dict(backend=b, producer=producer, jobs=40, servers=3, wip_cap=2) for b in ("production", "native")],
                count=2, stopping=Horizon(10, warmup=2),
            )
            status = run(definition, project=project, slots=SlotConfig(local=1), timeout=60)
            current = baseline.read_results(project, status["run_id"], baseline="production/base")
            reference = next(r for r in gate if r["mode"] == "active" and r["producer"] == producer)
            for a, b in zip(current["replications"], reference["replications"]):
                for key in ("backend_state", "snapshot", "kpis", "proof", "steps", "fingerprint_digest"):
                    assert a[key] == b[key], (producer, key)
                assert a["source_hash"] != b["source_hash"]
                assert a["request_id"] != b["request_id"]
            records.append(dict(producer=producer, original=current, matched_fields=[
                "backend_state", "snapshot", "kpis", "proof", "steps", "fingerprint_digest"
            ]))
    (output / "original-controls.json").write_text(json.dumps(records, indent=2) + "\n")
    print("Eight original-runtime controls match shared outcomes, native/domain state and ordinary semantic fingerprints")
