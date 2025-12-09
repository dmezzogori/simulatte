from __future__ import annotations

import pytest

from simulatte.simpy_extension.hash_store.hash_store import HashStore
from simulatte.simpy_extension.multi_store.multi_store import MultiStore
from simulatte.simpy_extension.sequential_multi_store.sequential_multi_store import SequentialMultiStore
from simulatte.simpy_extension.sequential_store.sequential_store import SequentialStore


def test_sequential_store_put_get_order():
    store = SequentialStore(capacity=3)
    env = store.env

    store.put("first")
    store.put("second")
    env.run()

    assert store.output_level == 1
    assert store.internal_store_level == 1
    assert store.level == 2

    getter = store.get(lambda _: True)
    env.run()
    assert getter.value == "first"
    assert store.output_level == 1  # second item promoted to output
    assert store.internal_store_level == 0


def test_sequential_store_capacity_validation():
    with pytest.raises(ValueError):
        SequentialStore(capacity=1)


def test_multi_store_put_and_get_returns_available_items():
    store = MultiStore(capacity=3)
    env = store.env

    store.put([1, 2])
    env.run()
    assert store.level == 2

    one = store.get(1)
    env.run()
    assert one.value == [1]
    assert store.level == 1

    all_items = store.get(5)
    env.run()
    assert all_items.value == [2]
    assert store.level == 0


def test_sequential_multi_store_custom_put_and_get():
    store = SequentialMultiStore(capacity=4)
    env = store.env

    env.process(store._do_put([1, 2, 3]))
    env.run()
    assert store.output_level == 1
    assert store.internal_store_level == 2

    getter = env.process(store._do_get(lambda _: True))
    env.run()
    assert getter.value == 1
    assert store.output_level == 1  # next item promoted


def test_hash_store_put_get_and_missing_key():
    env = SequentialStore().env
    env_store = HashStore(env=env, capacity=2)

    env_store.put(key="a", item=10)
    env.run()
    assert env_store.level == 1

    getter = env_store.get(key="a")
    env.run()
    assert getter.value == 10
    assert env_store.level == 0

    with pytest.raises(KeyError):
        env_store.get(key="missing", raise_missing=True)
        env.run()

    with pytest.raises(ValueError):
        HashStore(env=env, capacity=0)
