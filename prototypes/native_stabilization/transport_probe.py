"""Reproduce a live range source crossing successful result publication."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time

from simulatte.execution.coordinator import Coordinator
from simulatte.execution.snapshot import discover_project
from simulatte.stopping import Horizon
from simulatte.studio.app import StudioServer
from simulatte.studio.server import assets


def main(args):
    args.project.mkdir(exist_ok=False)
    for name in ("plant.py", "workloads.py", "pyproject.toml"):
        shutil.copyfile(args.source / name, args.project / name)
    with Coordinator(discover_project(args.project / "plant.py", args.project)) as coordinator:
        prepared = coordinator.prepare(timeout=30)
        studio = StudioServer(coordinator, 18765, assets())  # No listener needed for this range-source race.
        request = coordinator.make_request(
            prepared,
            parameters=dict(
                case="mixed",
                engine="native",
                retention="bounded",
                policy="spt",
                jobs=4000,
                budget=257,
                pace=0.004,
                fault=False,
            ),
            seed=3,
            stopping=Horizon(1024, warmup=100).to_dict(),
            level="kpi",
        )
        key = coordinator.submit(request, prepared.revision, timeout=30)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = studio.snapshot()
            active = state["active"]
            prefix = active["prefix"] if active else None
            if prefix and prefix["generation"] >= 2:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("No live prefix before publication")
        source, _ = studio._range_source(key.attempt_id, str(prefix["generation"]))
        source.validate(0, source.length - 1)
        expected = b"".join(source.chunks(0, source.length - 1))
        result = coordinator.wait(key, timeout=40)
        try:
            old = b"".join(source.chunks(0, source.length - 1))
        except (FileNotFoundError, ValueError) as error:
            failure = type(error).__name__
        else:
            assert old == expected
            failure = None
        refreshed, _ = studio._range_source(key.attempt_id, str(prefix["generation"]))
        actual = b"".join(refreshed.chunks(0, refreshed.length - 1))
        assert actual == expected
        args.output.write_text(
            json.dumps(
                dict(
                    reproduced=failure is not None,
                    original_source_error=failure,
                    prefix_length=len(expected),
                    prefix_sha256=hashlib.sha256(expected).hexdigest(),
                    refreshed_source_matches=True,
                    attempt=key.attempt_id,
                    request=result.key.request_id,
                ),
                indent=2,
            )
            + "\n"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
