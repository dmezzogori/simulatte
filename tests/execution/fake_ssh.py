"""Stand-in for ``ssh`` in tests; copied to ``<tmp>/bin/ssh`` by the ``fake_ssh`` fixture.

The behaviour is selected by the destination argument. Every call appends its argv, the
remote command (parsed as the remote shell would) and its environment to ``calls.jsonl``
next to this script. Nothing is executed and no network is touched.
"""

import json
import os
from pathlib import Path
import shlex
import sys

here = Path(__file__).resolve().parent
argv = sys.argv[1:]
separator = argv.index("--")
host = argv[separator - 1]
remote = shlex.split(" ".join(argv[separator + 1 :]))
with (here / "calls.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps({"argv": argv, "host": host, "remote": remote, "env": dict(os.environ)}) + "\n")

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
