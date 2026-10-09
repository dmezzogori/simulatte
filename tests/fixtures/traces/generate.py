"""Trace fixtures for the TypeScript conformance reader (spec §11.4, T13).

``python tests/fixtures/traces/generate.py [--out DIR]`` writes the *generated* fixtures: for each name in
:data:`SEEDS` a ``<name>.simtrace`` and a ``<name>.expected.json`` holding the canonical state at every chunk
boundary and at sampled cursors, computed by the reference Python reader. They are reproducible: explicit seeds,
explicit provenance, fixed volatile metadata and event-count chunk limits only (no byte or latency limits).
Their bytes are not stable across machines (the manifest records the platform and the zlib output may vary), so
``tests/core/test_trace_fixtures.py`` compares :func:`canonical_content`, not bytes.

The *frozen* fixtures in ``frozen/`` are made once by ``make_frozen.py`` and never regenerated.

The expected JSON is canonical: sorted keys, tuples as arrays, and the floats JSON cannot hold as the strings
``"+inf"``, ``"-inf"`` and ``"nan"`` (``-0.0`` is written as ``-0.0``). Do not put those strings in a state.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar
from unittest import mock

from simulatte._wire import FrozenMap
from simulatte.builders import build_immediate_release_system, build_lumscor_system
from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.environment import Environment
from simulatte.events import Deltas, DomainEvent, event_type
from simulatte.provenance import Provenance, VolatileMetadata
from simulatte.scenario import Scenario
from simulatte.trace import ChunkLimits, Trace, TraceRecorder

HERE = Path(__file__).resolve().parent

SEEDS: dict[str, int] = {"shop_small": 5, "lumscor": 20260508, "cell_ops": 7}
"""Generated fixtures and the explicit seed of each."""

PROVENANCE = Provenance(
    model="sha256:" + "1" * 64,
    source="sha256:" + "2" * 64,
    inputs="sha256:" + "3" * 64,
    dependencies="sha256:" + "4" * 64,
)
"""Explicit provenance: the manifest then records no installed-distribution list."""

_VOLATILE = VolatileMetadata(host="fixture-host", wall_clock_start="2026-01-01T00:00:00+00:00", run_seconds=0.0)
_MACHINE_DEPENDENT = frozenset({"simulatte_version", "python", "platform"})  # manifest fields that vary by machine


# ---------------------------------------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------------------------------------


@contextmanager
def fixed_volatile_metadata() -> Generator[None]:
    """Pin the host and the timings the recorder stores in the header and the footer."""
    with mock.patch.object(Environment, "volatile_metadata", lambda self: _VOLATILE):
        yield


def record(
    path: Path,
    build: Callable[[Environment], object],
    *,
    until: float,
    max_events: int,
    seed: int | None = None,
    env: Environment | None = None,
) -> Environment:
    """Run `build` in an environment recording to `path`, chunked by event count only; return the environment."""
    if env is None:
        env = Environment(seed=seed, provenance=PROVENANCE)
    limits = ChunkLimits(max_events=max_events, max_bytes=1 << 30, max_latency_s=3600.0)
    with fixed_volatile_metadata():
        TraceRecorder(env, path, chunk_limits=limits)
        build(env)
        env.run(until=until)
        env.close()
    return env


def build_shop_small(env: Environment) -> None:
    build_immediate_release_system(env=env, scenario=Scenario(n_servers=3))


def build_lumscor(env: Environment) -> None:
    build_lumscor_system(
        env=env, scenario=Scenario(n_servers=3), check_timeout=5.0, wl_norm_level=6.0, allowance_factor=2
    )


class Cell(Entity, kind="fixture_cell"):
    """A small entity whose fields hold every kind of value; ``note`` is absent from its initial state."""

    state_schema: ClassVar[StateSchema] = StateSchema(
        {
            "level": FieldSpec("any"),
            "tags": FieldSpec("str", collection="list"),
            "attrs": FieldSpec("any", collection="map"),
            "note": FieldSpec("any", nullable=True),
        }
    )

    def __init__(self, env: Environment, *, name: str, with_note: bool = False) -> None:
        self.values: dict[str, Any] = {"level": 1, "tags": (), "attrs": FrozenMap({})}
        if with_note:
            self.values["note"] = None
        env.entities.attach(self, name=name)

    def snapshot(self) -> dict[str, Any]:
        return {**self.values, "label": self.label}


@event_type("fixture.cell_changed", touches={"fixture_cell": ("level", "tags", "attrs", "note")})
class CellChanged(DomainEvent):
    cell: str
    op: str


def build_cell_ops(env: Environment) -> None:
    """Every delta operation, values of every wire type, entity creation and retirement, a late entity kind."""
    first = Cell(env, name="first")
    second = Cell(env, name="second", with_note=True)

    def change(cell: Cell, op: str, deltas: Deltas) -> None:
        env.emit(CellChanged(cell=cell.id, op=op, deltas=deltas))

    def script() -> Any:
        build = Deltas.build
        steps: list[tuple[Cell, str, Deltas]] = [
            (first, "set-str", build().set(first.id, "level", "high").done()),
            (first, "set-list", build().set(first.id, "level", (1, 2.5, "x", None, True)).done()),
            (first, "set-map", build().set(first.id, "level", FrozenMap({"a": 1, "b": (2, 3)})).done()),
            (first, "set-float", build().set(first.id, "level", 2.0).done()),
            (first, "insert-a", build().insert(first.id, "tags", 0, "a").done()),
            (first, "insert-c", build().insert(first.id, "tags", 1, "c").done()),
            (first, "insert-b", build().insert(first.id, "tags", 1, "b").done()),
            (first, "move-a-end", build().move(first.id, "tags", "a", 2).done()),
            (first, "remove-c", build().remove(first.id, "tags", "c").done()),
            (first, "put-x", build().put(first.id, "attrs", "x", 10).done()),
            (first, "put-y", build().put(first.id, "attrs", "y", (1, 2)).done()),
            (first, "put-x-again", build().put(first.id, "attrs", "x", 11).done()),
            (first, "delete-y", build().delete(first.id, "attrs", "y").done()),
            (first, "set-absent-note", build().set(first.id, "note", "created by set").done()),
            (second, "note-none", build().set(second.id, "note", None).done()),
            (second, "two-ops", build().set(second.id, "level", 3).insert(second.id, "tags", 0, "t").done()),
        ]
        for cell, op, deltas in steps:
            yield env.timeout(1.0)
            change(cell, op, deltas)
        yield env.timeout(0.0)  # same time as the previous event
        change(first, "same-time", Deltas.build().set(first.id, "level", "same time").done())
        yield env.timeout(1.5)
        third = Cell(env, name="third")
        change(third, "created", Deltas.build().set(third.id, "level", "fresh").done())
        yield env.timeout(1.0)
        late = _late_entity(env)
        change(first, "late", Deltas.build().set(first.id, "level", late.id).done())
        yield env.timeout(1.0)
        env.entities.retire(second)

    env.process(script())


_LATE_KIND_SCHEMA = StateSchema({"size": FieldSpec("int")})


def _late_entity(env: Environment) -> Entity:
    """An entity of a kind registered only after the recorder attached, so the trace holds a CATALOG_EXT record."""

    class Late(Entity, kind="fixture_late"):
        state_schema: ClassVar[StateSchema] = _LATE_KIND_SCHEMA

        def __init__(self) -> None:
            self.size = 3
            env.entities.attach(self, name="late")

    return Late()


BUILDERS: dict[str, tuple[Callable[[Environment], object], float, int]] = {
    "shop_small": (build_shop_small, 20.0, 15),
    "lumscor": (build_lumscor, 25.0, 20),
    "cell_ops": (build_cell_ops, 30.0, 7),
}
"""For each generated fixture: the builder, the horizon and the events per chunk."""


# ---------------------------------------------------------------------------------------------------------
# Canonical JSON
# ---------------------------------------------------------------------------------------------------------


def plain(value: Any) -> Any:
    """`value` as JSON-ready data: maps to dicts, tuples to lists, non-finite floats to strings."""
    if isinstance(value, float) and not math.isfinite(value):
        return "nan" if math.isnan(value) else ("+inf" if value > 0 else "-inf")
    if isinstance(value, Mapping):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def dump(document: Any) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def _cursor(cursor: tuple[float, int]) -> list[Any]:
    return [float(cursor[0]), int(cursor[1])]


def check_cursors(trace: Trace) -> list[tuple[str, tuple[float, int]]]:
    """The cursors the expected JSON covers: activation, both ends of every chunk, two sampled events, the end."""
    bounds = trace.cursor_range
    if bounds is None:
        return []
    cursors: list[tuple[str, tuple[float, int]]] = [("activation", bounds[0])]
    for i, info in enumerate(trace.index):
        cursors.append((f"chunk-{i}-first", info.first))
        cursors.append((f"chunk-{i}-last", info.last))
    events = list(trace.events())
    if events:
        for n in (1, 2):
            event = events[len(events) * n // 3]
            cursors.append((f"sample-{n}", (event.t, event.seq)))
    cursors.append(("end", bounds[1]))
    return cursors


def expected_document(path: Path) -> dict[str, Any]:
    """The expected JSON of the trace at `path`, computed by the reference Python reader."""
    trace = Trace.open(path)
    bounds = trace.cursor_range
    return {
        "trace": path.name,
        "level": trace.level,
        "outcome": trace.outcome,
        "truncated": trace.truncated,
        "seed": trace.manifest["seed"],
        "cursorRange": None if bounds is None else [_cursor(bounds[0]), _cursor(bounds[1])],
        "chunks": [[_cursor(info.first), _cursor(info.last)] for info in trace.index],
        "checks": [
            {"label": label, "cursor": _cursor(cursor), "state": plain(trace.state_at(cursor))}
            for label, cursor in check_cursors(trace)
        ],
    }


def canonical_content(path: Path) -> dict[str, Any]:
    """What a trace says, without what varies between machines and runs (bytes, platform, host, digest)."""
    trace = Trace.open(path)
    manifest = {k: v for k, v in trace.manifest.items() if k not in _MACHINE_DEPENDENT}
    bounds = trace.cursor_range
    return {
        "manifest": plain(manifest),
        "level": trace.level,
        "outcome": trace.outcome,
        "truncated": trace.truncated,
        "kpis": plain(trace.kpis()),
        "cursor_range": None if bounds is None else [_cursor(bounds[0]), _cursor(bounds[1])],
        "chunks": [[_cursor(i.first), _cursor(i.last), float(i.t_start), float(i.t_end), i.epoch] for i in trace.index],
        "initial": None if bounds is None else plain(trace.state_at(bounds[0])),
        "events": [
            plain([e.seq, e.ordinal, e.type, e.t, e.payload, [list(op) for op in e.deltas]]) for e in trace.events()
        ],
    }


def write_expected(trace_path: Path) -> Path:
    out = trace_path.with_name(trace_path.name.removesuffix(".simtrace") + ".expected.json")
    out.write_text(dump(expected_document(trace_path)), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------------------------


def generate(out: Path) -> list[Path]:
    """Write every generated fixture and its expected JSON into `out`."""
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, seed in SEEDS.items():
        build, until, max_events = BUILDERS[name]
        path = out / f"{name}.simtrace"
        record(path, build, until=until, max_events=max_events, seed=seed)
        written += [path, write_expected(path)]
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate the generated trace fixtures.")
    parser.add_argument("--out", type=Path, default=HERE / "generated", help="output directory")
    args = parser.parse_args(argv)
    for path in generate(args.out):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
