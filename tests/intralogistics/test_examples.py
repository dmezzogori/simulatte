from __future__ import annotations

import runpy
from pathlib import Path


def test_intralogistics_simple_example_runs(capsys) -> None:
    example = Path(__file__).resolve().parents[2] / "examples" / "intralogistics_simple.py"

    runpy.run_path(str(example), run_name="__main__")

    captured = capsys.readouterr()
    assert "Simple intralogistics example" in captured.out
    assert "Layout nodes: 5" in captured.out
    assert "AGVs: 2" in captured.out
    assert "Completed orders: 4/4" in captured.out
    assert "WH-A inventory: 100 -> 69" in captured.out
    assert "WH-B inventory: 0 -> 31" in captured.out
