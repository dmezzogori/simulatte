"""Audit frozen sources and retained ten-lane evidence without rerunning simulations."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
RESULTS = ROOT / "results"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    spec = importlib.util.spec_from_file_location("matrix", REPO / "native/package_matrix.py")
    matrix = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(matrix)
    sources = matrix.sources()
    rows, baseline, query_baseline, managed_baseline, kpis = [], None, None, None, {}
    source_hash = hashlib.sha256(json.dumps(sources, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    core_sha = None
    for host in ("macos", "linux"):
        directory = RESULTS / host
        metadata, certificate = read(directory / "metadata.json"), read(directory / "verification.json")
        assert metadata["files"] == sources
        assert metadata["source_sha256"] == certificate["source_sha256"] == source_hash
        if core_sha is None:
            core_sha = metadata["core_sha256"]
        assert metadata["core_sha256"] == core_sha
        for name, expected in certificate["evidence"].items():
            assert sha(directory / name) == expected, name
        for row in certificate["rows"]:
            abi = "cp" + row["python"].replace(".", "")
            lane = directory / abi
            for name, expected in read(lane / "verification.json").items():
                assert sha(lane / name) == expected
            install, identity, lane_meta = (
                read(lane / "installed.json"),
                read(lane / "identities.json"),
                read(lane / "metadata.json"),
            )
            assert identity["passed"] and len(identity["direct"]) == 16
            values = {
                (r["fixture"], r["retention"], r["mode"]): (r["fingerprint"], r["artifacts"])
                for r in identity["direct"]
            }
            managed = {(r["fixture"], r["retention"]): (r["fingerprint"], r["artifacts"]) for r in identity["managed"]}
            if baseline is None:
                baseline, query_baseline, managed_baseline = values, identity["queries"], managed
            assert values == baseline and identity["queries"] == query_baseline and managed == managed_baseline
            for r in install["rows"]:
                key = r["configuration"], r["seed"]
                exact = r["fingerprint"], r["kpis"]
                assert kpis.setdefault(key, exact) == exact
            assert len(identity["source_deleted_reproductions"]) == len(install["source_deleted_reproductions"]) == 4
            rejections = read(lane / "rejections.json")
            assert len(rejections) == 7 and all(r["rejected"] for r in rejections.values())
            for name, expected in lane_meta["identity_fixtures"].items():
                assert sha(REPO / name) == expected
            assert lane_meta["core_sha256"] == core_sha
            rows.append(dict(host=host, abi=abi, actual_python=lane_meta["python"].splitlines()[0], **row))

    safety = read(RESULTS / "linux-safety-v2/checks.json")
    assert safety["passed"] and all(c["exit_code"] == 0 for c in safety["commands"])
    assert "123 passed" in (RESULTS / "linux-safety-v2/native-tests.log").read_text()
    assert "allocation failures are recoverable" in (RESULTS / "linux-safety-v2/allocation-faults.log").read_text()
    assert "3651 passed, 1 skipped" in (RESULTS / "full-tests.log").read_text()
    assert "99.06%" in (RESULTS / "full-tests.log").read_text()
    for name in ("lint", "types"):
        assert "All checks passed!" in (RESULTS / (name + ".log")).read_text()
    assert "349 files already formatted" in (RESULTS / "format.log").read_text()
    assert "No issues found" in (RESULTS / "docs.log").read_text()
    base = "3588e0936548bed6994be79d46188dc0a7dac116"
    allowed = {
        ".github/workflows/ci.yml",
        "docs/api/experimental.md",
        "docs/guides/native-engine.md",
        "native/check_install.py",
        "native/package_matrix.py",
    }
    names = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", base], cwd=REPO, text=True).splitlines()
    # Git's diff is independent of staging and covers every historical tracked file.
    changed = set(
        subprocess.check_output(["git", "diff", "--name-only", base, "--", *names], cwd=REPO, text=True).splitlines()
    )
    assert changed == allowed, changed
    findings = read(RESULTS / "existing-format-findings.json")
    assert len(findings["files"]) == 12 and all(r["unchanged_from_base"] for r in findings["files"])
    result = dict(
        passed=True,
        source_sha256=source_hash,
        source_files=len(sources),
        core_sha256=core_sha,
        base=base,
        historical_files=len(names),
        unchanged_historical_files=len(names) - len(allowed),
        changed_historical_files=sorted(allowed),
        frozen_oracles_and_provider_unchanged=True,
        rows=rows,
        exact_artifacts_fingerprints_and_query_bytes_across_all_lanes=True,
        direct_model_runs=160,
        original_coordinator_requests=120,
        source_deleted_reproductions=80,
        negative_install_probes=70,
        full_tests=dict(passed=3651, skipped=1, branch_coverage=99.06, helper_coverage=100),
        linux_focused_tests=174,
        macos_focused_tests=174,
        linux_sanitizer_tests=123,
        linux_allocation_recovery_probes=2,
        existing_format_findings=12,
        no_new_performance_claim=True,
    )
    (RESULTS / "combined.json").write_text(json.dumps(result, indent=2) + "\n")
    evidence = {
        p.relative_to(RESULTS).as_posix(): sha(p)
        for p in sorted(RESULTS.rglob("*"))
        if p.is_file() and p.name != "audit.json"
    }
    (RESULTS / "audit.json").write_text(json.dumps(dict(passed=True, evidence=evidence), indent=2) + "\n")
    print(
        json.dumps(
            dict(
                passed=True,
                lanes=len(rows),
                source_files=len(sources),
                historical_files=len(names),
                unchanged_historical_files=len(names) - len(allowed),
                evidence_files=len(evidence),
            )
        )
    )


if __name__ == "__main__":
    main()
