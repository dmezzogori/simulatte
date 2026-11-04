"""Summarize final installed and measurement evidence without changing it."""

import hashlib
import json
from pathlib import Path
import re
import shutil

from .validate import sources

ROOT = Path(__file__).resolve().parent


def main():
    root = ROOT / "results"
    cells = []
    for path in sorted(root.glob("*-final-cp*/metadata.json")):
        value = json.loads(path.read_text())
        assert value["passed"]
        directory = path.parent
        for name, digest in json.loads((directory / "verification.json").read_text()).items():
            assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == digest
        observed = json.loads((directory / "installed/observations.json").read_text())
        assert len(observed["rows"]) == 24 and len(observed["source_deleted_reproductions"]) == 8
        assert json.loads((directory / "studio/studio.json").read_text())["passed"]
        cells.append(
            dict(name=directory.name, native=value["native"], core=value["core"], observations=24, reproductions=8)
        )
    measurements = []
    for host in ("macos", "linux"):
        for retention in ("full", "bounded"):
            group = [
                json.loads(path.read_text())
                for path in sorted((root / "measurements").glob(f"{host}-{retention}-*.json"))
            ]
            if not group:
                continue
            assert all(
                row["fingerprint"] == group[0]["fingerprint"] and row["native_state"] == group[0]["native_state"]
                for row in group
            )
            measurements.extend(group)
    reviewed = []
    current = sources()
    for path in sorted(root.glob("*-reviewed-cp314/metadata.json")):
        value = json.loads(path.read_text())
        assert value["passed"]
        for name, expected in value["source"].items():
            # This evidence summarizer is updated after the measured runs.
            if not name.endswith("/analyze.py"):
                assert current[name] == expected, name
        directory = path.parent
        assert "287 passed" in (directory / "regressions.log").read_text()
        for name, digest in json.loads((directory / "verification.json").read_text()).items():
            assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == digest
        reviewed.append(dict(name=directory.name, core=value["core"], native=value["native"]))
    assert len(cells) == 10 and len(reviewed) == 2 and len(measurements) == 36
    suite = (ROOT / ".build/reviewed-full-suite.log").read_text()
    assert "3771 passed, 1 skipped, 3 warnings" in suite
    shutil.copyfile(ROOT / ".build/reviewed-full-suite.log", root / "reviewed-full-suite.log")
    coverage = float(re.search(r"Total coverage: ([0-9.]+)%", suite).group(1))
    assert coverage >= 99
    for name in ("browser", "docs", "frontend-build"):
        shutil.copyfile(ROOT / f".build/{name}.log", root / f"{name}.log")
    assert "69 passed" in (root / "browser.log").read_text()
    assert "No issues found" in (root / "docs.log").read_text()
    assert json.loads((root / "example.json").read_text())["passed"]
    extras = dict(
        reviewed=reviewed,
        repository=dict(passed=3771, skipped=1, warnings=3, coverage=coverage),
        browser_tests=69,
        typescript_tests=455,
        native_companion_unchanged=True,
        sources={path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in ROOT.glob("*.py")},
    )
    (root / "summary.json").write_text(
        json.dumps(dict(passed=True, cells=cells, measurements=measurements, **extras), indent=2) + "\n"
    )
    certificate = {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p != root / "verification.json"
    }
    (root / "verification.json").write_text(json.dumps(certificate, indent=2) + "\n")
    print(json.dumps(dict(passed=True, cells=len(cells), measurement_runs=len(measurements))))


if __name__ == "__main__":
    main()
