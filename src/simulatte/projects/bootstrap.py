"""One-use launcher handoff to the selected runtime, before writer admission."""

from __future__ import annotations
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import sqlite3

from simulatte.projects.types import StudioTarget


def capabilities():
    from importlib.metadata import version
    from simulatte.studio.server import assets

    assets()
    return {"protocol": 1, "assets": True, "studio": True, "version": version("simulatte")}


def _validate(value):
    if (
        not isinstance(value, dict)
        or value.get("protocol") != 1
        or value.get("assets") is not True
        or value.get("studio") is not True
    ):
        raise ValueError("Selected runtime lacks compatible Studio protocol/assets; repair its Studio installation")
    return value


def validate_runtime(interpreter: Path):
    try:
        done = subprocess.run(
            [str(interpreter), "-I", "-m", "simulatte.projects.bootstrap", "--probe"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if done.returncode:
            raise ValueError("Selected runtime cannot start Studio: " + done.stderr[-4096:])
        return _validate(json.loads(done.stdout))
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise ValueError(f"Selected Studio runtime unavailable: {error}") from error


def selected_interpreter(target):
    from simulatte.execution.store_reader import StoreReader

    root = target.root / ".simulatte"
    if not (root / "index.sqlite").exists():
        return None
    reader = StoreReader.open(root)
    try:
        generation = next((g for g in reader.generations() if g["state"] == "selected"), None)
        if generation is None:
            return None
        spec = generation["evidence"]["spec"]
        if spec["kind"] == "existing":
            from simulatte.projects.environments import interpreter_of_another_project

            # Another project's interpreter (a same-machine copy's original) is never handed off to:
            # the project then opens in this runtime, which reports it unavailable.
            interpreter = Path(spec["interpreter"])
            return None if interpreter_of_another_project(target.root, interpreter) else interpreter
        from simulatte.projects.environments import environment_directory

        return environment_directory(root / "cache/environments", generation["id"]) / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
    finally:
        reader.close()


def handoff(interpreter, target, *, port, no_browser, generation=None, flags=None):
    # No store is open in this parent. Child proves its own assets/protocol before
    # receiving the one-use permission to acquire writer ownership.
    token = secrets.token_urlsafe(32)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(30)
        payload = {
            "root": str(target.root),
            "legacy_reference": target.legacy_reference,
            "port": port,
            "no_browser": no_browser,
            "handshake_port": listener.getsockname()[1],
            "generation": generation,
            "flags": flags,
        }
        process = subprocess.Popen(
            [str(interpreter), "-I", "-m", "simulatte.projects.bootstrap", "--handoff", json.dumps(payload)],
            env={**os.environ, "SIMULATTE_HANDOFF_TOKEN": token},
        )
        try:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(30)
                with connection.makefile("rwb") as stream:
                    message = json.loads(stream.readline(16384))
                    if not secrets.compare_digest(message.get("token", ""), token):
                        raise ValueError("Studio runtime handoff authentication failed")
                    _validate(message.get("capabilities"))
                    stream.write(b"start\n")
                    stream.flush()
            if process.wait() != 0:
                raise ValueError("Project Studio exited; reopen with the launcher to inspect saved results")
        except BaseException:
            process.terminate()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise


def launch_project(target: StudioTarget, *, port: int, no_browser: bool, configure=None):
    from simulatte.projects.service import ProjectService
    from simulatte.studio.command import serve_project

    try:
        interpreter = selected_interpreter(target)
        if interpreter is not None and interpreter.absolute() != Path(sys.executable).absolute():
            validate_runtime(interpreter)
            return handoff(
                interpreter, target, port=port, no_browser=no_browser, flags=getattr(configure, "flags", None)
            )
    except (ValueError, OSError, sqlite3.Error) as error:
        service = ProjectService.open(target, read_only=True)
        service.error = str(error)
    else:
        service = ProjectService.open(target)
    service.initial_options = configure
    try:
        serve_project(service, port=port, no_browser=no_browser)
        generation = service.restart_generation
        interpreter = service.restart_interpreter
    finally:
        service.close()
    if interpreter is not None:
        handoff(
            interpreter,
            target,
            port=port,
            no_browser=no_browser,
            generation=generation,
            flags=getattr(configure, "flags", None),
        )


def main():
    if sys.argv[1:] == ["--probe"]:
        print(json.dumps(capabilities()))
        return
    if len(sys.argv) != 3 or sys.argv[1] != "--handoff":
        raise ValueError("Internal Studio launcher invocation expected")
    payload = json.loads(sys.argv[2])
    token = os.environ.pop("SIMULATTE_HANDOFF_TOKEN")
    capability = capabilities()
    with socket.create_connection(("127.0.0.1", payload["handshake_port"]), timeout=30) as connection:
        with connection.makefile("rwb") as stream:
            stream.write((json.dumps({"token": token, "capabilities": capability}) + "\n").encode())
            stream.flush()
            if stream.readline(16) != b"start\n":
                raise ValueError("Studio handoff was not admitted")
    target = StudioTarget(Path(payload["root"]), payload["legacy_reference"])
    if payload.get("generation") is not None:
        from simulatte.execution.store import RunStore
        from simulatte.projects.environments import EnvironmentPool

        with RunStore.open(target.root / ".simulatte") as store:
            pool = EnvironmentPool(target.root, store)
            generation = pool.get(payload["generation"])
            if generation.interpreter.absolute() != Path(sys.executable).absolute():
                raise ValueError("Handoff interpreter differs from requested generation")
            pool.activate(generation.id)
    from argparse import Namespace
    from simulatte.studio.command import configure

    initial = None if payload.get("flags") is None else configure(Namespace(**payload["flags"]))
    launch_project(target, port=payload["port"], no_browser=payload["no_browser"], configure=initial)


if __name__ == "__main__":
    main()
