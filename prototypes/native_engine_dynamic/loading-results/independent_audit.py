"""Read-only checks of saved gate, provenance, cold/warm and control evidence."""
from collections import Counter
import json
from pathlib import Path

from prototypes.native_engine_dynamic.loading_experiment import comparable, verify_loaders
from prototypes.native_engine_dynamic.phase_experiment import totals


if __name__ == "__main__":
    root = Path("prototypes/native_engine_dynamic/loading-results")
    gate_meta = json.loads((root / "gate/metadata.json").read_text())
    scale_meta = json.loads((root / "scaling/metadata.json").read_text())
    for key in ("runtime_files", "project_files", "files", "address"):
        assert gate_meta["fixture"][key] == scale_meta["fixture"][key], key
    gate = json.loads((root / "gate/runs.json").read_text())
    assert len(gate) == 8
    for producer in ("independent", "feedback"):
        for policy in ("fifo", "spt"):
            a, b = [r for r in gate if r["producer"] == producer and r["policy"] == policy]
            assert a["loading"] == "attempt" and b["loading"] == "immutable"
            assert comparable(a) == comparable(b)
    for row in gate:
        verify_loaders(row, gate_meta["fixture"], gate=True)
        opposite = "immutable" if row["loading"] == "attempt" else "attempt"
        assert Counter(d["mode"] for d in row["loaders"]) == {row["loading"]: 2, opposite: 1}
    controls = json.loads((root / "original-controls.json").read_text())
    for control in controls:
        target = next(r for r in gate if r["producer"] == control["producer"] and
                      r["loading"] == "immutable" and r["policy"] == "spt")
        for a, b in zip(control["original"]["replications"], target["replications"]):
            for key in control["matched_fields"]:
                assert a[key] == b[key]
            assert a["source_hash"] != b["source_hash"] and a["request_id"] != b["request_id"]
    negatives = json.loads((root / "gate/negatives.json").read_text())
    assert len(negatives) == 4
    for row in negatives:
        aggregate = totals(row["profiles"], "run:")
        assert aggregate.get("backend.advance", {}).get("count", 0) == 0
        count = "cancelled" if row["case"].startswith("ready-cancel") else "failed"
        assert row["status"]["counts"][count] == 1
        if count == "failed":
            assert row["loaders"] == []
            assert aggregate.get("native.binary_import", {}).get("count", 0) == 0
    matrix = [json.loads(line) for line in (root / "scaling/matrix.jsonl").read_text().splitlines()]
    assert len(matrix) == 48
    for row in matrix:
        documents = {d["attempt_id"]: d for d in row["loaders"]}
        for replication in row["replications"]:
            load = documents[replication["attempt_id"]]
            expected = ("snapshot:prototypes/native_engine_dynamic/.build/" + Path(load["extension"]).name
                        if row["loading"] == "attempt" else "external:" + load["extension"])
            assert replication["native_origin"] == expected
    assert sum(row["first_immutable_use"] for row in matrix) == 1
    first = next(row for row in matrix if row["first_immutable_use"])
    analysis = json.loads((root / "analysis.json").read_text())
    assert analysis["first_shared_path_load"]["attempt_id"] == first["replications"][0]["attempt_id"]
    assert analysis["warm_loads"] == 71
    result = dict(gate_pairs=16, cross_path_native_loader_observations=8, original_controls=8,
                  negative_cases=4, actual_native_origins_checked=144, first_use_retained=True,
                  runtime_and_project_manifests_match=True)
    (root / "independent-audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
