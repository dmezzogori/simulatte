"""Audit fixed requirements, independent control, reporting windows and memory."""

import argparse
import copy
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess

from simulatte import Environment

BASE = "8501694ba04c80d3a0fa5af552c377ddaaac0f09"
ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
NAMES = {
    "makespan",
    "lateness",
    "tardiness",
    "tardy_fraction",
    "total_queue_time",
    "throughput",
    "utilization",
    "jobs_in_system",
}


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def expected_spec(seed, index):
    mask = 18446744073709551615
    bits = (index + seed * 11400714819323198485) & mask
    bits = ((bits ^ (bits >> 30)) * 13787848793156543929) & mask
    bits ^= bits >> 27
    routes = ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0))
    offered = float(16 * (index // 12) + (index % 12) / 8)
    return dict(
        id=index,
        offered=offered,
        route=list(routes[bits % 6]),
        durations=[0.25 * (2 + (bits >> (4 * k)) % 4) for k in range(3)],
        due=float(offered + 6 + (bits >> 16) % 13),
    )


def validate_controls(rows, control, params, seed):
    with Environment(seed=seed) as env:
        source_seed = env.rng("fixed-admission-input").getrandbits(32)
    live = {}
    cursor = completed = blocked = idle = tight_count = switches = pool_count = delay = pressure = 0
    mode = False
    spec_hash, admit_hash, decision_hash = hashlib.sha256(), hashlib.sha256(), hashlib.sha256()
    jobs = []
    for tick, row in enumerate(rows):
        at = tick * params["interval"]
        assert row["tick"] == tick and row["t"] == at
        assert [o["id"] for o in row["observed"]] == list(live)
        for obs in row["observed"]:
            assert (obs["arrival"], obs["offered"], obs["due"]) == live[obs["id"]]
            assert obs["state"] in ("scheduled", "active", "pool", "finished")
            assert (obs["released"] is not None) == (obs["state"] == "active")
            assert obs["released"] is None or obs["released"] < at
        done = [o["id"] for o in row["observed"] if o["state"] == "finished"]
        assert done == row["completed"]
        for identity in done:
            del live[identity]
        completed += len(done)
        pool = sum(o["state"] == "pool" for o in row["observed"])
        pool_count += pool > 0
        pressure = (pressure + (256 * pool // len(live) if live else 0)) // 2
        previous = mode
        if not params["feedback"]:
            mode = False
        elif pressure >= 64:
            mode = True
        elif pressure <= 16:
            mode = False
        switches += mode != previous
        tight_count += mode
        target = params["hard_limit"] // 2 if mode else params["hard_limit"]
        credit = max(0, target - len(live))
        offered_total = 12 * int(at // 16) + min(12, int((at % 16) * 8) + 1)
        ready = offered_total > cursor
        assert (row["pressure_q"], row["tight"], row["target"], row["credit"], row["ready"]) == (
            pressure,
            mode,
            target,
            credit,
            ready,
        )
        count = min(params["burst"], credit, max(0, offered_total - cursor))
        assert len(row["jobs"]) == count
        blocked += ready and not credit
        idle += not ready
        for actual in row["jobs"]:
            spec = expected_spec(source_seed, cursor)
            expected = dict(spec, arrival=float(at))
            assert actual == expected and actual["arrival"] >= actual["offered"]
            live[cursor] = actual["arrival"], actual["offered"], actual["due"]
            delay += round((at - spec["offered"]) * 8)
            spec_hash.update(encode(spec))
            admit_hash.update(encode(actual))
            jobs.append(actual)
            cursor += 1
        assert len(live) <= params["hard_limit"]
        decision_hash.update(encode(row))
    state = control["controller"]
    assert state["seed"] == source_seed and state["cursor"] == cursor and state["tick"] == len(rows)
    assert state["completed"] == completed and state["next_at"] == len(rows) * params["interval"]
    assert state["pressure_q"] == pressure and state["tight_mode"] == mode and state["switches"] == switches
    assert state["blocked"] == blocked and state["idle"] == idle and state["pool_polls"] == pool_count
    assert state["tight_polls"] == tight_count and state["relaxed_polls"] == len(rows) - tight_count
    assert state["delay_eighths"] == delay
    assert state["live"] == [dict(id=i, arrival=v[0], offered=v[1], due=v[2]) for i, v in live.items()]
    assert state["specifications_sha256"] == spec_hash.hexdigest()
    assert (
        state["admissions_sha256"] == admit_hash.hexdigest() and state["decisions_sha256"] == decision_hash.hexdigest()
    )
    assert not ({"warmup", "kpis", "reporting", "reports_sha256"} & state.keys())
    return dict(ticks=len(rows), jobs=jobs)


def validate_reports(rows, reporting):
    h = hashlib.sha256()
    for i, row in enumerate(rows):
        assert row["tick"] == i and set(row["kpis"]) == NAMES
        assert all(v is None or type(v) is float and math.isfinite(v) for v in row["kpis"].values())
        h.update(encode(row))
    assert reporting["reports_sha256"] == h.hexdigest()


def proof(output, address):
    with gzip.open(output / "proofs" / (address + ".gz"), "rt") as file:
        return [json.loads(line) for line in file]


def audit(output, plot=False):
    from .run import sources

    meta, certificate = read(output / "metadata.json"), read(output / "verification.json")
    assert meta["source"] == sources() and meta["source_sha256"] == certificate["source_sha256"]
    for name, h in certificate["files"].items():
        assert sha(output / name) == h, name
    gate = read(output / "gate.json")
    groups = {}
    windows = {}
    controls = {}
    reports = {}
    rejected = 0
    for row in gate["rows"]:
        params = row["parameters"]
        csha, rsha = row["proof_sha256"]["controls"], row["proof_sha256"]["reports"]
        if csha not in controls:
            frames = proof(output, csha)
            controls[csha] = validate_controls(frames, row["control"], params, row["seed"])
            if len(frames) > 20 and not rejected:
                for field in ("deadline", "pressure", "completion"):
                    bad = copy.deepcopy(frames)
                    if field == "deadline":
                        bad[0]["jobs"][0]["due"] += 1
                    elif field == "pressure":
                        bad[1]["pressure_q"] += 1
                    else:
                        frame = next(v for v in bad if v["completed"])
                        next(o for o in frame["observed"] if o["id"] == frame["completed"][0])["state"] = "active"
                    try:
                        validate_controls(bad, row["control"], params, row["seed"])
                    except AssertionError:
                        rejected += 1
                    else:
                        raise AssertionError("Corrupt control proof accepted")
        if rsha not in reports:
            frames = proof(output, rsha)
            validate_reports(frames, row["reporting"])
            reports[rsha] = len(frames)
        assert controls[csha]["ticks"] == reports[rsha]
        assert row["fingerprint"]["kpis"] == {
            "shopfloor/" + k: v for k, v in row["reporting"]["kpis"].items() if v is not None
        }
        if row["engine"] != "python":
            assert "site-packages/simulatte_native/_bin/" in row["origin"]
        groups.setdefault((row["scenario"], row["seed"], row["warmup"]), []).append(row)
        windows.setdefault((row["scenario"], row["seed"], row["engine"]), []).append(row)
    for members in groups.values():
        assert {r["engine"] for r in members} == {"python", "full", "bounded"}
        for row in members[1:]:
            for field in ("control", "reporting", "control_sha256", "reporting_sha256", "proof_sha256"):
                assert row[field] == members[0][field], field
            assert row["fingerprint"]["kpis"] == members[0]["fingerprint"]["kpis"]
    invariant_groups = python_digests = changed_reports = 0
    digest_invariants = {"python": 0, "full": 0, "bounded": 0}
    for (scenario, seed, engine), members in windows.items():
        if len(members) < 2:
            continue
        invariant_groups += 1
        digest_invariants[engine] += len({r["fingerprint"]["digest"] for r in members}) == 1
        for row in members[1:]:
            assert row["control"] == members[0]["control"] and row["control_sha256"] == members[0]["control_sha256"]
            assert row["proof_sha256"]["controls"] == members[0]["proof_sha256"]["controls"]
        if engine == "python":
            assert len({r["fingerprint"]["digest"] for r in members}) == 1
            python_digests += 1
        if len({encode(r["reporting"]["kpis"]) for r in members}) > 1:
            changed_reports += 1
        if scenario.startswith("long"):
            assert len({encode(r["reporting"]["kpis"]) for r in members}) > 1
    assert invariant_groups == 18 and python_digests == 6
    for policy in ("fifo", "spt"):
        on = next(
            r for r in gate["rows"] if r["scenario"] == "long-" + policy and r["seed"] == 100 and r["warmup"] == 10
        )
        off = next(r for r in gate["rows"] if r["scenario"] == "counter-" + policy)
        assert on["control"]["controller"]["admissions_sha256"] != off["control"]["controller"]["admissions_sha256"]
        left = controls[on["proof_sha256"]["controls"]]["jobs"]
        right = controls[off["proof_sha256"]["controls"]]["jobs"]
        for a, b in zip(left, right):
            assert {k: v for k, v in a.items() if k != "arrival"} == {k: v for k, v in b.items() if k != "arrival"}
        assert all(
            on["control"]["controller"][k] > 0 for k in ("switches", "tight_polls", "relaxed_polls", "blocked", "idle")
        )
    originals = {r["request"]: r for r in gate["rows"]}
    assert gate["sources_deleted"]
    for row in gate["reproductions"]:
        first = originals[row["request"]]
        assert row["attempt"] != first["attempt"]
        for field in ("fingerprint", "control", "reporting", "native", "artifact_sha256"):
            assert row[field] == first[field]
    memory = read(output / "rows.json")
    pairs = {}
    for row in memory:
        value = row["result"]
        pairs.setdefault((row["ticks"], row["seed"]), []).append(row)
        for diag in row["diagnostics"]:
            stats = diag["storage"]
            assert (
                diag["max_owned"] <= row["parameters"]["hard_limit"]
                and diag["max_callback_bytes"] < 8192
                and diag["entities"] == 2
            )
            assert stats["admitted"] == value["control"]["controller"]["cursor"]
            live = sum(o["state"] != "finished" for o in value["control"]["final_observed"])
            assert stats["completed"] == stats["admitted"] - live
            if row["engine"] == "bounded":
                assert stats["native_bytes"] < 131072 and stats["retained_jobs"] == live
                assert stats["forgotten"] == stats["completed"] == diag["expired_reads"] > 0
            else:
                assert stats["retained_jobs"] == stats["admitted"] and diag["expired_reads"] == 0
        for repeated in row["repetitions"]:
            assert repeated["attempt"] != value["attempt"]
            for field in ("fingerprint", "control", "reporting", "native", "artifact_sha256"):
                assert repeated[field] == value[field]
        if row["repetitions"]:
            assert row["parent_checkpoints"][-1] - row["parent_checkpoints"][0] < 16 * 2**20
    for members in pairs.values():
        base = members[0]["result"]
        for row in members[1:]:
            assert row["result"]["control"] == base["control"]
        for row in members:
            for twin in members:
                if row["engine"] == twin["engine"] and row["warmup"] == twin["warmup"]:
                    for field in ("fingerprint", "control", "reporting", "native", "artifact_sha256"):
                        assert row["result"][field] == twin["result"][field]
        if len({r["warmup"] for r in members}) == 3:
            assert {r["warmup"] for r in members} == {0, 10, 40}
            assert len({encode(r["result"]["reporting"]["kpis"]) for r in members}) > 1
    stable = [r for r in memory if not r["extra_queries"] and not r["repetitions"] and r["warmup"] == 10]
    table = []
    for ticks in sorted({r["ticks"] for r in stable}):
        entry = dict(ticks=ticks)
        for mode in ("full", "bounded"):
            selected = [r for r in stable if r["engine"] == mode and r["ticks"] == ticks]
            entry[mode] = dict(
                jobs=statistics.median(r["result"]["control"]["controller"]["cursor"] for r in selected),
                tree_mib=statistics.median(r["usage"]["tree_peak_rss"] for r in selected) / 2**20,
                child_mib=statistics.median(max(d["peak_rss"] for d in r["diagnostics"]) for r in selected) / 2**20,
                parent_mib=statistics.median(r["usage"]["parent_peak_rss"] for r in selected) / 2**20,
                wall_seconds=statistics.median(r["usage"]["wall_seconds"] for r in selected),
            )
        table.append(entry)
    if len(table) == 3:
        for field, limit in (("child_mib", 16), ("tree_mib", 24), ("parent_mib", 16)):
            v = [r["bounded"][field] for r in table]
            assert max(v) - min(v) < limit, (field, v)
        assert table[-1]["full"]["child_mib"] > table[-1]["bounded"]["child_mib"] + 16
    preserved = 0
    for record in subprocess.check_output(["git", "ls-tree", "-rz", BASE], cwd=REPO).split(b"\0"):
        if not record:
            continue
        fields, name = record.split(b"\t", 1)
        mode, _, blob = fields.split()
        path = REPO / name.decode()
        data = os.readlink(path).encode() if mode == b"120000" else path.read_bytes()
        assert hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == blob.decode(), name
        preserved += 1
    result = dict(
        passed=True,
        physical_scenarios=len(groups),
        control_proofs=len(controls),
        report_proofs=len(reports),
        audited_control_rows=sum(r["ticks"] for r in controls.values()),
        window_invariant_groups=invariant_groups,
        python_digest_invariant_groups=python_digests,
        digest_invariant_groups_by_engine=digest_invariants,
        reporting_changed_groups=changed_reports,
        source_deleted_reproductions=len(gate["reproductions"]),
        memory_cases=len(memory),
        deliberate_corruptions_rejected=rejected,
        preserved_base_files=preserved,
        stable_summary=table,
    )
    if plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(7, 4), constrained_layout=True)
        for mode, color in (("full", "#b34d3b"), ("bounded", "#237994")):
            ax.plot([r["ticks"] for r in table], [r[mode]["tree_mib"] for r in table], "o-", label=mode, color=color)
        ax.set(
            xscale="log",
            xlabel="Admission decision ticks",
            ylabel="Coordinator tree sampled peak RSS (MiB)",
            title="Fixed requirements · reporting-independent control · macOS/CPython",
        )
        ax.grid(alpha=0.2)
        ax.legend()
        fig.savefig(output / "memory.png", dpi=180)
        plt.close(fig)
    (output / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", type=Path)
    p.add_argument("--plot", action="store_true")
    a = p.parse_args()
    audit(a.output, a.plot)
