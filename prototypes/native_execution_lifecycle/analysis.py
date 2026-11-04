"""Independent lifecycle accounting, diagnostic, cleanup and source audit."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

from .run import sources, BASE, REPO


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def verify(directory):
    for name, expected in read(directory / "verification.json").items():
        assert sha(directory / name) == expected, name
    meta = read(directory / "metadata.json")
    assert meta["source"] == sources()
    return meta, read(directory / "result.json")


def main(root):
    summary = {}
    freezes = set()
    healthy = {}
    attempts = set()
    total = Counter()
    for host in ("macos", "linux"):
        for kind in ("release", "faults"):
            directory = root / (host + "-" + kind + "-final")
            meta, data = verify(directory)
            freezes.add(meta["source_sha256"])
            rows = data["rows"]
            assert len(rows) == (78 if kind == "release" else 8), len(rows)
            counts = Counter(r["status"] for r in rows)
            assert counts == (
                {"succeeded": 41, "cancelled": 16, "failed": 21} if kind == "release" else {"succeeded": 6, "failed": 2}
            ), counts
            stored = read(directory / "store-audit.json")
            assert stored["passed"] and stored["published"] == counts["succeeded"]
            assert stored["bad_results"] == 0
            outcomes = []
            last_bad = False
            for r in rows:
                assert r["attempt"] not in attempts
                attempts.add(r["attempt"])
                if r["status"] == "succeeded":
                    if r["kind"] == "source_deleted_reproduction":
                        continue
                    key = r["retention"]
                    expected = healthy.setdefault(key, (r["fingerprint"], r["artifact_sha256"]))
                    assert expected == (r["fingerprint"], r["artifact_sha256"])
                    assert r["markers"]["closed"]["engine_released"]
                    last_bad = False
                else:
                    assert not last_bad, "fault was not followed by a healthy fresh run"
                    last_bad = True
                    progress = r["markers"]["progress"]["progressed"]["storage"]
                    assert progress["completed"] == 1
                    if r["status"] == "cancelled":
                        assert r["cancel_to_cleanup_seconds"] < 5
                    if r["kind"] in (
                        "cancel_advance",
                        "cancel_advance_ignore_term",
                        "timeout_advance",
                        "cancel_advance_short",
                    ):
                        assert r["markers"]["busy"]["binding_close_rejected"]
                    if r["kind"] in ("cancel_advance", "cancel_advance_ignore_term"):
                        assert "closed" not in r["markers"] and "native-returned" not in r["markers"]
                    if r["kind"] == "cancel_advance_ignore_term":
                        assert r["cancel_to_cleanup_seconds"] >= 2.5 and "descendant" in r["markers"]
                    if r["kind"] == "late_cancel":
                        assert r["markers"]["closed"]["engine_released"] and "late" in r["markers"]
                    if r["kind"] == "cleanup_error":
                        assert "primary producer failure" in r["details"]["message"]
                        assert "secondary cleanup failure" in r["details"]["traceback"]
                    if r["kind"] == "timeout_advance":
                        assert r["details"]["code"] == "timeout" and r["elapsed_seconds"] < r["deadline_seconds"] + 5
                    outcomes.append(
                        dict(
                            retention=r["retention"],
                            kind=r["kind"],
                            status=r["status"],
                            stop_seconds=r["cancel_to_cleanup_seconds"],
                            elapsed_seconds=r["elapsed_seconds"],
                            deadline_seconds=r["deadline_seconds"],
                        )
                    )
            for retention in ("full", "bounded"):
                names = Counter(r["kind"] for r in rows if r["retention"] == retention)
                assert names["source_deleted_reproduction"] == 1
                if kind == "release":
                    assert names["cancel_advance"] == names["producer_error"] == 3
                    assert (
                        names["cancel_advance_short"]
                        == names["cancel_advance_ignore_term"]
                        == names["timeout_advance"]
                        == 1
                    )
                else:
                    assert names["allocation_error"] == 1
            if kind == "faults":
                direct = read(directory / "direct.json")
                assert direct["passed"] and direct["busy_close_races"] == 2
                assert all(
                    r["read_only_recoveries"] == r["invalid_input_recoveries"] == r["poisoned_owners"] == 100
                    for r in direct["results"]
                )
            rss = [r["parent_rss"] for r in rows if "parent_rss" in r]
            handles = [r["parent_handles"] for r in rows if "parent_handles" in r]
            summary[host + "-" + kind] = dict(
                counts=counts,
                outcomes=outcomes,
                parent_rss_mib_range=[min(rss) / 2**20, max(rss) / 2**20],
                parent_handle_range=[min(handles), max(handles)],
                core=meta["core"],
                native=meta["native"],
                python=data["python"],
            )
            total.update(counts)
        safety = read(root / ("lifecycle-" + host + "-safety-final") / "checks.json")
        assert safety["passed"] and all(c["exit_code"] == 0 for c in safety["commands"])
        sanitized = read(root / (host + "-direct-asan.json"))
        assert sanitized["passed"] and sanitized["busy_close_races"] == 2
        assert all(r["read_only_recoveries"] == r["poisoned_owners"] == 100 for r in sanitized["results"])
    assert len(freezes) == 1
    expected = {
        "src/simulatte/execution/backend.py",
        "src/simulatte/execution/driver.py",
        "src/simulatte/stopping.py",
        "tests/execution/test_backend.py",
        "docs/guides/native-engine.md",
        ".github/workflows/ci.yml",
    }
    original = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", BASE], cwd=REPO, text=True).splitlines()
    changed = set(
        subprocess.check_output(["git", "diff", "--name-only", BASE, "--", *original], cwd=REPO, text=True).splitlines()
    )
    assert changed == expected, changed
    result = dict(
        passed=True,
        hosts=summary,
        total=total,
        total_unique_attempts=len(attempts),
        source_sha256=next(iter(freezes)),
        base_files=len(original),
        unchanged_base_files=len(original) - len(expected),
        changed_base_files=sorted(expected),
        native_C_and_provider_unchanged=True,
        direct_read_only_recoveries=400,
        direct_poisoned_owners=400,
        direct_invalid_input_recoveries=400,
        busy_close_races=4,
        python_leak_detection=False,
        analysis_sha256=sha(Path(__file__)),
    )
    (root / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(dict(passed=True, counts=total, attempts=len(attempts), source_sha256=next(iter(freezes)))))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path)
    main(p.parse_args().root)
