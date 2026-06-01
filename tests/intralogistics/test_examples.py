from __future__ import annotations

import re
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


def test_intralogistics_intermediate_example_runs(monkeypatch, capsys) -> None:
    import matplotlib.pyplot

    monkeypatch.setattr(matplotlib.pyplot, "show", lambda: None)

    example = Path(__file__).resolve().parents[2] / "examples" / "intralogistics_intermediate.py"
    runpy.run_path(str(example), run_name="__main__")

    captured = capsys.readouterr()
    assert "Manufacturing Plant Floor" in captured.out
    assert "10 nodes" in captured.out
    assert "3 AGVs" in captured.out
    assert "8 submitted" in captured.out
    assert "8 completed" in captured.out
    assert "0 failed" in captured.out
    assert "Fleet report:" in captured.out
    assert "Warehouse inventory:" in captured.out
    assert "Steel Sheets" in captured.out
    assert "Plastic Pellets" in captured.out
    assert "Electronics" in captured.out


def test_intralogistics_advanced_example_runs(monkeypatch, capsys) -> None:
    import matplotlib.pyplot

    monkeypatch.setattr(matplotlib.pyplot, "show", lambda: None)

    example = Path(__file__).resolve().parents[2] / "examples" / "intralogistics_advanced.py"
    runpy.run_path(str(example), run_name="__main__")

    captured = capsys.readouterr()
    assert "Multi-Warehouse Distribution Hub" in captured.out
    assert "16 nodes" in captured.out
    assert "5 AGVs" in captured.out
    assert "Shift summary:" in captured.out
    assert "0 failed" in captured.out
    assert "replenishment)" in captured.out
    assert "Outbound:" in captured.out
    assert "Replenishment:" in captured.out
    # The 8-hour shift must still exhibit at least one replenishment (a late-shift,
    # time-triggered event). Guard against a future horizon change silently dropping it.
    repl = re.search(r"Replenishment:\s+(\d+) completed", captured.out)
    assert repl is not None and int(repl.group(1)) >= 1, "expected >=1 replenishment"
    # Charging is surfaced per-AGV in the fleet report as "charging=<pct>". The field is
    # printed for every AGV unconditionally, so assert at least one AGV has a non-zero
    # charging percentage to actually guard the documented charging behavior.
    charging_pcts = re.findall(r"charging=([\d.]+)%", captured.out)
    assert charging_pcts and any(float(p) > 0 for p in charging_pcts), "expected >=1 AGV to have charged"
    assert "Warehouse inventory" in captured.out
    assert "Receiving:" in captured.out
    assert "Bulk Storage:" in captured.out
    assert "Dispatch:" in captured.out
    assert "Fleet report:" in captured.out
    assert "Fleet utilization:" in captured.out
    assert "EMA metrics:" in captured.out
    assert "Fulfillment time:" in captured.out
