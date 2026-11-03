"""Frozen installed-wheel Coordinator gate and external process-tree memory monitor."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import random
import shutil
import subprocess
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources():
    paths = [*REPO.glob("src/simulatte/**/*.py"), *REPO.glob("native/**/*.py"), *REPO.glob("native/csrc/*")]
    paths += [*ROOT.glob("*.py"), *ROOT.glob("tests/*"), ROOT / "README.md"]
    paths += [
        REPO / name
        for name in (
            "pyproject.toml",
            "uv.lock",
            "native/pyproject.toml",
            "native/sources.json",
            "native/README.md",
            "docs/guides/native-engine.md",
            "docs/api/experimental.md",
            ".github/workflows/ci.yml",
            "tests/experimental/test_native.py",
            "tests/experimental/test_native_retention.py",
            "specs/research/2026-11-03-native-retention-integration-plan.md",
        )
    ]
    return {
        p.relative_to(REPO).as_posix(): sha(p)
        for p in sorted(set(paths))
        if p.is_file() and "__pycache__" not in p.parts
    }


def execute(command, log):
    p = subprocess.run(list(map(str, command)), text=True, capture_output=True, timeout=300)
    log.write_text(p.stdout + p.stderr)
    assert p.returncode == 0, p.stdout + p.stderr


def processes():
    raw = subprocess.check_output(["/bin/ps", "-axo", "pid=,ppid=,rss="], text=True)
    return {
        int(pid): (int(parent), int(rss) * 1024) for pid, parent, rss in (line.split() for line in raw.splitlines())
    }


def monitor(command, env, log, timeout=600, *, cwd=None):
    started = time.perf_counter()
    with log.open("w") as file:
        p = subprocess.Popen(list(map(str, command)), stdout=file, stderr=subprocess.STDOUT, env=env, cwd=cwd)
        peaks, tree_peak, parent_peak, count, descendants_peak = {}, 0, 0, 0, 0
        try:
            while p.poll() is None:
                if time.perf_counter() - started > timeout:
                    p.kill()
                    raise TimeoutError("Coordinator gate timeout")
                table = processes()
                members = {p.pid}
                for _ in range(12):
                    expanded = members | {pid for pid, (parent, _) in table.items() if parent in members}
                    if expanded == members:
                        break
                    members = expanded
                current = {pid: table[pid][1] for pid in members if pid in table}
                tree_peak = max(tree_peak, sum(current.values()))
                parent_peak = max(parent_peak, current.get(p.pid, 0))
                descendants_peak = max(descendants_peak, len(current) - 1)
                for pid, rss in current.items():
                    peaks[str(pid)] = max(peaks.get(str(pid), 0), rss)
                count += 1
                time.sleep(0.04)
            assert p.returncode == 0, log.read_text()
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
    return dict(
        parent_pid=p.pid,
        tree_peak_rss=tree_peak,
        parent_peak_rss=parent_peak,
        per_pid_peak_rss=peaks,
        max_descendants=descendants_peak,
        samples=count,
        wall_seconds=time.perf_counter() - started,
    )


def main(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    workspace = Path(tempfile.mkdtemp(prefix="simulatte-retention-installed-"))
    uv = shutil.which("uv")
    assert uv
    core, native = args.core_wheel.resolve(), args.native_wheel.resolve()
    frozen = sources()
    with zipfile.ZipFile(core) as archive:
        for name, expected in frozen.items():
            if name.startswith("src/simulatte/"):
                assert hashlib.sha256(archive.read(name.removeprefix("src/"))).hexdigest() == expected, name
    with zipfile.ZipFile(native) as archive:
        marker = json.loads(archive.read("simulatte_native/_bin/manifest.json"))
        assert marker["provider_api"] == 2
        assert marker["sources"] == json.loads((REPO / "native/sources.json").read_text())
        assert (
            archive.read("simulatte_native/__init__.py")
            == (REPO / "native/src/simulatte_native/__init__.py").read_bytes()
        )
    venv = workspace / "venv"
    execute([uv, "venv", venv, "--python", args.python], output / "venv.log")
    python = venv / "bin/python"
    execute([uv, "pip", "install", "--python", python, "--link-mode=copy", core, native], output / "install.log")
    for name in ("probe.py", "producer.py"):
        shutil.copyfile(ROOT / name, workspace / name)
    runtime = workspace / "runtime-path"
    runtime.mkdir()
    git = shutil.which("git")
    assert git
    (runtime / "git").symlink_to(git)
    env = dict(os.environ, PATH=str(runtime), CC="/no-runtime-compiler")
    metadata = dict(
        base=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        source=frozen,
        source_sha256=hashlib.sha256(json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        core=dict(name=core.name, sha256=sha(core)),
        native=dict(name=native.name, sha256=sha(native)),
        workspace=str(workspace),
        python=args.python,
        sizes=args.sizes,
        paired_seeds=args.repeats,
        host_platform=platform.platform(),
        compiler=subprocess.check_output([*shlex.split(os.environ.get("CC", "cc")), "--version"], text=True),
        compiler_unavailable_at_runtime=True,
        sampling_seconds=0.04,
    )
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")

    def invoke(name, arguments, *, python=python, extra_queries=False):
        assert (
            sources() == frozen
            and sha(core) == metadata["core"]["sha256"]
            and sha(native) == metadata["native"]["sha256"]
        )
        work = workspace / name
        work.mkdir()
        diag = work / "diagnostics"
        diag.mkdir()
        result = work / "result.json"
        child_env = dict(
            env, SIMULATTE_NATIVE_MEMORY_DIR=str(diag), SIMULATTE_NATIVE_QUERY_EXTRA=str(int(extra_queries))
        )
        usage = monitor(
            [python, "-I", workspace / "probe.py", result, *arguments],
            child_env,
            output / (name + ".log"),
            cwd=workspace,
        )
        value = json.loads(result.read_text())
        value["usage"] = usage
        value["diagnostics"] = [json.loads(p.read_text()) for p in sorted(diag.glob("*.json"))]
        value["extra_queries"] = extra_queries
        assert value["coordinator_pid"] == usage["parent_pid"]
        (output / (name + ".json")).write_text(json.dumps(value, indent=2) + "\n")
        return value

    gate = invoke("gate", [])
    assert len(gate["rows"]) == 16 and len(gate["reproductions"]) == 8
    assert len({r["attempt"] for r in [*gate["rows"], *gate["reproductions"]]}) == 24
    # Corrupt a private full-copy environment, never uv's cache or the measurement runtime.
    negative = workspace / "negative-venv"
    shutil.copytree(venv, negative, symlinks=True)
    invoke("corruption", ["--corrupt"], python=negative / "bin/python")
    cases = [
        (mode, n, 1.5, 100 + seed, False, 0)
        for mode in ("full", "bounded")
        for n in args.sizes
        for seed in range(args.repeats)
    ]
    if not args.quick:
        cases += [(mode, n, 0.125, 100, False, 0) for mode in ("full", "bounded") for n in (16384, 65536)]
        cases += [(mode, args.sizes[0], 1.5, 100, True, 0) for mode in ("full", "bounded")]
        cases += [("bounded", args.sizes[-2], 1.5, 101, False, 2)]
    random.Random(20261103).shuffle(cases)
    rows = []
    for i, (mode, n, spacing, seed, queries, repeats) in enumerate(cases):
        row = invoke(
            f"case-{i:02d}",
            [
                "--case",
                "--jobs",
                str(n),
                "--retention",
                mode,
                "--spacing",
                str(spacing),
                "--seed",
                str(seed),
                "--reproduce-count",
                str(repeats),
            ],
            extra_queries=queries,
        )
        rows.append(row)
        (output / "rows.json").write_text(json.dumps(rows, indent=2) + "\n")
        print(
            json.dumps(
                dict(
                    case=i + 1,
                    of=len(cases),
                    mode=mode,
                    jobs=n,
                    extra_queries=queries,
                    peak_tree_mib=row["usage"]["tree_peak_rss"] / 2**20,
                )
            ),
            flush=True,
        )
    assert sources() == frozen
    certificate = dict(
        source_sha256=metadata["source_sha256"], files={p.name: sha(p) for p in sorted(output.iterdir()) if p.is_file()}
    )
    (output / "verification.json").write_text(json.dumps(certificate, indent=2) + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--core-wheel", type=Path, required=True)
    p.add_argument("--native-wheel", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--python", default="3.14")
    p.add_argument("--sizes", type=int, nargs="+", default=[16384, 131072, 1048576])
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--quick", action="store_true")
    main(p.parse_args())
