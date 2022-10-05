from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from simulatte.simpy_extension import MultiStore

if TYPE_CHECKING:
    from simpy import Environment


@pytest.fixture(scope="function")
def multi_store(env: Environment):
    return MultiStore(env, capacity=5)


def test_multi_store_01(env: Environment, multi_store: MultiStore):
    """
    Test the MultiStore by simulating the following scenario:
    - The MultiStore is empty.
    - The MultiStore is filled with three items.
    - The MultiStore is filled with other two items.
    - We retrieve one item. It should be the first one.
    - We retrieve two items. It should be the second and third ones.
    - We retrieve the remaining items. The MultiStore should be empty.
    """

    def test():
        items = [1, 2, 3]
        with multi_store.put(items) as put_req:
            yield put_req
        # we test that the MultiStore has the correct number of items
        assert multi_store.items == items
        assert multi_store.level == len(items)

        items = [4, 5]
        with multi_store.put(items) as put_req:
            yield put_req

        # we test that the MultiStore has the correct number of items
        assert multi_store.items == [1, 2, 3, 4, 5]
        assert multi_store.level == 5

        # we retrieve one item
        with multi_store.get(n=1) as get_req:
            item = yield get_req
        # we test that we retrieved the correct item
        assert item == [1]
        assert multi_store.items == [2, 3, 4, 5]
        assert multi_store.level == 4

        # we retrieve two more items
        with multi_store.get(n=2) as get_req:
            items = yield get_req
        assert items == [2, 3]
        assert multi_store.items == [4, 5]
        assert multi_store.level == 2

        # we retrieve the remaining items
        with multi_store.get(n=2) as get_req:
            items = yield get_req
        assert items == [4, 5]
        assert multi_store.items == []
        assert multi_store.level == 0

    env.process(test())
    env.run()


def test_multi_store_02(env: Environment, multi_store: MultiStore):
    """
    Test the MultiStore by simulating the following scenario:
    - The MultiStore is empty.
    - The MultiStore is filled with three items.
    - We retrieve more items than the ones available.
    - The MultiStore should return all the available items, and be empty.
    """

    def test():
        items = [1, 2, 3]
        with multi_store.put(items) as put_req:
            yield put_req

        with multi_store.get(n=100) as get_req:
            items = yield get_req

        assert items == [1, 2, 3]
        assert multi_store.items == []
        assert multi_store.level == 0

    env.process(test())
    env.run()
