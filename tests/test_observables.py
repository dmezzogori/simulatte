from __future__ import annotations

import pytest

from simulatte.observables.area.base import Area


def test_area_append_remove_and_history(env):
    area = Area(capacity=1, owner="owner", env=env)
    assert area.is_empty
    area.append_exceed("item")
    assert area.last_in == "item"
    assert area.free_space == (float("inf") - 1 if area.capacity == float("inf") else 0)
    popped = area.pop()
    assert popped == "item"
    assert area.last_out == "item"


def test_area_capacity_limits(env):
    area = Area(capacity=1, owner="owner", env=env)
    area.append("a")
    with pytest.raises(RuntimeError):
        area.append("b")
