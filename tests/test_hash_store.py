from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from simulatte.simpy_extension import HashStore

if TYPE_CHECKING:
    from simpy import Environment


@pytest.fixture(scope="function")
def hash_store(env: Environment):
    return HashStore(env, 10)


def test_hash_store_01(env: Environment, hash_store: HashStore):
    """
    Test the HashStore by simulating the following scenario:
    - The HashStore is empty.
    - The HashStore is filled with one item.
    - The HashStore is filled with another item.
    - We retrieve the first item.
    - We retrieve the second item.
    - The HashStore should be empty.
    """

    def test():
        with hash_store.put("a", 1) as put_req:
            yield put_req

        assert hash_store.items == {"a": 1}
        assert hash_store.level == 1

        with hash_store.put("b", 2) as put_req:
            yield put_req

        assert hash_store.items == {"a": 1, "b": 2}
        assert hash_store.level == 2

        with hash_store.get("a") as get_req:
            item = yield get_req
        assert item == 1
        assert hash_store.items == {"b": 2}
        assert hash_store.level == 1

        with hash_store.get("b") as get_req:
            item = yield get_req
        assert item == 2
        assert hash_store.items == {}
        assert hash_store.level == 0

    env.process(test())
    env.run()


def test_hash_store_02(env: Environment, hash_store: HashStore):
    """
    Test the possibility to raise a KeyError when trying to get a missing item from the HashStore.
    """

    def test():
        with pytest.raises(KeyError):
            with hash_store.get("a", raise_missing=True) as get_req:
                yield get_req

    env.process(test())
    env.run()
