from __future__ import annotations

import functools
import operator
from typing import TYPE_CHECKING

import pytest

from simulatte.simpy_extension import SequentialMultiStore

if TYPE_CHECKING:
    from simpy import Environment


def putter(env, s, items):
    yield s.put(items)
    print(f"{env.now} putted {items}")


def getter(env, s, item):
    yield s.get(functools.partial(operator.eq, item))
    print(f"{env.now} getted {item}")


@pytest.fixture(scope="function")
def sequential_multi_store(env: Environment):
    return SequentialMultiStore(env, capacity=5)


def test_sequential_multi_store_01(env: Environment, sequential_multi_store: SequentialMultiStore):
    def put_items():
        yield env.timeout(10)
        yield sequential_multi_store.put(["A", "B", "C"])
        assert sequential_multi_store.items == ["A", "B", "C"]

    def put_more_items():
        yield env.timeout(20)
        yield sequential_multi_store.put(["D", "E"])
        assert sequential_multi_store.items == ["A", "B", "C", "D", "E"]

    def get_item():
        yield env.timeout(30)
        yield sequential_multi_store.get(lambda x: x == "A")
        assert sequential_multi_store.items == ["B", "C", "D", "E"]

    def test():
        # At time 0 we check that the store is empty
        assert sequential_multi_store.items == []

        # At time 10 we check that the store contains the items A, B, C
        yield env.timeout(10)
        # assert sequential_multi_store.items == ["A", "B", "C"]

    env.process(put_items())
    env.process(put_more_items())
    env.process(get_item())
    env.process(test())
    env.run()
