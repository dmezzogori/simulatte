from __future__ import annotations

import heapq


class PriorityItem:
    """An object with the priority attribute"""

    def __init__(self, key):
        self.key = key

    def __lt__(self, other):
        return self.key < other.key

    @property
    def priority(self):
        return self.key


class PriorityQueue:
    """
    An instance of this class represents a priority queue for items
    that already have the attribute prority.
    """

    def __init__(self, items=None, maxlen=float("inf")):
        self.items = items
        self.maxlen = maxlen
        if items is not None:
            self.items = [(i.priority, i) for i in items]
        heapq.heapify(self.items)

    def __len__(self):
        return len(self.items)

    def push(self, item):
        if self.items.__len__() >= self.maxlen:
            raise Exception("Capacity of PriorityQueue exceeded.")
        heap_item = (item.priority, item)
        heapq.heappush(self.items, heap_item)

    def pop(self):
        item = heapq.heappop(self.items)
        return item[1]

    def pushpop(self, item):
        heap_item = (item.priority, item)
        to_return = heapq.heappushpop(heap_item)
        return to_return[1]
