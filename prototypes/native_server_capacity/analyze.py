"""Verify preserved evidence and produce the final research summary/certificate."""

import hashlib
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

from .validate import sources

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
RESULTS = ROOT / "results"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    cells = []
    for host in ("macos", "linux"):
        for abi in range(311, 316):
            directory = RESULTS / f"{host}-final-cp{abi}"
            metadata = read(directory / "metadata.json")
            assert metadata["passed"] and metadata["source"] == sources()
            for prefix in (directory, directory / "installed"):
                for name, digest in read(prefix / "verification.json").items():
                    assert sha(prefix / name) == digest, (prefix, name)
            installed = directory / "installed"
            capacity = read(installed / "capacities.json")
            assert len(capacity["rows"]) == len(capacity["source_deleted_reproductions"]) == 8
            rejections = read(installed / "rejections.json")
            assert len(rejections) == 7 and all(row["rejected"] for row in rejections.values())
            assert "225 passed" in (directory / "regressions.log").read_text()
            cells.append(
                dict(
                    cell=directory.name,
                    regressions=225,
                    capacity_requests=8,
                    capacity_reproductions=8,
                    rejections=7,
                    core=metadata["core"],
                    native=metadata["native"],
                )
            )
    parity = read(RESULTS / "macos-final-parity.json")
    assert parity == read(RESULTS / "linux-final-parity.json") and parity["passed"] and parity["cases"] == 128
    assert (RESULTS / "legacy-provider.json").read_bytes() == (RESULTS / "current-unit-provider.json").read_bytes()
    legacy = len(read(RESULTS / "legacy-provider.json"))
    memory, safety = {}, {}
    for host, sanitizer in (("macos", "macos-final-capacity-asan"), ("linux", "linux-capacity-asan")):
        full, bounded = (read(RESULTS / f"{host}-memory-{mode}.json") for mode in ("full", "bounded"))
        assert full["final"] == bounded["final"]
        assert [r["snapshot"] for r in full["rows"]] == [r["snapshot"] for r in bounded["rows"]]
        assert len({r["storage"]["native_bytes"] for r in bounded["rows"]}) == 1
        memory[host] = dict(full=full["rows"][-1], bounded=bounded["rows"][-1])
        directory = RESULTS / sanitizer
        assert read(directory / "checks.json")["passed"]
        assert "123 passed" in (directory / "native-tests.log").read_text()
        assert "79 passed" in (directory / "capacity-tests.log").read_text()
        faults = read(directory / "capacity-faults.json")
        assert faults["passed"]
        safety[host] = dict(
            tests=202,
            induced_failures=sum(r["failed"] for r in faults["rows"]),
            successes=sum(r["succeeded"] for r in faults["rows"]),
            python_leak_detection=False,
        )
    full = ROOT / ".build/final-full-suite.log"
    assert "3738 passed, 1 skipped, 3 warnings" in full.read_text()
    shutil.copyfile(full, RESULTS / "full-suite.log")
    shutil.copyfile(ROOT / ".build/docs.log", RESULTS / "docs.log")
    assert "No issues found" in (RESULTS / "docs.log").read_text()
    coverage = ET.parse(REPO / "coverage.xml").getroot().attrib
    combined = float(re.search(r"Total coverage: ([0-9.]+)%", full.read_text()).group(1))
    assert combined >= 99
    evidence_sources = {
        p.relative_to(REPO).as_posix(): sha(p)
        for p in [*ROOT.glob("*.py"), ROOT / "README.md", REPO / "specs/research/2026-11-04-native-server-capacity.md"]
    }
    summary = dict(
        passed=True,
        base="29596aecbbd653713c0c0be5037b070deb15fc2a",
        cells=cells,
        capacity_requests=80,
        capacity_source_deleted_reproductions=80,
        production_parity_cases_per_host=128,
        identical_cross_host_states=True,
        legacy_unit_observations=legacy,
        memory=memory,
        sanitizer=safety,
        repository=dict(
            passed=3738,
            skipped=1,
            warnings=3,
            combined_coverage=combined,
            line_coverage=float(coverage["line-rate"]) * 100,
            branch_coverage=float(coverage["branch-rate"]) * 100,
        ),
        sources=evidence_sources,
    )
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    certificate = {
        p.relative_to(RESULTS).as_posix(): sha(p)
        for p in sorted(RESULTS.rglob("*"))
        if p.is_file() and p != RESULTS / "verification.json"
    }
    (RESULTS / "verification.json").write_text(json.dumps(certificate, indent=2) + "\n")
    print(json.dumps(dict(passed=True, cells=len(cells), coverage=combined, legacy_observations=legacy)))


if __name__ == "__main__":
    main()
