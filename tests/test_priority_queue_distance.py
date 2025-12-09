from __future__ import annotations


import pytest

from simulatte.simpy_extension.queue import PriorityItem, PriorityQueue
from simulatte.stores.warehouse_location import distance


def test_priority_queue_push_pop_and_capacity():
    items = [PriorityItem(key) for key in [5, 1, 3]]
    pq = PriorityQueue(items=items, maxlen=4)

    assert len(pq) == 3
    assert pq.pop().priority == 1  # smallest first

    pq.push(PriorityItem(0))
    assert pq.pop().priority == 0

    pq.maxlen = 1
    with pytest.raises(Exception):
        pq.push(PriorityItem(10))


class FakeLocation:
    def __init__(self, x, y):
        self.coordinates = (x, y)


def test_distance_functions():
    loc_a = FakeLocation(0, 0)
    loc_b = FakeLocation(3, 4)

    assert distance.euclidean(loc_a, loc_b) == 5
    assert distance.manhattan(loc_a, loc_b) == abs(loc_a.coordinates[0] - loc_a.coordinates[1]) + abs(
        loc_b.coordinates[0] - loc_b.coordinates[1]
    )
