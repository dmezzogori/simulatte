from __future__ import annotations

from simulatte.simpy_extension.filter_multi_store.filter_multi_store import FilterMultiStore


def test_filter_multi_store_get_all_items_when_no_filter(env):
    store = FilterMultiStore(env=env)

    store.put(items=[1, 2, 3])
    store.env.run()

    get_event = store.get(filter=None)
    store.env.run()

    assert get_event.value == [1, 2, 3]
    assert store.level == 0


def test_filter_multi_store_filters_items(env):
    store = FilterMultiStore(env=env)
    store.put(items=[1, 2, 3, 4])
    store.env.run()

    get_event = store.get(filter=lambda x: x % 2 == 0)
    store.env.run()

    assert get_event.value == [2, 4]
    assert store.level == 2
    assert store.items == [1, 3]
