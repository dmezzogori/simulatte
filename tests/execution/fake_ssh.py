"""Stand-in for ``ssh`` in tests; copied to ``<tmp>/bin/ssh`` by the ``fake_ssh`` fixture.

The behaviour is selected by the destination argument. Every call appends its argv, the
remote command (parsed as the remote shell would) and its environment to ``calls.jsonl``
next to this script. No network is touched.

Transport scenarios are selected by these destinations: ``local-sh`` runs the remote
command with the local ``/bin/sh`` (probe, stage and bootstrap scripts for real);
``banner-hello``, ``banner-hang``, ``eof-mid-fragment``, ``stderr-flood``, ``noise-overflow``,
``uv-missing`` and ``runtime-missing`` simulate a supervisor session and report the runtime as
already staged.
"""

import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time

here = Path(__file__).resolve().parent
argv = sys.argv[1:]
separator = argv.index("--")
host = argv[separator - 1]
remote = shlex.split(" ".join(argv[separator + 1 :]))
with (here / "calls.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps({"argv": argv, "host": host, "remote": remote, "env": dict(os.environ)}) + "\n")

SESSIONS = ("banner-hello", "banner-hang", "eof-mid-fragment", "stderr-flood", "noise-overflow")
if host == "local-sh":
    os.execv("/bin/sh", ["sh", "-c", remote[-1]])
if host in (*SESSIONS, "uv-missing", "runtime-missing"):
    if remote[-1].startswith("test -f"):
        sys.exit(0)  # the trusted runtime is reported as staged
    if host == "uv-missing":
        sys.stderr.write("simulatte: uv is missing; run: simulatte workers setup uv-missing --install-uv\n")
        sys.exit(3)
    if host == "runtime-missing":
        sys.stderr.write("simulatte: trusted runtime is not staged\n")
        sys.exit(4)
    out = sys.stdout.buffer
    worker = [sys.executable, "-c", "import os; from simulatte.cli import main; os._exit(main(['_worker']))"]
    if host == "noise-overflow":
        out.write(b"Last login: never\n" * 5000)  # > 64 KiB before any magic
        out.flush()
        time.sleep(60)  # stays connected: the client must be terminated
        sys.exit(0)
    out.write(b"Welcome to the fake host\nThe system is going down for maintenance never\n")
    out.flush()
    if host == "banner-hang":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)  # only SIGKILL reaps this client
        time.sleep(60)
        sys.exit(0)
    if host == "banner-hello":
        os.execv(sys.executable, worker)
    if host == "stderr-flood":
        child = subprocess.Popen(worker)  # inherits stdin/stdout: a normal supervisor session
        chunk = b"e" * (1 << 20)
        for _ in range(10):
            sys.stderr.buffer.write(chunk)
        sys.stderr.buffer.flush()
        sys.exit(child.wait())
    # eof-mid-fragment: hello and heartbeat, then a trace frame cut short once the run arrives.
    from simulatte.execution.protocol import MAGIC, FrameCodec, encode_message
    from simulatte.execution.supervisor import _key, worker_hello
    from simulatte.execution.transport import envelope

    out.write(MAGIC + encode_message(envelope("hello", worker_hello(), 0, None)))
    out.write(encode_message(envelope("heartbeat", {"phase": "idle"}, 1, None)))
    out.flush()
    codec = FrameCodec()
    run = None
    while run is None:
        data = os.read(0, 65536)
        if not data:
            sys.exit(0)
        run = next((message for message in codec.feed(data) if message.type == "run"), None)
    key = _key(run)
    frame = encode_message(envelope("trace_chunk", {"payload_bytes": 4096}, 2, key), b"t" * 4096)
    out.write(frame[: len(frame) // 2])
    out.flush()
    sys.exit(0)

installed = here / "uv-installed"
if "astral.sh" in remote[-1]:
    if host == "broken-install":
        sys.stderr.write("curl: (22) The requested URL returned error: 404\n")
        sys.exit(1)
    installed.write_text("yes")
    sys.exit(0)
if host == "down":
    sys.stderr.write("ssh: connect to host down port 22: Connection refused\n")
    sys.exit(255)
if host == "noisy":
    sys.stderr.write("x" * 10000)
    sys.exit(255)
if host == "garbage":
    sys.stdout.write("welcome to the machine\n")
    sys.exit(0)

system, arch, shell, uv, cache = "Linux", "x86_64", "/bin/bash", "uv 0.5.1 (abc123 2024-11-01)", "writable"
if host == "linux-nouv" or host == "broken-install":
    uv = "uv 0.5.1 (abc123 2024-11-01)" if installed.exists() else "missing"
elif host == "darwin-uv":
    system, arch, shell = "Darwin", "arm64", "/bin/zsh"
elif host == "msys":
    system = "MSYS_NT-10.0-19045"
elif host == "readonly":
    cache = "unwritable"
print(f"os={system}")
print(f"arch={arch}")
print(f"shell={shell}")
print(f"uv={uv}")
print(f"cache={cache}")
