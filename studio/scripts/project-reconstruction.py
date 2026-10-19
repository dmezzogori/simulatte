"""Native fresh-wheel managed copy/reconstruction proof (macOS/Windows/Linux CI).

Run after building Studio assets + wheel. Every project operation runs under a
fresh installed interpreter, never source-tree imports. Evidence is kept in --output.
"""

from __future__ import annotations

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
import time
import zipfile


def run(command, **kwargs):
    return subprocess.run(list(map(str, command)), check=True, **kwargs)


def python_at(environment):
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def retain_raw_files(output):
    """Retain the machine-dependent records/selectors separately from canonical identity."""
    from importlib import metadata

    files = {}
    for distribution in metadata.distributions():
        for relative in distribution.files or ():
            path = Path(distribution.locate_file(relative)).resolve()
            if (
                path.name in {"RECORD", "direct_url.json", "uv_cache.json"}
                or path.parent == Path(sys.executable).parent
            ):
                files[str(path)] = base64.b64encode(path.read_bytes()).decode()
    output.write_text(json.dumps({"encoding": "base64", "files": files}, indent=2))


def fixture_wheel(path):
    files = {
        "review_entry.py": (
            b'from pathlib import Path\nimport sys\ndef main():\n    Path(sys.argv[1]).write_text("launched")\n'
        ),
        "review_entry-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: review-entry\nVersion: 1.0\n",
        "review_entry-1.0.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: simulatte-native-proof\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        "review_entry-1.0.dist-info/entry_points.txt": (
            b"[console_scripts]\nreview-console = review_entry:main\n[gui_scripts]\nreview-gui = review_entry:main\n"
        ),
    }
    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for name, data in files.items():
        writer.writerow(
            (name, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("="), len(data))
        )
    writer.writerow(("review_entry-1.0.dist-info/RECORD", "", ""))
    files["review_entry-1.0.dist-info/RECORD"] = record.getvalue().encode()
    with zipfile.ZipFile(path, "w") as wheel:
        for name, data in files.items():
            wheel.writestr(name, data)


def installed_prepare(root, wheel):
    from simulatte.projects import install_starter, starter_files, load_workspace, apply_config
    from simulatte.projects.environments import EnvironmentPool, LockPreviewRequired
    from simulatte.execution.store import RunStore

    root.mkdir()
    install_starter(root, starter_files("production"))
    vendor = root / "vendor"
    vendor.mkdir()
    shutil.copy2(wheel, vendor / wheel.name)
    entry = "review_entry-1.0-py3-none-any.whl"
    fixture_wheel(vendor / entry)
    config = root / "pyproject.toml"
    config.write_text(
        config.read_text()
        .replace('include = ["models/*.py"]', 'include = ["models/*.py", "vendor/*.whl"]')
        .replace('"simulatte[studio]>=0.17.0"', '"simulatte[studio]>=0.17.0", "review-entry==1.0"')
        .replace(
            'requires-python = ">=3.11"',
            f'requires-python = "=={sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"',
        )
        + '\n[tool.uv.sources]\nsimulatte = {path = "vendor/'
        + wheel.name
        + '"}\nreview-entry = {path = "vendor/'
        + entry
        + '"}\n'
    )
    with RunStore.open(root / ".simulatte") as store:
        pool = EnvironmentPool(root, store)
        workspace = load_workspace(root)
        try:
            pool.prepare(workspace)
        except LockPreviewRequired as error:
            apply_config(error.edit)
        generation = pool.prepare(workspace)
        pool.activate(generation.id)
        (root.parent / "generation.json").write_text(
            json.dumps({"id": generation.id, "python": str(generation.interpreter)})
        )


def incapable_replay_proof(co, original):
    """A reused real worker advertising the older contract cannot receive model work."""
    from simulatte.execution.coordinator import ExecutionFailure

    actor = co._actors["local-1"]
    factory = actor.transport_factory
    commands = []

    class OldCapabilities:
        def __init__(self):
            self.worker = factory()

        def __getattr__(self, name):
            return getattr(self.worker, name)

        def receive(self, timeout):
            message = self.worker.receive(timeout)
            if message.type == "hello":
                from types import SimpleNamespace

                return SimpleNamespace(
                    type="hello",
                    body={
                        **message.body,
                        "capabilities": [c for c in message.body["capabilities"] if c != "installation-runtime-v1"],
                    },
                )
            return message

        def send(self, kind, body, **kwargs):
            commands.append(kind)
            return self.worker.send(kind, body, **kwargs)

    # Complete the real handshake before publishing the idle worker: the actor's
    # idle heartbeat reader must not race this harness for the initial hello.
    worker = OldCapabilities()
    hello = worker.receive(10)
    assert hello.type == "hello" and "metadata-artifacts" in hello.body["capabilities"]
    with co._condition:
        assert actor.worker is None and actor.current is None and actor.pending is None
        actor.capabilities = frozenset(hello.body["capabilities"])
        actor.worker = worker
    actor.transport_factory = OldCapabilities
    try:
        try:
            co.replay(original, timeout=30)
        except ExecutionFailure as error:
            assert "capability" in str(error), str(error)
        else:
            raise AssertionError("Replay admitted to incapable worker")
        assert not {"inspect", "run"}.intersection(commands), commands
    finally:
        actor.transport_factory = factory


def installed_execute(root, phase):
    from simulatte.projects import StudioTarget
    from simulatte.projects.service import ProjectService
    from simulatte.execution.schema import _plain
    from simulatte.experiments import load_experiment

    service = ProjectService.open(StudioTarget(root, None))
    try:
        assert not service.read_only, service.error
        co = service.ensure_coordinator()
        model = service.workspace.models[0].id
        if phase == "original-run":
            retain_raw_files(root.parent / "original-raw-files.json")
            prepared = co.prepare_model(model, timeout=30)
            request = co.make_request(prepared, level="kpi", seed=29)
            assert request.runtime["kind"] == "local-installation-v1"
            admitted = co.admit_run(model, request, prepared.revision, timeout=30)
            result = co.wait(admitted.key, timeout=40)
            (root.parent / "original-request.json").write_text(
                json.dumps(
                    {
                        "id": admitted.key.request_id,
                        "request": _plain(request.to_dict()),
                        "attempt": result.key.attempt_id,
                    }
                )
            )
            incapable_replay_proof(co, admitted.key.request_id)
        else:
            retain_raw_files(root.parent / "rebuilt-raw-files.json")
            original = json.loads((root.parent / "original-request.json").read_text())
            assert _plain(co.store.request(original["id"]).to_dict()) == original["request"]
            replay = co.replay(original["id"], timeout=30)
            assert replay.verification.verified, replay.verification
            assert _plain(co.store.request(original["id"]).to_dict()) == original["request"]
            prepared = co.prepare_model(model, timeout=30)
            request = co.make_request(prepared, level="full", seed=31)
            admitted = co.admit_run(model, request, prepared.revision, timeout=30)
            co.wait(admitted.key, timeout=40)
            path = root / "proof.experiment.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "name": "reconstructed",
                        "model": "models/production.py:build",
                        "replications": {"count": 1},
                        "configurations": {"grid": {"servers": [2, 3]}},
                    }
                )
            )
            study = co.contexts[model].start_experiment(load_experiment(path), timeout=30)
            assert co.wait_experiment(study, timeout=40)["status"] == "completed"
            assert {row["worker_kind"] for row in co.store.replications(study)} == {"local"}
        (root.parent / (phase + "-ok.json")).write_text(
            json.dumps({"phase": phase, "python": sys.executable, "runtime": _plain(request.runtime)})
        )
    finally:
        service.close()


def installed_reconstruct(root):
    from simulatte.execution.schema import _plain
    from simulatte.execution.store import RunStore
    from simulatte.projects.environments import EnvironmentPool

    with RunStore.open(root / ".simulatte") as store:
        pool = EnvironmentPool(root, store)
        original = pool.selected
        assert original is not None
        captured = _plain(original.evidence)
        current = pool.use_existing(Path(sys.executable))
        pool.activate(current.id)
        before = pool.selected.id
        rebuilt = pool.reconstruct(captured)
        assert pool.selected.id == before
        assert rebuilt.id == original.id and _plain(rebuilt.evidence) == captured
        actual = pool.installation_probe(rebuilt)
        assert actual["installation"] == captured["probe"]["installation"]
        assert actual["runtime"] != captured["probe"]["runtime"], "native install metadata must actually differ"
        (root.parent / "original-raw-probe.json").write_text(json.dumps(captured["probe"], indent=2))
        (root.parent / "rebuilt-raw-probe.json").write_text(json.dumps(actual, indent=2))
        for name in ("review-console", "review-gui"):
            executable = rebuilt.interpreter.parent / (name + (".exe" if os.name == "nt" else ""))
            marker = root / (name + ".txt")
            run([executable, marker])
            deadline = time.monotonic() + 10
            while not marker.exists():
                assert time.monotonic() < deadline
                time.sleep(0.05)
            assert marker.read_text() == "launched"
        # Copy-on-write replacements avoid changing uv's hard-linked package cache.
        payload = Path(actual["distribution_roots"][0]) / "review_entry.py"
        entrypoint = rebuilt.interpreter.parent / ("review-console.exe" if os.name == "nt" else "review-console")
        for target in (payload, entrypoint):
            old = target.read_bytes()
            mode = target.stat().st_mode
            changed = (
                old.replace(b"sys.exit", b"sys.oxit", 1)
                if target.suffix == ".exe"
                else old + b"\n# altered payload/body\n"
            )
            assert changed != old
            temporary = target.with_name(target.name + ".proof-tmp")
            temporary.write_bytes(changed)
            temporary.chmod(mode)
            os.replace(temporary, target)
            try:
                try:
                    pool.acquire(rebuilt.id, "tampered")
                except ValueError:
                    pass
                else:
                    raise AssertionError("altered payload/entrypoint was accepted")
            finally:
                temporary.write_bytes(old)
                temporary.chmod(mode)
                os.replace(temporary, target)
        pool.acquire(rebuilt.id, "intact")
        pool.release("intact")
        assert pool.selected.id == before
        pool.activate(rebuilt.id)  # Explicit selection only after proving reconstruction leaves it unchanged.
        (root.parent / "reconstruction-ok.json").write_text(
            json.dumps(
                {
                    "id": rebuilt.id,
                    "python": str(rebuilt.interpreter),
                    "selection_before": before,
                    "selection_after_reconstruct": before,
                }
            )
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--phase")
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    if args.phase:
        if args.phase == "prepare":
            installed_prepare(args.root, args.wheel)
        elif args.phase == "reconstruct":
            installed_reconstruct(args.root)
        else:
            installed_execute(args.root, args.phase)
        print(args.phase + " passed", flush=True)
        return
    wheel = args.wheel.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    launcher = output / "launcher"
    run(["uv", "venv", launcher, "--python", sys.executable])
    python = python_at(launcher)
    run(["uv", "pip", "install", "--python", python, str(wheel) + "[studio]"])
    script = Path(__file__).resolve()
    original = output / "original"
    copied = output / "copied project Vénétó"

    def phase(interpreter, name, root):
        run([interpreter, "-I", script, "--phase", name, "--root", root, "--wheel", wheel], cwd=output)

    phase(python, "prepare", original)
    generation = json.loads((output / "generation.json").read_text())
    phase(generation["python"], "original-run", original)
    shutil.copytree(
        original, copied, ignore=lambda directory, names: ["cache"] if Path(directory).name == ".simulatte" else []
    )
    phase(python, "reconstruct", copied)
    rebuilt = json.loads((output / "reconstruction-ok.json").read_text())
    phase(rebuilt["python"], "copied-run", copied)
    print(
        "Native copied-project reconstruction/run/replay/experiment/launcher/tamper proof passed: " + str(output),
        flush=True,
    )


if __name__ == "__main__":
    main()
