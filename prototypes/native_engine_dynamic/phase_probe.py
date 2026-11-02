"""Research timing recorder, copied only into disposable instrumented runtimes.

Diagnostics never enter simulation state, artifacts or result proposals. Coarse
spans retain a local monotonic timeline; hot calls retain aggregates only.
"""

from contextlib import contextmanager
from functools import wraps
import json
import os
from pathlib import Path
import threading
import time

_ACTIVE = None


class Recorder:
    def __init__(self, output, *, label="", clock=time.perf_counter_ns):
        self.output, self.label, self.clock = Path(output), label, clock
        self.started = clock()
        self.pid = os.getpid()
        self.local = threading.local()
        self.threads = []
        self.lock = threading.Lock()

    def _thread(self):
        if not hasattr(self.local, "data"):
            data = dict(thread=threading.get_ident(), stack=[], totals={}, spans=[])
            with self.lock:
                self.threads.append(data)
            self.local.data = data
        return self.local.data

    @contextmanager
    def scope(self, name, *, span=True):
        data = self._thread()
        stack = data["stack"]
        frame = [self.clock(), 0]
        stack.append(frame)
        try:
            yield
        finally:
            end = self.clock()
            assert stack.pop() is frame
            duration = end - frame[0]
            exclusive = duration - frame[1]
            assert 0 <= exclusive <= duration
            if stack:
                stack[-1][1] += duration
            total = data["totals"].setdefault(name, dict(count=0, inclusive_ns=0, exclusive_ns=0))
            total["count"] += 1
            total["inclusive_ns"] += duration
            total["exclusive_ns"] += exclusive
            if span:
                data["spans"].append(dict(name=name, start_ns=frame[0], end_ns=end))

    def flush(self):
        ended = self.clock()
        assert all(not data["stack"] for data in self.threads), "active timing scope at flush"
        document = dict(
            schema=1, pid=self.pid, label=self.label, started_ns=self.started, ended_ns=ended,
            clock=time.get_clock_info("perf_counter").implementation,
            threads=[{k: v for k, v in data.items() if k != "stack"} for data in self.threads],
        )
        self.output.parent.mkdir(parents=True, exist_ok=True)
        with self.output.open("x") as file:
            json.dump(document, file, separators=(",", ":"))
            file.write("\n")
        return document


def configure(config):
    global _ACTIVE
    if _ACTIVE is not None:
        raise RuntimeError("timing recorder already active")
    if config is not None:
        _ACTIVE = Recorder(config["output"], label=config.get("label", ""))


def finish():
    global _ACTIVE
    recorder, _ACTIVE = _ACTIVE, None
    return None if recorder is None else recorder.flush()


def timed(name, span=True):
    def decorate(function):
        @wraps(function)
        def measured(*args, **kwargs):
            recorder = _ACTIVE
            if recorder is None:
                return function(*args, **kwargs)
            with recorder.scope(name, span=span):
                return function(*args, **kwargs)
        return measured
    return decorate


def call(name, function):
    recorder = _ACTIVE
    if recorder is None:
        return function()
    with recorder.scope(name, span=False):
        return function()
