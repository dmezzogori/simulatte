from __future__ import annotations

import runpy
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def _run(name: str) -> str:
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        runpy.run_path(str(EXAMPLES / name), run_name="__main__")
    return buf.getvalue()


def test_gallery_dispatching_stateless_runs() -> None:
    out = _run("gallery_dispatching_stateless.py")
    for rule in ("SPT", "EDD", "ODD", "MODD", "CR", "FCFS", "WINQ"):
        assert rule in out
    assert "%Tardy" in out


def test_gallery_dispatching_stateless_rows_differ() -> None:
    # Spec success criterion: comparison rows must be distinct, not degenerate ties.
    import importlib.util

    path = EXAMPLES / "gallery_dispatching_stateless.py"
    spec = importlib.util.spec_from_file_location("gds", path)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    spt_tis = mod.run_rule(mod.RULES["SPT"])[1]
    fcfs_tis = mod.run_rule(mod.RULES["FCFS"])[1]
    assert spt_tis != fcfs_tis, "SPT and FCFS produced identical AvgTIS — harness is degenerate"
