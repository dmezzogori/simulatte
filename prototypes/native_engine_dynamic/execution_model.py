"""Source-snapshotted model entry point for the restricted execution gate."""

import time
from pathlib import Path
import hashlib
import json

from simulatte import model
from .execution_adapter import ExecutionAdapter, Parameters, templates


def verify_assets():
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "native-assets.json").read_text())
    for name, expected in manifest["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("native runtime asset integrity check failed")


@model(params=Parameters, horizon=30000)
def build(env, layout, params):
    verify_assets()
    started = time.perf_counter()
    data = templates(env, params)
    elapsed = time.perf_counter() - started
    ExecutionAdapter(env, layout, params, data, generation_s=elapsed)
