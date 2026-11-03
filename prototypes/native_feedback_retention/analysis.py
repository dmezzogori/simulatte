"""Independent feedback proof checks and frozen installed-gate evidence audit."""

import argparse
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess

from simulatte import Environment

BASE = "d26edb967b9bfc2eab7357910e0c1efbe056dcb5"
ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def validate_frames(rows, physical, parameters, seed):
    """Reconstruct the law and counter source, not by calling the controller."""
    with Environment(seed=seed) as env:
        source_seed = env.rng("feedback-input").getrandbits(32)
    live, admitted, completed, blocked = {}, 0, 0, 0
    jobs_hash, decision_hash = hashlib.sha256(), hashlib.sha256()
    short = normal = tight_count = pool_polls = 0
    for index, row in enumerate(rows):
        assert set(row["kpis"]) == {
            "makespan",
            "lateness",
            "tardiness",
            "tardy_fraction",
            "total_queue_time",
            "throughput",
            "utilization",
            "jobs_in_system",
        }
        assert row["tick"] == index and row["t"] == index * parameters["interval"]
        assert [v["id"] for v in row["observed"]] == list(live)
        for obs in row["observed"]:
            assert (obs["arrival"], obs["due"]) == live[obs["id"]]
            assert obs["state"] in ("scheduled", "pool", "active", "finished")
            assert obs["released"] is None or obs["released"] < row["t"]
            assert (obs["released"] is not None) == (obs["state"] == "active")
        finished = [o["id"] for o in row["observed"] if o["state"] == "finished"]
        assert row["completed"] == finished
        for identity in finished:
            del live[identity]
        completed += len(finished)
        pool = sum(o["state"] == "pool" for o in row["observed"])
        pool_polls += pool > 0
        tardy = row["kpis"]["tardy_fraction"]
        tight = parameters["feedback"] and tardy is not None and tardy > 0.35
        target = parameters["hard_limit"] // 2 if tight else parameters["hard_limit"]
        assert row["target"] == target
        tight_count += tight
        expected_choice = "short" if parameters["feedback"] and pool > 0 else "normal"
        assert row["choice"] == expected_choice
        credit = max(0, target - len(live))
        count = min(parameters["burst"], credit)
        assert len(row["jobs"]) == count
        blocked += count == 0
        for actual in row["jobs"]:
            # Independent counter arithmetic and route construction.
            mask = 18446744073709551615
            bits = (admitted + source_seed * 11400714819323198485) & mask
            bits = ((bits ^ (bits >> 30)) * 13787848793156543929) & mask
            bits ^= bits >> 27
            route = list(((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0))[bits % 6])
            durations = [((bits >> (4 * k)) % 4 + 2) * 0.25 for k in range(3)]
            if expected_choice == "short":
                route = [route[2], route[1]]
                durations = [durations[2] / 2, durations[1] / 2]
                short += 1
            else:
                normal += 1
            expected = dict(
                arrival=float(row["t"]), due=float(row["t"] + ((bits >> 16) % 17 - 4)), route=route, durations=durations
            )
            assert actual == expected
            live[admitted] = actual["arrival"], actual["due"]
            admitted += 1
            jobs_hash.update(encode(actual))
        assert len(live) <= parameters["hard_limit"]
        decision_hash.update(encode(row))
    state = physical["controller"]
    assert state["seed"] == source_seed and state["cursor"] == admitted and state["completed"] == completed
    assert state["tick"] == len(rows) and state["next_at"] == len(rows) * parameters["interval"]
    assert state["blocked"] == blocked and state["short"] == short and state["normal"] == normal
    assert state["tight"] == tight_count and state["relaxed"] == len(rows) - tight_count
    assert state["pool_polls"] == pool_polls
    assert state["live"] == [dict(id=i, arrival=v[0], due=v[1]) for i, v in live.items()]
    assert state["jobs_sha256"] == jobs_hash.hexdigest() and state["decisions_sha256"] == decision_hash.hexdigest()
    return dict(ticks=len(rows), admitted=admitted, completed=completed, blocked=blocked, short=short, normal=normal)


def audit(output, plot=False):
    from .run import sources

    meta, cert = read(output / "metadata.json"), read(output / "verification.json")
    assert meta["source"] == sources()
    assert cert["source_sha256"] == meta["source_sha256"]
    for name, expected in cert["files"].items():
        assert sha(output / name) == expected, name
    gate = read(output / "gate.json")
    groups, proofs, matched, rejected = {}, {}, 0, 0
    for row in gate["rows"]:
        key = (row["scenario"], int(row["seed"]))
        groups.setdefault(key, []).append(row)
        if row["proof_sha256"] not in proofs:
            with gzip.open(output / "proofs" / (row["proof_sha256"] + ".gz"), "rt") as file:
                frames = [json.loads(line) for line in file]
            proofs[row["proof_sha256"]] = validate_frames(frames, row["physical"], row["parameters"], int(row["seed"]))
            if len(frames) > 10 and not rejected:
                for field in ("choice", "job", "observation"):
                    bad = copy.deepcopy(frames)
                    if field == "choice":
                        bad[0]["choice"] = "short"
                    elif field == "job":
                        bad[0]["jobs"][0]["durations"][0] += 0.125
                    else:
                        target = next(r for r in bad if r["completed"])
                        next(o for o in target["observed"] if o["id"] == target["completed"][0])["state"] = "active"
                    try:
                        validate_frames(bad, row["physical"], row["parameters"], int(row["seed"]))
                    except AssertionError:
                        rejected += 1
                    else:
                        raise AssertionError("Corrupt feedback proof accepted")
        if row["engine"] != "python":
            assert "site-packages/simulatte_native/_bin/" in row["origin"]
    for members in groups.values():
        assert {r["engine"] for r in members} == {"python", "full", "bounded"}
        first = members[0]
        for row in members[1:]:
            for name in ("physical", "proof_sha256", "physical_sha256"):
                assert row[name] == first[name], (first["scenario"], name)
            assert row["fingerprint"]["kpis"] == first["fingerprint"]["kpis"]
        matched += 1
    originals = {r["request"]: r for r in gate["rows"]}
    assert gate["sources_deleted"]
    for row in gate["reproductions"]:
        first = originals[row["request"]]
        assert row["attempt"] != first["attempt"]
        for name in ("fingerprint", "physical", "native", "artifact_sha256"):
            assert row[name] == first[name]
    # The feedback-disabled twin must change both admitted work and decisions.
    for policy in ("fifo", "spt"):
        on = next(r for r in gate["rows"] if r["scenario"] == "long-" + policy and int(r["seed"]) == 100)
        off = next(r for r in gate["rows"] if r["scenario"] == "fixed-" + policy)
        for field in ("jobs_sha256", "decisions_sha256"):
            assert on["physical"]["controller"][field] != off["physical"]["controller"][field]
        for field in ("blocked", "short", "normal", "tight", "relaxed", "pool_polls"):
            assert on["physical"]["controller"][field] > 0
    memory = read(output / "rows.json")
    paired = {}
    for row in memory:
        value = row["result"]
        key = (row["ticks"], row["seed"])
        paired.setdefault(key, []).append(row)
        for diag in row["diagnostics"]:
            assert diag["max_owned"] <= row["parameters"]["hard_limit"]
            assert diag["max_callback_bytes"] < 8192 and diag["entities"] == 2
            stats = diag["storage"]
            assert stats["admitted"] == value["physical"]["controller"]["cursor"]
            outstanding = sum(o["state"] != "finished" for o in value["physical"]["final_observed"])
            assert stats["completed"] == stats["admitted"] - outstanding
            if row["engine"] == "bounded":
                assert stats["native_bytes"] < 131072 and stats["retained_jobs"] == outstanding
                assert stats["forgotten"] == stats["completed"] == diag["expired_reads"] > 0
            else:
                assert stats["retained_jobs"] == stats["admitted"] and diag["expired_reads"] == 0
        assert len(row["parent_checkpoints"]) == 1 + len(row["repetitions"])
        if row["repetitions"]:
            assert row["parent_checkpoints"][-1] - row["parent_checkpoints"][0] < 16 * 2**20
        for repeated in row["repetitions"]:
            assert repeated["attempt"] != value["attempt"]
            for field in ("fingerprint", "physical", "native", "artifact_sha256"):
                assert repeated[field] == value[field]
    for members in paired.values():
        base = members[0]["result"]
        for row in members[1:]:
            assert row["result"]["physical"] == base["physical"]
            assert row["result"]["fingerprint"]["kpis"] == base["fingerprint"]["kpis"]
            twins = [r for r in members if r["engine"] == row["engine"]]
            for twin in twins:
                for field in ("fingerprint", "physical", "native", "artifact_sha256"):
                    assert row["result"][field] == twin["result"][field]
    stable = [r for r in memory if not r["extra_queries"] and not r["repetitions"]]
    table = []
    for ticks in sorted({r["ticks"] for r in stable}):
        entry = dict(ticks=ticks)
        for mode in ("full", "bounded"):
            selected = [r for r in stable if r["engine"] == mode and r["ticks"] == ticks]
            entry[mode] = dict(
                jobs=statistics.median(r["result"]["physical"]["controller"]["cursor"] for r in selected),
                tree_mib=statistics.median(r["usage"]["tree_peak_rss"] for r in selected) / 2**20,
                child_mib=statistics.median(max(d["peak_rss"] for d in r["diagnostics"]) for r in selected) / 2**20,
                parent_mib=statistics.median(r["usage"]["parent_peak_rss"] for r in selected) / 2**20,
                wall_seconds=statistics.median(r["usage"]["wall_seconds"] for r in selected),
            )
        table.append(entry)
    if len(table) == 3:
        for field, limit in (("tree_mib", 24), ("child_mib", 16), ("parent_mib", 16)):
            values = [r["bounded"][field] for r in table]
            assert max(values) - min(values) < limit, (field, values)
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
        physical_scenarios=matched,
        physical_boundaries=sum(members[0]["physical"]["controller"]["tick"] for members in groups.values()),
        proofs=len(proofs),
        decision_rows=sum(v["ticks"] for v in proofs.values()),
        source_deleted_reproductions=len(gate["reproductions"]),
        memory_cases=len(memory),
        preserved_base_files=preserved,
        deliberate_corruptions_rejected=rejected,
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
            xlabel="Feedback decision ticks",
            ylabel="Coordinator tree sampled peak RSS (MiB)",
            title="Live feedback admission · installed provider · macOS/CPython",
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
    args = p.parse_args()
    audit(args.output, args.plot)
