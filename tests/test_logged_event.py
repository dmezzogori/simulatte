from __future__ import annotations

from simulatte.events.logged_event import LoggedEvent


def test_logged_event_succeed_logs_and_returns_value():
    event = LoggedEvent()
    result = event.succeed({"message": "hello"})
    assert result.value == {"message": "hello"}
    assert event.triggered
