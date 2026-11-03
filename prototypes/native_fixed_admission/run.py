"""Serial installed feedback/memory gate; canonical provider sources stay unchanged."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import shutil
import subprocess
import tempfile
import zipfile

from prototypes.native_retention_integration.run import monitor, execute

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources():
    paths = [
        *REPO.glob("src/simulatte/**/*.py"),
        *REPO.glob("native/**/*.py"),
        *REPO.glob("native/csrc/*"),
        *ROOT.glob("*.py"),
        *ROOT.glob("tests/*.py"),
        ROOT / "README.md",
        REPO / "specs/research/2026-11-03-native-fixed-admission-plan.md",
        REPO / "prototypes/native_retention_integration/run.py",
        REPO / "pyproject.toml",
        REPO / "uv.lock",
        REPO / "native/sources.json",
    ]
    return {
        p.relative_to(REPO).as_posix(): sha(p)
        for p in sorted(set(paths))
        if p.is_file() and "__pycache__" not in p.parts
    }


def main(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    workspace = Path(tempfile.mkdtemp(prefix="simulatte-fixed-admission-installed-"))
    core, native = args.core_wheel.resolve(), args.native_wheel.resolve()
    frozen = sources()
    with zipfile.ZipFile(core) as archive:
        for name, h in frozen.items():
            if name.startswith("src/simulatte/"):
                assert hashlib.sha256(archive.read(name.removeprefix("src/"))).hexdigest() == h, name
    with zipfile.ZipFile(native) as archive:
        marker = json.loads(archive.read("simulatte_native/_bin/manifest.json"))
        assert marker["provider_api"] == 2 and marker["sources"] == json.loads(
            (REPO / "native/sources.json").read_text()
        )
    uv = shutil.which("uv")
    assert uv
    venv = workspace / "venv"
    python = venv / "bin/python"
    execute([uv, "venv", venv, "--python", args.python], output / "venv.log")
    execute([uv, "pip", "install", "--python", python, "--link-mode=copy", core, native], output / "install.log")
    for name in ("plant.py", "probe.py"):
        shutil.copyfile(ROOT / name, workspace / name)
    runtime = workspace / "runtime-path"
    runtime.mkdir()
    (runtime / "git").symlink_to(shutil.which("git"))
    env = dict(os.environ, PATH=str(runtime), CC="/no-runtime-compiler")
    meta = dict(
        base=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        source=frozen,
        source_sha256=hashlib.sha256(json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        core=dict(name=core.name, sha256=sha(core)),
        native=dict(name=native.name, sha256=sha(native)),
        workspace=str(workspace),
        host=platform.platform(),
        sizes=args.sizes,
        seeds=list(range(100, 100 + args.repeats)),
        sampling_seconds=0.04,
    )
    (output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")

    def invoke(name, extra=(), queries=False):
        assert sources() == frozen and sha(core) == meta["core"]["sha256"] and sha(native) == meta["native"]["sha256"]
        work = workspace / name
        work.mkdir()
        diag = work / "diagnostics"
        diag.mkdir()
        result = work / "result.json"
        child_env = dict(env, SIMULATTE_FIXED_DIAGNOSTICS=str(diag), SIMULATTE_FIXED_EXTRA_QUERIES=str(int(queries)))
        usage = monitor(
            [python, "-I", workspace / "probe.py", result, "--evidence", output / "proofs", *extra],
            child_env,
            output / (name + ".log"),
            cwd=workspace,
        )
        value = json.loads(result.read_text())
        value.update(
            usage=usage,
            extra_queries=queries,
            diagnostics=[json.loads(p.read_text()) for p in sorted(diag.glob("*.json"))],
        )
        assert value["coordinator_pid"] == usage["parent_pid"]
        (output / (name + ".json")).write_text(json.dumps(value, indent=2) + "\n")
        return value

    gate = invoke("gate")
    assert len(gate["rows"]) == 60 and len(gate["reproductions"]) == 14
    cases = [
        (mode, n, seed, False, 0, 10)
        for mode in ("full", "bounded")
        for n in args.sizes
        for seed in range(100, 100 + args.repeats)
    ]
    if not args.quick:
        cases += [(mode, args.sizes[0], 100, True, 0, 10) for mode in ("full", "bounded")]
        cases += [("bounded", args.sizes[-2], 101, False, 2, 10)]
    if not args.quick:
        cases += [("bounded", args.sizes[-2], 101, False, 0, w) for w in (0, 40)]
    random.Random(20261103).shuffle(cases)
    rows = []
    for i, (mode, n, seed, queries, repeats, warmup) in enumerate(cases):
        value = invoke(
            f"case-{i:02d}",
            [
                "--case",
                "--engine",
                mode,
                "--ticks",
                str(n),
                "--seed",
                str(seed),
                "--reproduce-count",
                str(repeats),
                "--warmup",
                str(warmup),
            ],
            queries,
        )
        rows.append(value)
        (output / "rows.json").write_text(json.dumps(rows, indent=2) + "\n")
        print(
            json.dumps(
                dict(
                    case=i + 1,
                    of=len(cases),
                    engine=mode,
                    ticks=n,
                    jobs=value["result"]["control"]["controller"]["cursor"],
                    tree_mib=value["usage"]["tree_peak_rss"] / 2**20,
                )
            ),
            flush=True,
        )
    assert sources() == frozen
    cert = dict(
        source_sha256=meta["source_sha256"],
        files={p.relative_to(output).as_posix(): sha(p) for p in sorted(output.rglob("*")) if p.is_file()},
    )
    (output / "verification.json").write_text(json.dumps(cert, indent=2) + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--core-wheel", type=Path, required=True)
    p.add_argument("--native-wheel", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--python", default="3.14")
    p.add_argument("--sizes", type=int, nargs="+", default=[4096, 32768, 262144])
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--quick", action="store_true")
    main(p.parse_args())
