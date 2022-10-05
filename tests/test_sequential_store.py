from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from simulatte.simpy_extension import SequentialStore

if TYPE_CHECKING:
    from simpy import Environment


@pytest.fixture(scope="function")
def sequential_store(env: Environment):
    return SequentialStore(env, 10)


def test_sequential_store_single_request(
    env: Environment, sequential_store: SequentialStore
):
    def get_item():
        yield sequential_store.get(lambda x: x == "A")

    def put_item():
        yield env.timeout(10)
        yield sequential_store.put("A")

    def test():
        yield env.timeout(1)

        assert len(sequential_store._output.get_queue) == 1

        yield env.timeout(9)  # 10

        assert sequential_store.items == ["A"]

        yield env.timeout(1)  # 11

        assert len(sequential_store._output.get_queue) == 0
        assert sequential_store.items == []

    env.process(get_item())
    env.process(put_item())
    env.process(test())
    env.run()


def test_sequential_store_multiple_requests(
    env: Environment, sequential_store: SequentialStore
):
    """
    Test the SequentialStore by simulating the following scenario:
    - The SequentialStore is empty.
    - At time 0, we request to get the item "A".
    - At time 10, we put the item "B".
    - At time 20, we put the item "A".
    - At time 30, we request to get the item "B".
    Thus, the following should happen:
    - The process requesting to get the item "A" should wait until time 20.
    - The process requesting to get the item "B" will receive the item immediately, at time 30.
    """

    def get_item_a():
        """
        At time 0, we request to get the item "A".
        """
        yield env.timeout(0)
        yield sequential_store.get(lambda i: i == "A")

    def put_item_b():
        """
        At time 10, we put the item "B".
        """
        yield env.timeout(10)
        yield sequential_store.put("B")

    def put_item_a():
        """
        At time 20, we put the item "A".
        """
        yield env.timeout(20)
        yield sequential_store.put("A")

    def get_item_b():
        """
        At time 30, we request to get the item "B".
        """
        yield env.timeout(30)
        yield sequential_store.get(lambda i: i == "B")

    def test():
        yield env.timeout(10)  # env.now = 10
        # we test that the SequentialStore has received the item "B"
        assert sequential_store.items == ["B"]
        # we test that the overall SequentialStore level is 1
        # (the internal store is empty, while the output contains the item "B")
        assert sequential_store.level == 1
        assert sequential_store.internal_store_level == 0
        assert sequential_store.output_level == 1
        # we test that the process requesting to get the item "A" is still waiting
        assert len(sequential_store._output.get_queue) == 1

        yield env.timeout(10)  # env.now = 20
        # we test that the SequentialStore has received the item "A"
        assert sequential_store.items == ["B", "A"]
        assert sequential_store.level == 2
        assert sequential_store.internal_store_level == 1
        assert sequential_store.output_level == 1
        assert len(sequential_store._output.get_queue) == 1

        yield env.timeout(1)  # env.now = 21
        # we test that the request to get the item "A" has been fulfilled
        assert sequential_store.items == ["B", "A"]
        assert sequential_store.level == 2
        assert sequential_store.internal_store_level == 1
        assert sequential_store.output_level == 1
        assert len(sequential_store._output.get_queue) == 1

        yield env.timeout(9)  # env.now = 30
        # we test that the request to get the item "B" has been fulfilled
        assert sequential_store.items == ["A"]
        assert sequential_store.level == 1
        # "A" is still in the internal store
        assert sequential_store.internal_store_level == 1
        assert sequential_store.output_level == 0
        # item "A" should have been moved to the output store
        assert len(sequential_store._output.get_queue) == 1

        yield env.timeout(1)  # env.now = 31
        # we test that the request to get the item "A" has been fulfilled
        assert sequential_store.items == []
        assert sequential_store.level == 0
        assert sequential_store.internal_store_level == 0
        assert sequential_store.output_level == 0
        assert len(sequential_store._output.get_queue) == 0

    env.process(get_item_a())
    env.process(put_item_b())
    env.process(put_item_a())
    env.process(get_item_b())
    env.process(test())
    env.run()
