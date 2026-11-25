"""The frame fixtures of the Studio map (``studio/fixtures/map``) are what ``generate_map_frames.py`` writes.

The TypeScript tests and the render harness load these files; this test regenerates them into a temporary directory
and compares each with the committed one, byte for byte (the generator writes canonical JSON).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "tests" / "fixtures" / "traces" / "generate_map_frames.py"
COMMITTED = ROOT / "studio" / "fixtures" / "map"


def test_map_frame_fixtures_up_to_date(tmp_path: Path) -> None:
    # A fresh interpreter, as for generate.py: the recordings carry the registered entity kinds, which other tests
    # extend.
    subprocess.run(
        [sys.executable, str(GENERATOR), "--out", str(tmp_path)], check=True, timeout=120, capture_output=True
    )
    committed = sorted(p.name for p in COMMITTED.glob("*.json"))
    assert committed, "no map frame fixtures are committed"
    assert sorted(p.name for p in tmp_path.glob("*.json")) == committed
    for name in committed:
        assert (tmp_path / name).read_bytes() == (COMMITTED / name).read_bytes(), name
