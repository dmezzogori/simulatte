from __future__ import annotations

import pytest

from simulatte.buffer import EOQBuffer
from simulatte.unitload import PaperSheet


def test_eoq_buffer_rejects_init_over_capacity(env):
    with pytest.raises(ValueError):
        EOQBuffer(
            items_type=PaperSheet,
            reorder_level=1,
            eoq=2,
            get_time=1,
            put_time=1,
            capacity=1,
            init=2,
            env=env,
        )


def test_eoq_buffer_put_get_and_refill_flag(env):
    buffer = EOQBuffer(
        items_type=PaperSheet,
        reorder_level=2,
        eoq=3,
        get_time=1,
        put_time=1,
        init=0,
        env=env,
    )

    # Refill with two items; each put costs 1 time unit
    buffer.put(items=[PaperSheet(), PaperSheet()])
    buffer.env.run()

    assert buffer.level == 2
    assert buffer.need_refill is False
    assert buffer.env.now == 2

    # Remove one item; get costs 1 time unit
    get_proc = buffer.get()
    buffer.env.run()

    assert get_proc.value is not None
    assert isinstance(get_proc.value, PaperSheet)
    assert buffer.level == 1
    assert buffer.env.now == 3
    assert buffer.need_refill is True


def test_eoq_buffer_initial_items_and_capacity_property(env):
    buffer = EOQBuffer(
        items_type=PaperSheet,
        reorder_level=1,
        eoq=1,
        get_time=0,
        put_time=0,
        capacity=5,
        init=1,
        env=env,
    )

    assert buffer.capacity == 5
    assert buffer.level == 1
