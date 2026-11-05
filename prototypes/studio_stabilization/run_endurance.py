"""Run the installed Studio sessions and collect evidence even without a terminal."""

import argparse
from contextlib import suppress
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import time
from urllib.error import URLError
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
HOSTS = ("macos", "linux")
MIB = 1 << 20


def ssh(host, *command, forward=None):
    options = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=15"]
    if forward is not None:
        options += ["-o", "ExitOnForwardFailure=yes", "-L", f"127.0.0.1:{forward}:127.0.0.1:{forward}"]
    return [*options, host, shlex.join(command)]


def scp(host, paths, destination, *, timeout=240):
    subprocess.run(
        ["scp", "-qBC", "-o", "ConnectTimeout=8", *(f"{host}:{path}" for path in paths), str(destination)],
        check=True,
        timeout=timeout,
    )


def wait_ready(process, port):
    end = time.monotonic() + 40
    while time.monotonic() < end and process.poll() is None:
        try:
            with urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.5)
    raise RuntimeError(f"Studio on port {port} did not become ready")


def terminate(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run(args):
    root = args.output.resolve()
    if (root / "status.json").exists():
        raise FileExistsError("Use a new evidence directory for each endurance run")

    def status(phase, **details):
        value = dict(phase=phase, pid=os.getpid(), seconds=args.seconds, updated_at=time.time(), **details)
        temporary = root / "status.tmp"
        temporary.write_text(json.dumps(value, indent=2) + "\n")
        temporary.replace(root / "status.json")

    def prepared(host):
        return json.loads((root / f"{host}-prepared.json").read_text())

    def start(label, command):
        with (root / f"{label}.log").open("x") as log:
            return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)

    mac, linux = (prepared(host) for host in HOSTS)
    remote = args.remote_root
    local_server, remote_server = root / "macos-server", f"{remote}/linux-server"
    local_access = Path(mac["work"]) / "access.json"
    remote_access = root / "linux-access.json"
    ports = {"macos": args.local_port, "linux": args.remote_port}
    servers = {}
    browser = None
    started = None
    try:
        status("starting")
        servers["macos"] = start(
            "macos-server",
            [
                mac["python"],
                "-I",
                str(HERE / "serve.py"),
                "--work",
                mac["work"],
                "--output",
                str(local_server),
                "--port",
                str(ports["macos"]),
            ],
        )
        servers["linux"] = start(
            "linux-server",
            ssh(
                args.remote_host,
                "env",
                "PYTHONUNBUFFERED=1",
                linux["python"],
                "-I",
                f"{remote}/studio/serve.py",
                "--work",
                linux["work"],
                "--output",
                remote_server,
                "--port",
                str(ports["linux"]),
                forward=ports["linux"],
            ),
        )
        for host, process in servers.items():
            wait_ready(process, ports[host])
        scp(args.remote_host, [f"{linux['work']}/access.json"], remote_access, timeout=30)
        scp(args.remote_host, [f"{remote_server}/server.json"], root / "linux-server-start.json", timeout=30)
        for metadata in (local_server / "server.json", root / "linux-server-start.json"):
            assert json.loads(metadata.read_text())["assets_sha256"] == args.assets_sha256
        targets = root / "targets.json"
        targets.write_text(
            json.dumps(
                [
                    dict(name=host, access=str(access), output=str(root / f"{host}-browser"))
                    for host, access in zip(HOSTS, (local_access, remote_access), strict=True)
                ]
            )
        )
        browser = start(
            "browser",
            [
                args.node,
                str(HERE / "study.mjs"),
                "--targets",
                str(targets),
                "--seconds",
                str(args.seconds),
                "--keep-open",
                "chromium",
            ],
        )
        started = time.time()
        status("running", started_at=started, expected_end=started + args.seconds, browser_pid=browser.pid)
        deadline = time.monotonic() + args.seconds + 180
        while browser.poll() is None:
            if time.monotonic() >= deadline:
                raise TimeoutError("Studio browsers exceeded the planned duration and drain period")
            if any(process.poll() is not None for process in servers.values()):
                raise RuntimeError("A Studio server or its connection stopped")
            if shutil.disk_usage(root).free < 8 * 1024 * MIB:
                raise RuntimeError("Less than 8 GiB free on the browser and evidence host")
            time.sleep(5)
        if browser.returncode != 0:
            raise RuntimeError("A Studio browser session failed; see browser.log")
        status("auditing", started_at=started)
        for host, access in zip(HOSTS, (local_access, remote_access), strict=True):
            subprocess.run(
                [
                    mac["python"],
                    "-I",
                    str(HERE / "revocations.py"),
                    "--access",
                    str(access),
                    "--browser",
                    str(root / f"{host}-browser/summary.json"),
                    "--output",
                    str(root / f"final-{host}-revocations.json"),
                ],
                check=True,
                timeout=180,
            )
    except BaseException as error:
        status("interrupted", started_at=started, error=type(error).__name__)
        raise
    finally:
        if browser is not None:
            terminate(browser)
        if "macos" in servers:
            (Path(mac["work"]) / "stop").touch()
        if "linux" in servers:
            with suppress(subprocess.SubprocessError):
                subprocess.run(ssh(args.remote_host, "touch", f"{linux['work']}/stop"), check=True, timeout=20)
        for process in servers.values():
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                terminate(process)
        remote_access.unlink(missing_ok=True)

    try:
        subprocess.run(
            [
                mac["python"],
                "-I",
                str(HERE / "audit.py"),
                "--project",
                f"{mac['work']}/project",
                "--browser",
                str(root / "macos-browser/summary.json"),
                "--output",
                str(root / "final-macos-audit.json"),
            ],
            check=True,
            timeout=300,
        )
        remote_summary = f"{remote}/linux-browser-summary.json"
        subprocess.run(
            ["scp", "-qB", str(root / "linux-browser/summary.json"), f"{args.remote_host}:{remote_summary}"],
            check=True,
            timeout=30,
        )
        remote_audit = f"{remote}/final-linux-audit.json"
        subprocess.run(
            ssh(
                args.remote_host,
                linux["python"],
                "-I",
                f"{remote}/studio/audit.py",
                "--project",
                f"{linux['work']}/project",
                "--browser",
                remote_summary,
                "--output",
                remote_audit,
            ),
            check=True,
            timeout=300,
        )
        scp(args.remote_host, [remote_audit], root, timeout=30)
        linux_logs = root / "linux-server"
        linux_logs.mkdir()
        scp(
            args.remote_host,
            [f"{remote_server}/{file}" for file in ("reads.jsonl", "resources.jsonl", "server.json")],
            linux_logs,
        )
        result = subprocess.run(
            [
                mac["python"],
                str(HERE / "endurance_analysis.py"),
                "--evidence",
                str(root),
                "--seconds",
                str(args.seconds),
            ],
            timeout=900,
        )
        if result.returncode not in (0, 1) or not (root / "report.json").is_file():
            raise RuntimeError("Endurance analysis did not finish")
        status("complete", started_at=started, passed=result.returncode == 0, report="report.json")
        return result.returncode
    except BaseException as error:
        status("audit_failed", started_at=started, error=type(error).__name__)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--remote-host", required=True)
    parser.add_argument("--remote-root", required=True)
    parser.add_argument("--local-port", required=True, type=int)
    parser.add_argument("--remote-port", required=True, type=int)
    parser.add_argument("--assets-sha256", required=True)
    parser.add_argument("--seconds", type=int, default=7200)
    parser.add_argument("--node", default=shutil.which("node"))
    args = parser.parse_args()
    if args.seconds < 900:
        parser.error("The endurance audit requires at least 900 seconds")

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    raise SystemExit(run(args))
