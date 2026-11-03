"""Audit the optional-provider gate against its exact source and evidence bytes."""

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
BASE = "97fc6339c3c777a00bb39e0e6731917ff60cb0ed"
FORMATTED = (
    "src/simulatte/execution/bootstrap.py",
    "src/simulatte/execution/coordinator.py",
    "src/simulatte/execution/driver.py",
    "src/simulatte/execution/imports.py",
    "src/simulatte/execution/native_assets.py",
    "tests/execution/test_native_assets_integration.py",
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def main():
    spec = importlib.util.spec_from_file_location("package_matrix", ROOT / "native/package_matrix.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    files = module.sources()
    source_sha = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    seen, direct, core, totals = {}, None, None, [0, 0, 0]
    for host in ("macos", "linux"):
        directory = EVIDENCE / host
        metadata = read(directory / "metadata.json")
        assert metadata["base"] == BASE
        assert metadata["files"] == files
        assert metadata["source_sha256"] == source_sha
        core = core or metadata["core_sha256"]
        assert metadata["core_sha256"] == core
        certificate = read(directory / "verification.json")
        assert certificate["source_sha256"] == source_sha
        for name, expected in certificate["evidence"].items():
            assert sha(directory / name) == expected, (host, name)
        assert len(certificate["rows"]) == 3
        for row in certificate["rows"]:
            abi = "cp" + row["python"].replace(".", "")
            case = directory / abi
            for name, expected in read(case / "verification.json").items():
                assert sha(case / name) == expected, (host, abi, name)
            installed, facts = read(case / "installed.json"), read(case / "metadata.json")
            assert facts["native_sha256"] == row["wheel_sha256"]
            assert facts["core_sha256"] == core
            assert facts["native_extra_installs_provider"] and facts["compiler_unavailable_at_runtime"]
            assert facts["runner_sha256"] == sha(ROOT / "native/check_install.py")
            assert facts["probe_sha256"] == sha(ROOT / "native/install_probe.py")
            assert len(installed["rows"]) == row["managed_runs"] == 8
            assert len(installed["source_deleted_reproductions"]) == row["reproductions"] == 4
            direct = direct or installed["direct"]
            assert installed["direct"] == direct
            for run in installed["rows"]:
                key = run["configuration"], run["seed"]
                assert seen.setdefault(key, run["kpis"]) == run["kpis"], (host, abi, key)
            rejections = read(case / "rejections.json")
            assert set(rejections) == {
                "abi",
                "api",
                "manifest",
                "binary",
                "dependency",
                "runtime-mutation",
                "wheel-tag",
            }
            assert all(value["rejected"] for value in rejections.values())
            totals = [a + b for a, b in zip(totals, (8, 4, 7))]
    for name, expected in read(ROOT / "native/sources.json").items():
        native = ROOT / "native" / name
        original = "prototypes/native_engine_dynamic/csrc/" + native.name
        assert sha(native) == expected
        assert native.read_bytes() == (ROOT / original).read_bytes() == git("show", BASE + ":" + original)
    history = git("ls-tree", "-rz", BASE, "--", "prototypes/")
    historical = 0
    for record in history.split(b"\0"):
        if not record:
            continue
        prefix, path = record.split(b"\t", 1)
        _, kind, blob = prefix.split()
        assert kind == b"blob"
        data = (ROOT / path.decode()).read_bytes()
        assert hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == blob.decode(), path
        historical += 1
    for name in FORMATTED:
        assert ast.dump(ast.parse(git("show", BASE + ":" + name))) == ast.dump(ast.parse((ROOT / name).read_bytes())), (
            name
        )
    checks = read(EVIDENCE / "checks.json")
    assert checks["source_sha256"] == source_sha
    assert all(row["exit_code"] == 0 for row in checks["commands"])
    for row in checks["regressions"]:
        assert row["exit_code"] == 0 and row["coverage_percent"] >= 99
        assert sha(EVIDENCE / row["log"]) == row["sha256"]
        log = (EVIDENCE / row["log"]).read_text()
        assert "Required test coverage of 99% reached" in log
        assert "FAIL Required test coverage" not in log
        assert str(row["passed"]) + " passed" in log
    for name, expected in checks["evidence"].items():
        assert sha(EVIDENCE / name) == expected, name
    assert totals == [48, 24, 42]
    print(
        json.dumps(
            dict(
                passed=True,
                source_sha256=source_sha,
                source_files=len(files),
                managed_runs=totals[0],
                source_deleted_reproductions=totals[1],
                negative_rejections=totals[2],
                exact_cross_platform_abi_kpis=True,
                unchanged_historical_files=historical,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
