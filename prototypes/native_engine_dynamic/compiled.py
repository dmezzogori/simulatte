"""Opt-in compiled scalar boundary; C API state owns engine counts and lifetime."""

from functools import lru_cache
import importlib.util
import os
from threading import RLock

from prototypes.native_engine.model import Job

from .compiled_build import EXTENSION
from .handles import JobHandle
from .native import _count, _number


@lru_cache(maxsize=1)
def extension():
    spec = importlib.util.spec_from_file_location("_nd_scalar", EXTENSION)
    if spec is None or spec.loader is None:
        raise ImportError("build the scalar extension with this interpreter first")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CompiledStream:
    def __init__(self, servers, *, wip_cap=None, policy="fifo", mode="none", trace_path=None):
        self._engine = None
        self._lock, self._owner = RLock(), object()
        servers = _count(servers, "servers", positive=True)
        cap = 0 if wip_cap is None else _count(wip_cap, "wip_cap", positive=True)
        if policy not in ("fifo", "spt") or mode not in ("none", "kpi", "record"):
            raise ValueError("invalid policy or observation mode")
        if (mode == "record") != (trace_path is not None):
            raise ValueError("trace_path is required exactly in record mode")
        path = os.fsencode(trace_path) if trace_path is not None else None
        if path is not None and (not path or b"\x00" in path):
            raise ValueError("invalid trace path")
        self._module = extension()
        self._engine = self._module.create(
            servers, cap, int(policy == "spt"), ("none", "kpi", "record").index(mode), path
        )

    def _check(self):
        if self._engine is None:
            raise RuntimeError("stream is closed")

    @property
    def now(self):
        with self._lock:
            self._check()
            return self._module.now(self._engine)

    def add(self, job):
        with self._lock:
            self._check()
            # State/lifecycle is checked before touching user input, as in ctypes.
            self._module.accepting(self._engine)
            if type(job) is not Job:
                raise TypeError("add requires an exact validated Job")
            identity = self._module.add(self._engine, job.arrival, job.due, job.route, job.durations)
            return JobHandle(self._owner, identity)

    def advance(self, until):
        with self._lock:
            self._check()
            self._module.accepting(self._engine)
            self._module.advance(self._engine, _number(until))

    def inspect(self, handle):
        with self._lock:
            self._check()
            self._module.now(self._engine)  # Validate failed state before the handle.
            if type(handle) is not JobHandle:
                raise TypeError("inspect requires a JobHandle")
            if handle._owner is not self._owner:
                raise ValueError("foreign job handle")
            return self._module.inspect(self._engine, _count(handle.id, "job ID"))

    def run(self):
        with self._lock:
            self._check()
            self._module.run(self._engine)

    def result(self):
        with self._lock:
            self._check()
            return self._module.result(self._engine)

    def close(self):
        with self._lock:
            if self._engine is not None:
                self._module.close(self._engine)
                self._engine = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
