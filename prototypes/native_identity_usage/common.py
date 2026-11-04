"""Source freeze and immutable fixture provenance shared by the two studies."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
BASE = "f6693d39c2cdb1531594f5f46f54d181bd89c81c"
COPIES = {
    name: "prototypes/native_prepared_identity/" + name
    for name in ("plant.py", "fixed_case.py", "coupled_case.py", "reference_projection.py", "unprepared_projection.py")
} | {
    "prepared_projection.py": "prototypes/native_prepared_identity/projection.py",
    "projection.py": "prototypes/native_identity_helper/projection.py",
    "reference_measurement.py": "prototypes/native_prepared_identity/measurement.py",
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def sources():
    paths = [
        *REPO.glob("src/simulatte/**/*.py"),
        *REPO.glob("native/csrc/*"),
        *REPO.glob("native/src/**/*.py"),
        *ROOT.glob("*.py"),
        *ROOT.glob("tests/*.py"),
        ROOT / "README.md",
        REPO / "pyproject.toml",
        REPO / "uv.lock",
        REPO / "native/sources.json",
        REPO / "specs/research/2026-11-04-native-identity-usage-plan.md",
        REPO / "prototypes/native_retention_integration/run.py",
    ]
    paths.extend(REPO / name for name in COPIES.values())
    return {
        p.relative_to(REPO).as_posix(): sha(p)
        for p in sorted(set(paths))
        if p.is_file() and "__pycache__" not in p.parts
    }


def assert_copies():
    for name, original in COPIES.items():
        assert (ROOT / name).read_bytes() == (REPO / original).read_bytes(), name
