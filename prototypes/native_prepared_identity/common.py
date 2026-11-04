"""Shared source freeze for query and live gates."""

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]


def sources():
    paths = [
        *REPO.glob("src/simulatte/**/*.py"),
        *REPO.glob("native/**/*.py"),
        *REPO.glob("native/csrc/*"),
        *ROOT.glob("*.py"),
        *ROOT.glob("tests/*.py"),
        ROOT / "README.md",
        REPO / "pyproject.toml",
        REPO / "uv.lock",
        REPO / "native/sources.json",
        REPO / "specs/research/2026-11-04-native-prepared-identity-plan.md",
        REPO / "prototypes/native_retention_integration/run.py",
        REPO / "prototypes/native_fixed_admission/analysis.py",
        REPO / "prototypes/native_feedback_retention/analysis.py",
    ]
    return {
        p.relative_to(REPO).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(set(paths))
        if p.is_file() and "__pycache__" not in p.parts
    }
