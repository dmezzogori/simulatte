"""Stand-in for ``uv`` in tests; copied to ``<bin>/uv`` (a PATH entry) by the ``fake_uv`` helpers.

``uv run ... -- python ARGS`` execs the current interpreter with ARGS (the remote supervisor).
``uv sync ...`` appends its argv, cwd, the files in cwd and ``UV_PROJECT_ENVIRONMENT`` to
``uv.jsonl`` next to this script and builds a fake environment there. ``fake-uv.json`` next to the
script selects the behaviour of ``sync``:

* ``{"site": "link"}`` (default): ``lib/pythonX.Y/site-packages`` is a symlink to this
  interpreter's ``purelib``, so a remote-kind child imports the test venv's dependencies.
* ``{"site": "dir"}``: a real ``site-packages`` with one small fake distribution.
* ``{"site": "none"}``: an environment without ``site-packages``.
* ``{"exit": N, "stderr": TEXT}``: write TEXT to stderr and exit N without building.
* ``{"sleep": S}``: start a grandchild ``sleep`` (pid in ``sleeper.pid``), then sleep S seconds.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import time

here = Path(__file__).resolve().parent
args = sys.argv[1:]
if args[:1] == ["run"]:
    command = args[args.index("--") + 1 :]
    assert command[0] == "python"
    os.execv(sys.executable, [sys.executable, *command[1:]])
assert args[:1] == ["sync"], args
mode_path = here / "fake-uv.json"
mode = json.loads(mode_path.read_text()) if mode_path.exists() else {}
target = Path(os.environ["UV_PROJECT_ENVIRONMENT"])
with (here / "uv.jsonl").open("a", encoding="utf-8") as log:
    record = {
        "argv": args,
        "cwd": os.getcwd(),
        "files": sorted(os.listdir(".")),
        "environment": str(target),
        "env": {name: value for name, value in os.environ.items() if name.startswith("UV")},
    }
    log.write(json.dumps(record) + "\n")
if "sleep" in mode:
    sleeper = subprocess.Popen(["sleep", "60"])
    (here / "sleeper.pid").write_text(str(sleeper.pid))
    time.sleep(mode["sleep"])
if "exit" in mode:
    sys.stderr.write(mode.get("stderr", ""))
    sys.exit(mode["exit"])
site = target / "lib" / f"python{sys.version_info[0]}.{sys.version_info[1]}" / "site-packages"
site.parent.mkdir(parents=True, exist_ok=True)
(target / "pyvenv.cfg").write_text(f"version = {sys.version.split()[0]}\n")
if mode.get("site") == "none":
    sys.exit(0)
if mode.get("site", "link") == "link":
    site.symlink_to(sysconfig.get_path("purelib"), target_is_directory=True)
else:
    info = site / "example_package-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: example-package\nVersion: 1.0\n")
    (info / "RECORD").write_text("example_package-1.0.dist-info/METADATA,,\n")
