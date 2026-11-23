"""The random worlds of ``random_worlds.py``: a deterministic description, equal runs in both modes, a monitor that
detects a violation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from simulatte.intralogistics.graph import Node
from simulatte.intralogistics.traffic_holdings import HoldingsLedger
from tests.intralogistics.random_worlds import run_world, world_spec

ROOT = Path(__file__).resolve().parents[2]
SEEDS = (45, 54, 82, 88, 110)
"""Between them: block lengths, recorded arc lengths, zones with and without hold-back, nodes of capacity 2, parking,
low batteries, cancellations and interruptions, one-way arcs, passing segments and two fleets on one controller."""


def test_world_spec_is_deterministic_per_seed():
    assert world_spec(45) == world_spec(45)
    assert world_spec(45) != world_spec(46)
    code = "from tests.intralogistics.random_worlds import world_spec; print(repr(world_spec(45)))"
    for hash_seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        out = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True
        )
        assert out.stdout.strip() == repr(world_spec(45))


def test_no_world_has_two_nodes_at_one_position():
    for seed in range(200):
        positions = [(x, y) for _, x, y in world_spec(seed).nodes]
        assert len(set(positions)) == len(positions), seed


@pytest.mark.parametrize("seed", SEEDS)
def test_a_world_runs_alike_built_explicitly_and_through_the_layout(seed):
    spec = world_spec(seed)
    explicit = run_world(spec, mode="explicit", controller="reservation")
    layout = run_world(spec, mode="layout", controller="reservation")
    assert layout.events and layout.quiescent and not layout.violations
    assert explicit == layout


def test_the_monitor_reports_an_admission_that_ignores_node_capacity(monkeypatch):
    admit = HoldingsLedger.blockers

    def blockers(self, agv, item, among=None):
        if among is None and isinstance(item.resource, Node):
            return []
        return admit(self, agv, item, among)

    monkeypatch.setattr(HoldingsLedger, "blockers", blockers)
    record = run_world(world_spec(88), mode="layout", controller="reservation")
    found = {violation[1] for violation in record.violations}
    assert {"PHYSICAL node over capacity", "LEDGER node over capacity"} <= found
