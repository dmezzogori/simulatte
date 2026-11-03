"""Replay with the ASan/UBSan extension and separately verified engine library."""

from pathlib import Path
import importlib
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import prototypes.native_engine_streaming.stream as stream

stream.BUILD = Path(sys.argv[1]).resolve()
pytest = importlib.import_module("pytest")

raise SystemExit(pytest.main(["prototypes/native_engine_streaming/tests", "-o", "addopts=", "-q", "--tb=short"]))
