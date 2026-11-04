"""Clean-install/ABI gate, invoked by CI; child probes run outside the checkout."""

import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execute(command, *, env=None, cwd=None, success=True):
    result = subprocess.run(list(map(str, command)), env=env, cwd=cwd, text=True, capture_output=True, timeout=240)
    if success and result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    if not success:
        assert result.returncode, result.stdout
    return result


def rewrite_manifest(python, mutate, *, record=True):
    directory = Path(
        execute([python, "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"]).stdout.strip()
    )
    path = directory / "simulatte_native/_bin/manifest.json"
    value = json.loads(path.read_text())
    mutate(value)
    path.write_text(json.dumps(value) + "\n")
    if not record:
        return
    record = next(directory.glob("simulatte_native-*.dist-info/RECORD"))
    rows = list(csv.reader(io.StringIO(record.read_text())))
    for row in rows:
        if row[0] == "simulatte_native/_bin/manifest.json":
            row[1] = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(path.read_bytes()).digest()).decode().rstrip(
                "="
            )
            row[2] = str(path.stat().st_size)
    with record.open("w", newline="") as file:
        csv.writer(file).writerows(rows)


def main(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    core, native = args.core_wheel.resolve(), args.native_wheel.resolve()
    with zipfile.ZipFile(core) as archive:
        assert not any(name.endswith((".so", ".dylib", ".pyd")) for name in archive.namelist())
        assert (
            "Root-Is-Purelib: true"
            in archive.read(next(n for n in archive.namelist() if n.endswith("/WHEEL"))).decode()
        )
    with zipfile.ZipFile(native) as archive:
        assert (
            "Root-Is-Purelib: false"
            in archive.read(next(n for n in archive.namelist() if n.endswith("/WHEEL"))).decode()
        )
        assert sum("/_bin/" in name and name.endswith((".so", ".dylib")) for name in archive.namelist()) == 2
        assert not any(name.startswith("prototypes/") for name in archive.namelist())
    uv = args.uv or shutil.which("uv")
    assert uv
    with tempfile.TemporaryDirectory(prefix="native-installed-gate-") as tmp:
        root = Path(tmp).resolve()
        venv = root / "venv"
        execute([uv, "venv", venv, "--python", args.python])
        python = venv / "bin/python"
        execute([uv, "pip", "install", "--python", python, core])
        runtime_path = root / "runtime-path"
        runtime_path.mkdir()
        git = shutil.which("git")
        assert git
        (runtime_path / "git").symlink_to(git)
        env = dict(os.environ, CC="/no-runtime-compiler", PATH=str(runtime_path))
        base = execute(
            [
                python,
                "-I",
                "-c",
                """import importlib.util,sys
from simulatte import Environment
from simulatte.experimental.native import NativeStream
from simulatte.experimental.native_identity import compose_native_identity
assert importlib.util.find_spec('simulatte_native') is None
assert callable(compose_native_identity) and 'simulatte_native' not in sys.modules
try: NativeStream(1)
except ImportError as error: assert 'simulatte[native]' in str(error)
else: raise AssertionError('base install unexpectedly provided native code')
assert not any(name.endswith('_nd_scalar') for name in sys.modules)
print('pure base and optional dependency error pass')
""",
            ],
            env=env,
            cwd=root,
        )
        execute(
            [
                uv,
                "pip",
                "install",
                "--python",
                python,
                "--no-index",
                "--find-links",
                native.parent,
                str(core) + "[native]",
            ]
        )
        probe = root / "install_probe.py"
        shutil.copyfile(Path(__file__).with_name("install_probe.py"), probe)
        installed = root / "installed.json"
        execute([python, "-I", probe, installed], env=env, cwd=root)
        shutil.copyfile(installed, output / "installed.json")
        identity_probe = root / "identity_probe.py"
        shutil.copyfile(Path(__file__).with_name("identity_probe.py"), identity_probe)
        fixtures = root / "identity-fixtures"
        fixtures.mkdir()
        repository = Path(__file__).resolve().parents[1]
        frozen = repository / "prototypes/native_prepared_identity"
        fixture_sources = {}
        for name in (
            "plant.py",
            "fixed_case.py",
            "coupled_case.py",
            "reference_projection.py",
            "unprepared_projection.py",
        ):
            source = frozen / name
            shutil.copyfile(source, fixtures / name)
            fixture_sources[str(source.relative_to(repository))] = digest(source)
        shutil.copyfile(frozen / "projection.py", fixtures / "prepared_projection.py")
        fixture_sources["prototypes/native_prepared_identity/projection.py"] = digest(frozen / "projection.py")
        adapter = repository / "prototypes/native_identity_helper/projection.py"
        shutil.copyfile(adapter, fixtures / "projection.py")
        fixture_sources[str(adapter.relative_to(repository))] = digest(adapter)
        identities = root / "identities.json"
        execute([python, "-I", identity_probe, identities, fixtures], env=env, cwd=root)
        shutil.copyfile(identities, output / "identities.json")
        capacity_probe = root / "capacity_probe.py"
        shutil.copyfile(Path(__file__).with_name("capacity_probe.py"), capacity_probe)
        capacities = root / "capacities.json"
        execute([python, "-I", capacity_probe, capacities], env=env, cwd=root)
        shutil.copyfile(capacities, output / "capacities.json")
        observation_probe = root / "observation_probe.py"
        shutil.copyfile(Path(__file__).with_name("observation_probe.py"), observation_probe)
        observations = root / "observations.json"
        execute([python, "-I", observation_probe, observations], env=env, cwd=root)
        shutil.copyfile(observations, output / "observations.json")
        for retention in ("full", "bounded"):
            shutil.copyfile(root / f"observation-{retention}.simtrace", output / f"observation-{retention}.simtrace")
        rejected = {}
        for kind in ("abi", "api", "manifest", "binary", "dependency", "runtime-mutation"):
            copy = root / kind
            shutil.copytree(venv, copy, symlinks=True)
            child = copy / "bin/python"
            if kind in ("abi", "api"):
                rewrite_manifest(
                    child,
                    lambda value: (
                        value["abi"].update(soabi="wrong") if kind == "abi" else value.update(provider_api=999)
                    ),
                )
            elif kind == "manifest":
                rewrite_manifest(child, lambda value: value.update(schema=999), record=False)
            elif kind == "binary":
                site = Path(
                    execute(
                        [child, "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"]
                    ).stdout.strip()
                )
                extension = next((site / "simulatte_native/_bin").glob("_nd_scalar.*"))
                with extension.open("ab") as file:
                    file.write(b"corruption")
            if kind == "runtime-mutation":
                work = root / "mutation-work"
                work.mkdir()
                case = work / "rejection.json"
                execute([child, "-I", probe, case, "--corrupt"], env=env, cwd=work)
                rejected[kind] = json.loads(case.read_text())
                continue
            code = """from pathlib import Path
import ctypes,json,shutil,sys
import simulatte_native
from simulatte.experimental.native import NativeStream
kind=sys.argv[1]
if kind=='dependency':
    library=next((Path(simulatte_native.__file__).parent/'_bin').glob('simulatte_native_engine.*'))
    target=Path('wrong-origin')/library.name
    target.parent.mkdir()
    shutil.copyfile(library,target)
    ctypes.CDLL(str(target.resolve()))
try: NativeStream(1)
except (ImportError,ValueError) as error:
    text=str(error)
    expected={'abi':'ABI mismatch','api':'provider API','manifest':'wheel RECORD',
              'binary':'length differs','dependency':'unexpected origin'}[kind]
    assert expected in text, text
    if kind!='dependency':
        assert 'simulatte_native._nd_scalar' not in sys.modules
    print(json.dumps(dict(rejected=True,error=text)))
else: raise AssertionError('invalid native assets accepted')
"""
            result = execute([child, "-I", "-c", code, kind], env=env, cwd=root)
            rejected[kind] = json.loads(result.stdout)
        parts = native.stem.split("-")
        foreign = "cp311" if parts[-3] != "cp311" else "cp314"
        parts[-3:-1] = [foreign, foreign]
        wrong = root / ("-".join(parts) + ".whl")
        shutil.copyfile(native, wrong)
        empty = root / "wrong-abi-install"
        execute([uv, "venv", empty, "--python", args.python])
        bad = execute([uv, "pip", "install", "--python", empty / "bin/python", "--no-deps", wrong], success=False)
        rejected["wheel-tag"] = dict(rejected=True, error=bad.stderr)
        (output / "rejections.json").write_text(json.dumps(rejected, indent=2) + "\n")
        metadata = dict(
            core=core.name,
            core_sha256=digest(core),
            native=native.name,
            native_sha256=digest(native),
            base_probe=base.stdout,
            compiler_unavailable_at_runtime=True,
            native_extra_installs_provider=True,
            runner_sha256=digest(Path(__file__)),
            probe_sha256=digest(Path(__file__).with_name("install_probe.py")),
            identity_probe_sha256=digest(Path(__file__).with_name("identity_probe.py")),
            capacity_probe_sha256=digest(Path(__file__).with_name("capacity_probe.py")),
            observation_probe_sha256=digest(Path(__file__).with_name("observation_probe.py")),
            identity_fixtures=fixture_sources,
            python=execute([python, "-I", "-c", "import sys; print(sys.version)"]).stdout.strip(),
        )
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    certificate = {path.name: digest(path) for path in sorted(output.iterdir()) if path.is_file()}
    (output / "verification.json").write_text(json.dumps(certificate, indent=2) + "\n")
    print(json.dumps(dict(passed=True, output=str(output), rejected=list(rejected))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-wheel", type=Path, required=True)
    parser.add_argument("--native-wheel", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--uv")
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
