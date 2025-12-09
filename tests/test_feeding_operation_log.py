from __future__ import annotations

import pytest

from simulatte.operations.feeding_operation import FeedingOperationLog


class DummyFeedingOperation:
    def __repr__(self):
        return "DummyFO"


def test_feeding_operation_log_properties_and_ordering():
    log = FeedingOperationLog(DummyFeedingOperation(), created=1.0)
    log.started_retrieval = 2.0
    log.finished_retrieval = 3.0
    log.started_agv_trip_to_store = 4.0
    log.finished_agv_trip_to_store = 5.0
    log.started_loading = 6.0
    log.finished_loading = 7.0
    log.started_agv_trip_to_cell = 8.0
    log.finished_agv_trip_to_cell = 9.0
    log.started_agv_trip_to_staging_area = 10.0
    log.finished_agv_trip_to_staging_area = 11.0
    log.started_agv_trip_to_internal_area = 12.0
    log.finished_agv_trip_to_internal_area = 13.0
    log.started_agv_return_trip_to_store = 14.0
    log.finished_agv_return_trip_to_store = 15.0
    log.started_agv_unloading_for_return_trip_to_store = 16.0
    log.finished_agv_unloading_for_return_trip_to_store = 17.0
    log.started_agv_return_trip_to_recharge = 18.0
    log.finished_agv_return_trip_to_recharge = 19.0

    assert log.feeding_operation_starts == 3.0
    assert log.agv_move_to_store == 1.0
    assert log.agv_waiting_at_store == 1.0
    assert log.agv_move_from_store_to_cell == 1.0
    assert log.agv_waiting_at_cell == 1.0
    assert log.agv_waiting_at_staging == 1.0
    assert log.agv_move_from_staging_to_internal == 1.0
    assert log.agv_waiting_at_internal == 1.0
    assert log.feeding_operation_life_time == 16.0

    log.check()  # should not raise

    other = FeedingOperationLog(DummyFeedingOperation(), created=2.0)
    assert log < other
    assert log != other


def test_feeding_operation_log_validation_errors():
    log = FeedingOperationLog(DummyFeedingOperation(), created=0)
    log.started_retrieval = 2
    log.finished_retrieval = 1  # invalid ordering
    with pytest.raises(ValueError):
        log.check()
