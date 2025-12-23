from __future__ import annotations

from simulatte.environment import Environment
from simulatte.logger import _format_sim_time, _patch_sim_time


def test_format_sim_time_zero() -> None:
    result = _format_sim_time(0)
    # When input is int, // returns int, so days=0 formats as "00"
    assert result == "00d 00:00:0.00"


def test_format_sim_time_seconds_only() -> None:
    result = _format_sim_time(45.5)
    # When input is float, // returns float, so days=0.0 formats as "0.0"
    assert result == "0.0d 00:00:45.50"


def test_format_sim_time_minutes() -> None:
    result = _format_sim_time(125.25)  # 2 minutes, 5.25 seconds
    assert result == "0.0d 00:02:5.25"


def test_format_sim_time_hours() -> None:
    result = _format_sim_time(3661.5)  # 1 hour, 1 minute, 1.5 seconds
    assert result == "0.0d 01:01:1.50"


def test_format_sim_time_days() -> None:
    result = _format_sim_time(90061.75)  # 1 day, 1 hour, 1 minute, 1.75 seconds
    assert result == "1.0d 01:01:1.75"


def test_format_sim_time_multiple_days() -> None:
    result = _format_sim_time(259200.0)  # 3 days as float
    assert result == "3.0d 00:00:0.00"


def test_patch_sim_time_with_env() -> None:
    env = Environment()
    env.run(until=100)  # Advance to t=100

    record = {"extra": {"env": env}}
    _patch_sim_time(record)  # type: ignore[arg-type]

    # env.now is float, so format uses float division
    assert record["extra"]["now"] == "0.0d 00:01:40.00"


def test_patch_sim_time_with_now_seconds() -> None:
    record = {"extra": {"now_seconds": 3600}}  # int
    _patch_sim_time(record)  # type: ignore[arg-type]

    # int input produces int division, but gets converted to float
    assert record["extra"]["now"] == "0.0d 01:00:0.00"


def test_patch_sim_time_with_neither() -> None:
    record = {"extra": {}}
    _patch_sim_time(record)  # type: ignore[arg-type]

    assert record["extra"]["now"] == "--d --:--:--.--"


def test_patch_sim_time_env_takes_precedence() -> None:
    env = Environment()
    env.run(until=50)

    record = {"extra": {"env": env, "now_seconds": 100}}
    _patch_sim_time(record)  # type: ignore[arg-type]

    # env.now (50.0) should take precedence over now_seconds (100)
    assert record["extra"]["now"] == "0.0d 00:00:50.00"


def test_patch_sim_time_with_int_now_seconds() -> None:
    record = {"extra": {"now_seconds": 60}}  # int, not float
    _patch_sim_time(record)  # type: ignore[arg-type]

    # int converted to float, then used
    assert record["extra"]["now"] == "0.0d 00:01:0.00"


def test_patch_sim_time_with_invalid_env() -> None:
    # env without 'now' attribute
    class FakeEnv:
        pass

    record = {"extra": {"env": FakeEnv()}}
    _patch_sim_time(record)  # type: ignore[arg-type]

    assert record["extra"]["now"] == "--d --:--:--.--"


def test_patch_sim_time_with_env_now_string() -> None:
    # env with 'now' as string (invalid)
    class FakeEnv:
        now = "not a number"

    record = {"extra": {"env": FakeEnv()}}
    _patch_sim_time(record)  # type: ignore[arg-type]

    assert record["extra"]["now"] == "--d --:--:--.--"
