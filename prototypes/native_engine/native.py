"""Thin ctypes boundary; all simulation state and transitions live in C.

Build the release library explicitly with ``python -m prototypes.native_engine.build``.
Construction copies the lowered inputs into C; result extraction copies C arrays
into detached Python lists. Neither phase is hidden outside the runner lifecycle.
Recording flushes at the end of run, so write errors prevent successful results.
Event times must stay finite and advance; aggregate KPIs can overflow to infinity
for extreme finite inputs, matching the Python reference without changing events.
"""

from __future__ import annotations

import ctypes as ct
import os
import operator
from threading import RLock

from .build import LIBRARY

_U32 = ct.c_uint32
_U64 = ct.c_uint64
_DoubleP = ct.POINTER(ct.c_double)
_U32P = ct.POINTER(_U32)
_Hook = ct.CFUNCTYPE(ct.c_int, ct.c_void_p, ct.c_double, _U32, ct.c_size_t, _U32P, _DoubleP, _U32P)
_MAX_COUNT = 0xFFFFFFFF


class _Input(ct.Structure):
    _fields_ = [
        ("servers", _U32),
        ("jobs", _U32),
        ("operations", _U32),
        ("arrivals", _DoubleP),
        ("due", _DoubleP),
        ("offsets", _U32P),
        ("routes", _U32P),
        ("durations", _DoubleP),
    ]


class _Result(ct.Structure):
    _fields_ = [
        ("starts", _DoubleP),
        ("ends", _DoubleP),
        ("finished", _DoubleP),
        ("dispatch", _U32P),
        ("makespan", ct.c_double),
        ("completed", _U64),
        ("events", _U64),
        ("callbacks", _U64),
        ("records", _U64),
        ("total_flow", ct.c_double),
        ("total_wait", ct.c_double),
        ("total_tardiness", ct.c_double),
        ("busy", _DoubleP),
        ("wip_area", ct.c_double),
        ("max_wip", _U64),
    ]


_library = None
_library_lock = RLock()


def _load_library():
    global _library
    with _library_lock:
        if _library is None:
            library = ct.CDLL(str(LIBRARY))
            library.ne_create.argtypes = [
                ct.POINTER(_Input),
                ct.c_int,
                ct.c_int,
                _Hook,
                ct.c_void_p,
                ct.c_char_p,
                ct.c_char_p,
                ct.c_size_t,
            ]
            library.ne_create.restype = ct.c_void_p
            library.ne_run.argtypes = [ct.c_void_p]
            library.ne_run.restype = ct.c_int
            library.ne_get_result.argtypes = [ct.c_void_p]
            library.ne_get_result.restype = ct.POINTER(_Result)
            library.ne_error.argtypes = [ct.c_void_p]
            library.ne_error.restype = ct.c_char_p
            library.ne_destroy.argtypes = [ct.c_void_p]
            library.ne_destroy.restype = None
            _library = library
        return _library


def _count(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < int(positive) or value >= _MAX_COUNT:
        raise ValueError(f"{name} is outside the native count range")
    return value


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("times and durations must be real numbers (not bool)")
    try:
        return float(value)
    except OverflowError as error:
        raise ValueError("time or duration is outside binary64 range") from error


def _raise_native(message):
    text = message.decode("utf-8", errors="replace")
    if "allocation" in text:
        raise MemoryError(text)
    if "record" in text and any(word in text for word in ("open", "write", "flush")):
        raise OSError(text)
    raise ValueError(text)


class NativeRunner:
    def __init__(self, workload, *, policy="fifo", mode="none", hook=None, trace_path=None):
        self._engine = None
        self._lock = RLock()
        self._running = False
        self._attempted = False
        self._success = False
        self._callback = None
        self._callback_error = None
        if policy not in ("fifo", "spt") or mode not in ("none", "kpi", "record"):
            raise ValueError("invalid policy or observation mode")
        if hook is not None and not callable(hook):
            raise TypeError("hook must be callable")
        if (mode == "record") != (trace_path is not None):
            raise ValueError("trace_path is required exactly in record mode")
        path = os.fsencode(trace_path) if mode == "record" else None
        if path is not None and (not path or b"\x00" in path):
            raise ValueError("invalid trace path")
        self._mode = mode
        self._servers = _count(workload.servers, "servers", positive=True)
        self._jobs = _count(len(workload.jobs), "jobs")
        self._operations = _count(workload.operation_count, "operation_count")
        arrivals, due, offsets, routes, durations = [], [], [0], [], []
        for job in workload.jobs:
            arrivals.append(_number(job.arrival))
            due.append(_number(job.due))
            if len(job.route) != len(job.durations):
                raise ValueError("route and duration lengths differ")
            routes.extend(_count(server, "route server") for server in job.route)
            durations.extend(_number(duration) for duration in job.durations)
            offsets.append(_count(len(routes), "operation offset"))
        if len(routes) != self._operations:
            raise ValueError("operation_count does not match routes")
        # These temporary arrays are live through create, which validates and copies.
        c_arrivals = (ct.c_double * self._jobs)(*arrivals)
        c_due = (ct.c_double * self._jobs)(*due)
        c_offsets = (_U32 * (self._jobs + 1))(*offsets)
        c_routes = (_U32 * self._operations)(*routes)
        c_durations = (ct.c_double * self._operations)(*durations)
        inputs = _Input(
            self._servers, self._jobs, self._operations, c_arrivals, c_due, c_offsets, c_routes, c_durations
        )
        if hook is not None:

            def callback(context, now, server, count, ids, lengths, selected):
                try:
                    candidates = tuple((ids[i], lengths[i]) for i in range(count))
                    choice = hook(now, server, candidates)
                    if isinstance(choice, bool):
                        raise TypeError("hook must return an integer operation ID (not bool)")
                    choice = operator.index(choice)
                    if not any(choice == op for op, _ in candidates):
                        raise ValueError("hook selected an operation outside its candidates")
                    selected[0] = choice
                    return 0
                except BaseException as error:
                    self._callback_error = error
                    return 1

            self._callback = _Hook(callback)
        else:
            self._callback = _Hook()  # a genuine NULL callback; built-ins never enter Python
        self._library = _load_library()
        error = ct.create_string_buffer(256)
        self._engine = self._library.ne_create(
            ct.byref(inputs),
            int(policy == "spt"),
            ("none", "kpi", "record").index(mode),
            self._callback,
            None,
            path,
            error,
            len(error),
        )
        if not self._engine:
            self._callback = None
            _raise_native(error.value)

    def run(self):
        with self._lock:
            if not self._engine or self._attempted:
                raise RuntimeError("runner is closed or has already run")
            self._attempted = True
            self._running = True
            try:
                status = self._library.ne_run(self._engine)
                if self._callback_error is not None:
                    error, self._callback_error = self._callback_error, None
                    raise error
                if status:
                    _raise_native(self._library.ne_error(self._engine))
                self._success = True
            finally:
                self._running = False

    def result(self):
        with self._lock:
            if not self._engine or not self._success or self._running:
                raise RuntimeError("result requires a successful, open runner")
            view = self._library.ne_get_result(self._engine).contents
            return {
                "starts": list(view.starts[: self._operations]),
                "ends": list(view.ends[: self._operations]),
                "finished": list(view.finished[: self._jobs]),
                "dispatch": list(view.dispatch[: self._operations]),
                "makespan": view.makespan,
                "completed": view.completed,
                "events": view.events,
                "callbacks": view.callbacks,
                "records": view.records,
                "kpis": None
                if self._mode == "none"
                else {
                    "total_flow": view.total_flow,
                    "total_wait": view.total_wait,
                    "total_tardiness": view.total_tardiness,
                    "busy": list(view.busy[: self._servers]),
                    "wip_area": view.wip_area,
                    "max_wip": view.max_wip,
                },
            }

    def close(self):
        with self._lock:
            if self._running:
                raise RuntimeError("cannot close a runner from its active callback")
            if self._engine:
                engine, self._engine = self._engine, None
                self._library.ne_destroy(engine)
            self._callback = None
            self._callback_error = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


Runner = NativeRunner
