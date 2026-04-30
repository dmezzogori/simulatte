from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics.graph import Node
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.speed import TrapezoidalProfile


@pytest.fixture
def env() -> Environment:
    return Environment()


@pytest.fixture
def simple_speed_profile() -> TrapezoidalProfile:
    return TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0)


@pytest.fixture
def node_a() -> Node:
    return Node(id="A", x=0.0, y=0.0)


@pytest.fixture
def node_b() -> Node:
    return Node(id="B", x=10.0, y=0.0)


@pytest.fixture
def steel_sku() -> SKU:
    return SKU(id="STEEL", weight=10.0, volume=0.05)
