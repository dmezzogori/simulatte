"""Independent artifact, ordering, timing and source audit for public-helper usage."""

import argparse
import itertools
import json
from pathlib import Path
import statistics as st
import subprocess

from .common import BASE, REPO, assert_copies, digest, sha, sources


def read(path):
    return json.loads(path.read_text())


def verify(directory):
    meta, cert = read(directory / "metadata.json"), read(directory / "verification.json")
    assert meta["source"] == sources() and meta["source_sha256"] == digest(sources()) == cert["source_sha256"]
    for name, expected in cert["files"].items():
        assert sha(directory / name) == expected, name
    return meta


def statistics(values):
    return dict(median=st.median(values), min=min(values), max=max(values), values=values)


def physical(row, identity=True):
    artifacts = dict(row["artifacts"])
    if not identity:
        artifacts.pop("identities.json", None)
    return row["fingerprint"], artifacts


def analyze(root):
    assert_copies()
    live, query = root / "live-final", root / "query-final"
    lm, qm = verify(live), verify(query)
    assert lm["quick"] == qm["quick"]
    quick = lm["quick"]
    seeds = range(100, 101 if quick else 106)
    rates = (0, 16, 1) if quick else (0, 64, 16, 1)
    assert lm["core"] == qm["core"] and lm["native"] == qm["native"]
    rows = read(live / "rows.json")
    primary = [r for r in rows if not r["disabled"]]
    assert len(primary) == (18 if quick else 144) and len(rows) == (20 if quick else 150)
    assert [r["case"] for r in rows] == read(live / "schedule.json")
    exact, groups, attempts = {}, {}, set()
    max_queries = max_callback = max_native = max_view = max_owned = max_alloc = 0
    child_peaks = []
    for row in rows:
        assert row["ticks"] == lm["ticks"]
        result = row["result"]
        assert result["attempt"] not in attempts
        attempts.add(result["attempt"])
        for name, expected in result["artifacts"].items():
            assert sha(live / "evidence" / (expected + Path(name).suffix)) == expected
        key = row["fixture"], row["seed"]
        reduced = physical(result, identity=False)
        assert exact.setdefault(key, reduced) == reduced
        if not row["disabled"]:
            full = key + ("identity",)
            assert exact.setdefault(full, physical(result)) == physical(result)
            count = 2 + (row["ticks"] // row["every"] if row["every"] else 0)
            assert len(row["identity_diagnostics"]) == 1
            diag = row["identity_diagnostics"][0]
            assert diag["queries"] == count
            max_queries = max(max_queries, count)
            max_native = max(max_native, diag["max_raw_bytes"])
            max_view = max(max_view, diag["max_view_bytes"])
            groups.setdefault((row["fixture"], row["every"], row["seed"]), {})[row["mode"]] = row
        else:
            assert "identities.json" not in result["artifacts"] and not row["identity_diagnostics"]
        for diag in row["diagnostics"]:
            max_callback = max(max_callback, diag["max_callback_bytes"])
            max_owned = max(max_owned, diag["max_owned"])
            if diag["storage"]:
                max_alloc = max(max_alloc, diag["storage"]["native_bytes"])
                child_peaks.append(diag["peak_rss"])
    reproduced = []
    for fixture in ("fixed", "coupled"):
        reproduction = read(live / ("reproduce-" + fixture + ".json"))
        assert reproduction["sources_deleted"]
        before = groups[fixture, 16, 100]["helper"]["result"]
        after = reproduction["result"]
        assert physical(before) == physical(after) and before["data"] == after["data"]
        assert after["attempt"] not in attempts
        attempts.add(after["attempt"])
        reproduced.append(after["attempt"])
    comparisons = []
    overhead = []
    for fixture in ("fixed", "coupled"):
        for every in rates:
            blocks = [groups[fixture, every, seed] for seed in seeds]
            orders = {
                tuple(mode for mode, row in sorted(block.items(), key=lambda x: x[1]["case"]["order"]))
                for block in blocks
            }
            if not quick:
                assert orders == set(itertools.permutations(("helper", "prepared", "unprepared")))
            metrics = {}
            for metric in ("cold_seconds", "dispatch_seconds"):
                medians = {
                    mode: st.median(b[mode][metric] for b in blocks) for mode in ("helper", "prepared", "unprepared")
                }
                metrics[metric] = dict(
                    medians=medians,
                    helper_over_prepared=statistics([b["helper"][metric] / b["prepared"][metric] for b in blocks]),
                    helper_reduction_vs_unprepared=statistics(
                        [100 * (1 - b["helper"][metric] / b["unprepared"][metric]) for b in blocks]
                    ),
                )
            comparisons.append(
                dict(fixture=fixture, every=every, queries=2 + (lm["ticks"] // every if every else 0), metrics=metrics)
            )
            overhead.append(
                dict(
                    fixture=fixture,
                    every=every,
                    helper_cold_over_endpoint=statistics(
                        [
                            groups[fixture, every, seed]["helper"]["cold_seconds"]
                            / groups[fixture, 0, seed]["helper"]["cold_seconds"]
                            for seed in seeds
                        ]
                    ),
                )
            )
    micro = []
    for path in sorted(query.glob("query-*-query/query.json")):
        value = read(path)
        name = path.parent.name.removesuffix("-query")
        context = read(query / (name + ".json"))
        assert context["passed"] and context["baseline"] == context["measured"] and value["gc_enabled"]
        batches = value["batches"]
        assert len(batches) == 12 and all(len(v) == 5 for v in batches.values())
        medians = {k: st.median(r["wall_ns"] / r["calls"] for r in v) for k, v in batches.items()}
        cpu = {k: st.median(r["cpu_ns"] / r["calls"] for r in v) for k, v in batches.items()}
        micro.append(
            dict(
                fixture=context["fixture"],
                retention=context["retention"],
                at=context["at"],
                context=value["context"],
                wall_ns=medians,
                cpu_ns=cpu,
                helper_pipeline_reduction_vs_unprepared=100
                * (1 - medians["helper_pipeline"] / medians["unprepared_pipeline"]),
                helper_composition_extra_ns=medians["helper_composition"] - medians["prepared_composition"],
                profiles={
                    k: dict(
                        calls=p["calls"], self_categories=p["self_categories"], accounted_seconds=p["accounted_seconds"]
                    )
                    for k, p in value["profiles"].items()
                },
                traced_python_memory=value["traced_python_memory"],
            )
        )
    assert len(micro) == (4 if quick else 8)
    # All files already present at the base must stay byte-identical.
    changed = subprocess.check_output(
        [
            "git",
            "diff",
            "--name-only",
            BASE,
            "--",
            *subprocess.check_output(["git", "ls-tree", "-r", "--name-only", BASE], cwd=REPO, text=True).splitlines(),
        ],
        cwd=REPO,
        text=True,
    )
    assert not changed, changed
    result = dict(
        passed=True,
        source_sha256=lm["source_sha256"],
        source_files=len(lm["source"]),
        quick=quick,
        primary_attempts=len(primary),
        disabled_controls=2 if quick else 6,
        source_deleted_reproductions=reproduced,
        total_unique_attempts=len(attempts),
        comparisons=comparisons,
        frequency_overhead=overhead,
        micro=micro,
        resources=dict(
            max_queries=max_queries,
            max_callback_bytes=max_callback,
            max_native_bytes=max_native,
            max_identity_bytes=max_view,
            max_owned=max_owned,
            max_native_allocated_bytes=max_alloc,
            child_peak_bytes=statistics(child_peaks),
            tree_peak_bytes=statistics([r["usage"]["tree_peak_rss"] for r in rows]),
        ),
        all_base_files_unchanged=True,
        all_outputs_identical_across_consumers_and_rates=True,
        limitations=[
            "six paired seeds, one host/CPython ABI",
            "periodic rates are model assumptions, not production measurements",
            "disabled control also omits action-ledger work",
            "cold Coordinator wall time includes startup/snapshot/scheduling overhead",
            "instrumented self times and traced peaks are distinct from latency/native allocation/RSS",
        ],
    )
    (root / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(dict(passed=True, attempts=len(attempts), micro_cells=len(micro), source_sha256=lm["source_sha256"]))
    )
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path)
    analyze(p.parse_args().root)
