"""Bounded diagnostic tails; loss is counted, never fed back into semantic events."""

from __future__ import annotations

import threading
import math
import time


class Diagnostics:
    TAIL = 1 << 20
    RATE = 64 << 10

    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self._epoch = clock()
        self._used = 0
        self._tails = {"stdout": bytearray(), "stderr": bytearray()}
        self._pending = {"stdout": bytearray(), "stderr": bytearray()}
        self._dropped = 0
        self._lock = threading.Lock()

    def append(self, stream: str, data: bytes) -> None:
        with self._lock:
            tail = self._tails[stream]
            tail.extend(data[-self.TAIL :])
            del tail[: -self.TAIL]
            if self.clock() - self._epoch >= 1:
                self._epoch = self.clock()
                self._used = 0
            # Budget the actual UTF-8 representation, including replacement chars.
            represented = data.decode("utf-8", errors="replace").encode("utf-8")
            available = min(self.RATE - self._used, self.RATE - sum(map(len, self._pending.values())))
            accepted = represented[:available].decode("utf-8", errors="ignore").encode("utf-8")
            self._used += len(accepted)
            self._pending[stream].extend(accepted)
            self._dropped += max(0, len(represented) - len(accepted))

    def tail(self, stream: str) -> bytes:
        with self._lock:
            return bytes(self._tails[stream])

    def take(self) -> dict:
        with self._lock:
            result: dict = {name: bytes(data).decode("utf-8") for name, data in self._pending.items()}
            for data in self._pending.values():
                data.clear()
            result["dropped_bytes"] = self._dropped
            result["truncated"] = self._dropped > 0
            return result


class Progress:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.last_change = self.started
        self.state = {
            "phase": "starting",
            "simulation_time": 0.0,
            "event_count": 0,
            "spool_bytes": 0,
            "spool_files": 0,
            "backpressure": False,
        }
        self._lock = threading.Lock()

    def update(self, **values) -> None:
        # A malformed child must not grow the supervisor's retained state with
        # arbitrary keys or values, even across otherwise bounded frames.
        if set(values) - self.state.keys():
            raise ValueError("Unknown progress fields")
        for name, value in values.items():
            if name == "phase":
                valid = value in ("starting", "inspecting", "preparing", "ready", "running", "finalizing")
            elif name == "simulation_time":
                valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
            elif name == "backpressure":
                valid = type(value) is bool
            else:
                maximum = {"event_count": 2**53 - 1, "spool_bytes": 256 << 20, "spool_files": 1024}[name]
                valid = type(value) is int and 0 <= value <= maximum
            if not valid:
                raise ValueError("Invalid progress value: " + name)
        with self._lock:
            if any(self.state.get(key) != value for key, value in values.items()):
                self.last_change = self.clock()
                self.state.update(values)

    def heartbeat(self) -> dict:
        with self._lock:
            now = self.clock()
            return {
                **self.state,
                "elapsed": now - self.started,
                "stalled": not self.state["backpressure"] and now - self.last_change >= 10,
            }
