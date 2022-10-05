from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from simulatte.simpy_extension import FilterMultiStore

if TYPE_CHECKING:
    from simpy import Environment


@pytest.fixture(scope="function")
def filter_multi_store(env: Environment):
    return FilterMultiStore(env, capacity=5)


def test_filter_multi_store_01(env: Environment, filter_multi_store: FilterMultiStore):
    """
    Test the FilterMultiStore by simulating the following scenario:
    - The FilterMultiStore is empty.
    - The FilterMultiStore is filled with three items.
    - The FilterMultiStore is filled with other two items.
    - We retrieve the even items. It should be the second and fourth ones.
    """

    def test():
        items = [1, 2, 3]
        with filter_multi_store.put(items) as put_req:
            yield put_req
        # we test that the FilterMultiStore has the correct number of items
        assert filter_multi_store.items == items
        assert filter_multi_store.level == len(items)

        items = [4, 5]
        with filter_multi_store.put(items) as put_req:
            yield put_req

        # we test that the FilterMultiStore has the correct number of items
        assert filter_multi_store.items == [1, 2, 3, 4, 5]
        assert filter_multi_store.level == 5

        # we retrieve the even numbers from the FilterMultiStore using a lambda
        with filter_multi_store.get(filter=lambda x: x % 2 == 0) as get_req:
            items = yield get_req
        # we test that we retrieved the correct items
        assert items == [2, 4]
        assert filter_multi_store.items == [1, 3, 5]
        assert filter_multi_store.level == 3

    env.process(test())
    env.run()


def test_filter_multi_store_02(env: Environment, filter_multi_store: FilterMultiStore):
    """
    Test the FilterMultiStore by simulating the following scenario:
    - The FilterMultiStore is empty.
    - We try to retrieve the even items.
    - We test that the FilterMultiStore get queue is filled with one request, still pending.
    """

    def empty_get():
        # we retrieve the even numbers from the FilterMultiStore using a lambda
        with filter_multi_store.get(filter=lambda x: x % 2 == 0) as get_req:
            yield get_req

    def test():
        # a simple timout to make sure the empty_get process is started before this test
        yield env.timeout(1)
        # we test that the FilterMultiStore get queue is filled with one request, still pending
        assert len(filter_multi_store.get_queue) == 1

    env.process(empty_get())
    env.process(test())
    env.run()
