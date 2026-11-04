"""Installed lifecycle fixture: real native progress before every abort/fault."""

from dataclasses import dataclass
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from simulatte import model
from simulatte.experimental.native import NativeProduction, Job


@dataclass(frozen=True)
class Params:
    retention: str = "full"
    kind: str = "healthy"
    diagnostic: str = ""
    jobs: int = 160000


@model(params=Params, horizon=1_000_000)
def build(env, layout, params):
    directory = Path(params.diagnostic)
    cursor = 0
    phase = "warmup"
    advanced, returned = threading.Event(), threading.Event()
    progressed = {}

    def record(name, **values):
        value = dict(pid=os.getpid(), at=float(env.now), wall=time.monotonic(), **values)
        path = directory / (name + ".json")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value) + "\n")
        temporary.replace(path)

    def state():
        if phase == "fault" and params.kind == "callback_error":
            raise ValueError("primary input callback failure")
        return dict(seed=100, cursor=cursor, phase=phase, kind=params.kind, jobs=params.jobs)

    class Backend(NativeProduction):
        def advance(self, until):
            if phase == "busy":
                advanced.set()
            try:
                super().advance(until)
            finally:
                if advanced.is_set():
                    returned.set()
                    record("native-returned")
            if phase == "post_advance" and params.kind == "advance_error":
                raise MemoryError("primary post-advance failure")

        def close(self):
            was_closed = self._closed
            try:
                super().close()
            finally:
                if not was_closed:
                    record("closed", engine_released=self._stream is None or self._stream._engine is None)
            if not was_closed and params.kind in ("cleanup_error", "close_error"):
                raise RuntimeError(
                    "secondary cleanup failure" if params.kind == "cleanup_error" else "primary close failure"
                )
            if not was_closed and params.kind == "late_cancel":
                record("late", progressed=progressed)
                deadline = time.monotonic() + 15
                while not (directory / "release").exists():
                    if time.monotonic() > deadline:
                        raise RuntimeError("late cancellation barrier was not released")
                    time.sleep(0.005)

    long_advance = params.kind in ("cancel_advance", "cancel_advance_ignore_term", "timeout_advance")
    backend = Backend(
        1,
        wip_cap=None if long_advance else 1,
        policy="spt" if long_advance else "fifo",
        retention=params.retention,
        input_state=state,
    )
    env.attach_execution_backend(backend, scope="shopfloor")

    def watcher():
        if not advanced.wait(20):
            return
        stream = backend._stream
        module = stream._module
        query = module.retention_now if params.retention == "bounded" else module.now
        close = module.retention_close if params.retention == "bounded" else module.close
        deadline = time.monotonic() + 10
        while not returned.is_set() and time.monotonic() < deadline:
            try:
                query(stream._engine)
            except RuntimeError as error:
                if "busy" in str(error):
                    try:
                        close(stream._engine)
                    except RuntimeError as close_error:
                        assert "busy" in str(close_error)
                    else:
                        raise AssertionError("binding allowed close while busy")
                    record("busy", progressed=progressed, binding_close_rejected=True)
                    return
            time.sleep(0.001)
        record("missed-busy")

    def inject():
        import simulatte_native

        library = next((Path(simulatte_native.__file__).parent / "_bin").glob("simulatte_native_engine.*"))
        owner = ctypes.CDLL(str(library))
        function = owner.nd_test_fail_allocation
        function.argtypes = [ctypes.c_size_t]
        function(1)
        return function

    def source():
        nonlocal cursor, phase, progressed
        backend.add(Job(0, (0,), (0.25,), 1))
        cursor = 1
        yield env.timeout(2)
        progressed = dict(storage=backend.storage_stats(), kpis=dict(backend.kpi_values()))
        assert progressed["storage"]["completed"] == 1
        record("progress", progressed=progressed)
        if params.kind == "producer_error" or params.kind == "cleanup_error":
            raise ValueError("primary producer failure")
        if params.kind == "process_exit":
            os._exit(23)
        if params.kind == "invalid_input":
            backend.add(Job(-1, (0,), (0.25,), 3))
        if params.kind == "callback_error":
            phase = "fault"
            backend.state_projection()
        if params.kind == "history_error":

            class Broken:
                def update(self, records):
                    raise RuntimeError("primary history commit failure")

            backend._stream._history = Broken()
        if params.kind == "allocation_error":
            while backend.storage_stats()["retained_jobs"] < backend.storage_stats()["job_capacity"]:
                backend.add(Job(2, (0,), (0.25,), 4))
                cursor += 1
            reset = inject()
            try:
                backend.add(Job(2, (0,), (0.25,), 4))
            finally:
                reset(0)
            raise AssertionError("mutating allocation failure not exercised")
        if params.kind == "cancel_boundary":
            phase = "boundary"
            record("boundary", progressed=progressed)
            while True:
                yield env.timeout(0)
        if params.kind == "cancel_admission":
            phase = "admitting"
            for index in range(1_000_000):
                backend.add(Job(2, (0,), (0.25,), 4 + index))
                cursor += 1
                if index == 1024:
                    record("admitting", progressed=progressed)
            raise AssertionError("admission was not cancelled in time")
        if params.kind in ("cancel_advance", "cancel_advance_short", "cancel_advance_ignore_term", "timeout_advance"):
            for index in range(params.jobs):
                backend.add(Job(2, (0,), (0.25,), 4 + index))
                cursor += 1
            if params.kind == "cancel_advance_ignore_term":
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                descendant = subprocess.Popen([sys.executable, "-I", "-S", "-c", "import time; time.sleep(120)"])
                record("descendant", child_pid=descendant.pid)
            phase = "busy"
            record("armed", progressed=progressed)
            threading.Thread(target=watcher, daemon=True).start()
            yield env.timeout(1_000_000)
            raise AssertionError("native stress call returned before cancellation")
        for index in range(8):
            backend.add(Job(2, (0,), (0.25,), 4 + index))
            cursor += 1
        phase = "post_advance" if params.kind == "advance_error" else "settle"
        yield env.timeout(4)
        phase = "finished"
        (env.artifacts_dir / "final.json").write_text(
            json.dumps(dict(storage=backend.storage_stats(), kpis=dict(backend.kpi_values())), sort_keys=True) + "\n"
        )

    env.process(source())
