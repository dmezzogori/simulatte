"""`benchmarks/layout_safety.py`: the safety runner exits 0 on clean runs and 1 on an exception, a run cut by its
wall-clock limit and a monitor violation, and refuses a limit that would remove the bound."""

from __future__ import annotations

import dataclasses
import importlib.util
from pathlib import Path
import signal
import time

import pytest

from tests.intralogistics import random_worlds

pytestmark = pytest.mark.skipif(not hasattr(signal, "SIGALRM"), reason="the runner bounds a run with SIGALRM")

SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "layout_safety.py"
SPEC = importlib.util.spec_from_file_location("layout_safety", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
layout_safety = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(layout_safety)

ONE_WORLD = ["--worlds", "1", "--first", "5", "--jobs", "1"]


def test_clean_runs_exit_zero_and_are_counted(capsys):
    assert layout_safety.main([*ONE_WORLD, "--worlds", "2"]) == 0
    out = capsys.readouterr().out
    assert "worlds 2 (seeds 5..6), runs 12," in out
    assert "runs that raised 0, runs that did not return within the limit 0" in out


def test_a_run_that_raises_exits_one(monkeypatch, capsys):
    def raises(spec, **options):
        raise RuntimeError("boom")

    monkeypatch.setattr(layout_safety, "run_world", raises)
    assert layout_safety.main(ONE_WORLD) == 1
    out = capsys.readouterr().out
    assert "runs that raised 6," in out and "RAISED seed 5 reservation shortest: RuntimeError: boom" in out


def test_a_run_cut_by_the_wall_clock_limit_is_a_failure_not_a_pass(monkeypatch, capsys):
    monkeypatch.setattr(layout_safety, "run_world", lambda spec, **options: time.sleep(5))
    started = time.perf_counter()
    assert layout_safety.main([*ONE_WORLD, "--limit", "0.05"]) == 1
    assert time.perf_counter() - started < 4
    out = capsys.readouterr().out
    assert "runs that did not return within the limit 6" in out
    assert "DID NOT RETURN seed 5 reservation shortest: no return within 0.05 s" in out


def test_a_violation_exits_one_and_names_its_run(monkeypatch, capsys):
    real = random_worlds.run_world

    def violating(spec, **options):
        return dataclasses.replace(real(spec, **options), violations=((1.0, "seeded", "x"),))

    monkeypatch.setattr(layout_safety, "run_world", violating)
    assert layout_safety.main(ONE_WORLD) == 1
    out = capsys.readouterr().out
    assert "runs with monitor violations 6" in out and "VIOLATION 'seeded': 6 runs" in out


def test_a_limit_that_would_remove_the_bound_is_refused(capsys):
    for text in ("0", "-1"):
        with pytest.raises(SystemExit):
            layout_safety.main([*ONE_WORLD, "--limit", text])
    assert "must be positive" in capsys.readouterr().err
