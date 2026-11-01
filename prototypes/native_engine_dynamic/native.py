"""ctypes stream boundary: C owns state, Python owns detached handles/results.

Build explicitly with ``python -m prototypes.native_engine_dynamic.build``.
Incremental registration copies each validated Job; C grows storage geometrically.
No pointers into the engine escape. Calls on one stream are serialized, including
close, because ctypes releases the GIL while C runs.
"""

from __future__ import annotations

import ctypes as ct
import math
import os
from array import array
from threading import RLock

from prototypes.native_engine.model import Job

from .build import LIBRARY
from .handles import JobHandle

_U32 = ct.c_uint32
_U64 = ct.c_uint64
_DoubleP = ct.POINTER(ct.c_double)
_U32P = ct.POINTER(_U32)
_MAX_COUNT = 0xFFFFFFFF


class _Result(ct.Structure):
    _fields_ = [
        ("starts", _DoubleP),
        ("ends", _DoubleP),
        ("finished", _DoubleP),
        ("released", _DoubleP),
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
        ("total_pool_wait", ct.c_double),
        ("floor_wip_area", ct.c_double),
        ("max_floor_wip", _U64),
    ]


class _Snapshot(ct.Structure):
    _fields_ = [("arrival", ct.c_double), ("released", ct.c_double), ("finished", ct.c_double), ("state", _U32)]


_library = None
_library_lock = RLock()


def _load_library():
    global _library
    with _library_lock:
        if _library is None:
            library = ct.CDLL(str(LIBRARY))
            signatures = {
                "nd_create": ([_U32, _U32, ct.c_int, ct.c_int, ct.c_char_p, ct.c_char_p, ct.c_size_t], ct.c_void_p),
                "nd_add": (
                    [ct.c_void_p, ct.c_double, ct.c_double, _U32, _U32P, ct.c_size_t, _DoubleP, ct.c_size_t, _U32P],
                    ct.c_int,
                ),
                "nd_add_many": (
                    [
                        ct.c_void_p,
                        _U32,
                        _DoubleP,
                        ct.c_size_t,
                        _DoubleP,
                        ct.c_size_t,
                        _U32P,
                        ct.c_size_t,
                        _U32P,
                        ct.c_size_t,
                        _DoubleP,
                        ct.c_size_t,
                        _U32P,
                    ],
                    ct.c_int,
                ),
                "nd_advance": ([ct.c_void_p, ct.c_double], ct.c_int),
                "nd_run": ([ct.c_void_p], ct.c_int),
                "nd_inspect": ([ct.c_void_p, _U32, ct.POINTER(_Snapshot)], ct.c_int),
                "nd_now": ([ct.c_void_p], ct.c_double),
                "nd_get_result": ([ct.c_void_p], ct.POINTER(_Result)),
                "nd_error": ([ct.c_void_p], ct.c_char_p),
                "nd_destroy": ([ct.c_void_p], None),
            }
            for name, (arguments, result) in signatures.items():
                function = getattr(library, name)
                function.argtypes, function.restype = arguments, result
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
        raise TypeError("times must be real numbers (not bool)")
    try:
        value = float(value)
    except OverflowError as error:
        raise ValueError("time is outside binary64 range") from error
    if not math.isfinite(value):
        raise ValueError("times must be finite")
    return value


def _copy_list(pointer, count, typecode):
    if not count:
        return []
    buffer = array(typecode, [0]) * count
    ct.memmove(buffer.buffer_info()[0], pointer, count * buffer.itemsize)
    return buffer.tolist()


def _raise_native(message):
    text = message.decode("utf-8", errors="replace")
    if "allocation" in text:
        raise MemoryError(text)
    if "record" in text and any(word in text for word in ("open", "write", "flush")):
        raise OSError(text)
    raise ValueError(text)


class NativeStream:
    def __init__(self, servers, *, wip_cap=None, policy="fifo", mode="none", trace_path=None):
        self._engine = None
        self._lock = RLock()
        self._owner = object()
        self._failed = self._sealed = self._success = False
        self._jobs = self._operations = 0
        self._last_arrival = 0.0
        self._servers = _count(servers, "servers", positive=True)
        cap = 0 if wip_cap is None else _count(wip_cap, "wip_cap", positive=True)
        if policy not in ("fifo", "spt") or mode not in ("none", "kpi", "record"):
            raise ValueError("invalid policy or observation mode")
        if (mode == "record") != (trace_path is not None):
            raise ValueError("trace_path is required exactly in record mode")
        path = os.fsencode(trace_path) if mode == "record" else None
        if path is not None and (not path or b"\x00" in path):
            raise ValueError("invalid trace path")
        if array("d").itemsize != ct.sizeof(ct.c_double) or ct.sizeof(ct.c_double) != 8:
            raise RuntimeError("native binding requires matching binary64 double buffers")
        if array("I").itemsize != ct.sizeof(_U32) or ct.sizeof(_U32) != 4:
            raise RuntimeError("native binding requires matching uint32 buffers")
        self._mode = mode
        self._library = _load_library()
        error = ct.create_string_buffer(256)
        self._engine = self._library.nd_create(
            servers,
            cap,
            int(policy == "spt"),
            ("none", "kpi", "record").index(mode),
            path,
            error,
            len(error),
        )
        if not self._engine:
            _raise_native(error.value)

    def _check(self, *, accepting=False):
        if not self._engine or self._failed or (accepting and self._sealed):
            raise RuntimeError("stream is closed, failed or sealed")

    def _status(self, status):
        if status:
            self._failed = True
            _raise_native(self._library.nd_error(self._engine))

    @property
    def now(self):
        with self._lock:
            self._check()
            return self._library.nd_now(self._engine)

    def add(self, job):
        with self._lock:
            self._check(accepting=True)
            if type(job) is not Job:
                raise TypeError("add requires an exact validated Job")
            count = _count(len(job.route), "operations", positive=True)
            _count(self._jobs + 1, "jobs")
            _count(self._operations + count, "operations")
            if len(job.durations) != count or any(server >= self._servers for server in job.route):
                raise ValueError("invalid route or duration lengths")
            if job.arrival < self._last_arrival or job.arrival < self._library.nd_now(self._engine):
                raise ValueError("arrival precedes stream time or previous registration")
            # Job validates primitive fields and unique routes on construction;
            # C repeats validation before dereferencing or mutating engine arrays.
            routes, durations = array("I", job.route), array("d", job.durations)
            c_routes = (_U32 * count).from_buffer(routes)
            c_durations = (ct.c_double * count).from_buffer(durations)
            identity = _U32()
            self._status(
                self._library.nd_add(
                    self._engine,
                    job.arrival,
                    job.due,
                    count,
                    c_routes,
                    len(routes),
                    c_durations,
                    len(durations),
                    ct.byref(identity),
                )
            )
            self._jobs += 1
            self._operations += count
            self._last_arrival = job.arrival
            return JobHandle(self._owner, identity.value)

    def add_many(self, jobs):
        """Copy an exact tuple of validated Jobs in one native call.

        Input validation finishes before C mutation. Each job still receives
        its own arrival event and stable handle. Future inputs must already be
        known; this call does not advance time or run Python producer code.
        """
        with self._lock:
            self._check(accepting=True)
            if type(jobs) is not tuple:
                raise TypeError("add_many requires an exact tuple of Jobs")
            count = _count(len(jobs), "jobs")
            _count(self._jobs + count, "jobs")
            if not count:
                return []
            now = self._library.nd_now(self._engine)
            last = self._last_arrival
            arrivals, dues = array("d"), array("d")
            offsets, routes, durations = array("I", [0]), array("I"), array("d")
            for job in jobs:
                if type(job) is not Job:
                    raise TypeError("add_many requires exact validated Jobs")
                operations = _count(len(job.route), "operations", positive=True)
                _count(self._operations + len(routes) + operations, "operations")
                if len(job.durations) != operations or any(server >= self._servers for server in job.route):
                    raise ValueError("invalid route or duration lengths")
                if job.arrival < last or job.arrival < now:
                    raise ValueError("arrival precedes stream time or previous registration")
                arrivals.append(job.arrival)
                dues.append(job.due)
                routes.extend(job.route)
                durations.extend(job.durations)
                offsets.append(len(routes))
                last = job.arrival
            c_arrivals = (ct.c_double * count).from_buffer(arrivals)
            c_dues = (ct.c_double * count).from_buffer(dues)
            c_offsets = (_U32 * len(offsets)).from_buffer(offsets)
            c_routes = (_U32 * len(routes)).from_buffer(routes)
            c_durations = (ct.c_double * len(durations)).from_buffer(durations)
            first = _U32()
            self._status(
                self._library.nd_add_many(
                    self._engine,
                    count,
                    c_arrivals,
                    len(arrivals),
                    c_dues,
                    len(dues),
                    c_offsets,
                    len(offsets),
                    c_routes,
                    len(routes),
                    c_durations,
                    len(durations),
                    ct.byref(first),
                )
            )
            self._jobs += count
            self._operations += len(routes)
            self._last_arrival = last
            return [JobHandle(self._owner, first.value + index) for index in range(count)]

    def advance(self, until):
        with self._lock:
            self._check(accepting=True)
            until = _number(until)
            if until < self._library.nd_now(self._engine):
                raise ValueError("advance cannot move backwards")
            self._status(self._library.nd_advance(self._engine, until))

    def inspect(self, handle):
        with self._lock:
            self._check()
            if type(handle) is not JobHandle:
                raise TypeError("inspect requires a JobHandle")
            if handle._owner is not self._owner:
                raise ValueError("foreign job handle")
            identity = _count(handle.id, "job ID")
            if identity >= self._jobs:
                raise ValueError("invalid job handle ID")
            snapshot = _Snapshot()
            if self._library.nd_inspect(self._engine, identity, ct.byref(snapshot)):
                raise RuntimeError("inspection failed")
            return {
                "id": identity,
                "arrival": snapshot.arrival,
                "released": snapshot.released if snapshot.state >= 2 else None,
                "finished": snapshot.finished if snapshot.state == 3 else None,
                "state": ("scheduled", "pool", "active", "finished")[snapshot.state],
            }

    def run(self):
        with self._lock:
            self._check(accepting=True)
            self._sealed = True
            self._status(self._library.nd_run(self._engine))
            self._success = True

    def result(self):
        with self._lock:
            self._check()
            if not self._success:
                raise RuntimeError("result requires a successful run")
            view = self._library.nd_get_result(self._engine).contents
            return {
                "starts": _copy_list(view.starts, self._operations, "d"),
                "ends": _copy_list(view.ends, self._operations, "d"),
                "finished": _copy_list(view.finished, self._jobs, "d"),
                "released": _copy_list(view.released, self._jobs, "d"),
                "dispatch": _copy_list(view.dispatch, self._operations, "I"),
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
                    "busy": _copy_list(view.busy, self._servers, "d"),
                    "wip_area": view.wip_area,
                    "max_wip": view.max_wip,
                    "total_pool_wait": view.total_pool_wait,
                    "floor_wip_area": view.floor_wip_area,
                    "max_floor_wip": view.max_floor_wip,
                },
            }

    def close(self):
        with self._lock:
            if self._engine:
                engine, self._engine = self._engine, None
                self._library.nd_destroy(engine)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
