"""Independent proof reconstruction, identity comparisons and resource audit."""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess

from simulatte._wire import canonical_pack, freeze
from prototypes.native_feedback_retention.analysis import validate_frames
from prototypes.native_fixed_admission.analysis import validate_controls, validate_reports
from . import projection
from .run import sources

BASE = "fb55e0cc6368cddc540894dffbef39bd3ed4cb00"
REPO = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def actions_from_proof(rows):
    # Independently normalize the recorded actual inputs; do not call Actions.update.
    rolling = hashlib.sha256(b"admission-actuation-v1\0")
    cursor = count = 0
    for row in rows:
        jobs = []
        for actual in row["jobs"]:
            if "id" in actual:
                assert actual["id"] == cursor
            jobs.append(dict(id=cursor, **{k: actual[k] for k in ("arrival", "route", "durations", "due")}))
            cursor += 1
        data = canonical_pack(freeze(dict(t=float(row["t"]), tick=row["tick"], target=row["target"], jobs=jobs)))
        rolling.update(len(data).to_bytes(8, "little"))
        rolling.update(data)
        count += 1
    return dict(schema="admission-actuation-v1", rows=count, digest=rolling.hexdigest())


def same(a, b, identity=True):
    for field in ("fingerprint", "physical", "reporting", "native", "proof_sha256"):
        assert a[field] == b[field], field
    x, y = dict(a["artifact_sha256"]), dict(b["artifact_sha256"])
    if not identity:
        x.pop("identities.json", None)
        y.pop("identities.json", None)
    assert x == y
    if identity:
        assert a["identities"] == b["identities"]


def audit(output):
    meta, cert = read(output / "metadata.json"), read(output / "verification.json")
    assert meta["source"] == sources()
    assert cert["source_sha256"] == meta["source_sha256"]
    for name, expected in cert["files"].items():
        assert sha(output / name) == expected, name
    gate, memory = read(output / "gate.json"), read(output / "rows.json")
    groups, window_groups = {}, {}
    proof_count, boundaries, actions = set(), 0, {}
    for row in gate["rows"]:
        params = row["parameters"]
        fixture, engine = params["fixture"], params["engine"]
        groups.setdefault((fixture, params["policy"], row["seed"], row["warmup"]), []).append(row)
        window_groups.setdefault((fixture, params["policy"], row["seed"], engine), []).append(row)
        name = "controls" if fixture == "fixed" else "frames"
        proof = row["proof_sha256"][name]
        with gzip.open(output / "proofs" / (proof + ".gz"), "rt") as file:
            frames = [json.loads(line) for line in file]
        if fixture == "fixed":
            validate_controls(frames, row["physical"], params, row["seed"])
            with gzip.open(output / "proofs" / (row["proof_sha256"]["reports"] + ".gz"), "rt") as file:
                validate_reports([json.loads(line) for line in file], row["reporting"])
        else:
            validate_frames(frames, row["physical"], params, row["seed"])
        actuation = actions_from_proof(frames)
        assert row["identities"]["actions"] == actuation
        actions[proof] = actuation
        proof_count.update(row["proof_sha256"].values())
        if engine != "python":
            assert "site-packages/simulatte_native/_bin/" in row["origin"]
            for phase in ("initial", "final"):
                projection.validate(row["identities"][phase])
            assert row["identities"]["final"]["authority"] == row["native"]
            assert row["identities"]["final"]["evidence"]["trajectory"]["actions"] == actuation
    for members in groups.values():
        assert {r["parameters"]["engine"] for r in members} == {"python", "full", "bounded"}
        first = members[0]
        for row in members[1:]:
            for field in ("physical", "reporting", "proof_sha256"):
                assert row[field] == first[field], field
            assert row["fingerprint"]["kpis"] == first["fingerprint"]["kpis"]
            assert row["identities"]["actions"] == first["identities"]["actions"]
        native = [r for r in members if r["native"] is not None]
        assert native[0]["identities"]["final"]["reporting"] == native[1]["identities"]["final"]["reporting"]
        boundaries += first["physical"]["controller"]["tick"]
    stable, coupled_changed, initial_same = 0, 0, 0
    for (fixture, _, seed, engine), members in window_groups.items():
        assert {r["warmup"] for r in members} == {0, 10, 40}
        if engine == "python":
            continue
        views = [r["identities"] for r in members]
        assert len({projection.digest(v["initial"]["trajectory_control"]) for v in views}) == 1
        initial_same += 1
        for phase in ("initial", "final"):
            for component in ("reporting", "reproduction_state"):
                assert len({projection.digest(v[phase][component]) for v in views}) == 3
            for other in views[1:]:
                try:
                    projection.require_reproduction(views[0][phase], other[phase])
                except ValueError:
                    pass
                else:
                    raise AssertionError("Different reporting windows accepted for reproduction")
        if fixture == "fixed":
            assert len({projection.digest(v["final"]["trajectory_control"]) for v in views}) == 1
            assert len({projection.digest(v["actions"]) for v in views}) == 1
            stable += 1
        else:
            for i, a in enumerate(views):
                for b in views[i + 1 :]:
                    if a["actions"] != b["actions"]:
                        assert a["final"]["trajectory_control"] != b["final"]["trajectory_control"]
            changed = len({projection.digest(v["actions"]) for v in views}) > 1
            if seed == 100:
                assert changed
            coupled_changed += changed
    originals = {r["request"]: r for r in gate["rows"]}
    assert gate["sources_deleted"]
    for row in gate["reproductions"]:
        first = originals[row["request"]]
        same(first, row)
        assert first["attempt"] != row["attempt"]
        if first["native"] is not None:
            projection.require_reproduction(first["identities"]["final"], row["identities"]["final"])
    for control in gate["controls"]:
        same(originals[control["reference"]], control["result"], identity=control["kind"] == "dense")
    attempts = [r["attempt"] for r in gate["rows"] + gate["reproductions"]]
    attempts += [c["result"]["attempt"] for c in gate["controls"]]
    resource_groups = {}
    for row in memory:
        value = row["result"]
        resource_groups.setdefault((row["fixture"], row["ticks"], row["seed"]), []).append(row)
        attempts.extend(v["attempt"] for v in [value, *row["repetitions"]])
        for diag in row["diagnostics"]:
            assert diag["max_owned"] <= 12 and diag["max_callback_bytes"] < 8192 and diag["entities"] == 2
            stats = diag["storage"]
            assert stats["admitted"] == value["physical"]["controller"]["cursor"]
            assert stats["native_bytes"] < 131072
            assert stats["forgotten"] == stats["completed"] == diag["expired_reads"] > 0
            assert stats["retained_jobs"] == sum(o["state"] != "finished" for o in value["physical"]["final_observed"])
        if not row["disabled"]:
            assert len(row["identity_diagnostics"]) == len(row["diagnostics"])
            for diag in row["identity_diagnostics"]:
                assert diag["max_raw_bytes"] < 65536 and diag["max_view_bytes"] < 16384
                assert diag["artifact_bytes"] < 32768 and diag["queries"] >= 2
            projection.validate(value["identities"]["final"])
        else:
            assert not row["identity_diagnostics"] and value["identities"] is None
        if row["repetitions"]:
            assert row["parent_checkpoints"][-1] - row["parent_checkpoints"][0] < 16 * 2**20
        for other in row["repetitions"]:
            same(value, other)
            assert value["attempt"] != other["attempt"]
    assert len(attempts) == len(set(attempts))
    for members in resource_groups.values():
        for row in members[1:]:
            same(members[0]["result"], row["result"], identity=not (members[0]["disabled"] or row["disabled"]))
    table = []
    for fixture in ("fixed", "coupled"):
        selected = [
            r
            for r in memory
            if r["fixture"] == fixture and r["query_every"] == 64 and not r["disabled"] and not r["repetitions"]
        ]
        entries = []
        for ticks in sorted({r["ticks"] for r in selected}):
            group = [r for r in selected if r["ticks"] == ticks]
            entry = dict(
                fixture=fixture,
                ticks=ticks,
                jobs=statistics.median(r["result"]["physical"]["controller"]["cursor"] for r in group),
                tree_mib=statistics.median(r["usage"]["tree_peak_rss"] for r in group) / 2**20,
                parent_mib=statistics.median(r["usage"]["parent_peak_rss"] for r in group) / 2**20,
                child_mib=statistics.median(max(d["peak_rss"] for d in r["diagnostics"]) for r in group) / 2**20,
                wall_seconds=statistics.median(r["usage"]["wall_seconds"] for r in group),
            )
            entries.append(entry)
            table.append(entry)
        for field, limit in (("tree_mib", 24), ("child_mib", 16), ("parent_mib", 16)):
            values = [e[field] for e in entries]
            assert max(values) - min(values) < limit, (fixture, field, values)
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
        primary_cases=len(gate["rows"]),
        physical_scenarios=len(groups),
        physical_boundaries=boundaries,
        proof_streams=len(proof_count),
        fixed_trajectory_invariant_groups=stable,
        coupled_trajectory_changed_groups=coupled_changed,
        initial_equal_trajectory_groups=initial_same,
        source_deleted_reproductions=len(gate["reproductions"]) + sum(len(r["repetitions"]) for r in memory),
        baseline_controls=sum(c["kind"] == "baseline" for c in gate["controls"]),
        dense_query_controls=sum(c["kind"] == "dense" for c in gate["controls"]),
        memory_cases=len(memory),
        fresh_attempts=len(attempts),
        preserved_base_files=preserved,
        stable_summary=table,
    )
    (output / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    audit(parser.parse_args().output)
