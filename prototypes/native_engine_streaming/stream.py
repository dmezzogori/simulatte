"""Research-only retention experiment. No installed-backend API changes."""

import hashlib
import importlib.util
import json
import sys
import sysconfig
from threading import RLock

from simulatte._wire import canonical_pack, freeze
from simulatte.experimental.native import Job, JobHandle, _configuration, _number
from simulatte.execution.native_assets import _checked, _images
from .build import BUILD, sources

_module = None
_manifest = None
_loader_lock = RLock()


class ExpiredHandleError(KeyError):
    """A completed job was deliberately forgotten by bounded retention."""


def verify():
    manifest = json.loads((BUILD / "manifest.json").read_text())
    if manifest["sources"] != sources() or (_manifest is not None and manifest != _manifest):
        raise RuntimeError("Streaming experiment sources/manifest changed; rebuild and restart")
    for name, facts in manifest["files"].items():
        _checked(BUILD / name, facts["sha256"], facts["size"], readonly=False)
    library = "streaming_engine.dylib" if sys.platform == "darwin" else "streaming_engine.so"
    if _module is not None and _images(library) != {BUILD / library}:
        raise RuntimeError("Unexpected research engine dependency origin")
    return manifest


def load():
    global _module, _manifest
    with _loader_lock:
        marker = verify()
        if _module is None:
            path = BUILD / ("_nb_stream" + sysconfig.get_config_var("EXT_SUFFIX"))
            spec = importlib.util.spec_from_file_location("_nb_stream", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _module, _manifest = module, marker
            sys.modules["_nb_stream"] = module
        verify()
        return _module


class Stream:
    def __init__(self, servers, *, wip_cap=12, policy="spt", warmup=0, retention="full", input_state=None):
        self._engine = None
        self._lock, self._owner = RLock(), object()
        n, cap, policy = _configuration(servers, wip_cap, policy)
        warmup = _number(warmup, "warmup")
        if warmup < 0 or retention not in ("full", "bounded"):
            raise ValueError("Invalid warmup or retention mode")
        if input_state is not None and not callable(input_state):
            raise TypeError("input_state must be a pure compact-data callback")
        self._module = load()
        self._engine = self._module.create(n, cap, int(policy == "spt"), int(retention == "bounded"), warmup)
        self._history = hashlib.sha256()
        self._input_state = input_state
        self.retention = retention

    def _check(self):
        if self._engine is None:
            raise RuntimeError("Streaming experiment is closed")

    @property
    def now(self):
        with self._lock:
            self._check()
            return self._module.now(self._engine)

    def add(self, job):
        with self._lock:
            self._check()
            if type(job) is not Job:
                raise TypeError("Validated Job required")
            return JobHandle(
                self._owner, self._module.add(self._engine, job.arrival, job.due, job.route, job.durations)
            )

    def _step(self, until=None):
        self._check()
        try:
            records = (
                self._module.run(self._engine)
                if until is None
                else self._module.advance(self._engine, _number(until, "until"))
            )
            self._history.update(records)
        except BaseException:
            self.close()
            raise

    def advance(self, until):
        with self._lock:
            self._step(until)

    def run(self):
        with self._lock:
            self._step()

    def inspect(self, handle):
        with self._lock:
            self._check()
            if type(handle) is not JobHandle or handle._owner is not self._owner:
                raise ValueError("Foreign handle")
            try:
                return self._module.inspect(self._engine, handle.id)
            except KeyError as error:
                raise ExpiredHandleError(handle.id) from error

    def snapshot(self):
        with self._lock:
            self._check()
            start, end, closed, values = self._module.snapshot(self._engine)
            from simulatte.collectors import ShopFloorKPIs

            return dict(
                window=(start, end, closed),
                scalars={
                    "shopfloor/" + kpi.name: value
                    for kpi, value in zip(ShopFloorKPIs.kpis, values)
                    if value is not None
                },
            )

    def stats(self):
        with self._lock:
            self._check()
            return self._module.stats(self._engine)

    def state_bytes(self):
        with self._lock:
            self._check()
            native = self._module.state(self._engine)
            controller = None if self._input_state is None else freeze(self._input_state())
            return (
                b"NBDS0001"
                + self._history.digest()
                + native
                + canonical_pack(freeze(dict(inputs=controller, kpis=self.snapshot())))
            )

    def state_digest(self):
        return hashlib.sha256(self.state_bytes()).hexdigest()

    @property
    def history_digest(self):
        with self._lock:
            self._check()
            return self._history.hexdigest()

    def verify(self):
        return verify()

    def close(self):
        with self._lock:
            if self._engine is not None:
                engine, self._engine = self._engine, None
                self._module.close(engine)

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, *args):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
