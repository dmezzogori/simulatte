"""Serial installed study with predeclared balanced paired orders and source freeze."""

import argparse
import itertools
import json
import os
from pathlib import Path
import platform
import random
import shutil
import sys
import tempfile
import zipfile

from .common import ROOT, REPO, BASE, COPIES, assert_copies, digest, sha, sources
from prototypes.native_retention_integration.run import execute, monitor


def schedule(quick=False):
    rng = random.Random(20261104)
    blocks = [
        (f, q, s)
        for f in ("fixed", "coupled")
        for q in ((0, 16, 1) if quick else (0, 64, 16, 1))
        for s in range(100, 101 if quick else 106)
    ]
    rng.shuffle(blocks)
    orders = {}
    for fixture in ("fixed", "coupled"):
        for every in (0, 64, 16, 1):
            permutations = list(itertools.permutations(("helper", "prepared", "unprepared")))
            rng.shuffle(permutations)
            for seed, order in zip(range(100, 106), permutations, strict=True):
                orders[fixture, every, seed] = order
    rows = []
    for block, (fixture, every, seed) in enumerate(blocks):
        for order, mode in enumerate(orders[fixture, every, seed]):
            rows.append(
                dict(block=block, order=order, fixture=fixture, every=every, seed=seed, mode=mode, disabled=False)
            )
    for fixture in ("fixed", "coupled"):
        for seed in range(100, 101 if quick else 103):
            rows.append(
                dict(block=len(blocks), order=0, fixture=fixture, every=0, seed=seed, mode="helper", disabled=True)
            )
    return rows


def main(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    assert_copies()
    frozen = sources()
    core, native = args.core_wheel.resolve(), args.native_wheel.resolve()
    with zipfile.ZipFile(core) as archive:
        for name, expected in frozen.items():
            if name.startswith("src/simulatte/"):
                assert __import__("hashlib").sha256(archive.read(name.removeprefix("src/"))).hexdigest() == expected
    with zipfile.ZipFile(native) as archive:
        marker = json.loads(archive.read("simulatte_native/_bin/manifest.json"))
        assert marker["sources"] == json.loads((REPO / "native/sources.json").read_text())
        for name in frozen:
            if name.startswith("native/src/"):
                assert archive.read(name.removeprefix("native/src/")) == (REPO / name).read_bytes()
    workspace = Path(tempfile.mkdtemp(prefix="simulatte-identity-usage-"))
    uv = shutil.which("uv")
    python = workspace / "venv/bin/python"
    execute([uv, "venv", workspace / "venv", "--python", args.python], output / "venv.log")
    execute([uv, "pip", "install", "--python", python, "--link-mode=copy", core, native], output / "install.log")
    for name in (*COPIES, "probe.py", "query_probe.py", "measurement.py"):
        shutil.copyfile(ROOT / name, workspace / name)
    runtime = workspace / "runtime-path"
    runtime.mkdir()
    (runtime / "git").symlink_to(shutil.which("git"))
    env = dict(os.environ, PATH=str(runtime), CC="/no-runtime-compiler")
    meta = dict(
        base=BASE,
        source=frozen,
        source_sha256=digest(frozen),
        core=dict(name=core.name, sha256=sha(core)),
        native=dict(name=native.name, sha256=sha(native)),
        host=platform.platform(),
        workspace=str(workspace),
        driver_python=sys.version,
        quick=args.quick,
        study=args.study,
        ticks=512 if args.quick else 32768,
        monitor_sampling_seconds=0.04,
        compiler_unavailable_at_runtime=True,
    )
    (output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")

    def invoke(name, command, extra_env):
        assert sources() == frozen and sha(core) == meta["core"]["sha256"] and sha(native) == meta["native"]["sha256"]
        work = workspace / name
        work.mkdir()
        diagnostics = work / "diagnostics"
        diagnostics.mkdir()
        identities = work / "identity-diagnostics"
        identities.mkdir()
        child_env = dict(
            env,
            SIMULATTE_FIXED_DIAGNOSTICS=str(diagnostics),
            SIMULATTE_FEEDBACK_DIAGNOSTICS=str(diagnostics),
            SIMULATTE_IDENTITY_DIAGNOSTICS=str(identities),
            **extra_env,
        )
        result = work / "result.json"
        usage = monitor(
            [python, "-I", workspace / command[0], result, *command[1:]],
            child_env,
            output / (name + ".log"),
            cwd=workspace,
        )
        value = json.loads(result.read_text())
        value.update(usage=usage, diagnostics=[json.loads(p.read_text()) for p in sorted(diagnostics.glob("*.json"))])
        value["identity_diagnostics"] = [json.loads(p.read_text()) for p in sorted(identities.glob("*.json"))]
        (output / (name + ".json")).write_text(json.dumps(value, indent=2) + "\n")
        return value, work

    if args.study == "live":
        cases = schedule(args.quick)
        (output / "schedule.json").write_text(json.dumps(cases, indent=2) + "\n")
        rows, selected = [], {}
        for index, case in enumerate(cases):
            name = f"case-{index:03d}"
            value, work = invoke(
                name,
                [
                    "probe.py",
                    "--evidence",
                    output / "evidence",
                    "--fixture",
                    case["fixture"],
                    "--seed",
                    str(case["seed"]),
                    "--ticks",
                    str(meta["ticks"]),
                ],
                dict(
                    SIMULATTE_PROJECTION_MODE=case["mode"],
                    SIMULATTE_IDENTITIES_QUERY_EVERY=str(case["every"]),
                    SIMULATTE_IDENTITIES_DISABLE=str(int(case["disabled"])),
                ),
            )
            value.update(case=case, name=name)
            assert value["parent_pid"] == value["usage"]["parent_pid"]
            rows.append(value)
            (output / "rows.json").write_text(json.dumps(rows, indent=2) + "\n")
            if case["mode"] == "helper" and case["every"] == 16 and case["seed"] == 100:
                selected[case["fixture"]] = value, work
            print(
                json.dumps(
                    dict(
                        done=index + 1,
                        of=len(cases),
                        fixture=case["fixture"],
                        every=case["every"],
                        mode=case["mode"],
                        seconds=value["cold_seconds"],
                    )
                ),
                flush=True,
            )
        for fixture, (original, work) in selected.items():
            value, _ = invoke(
                "reproduce-" + fixture,
                [
                    "probe.py",
                    "--evidence",
                    output / "evidence",
                    "--fixture",
                    fixture,
                    "--reproduce-request",
                    original["result"]["request"],
                    "--project",
                    work / "project",
                ],
                dict(
                    SIMULATTE_PROJECTION_MODE="helper",
                    SIMULATTE_IDENTITIES_QUERY_EVERY="16",
                    SIMULATTE_IDENTITIES_DISABLE="0",
                ),
            )
            for key in ("artifacts", "fingerprint", "data"):
                assert value["result"][key] == original["result"][key]
            assert value["result"]["attempt"] != original["result"]["attempt"]
    else:
        cells = [
            (f, r, at)
            for f in ("fixed", "coupled")
            for r in ("full", "bounded")
            for at in ((65,) if args.quick else (65, 4096))
        ]
        for index, (fixture, retention, at) in enumerate(cells):
            name = f"query-{index:02d}"
            _, work = invoke(
                name,
                ["query_probe.py", "--fixture", fixture, "--retention", retention, "--at", str(at)],
                dict(
                    SIMULATTE_PROJECTION_MODE="helper",
                    SIMULATTE_IDENTITIES_QUERY_EVERY="0",
                    SIMULATTE_IDENTITIES_DISABLE="0",
                ),
            )
            shutil.copytree(work / "query", output / (name + "-query"))
            print(
                json.dumps(dict(done=index + 1, of=len(cells), fixture=fixture, retention=retention, at=at)), flush=True
            )
    assert sources() == frozen
    files = {p.relative_to(output).as_posix(): sha(p) for p in sorted(output.rglob("*")) if p.is_file()}
    (output / "verification.json").write_text(
        json.dumps(dict(source_sha256=meta["source_sha256"], files=files), indent=2) + "\n"
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--core-wheel", type=Path, required=True)
    p.add_argument("--native-wheel", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--python", default="3.14")
    p.add_argument("--study", choices=("live", "query"), required=True)
    p.add_argument("--quick", action="store_true")
    main(p.parse_args())
