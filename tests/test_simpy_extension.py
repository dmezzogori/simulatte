from __future__ import annotations

import pytest

from simulatte.simpy_extension.hash_store import HashStore
from simulatte.simpy_extension.multi_store import MultiStore
from simulatte.simpy_extension.sequential_store.sequential_store import SequentialStore


def test_sequential_store_put_get_order(env):
    store = SequentialStore(capacity=3, env=env)

    store.put("first")
    store.put("second")
    store.env.run()

    assert store.output_level == 2
    assert store.level == 2

    getter = store.get(lambda item: item == "first")
    store.env.run()
    assert getter.value == "first"
    assert store.level == 1


def test_sequential_store_capacity_validation(env):
    with pytest.raises(ValueError):
        SequentialStore(capacity=0, env=env)


def test_multi_store_put_and_get_returns_available_items(env):
    store = MultiStore(capacity=3, env=env)

    store.put([1, 2])
    store.env.run()
    assert store.level == 2

    one = store.get(1)
    store.env.run()
    assert one.value == [1]
    assert store.level == 1

    all_items = store.get(5)
    store.env.run()
    assert all_items.value == [2]
    assert store.level == 0


def test_hash_store_put_get_and_missing_key(env):
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
