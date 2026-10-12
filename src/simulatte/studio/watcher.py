"""Read-only project observation; execution always captures its own immutable bytes.

watchfiles is imported only when starting an async watcher. Notification paths
are hints: manifest membership and content hashes decide whether inputs changed.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field, replace
import fnmatch
import hashlib
from pathlib import Path
import os
import time
import threading
from itertools import chain

from simulatte.execution.hashing import canonical_hash
from simulatte.execution.schema import _hash
from simulatte.execution.snapshot import (
    ProjectSpec,
    _RESERVED,
    _copy,
    _git,
    _members,
    _validate_names,
    discover_project,
    non_source_members,
)


@dataclass(frozen=True, slots=True)
class ProjectState:
    source_hash: str
    input_hash: str
    layout_hash: str | None
    config_hash: str
    # Experiment files and layout variants (D104): not execution state, so not compared;
    # None when only captured bytes are known.
    experiments_hash: str | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class InputChange:
    state: ProjectState | None
    changed: frozenset[str]
    paths: tuple[str, ...] = ()
    rescan: bool = False
    error: str | None = None
    permission_denied: bool = False


def captured_state(snapshot, *, layout_hash):
    """Acknowledge execution bytes, never whatever happens to be on disk now.

    Snapshots hold no experiment files, so the experiments class stays unknown here and
    the watcher keeps its own last observation of it.
    """
    configuration = asdict(snapshot.source.project)
    configuration.pop("root")
    configuration["pyproject"] = next(
        (
            entry.sha256
            for entry in (*snapshot.source.entries, *snapshot.inputs.entries)
            if entry.path == "pyproject.toml"
        ),
        None,
    )
    return ProjectState(
        snapshot.source.content_hash,
        snapshot.inputs.content_hash,
        layout_hash,
        canonical_hash("watch-config-v1", configuration),
    )


def _file_hash(path: Path, *, optional=False):
    try:
        with path.open("rb") as file:
            raw = file.read((16 << 20) + 1)
    except FileNotFoundError:
        if optional:
            return None
        raise
    if len(raw) > 16 << 20:
        raise ValueError("Project metadata file exceeds 16 MiB")
    return hashlib.sha256(raw).hexdigest()


class ProjectWatcher:
    def __init__(self, project: ProjectSpec, *, inputs=(), baseline=None, interval=5.0, force_polling=None):
        if type(interval) not in (int, float) or not 0 < interval <= 5:
            raise ValueError("Rescan interval must be positive and at most five seconds")
        self.project = project
        self.inputs = tuple(inputs)
        _validate_names(self.inputs)
        self.interval, self.force_polling = interval, force_polling
        self._lock = threading.RLock()
        self._generation = 0
        self._last: ProjectState | None = baseline
        self._error = None
        self._stop = asyncio.Event()
        self._watch_stop = asyncio.Event()
        self._git = self._git_directories()

    def _git_directories(self):
        if not self.project.git:
            return ()
        raw = _git(self.project.root, "rev-parse", "--absolute-git-dir", "--git-common-dir")
        return tuple(dict.fromkeys((self.project.root / os.fsdecode(line)).resolve() for line in raw.splitlines()))

    def accept(self, change, name):
        path = Path(name).absolute()
        for root in self._git:
            if path.is_relative_to(root):
                relative = path.relative_to(root).as_posix()
                return relative in ("index", "HEAD", "packed-refs") or relative.startswith("refs/")
        if not path.is_relative_to(self.project.root):
            return False
        parts = path.relative_to(self.project.root).parts
        return not set(parts) & _RESERVED

    def configure(self, *, inputs, project=None, baseline=None):
        """Adopt metadata discovery without treating it as a watcher observation."""
        values = tuple(inputs)
        _validate_names(values)
        with self._lock:
            if project is not None:
                if project.root != self.project.root:
                    raise ValueError("Watcher cannot switch project roots")
                self.project = project
            self.inputs = values
            if baseline is not None:
                if baseline.experiments_hash is None and self._last is not None:
                    baseline = replace(baseline, experiments_hash=self._last.experiments_hash)
                self._last, self._error = baseline, None
            self._generation += 1
            self._watch_stop.set()

    def acknowledge_layout(self, content_hash):
        """Suppress our own completed save; simultaneous source changes still count."""
        if content_hash is not None:
            _hash(content_hash)
        with self._lock:
            self._generation += 1
            if self._last is not None:
                self._last = replace(self._last, layout_hash=content_hash)

    def scan(self):
        # Discover again: include globs, excludes and local import roots can change
        # without changing any previously watched source file.
        with self._lock:
            captured, inputs = self.project, self.inputs
        project = discover_project(captured.root / captured.model, captured.root, layout=captured.layout)
        if project.layout in inputs or any(
            set(Path(name).parts) & _RESERVED or any(fnmatch.fnmatchcase(name, pattern) for pattern in project.exclude)
            for name in inputs
        ):
            raise ValueError("Declared inputs conflict with bundle exclusions or layout")
        names = _members(project)
        if len(names) > project.max_files or len(inputs) > project.max_files:
            raise ValueError("Snapshot file limit exceeded")
        remaining = project.max_bytes
        source, data = [], []
        declared = set(inputs)
        for name, target in chain(
            ((name, source) for name in names if name not in declared),
            ((name, data) for name in sorted(inputs, key=lambda value: value.encode("utf-8"))),
        ):
            entry = _copy(project.root, name, None, remaining)
            remaining -= entry.size
            target.append(entry.to_dict())
        experiments = [
            _copy(project.root, name, None, project.max_bytes).to_dict() for name in non_source_members(project)
        ]
        layout = project.root / project.layout
        if not layout.resolve().is_relative_to(project.root):
            raise ValueError("Layout escapes project root")
        configuration = asdict(project)
        configuration.pop("root")
        configuration["pyproject"] = _file_hash(project.root / "pyproject.toml", optional=True)
        return ProjectState(
            canonical_hash("source-manifest-v1", source),
            canonical_hash("inputs-manifest-v1", data),
            _file_hash(layout, optional=True),
            canonical_hash("watch-config-v1", configuration),
            canonical_hash("watch-experiments-v1", experiments),
        )

    def check(self, paths=(), *, rescan=False):
        """One bounded notification, including recoverable scan failures."""
        bounded = []
        used = 0
        for path in paths:
            text = str(path)
            if len(bounded) >= 256 or used + len(text.encode("utf-8")) > 65536:
                rescan = True
                break
            bounded.append(text)
            used += len(text.encode("utf-8"))
        with self._lock:
            generation = self._generation
        try:
            state = self.scan()
        except (OSError, ValueError) as error:
            detail = str(error)[:4096]
            with self._lock:
                if generation != self._generation or (self._error == detail and not bounded):
                    return None
                self._error = detail
            return InputChange(
                None, frozenset(("unknown",)), tuple(bounded), rescan, detail, isinstance(error, PermissionError)
            )
        with self._lock:
            if generation != self._generation:
                return None
            previous = self._last
            changed = frozenset(
                name
                for name, attribute in (
                    ("source", "source_hash"),
                    ("inputs", "input_hash"),
                    ("layout", "layout_hash"),
                    ("configuration", "config_hash"),
                    ("experiments", "experiments_hash"),
                )
                if previous is None
                or (
                    getattr(previous, attribute) != getattr(state, attribute)
                    # An unknown experiments baseline is adopted, not reported.
                    and (attribute != "experiments_hash" or previous.experiments_hash is not None)
                )
            )
            recovered = self._error is not None
            self._last, self._error = state, None
        if not changed and not recovered:
            return None
        return InputChange(state, changed, tuple(bounded), rescan)

    async def events(self):
        if self._stop.is_set():
            return
        from watchfiles import awatch

        initial = await asyncio.to_thread(self.check, rescan=True)
        if self._stop.is_set():
            return
        if initial is not None:
            yield initial
        while not self._stop.is_set():
            try:
                with self._lock:
                    self._watch_stop = watch_stop = asyncio.Event()
                    generation = self._generation
                    recovering = self._error is not None
                self._git = self._git_directories()
                paths = tuple(dict.fromkeys((self.project.root, *self._git)))
                next_scan = time.monotonic() + (0 if recovering else self.interval)
                async for batch in awatch(
                    *paths,
                    watch_filter=self.accept,
                    debounce=1000,
                    step=250,
                    rust_timeout=1000,
                    yield_on_timeout=True,
                    stop_event=watch_stop,
                    force_polling=self.force_polling,
                    ignore_permission_denied=False,
                    debug=False,
                ):
                    due = time.monotonic() >= next_scan
                    if batch or due:
                        change = await asyncio.to_thread(self.check, (path for _, path in batch), rescan=due)
                        next_scan = time.monotonic() + self.interval
                        if self._stop.is_set():
                            return
                        if change is not None:
                            yield change
                if not watch_stop.is_set():
                    return
            except (OSError, ValueError, RuntimeError) as error:
                with self._lock:
                    if generation != self._generation:
                        continue
                    self._error = str(error)[:4096]
                yield InputChange(
                    None,
                    frozenset(("unknown",)),
                    rescan=True,
                    error=str(error)[:4096],
                    permission_denied=isinstance(error, PermissionError),
                )
                try:
                    await asyncio.wait_for(self._stop.wait(), self.interval)
                except TimeoutError:
                    pass  # Reopen watching before reporting recovery.

    def close(self):
        with self._lock:
            self._generation += 1
            self._stop.set()
            self._watch_stop.set()
