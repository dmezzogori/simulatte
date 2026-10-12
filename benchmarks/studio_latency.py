"""Benchmark-only Studio wrapper: timestamp durable trace commits, never credentials.

Invoked by studio_latency.mjs with an installed Python and an owned temporary
project. Runtime instrumentation is local to this process; no package is edited.
"""

import json
from pathlib import Path
import sys
import time

from studio_execution import MODEL
from simulatte.cli import main
from simulatte.execution.coordinator import Coordinator
from simulatte.execution.receipts import SegmentReceiver

root = Path(sys.argv[1])
(root / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude = ["plant.py"]\n')
(root / "plant.py").write_text(
    "import time\n"
    + MODEL.replace("EVENTS", "160").replace(
        "yield env.timeout(1)", "time.sleep(.05)\n            yield env.timeout(1)"
    )
)
commits = {}
receive = SegmentReceiver.receive
emit = Coordinator._emit


def received(self, descriptor, offset, payload):
    receipt = receive(self, descriptor, offset, payload)
    if receipt.segment_complete and descriptor["stream"] == "trace":
        commits[self.key.attempt_id] = time.time() * 1000
    return receipt


def emitted(self, kind, key, body):
    if kind == "trace_available" and key is not None:
        print(
            "SP4_COMMIT:" + json.dumps({"epoch_ms": commits[key.attempt_id], "cursor": body["last_cursor"]}), flush=True
        )
    return emit(self, kind, key, body)


SegmentReceiver.receive = received
Coordinator._emit = emitted
raise SystemExit(main(["studio", str(root / "plant.py"), "--no-browser", "--port", "0"]))
