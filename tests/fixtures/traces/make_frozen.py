"""Make the frozen trace fixtures (spec §11.4, T13). Run once; never regenerated automatically.

The frozen fixtures in ``frozen/`` are binary files committed as they were written, so that a decoder regression
in either language shows up as a failure instead of being regenerated away. This script records how they were made.
It refuses to overwrite an existing file; to change a frozen fixture, add a new one.

``default_seed`` uses an environment without an explicit seed (drawn from ``os.urandom``), as users do; the seed is
retried until it exceeds 2**53, the case where a JSON number or a JavaScript number cannot hold it and the manifest's
decimal string is the only faithful form. The other fixtures use explicit seeds and the provenance of
``generate.py``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import generate

from simulatte._wire import FrozenMap
from simulatte.builders import build_immediate_release_system
from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.environment import Environment
from simulatte.events import Deltas, DomainEvent, event_type
from simulatte.scenario import Scenario
from simulatte.trace.format import TRAILER
from simulatte.trace.reader import Trace

FROZEN = generate.HERE / "frozen"

HOSTILE_NAMES = ("__proto__", "constructor", "prototype", "~x", "~__proto__")

_HOSTILE_FIELDS = {name: FieldSpec("any") for name in (*HOSTILE_NAMES, "nested", "plain")}


class Hostile(Entity, kind="frozen_hostile"):
    """An entity whose state field names are the keys that break naive JavaScript decoders."""

    state_schema: ClassVar[StateSchema] = StateSchema(_HOSTILE_FIELDS)

    def __init__(self, env: Environment, *, name: str) -> None:
        self.values: dict[str, Any] = {
            "__proto__": 1,
            "constructor": "c",
            "prototype": (1, 2),
            "~x": FrozenMap({"__proto__": 0}),
            "~__proto__": None,
            "nested": FrozenMap(
                {
                    "__proto__": FrozenMap({"constructor": FrozenMap({"prototype": 1, "~x": 2})}),
                    "~~y": 3,
                    "toString": 4,
                    "hasOwnProperty": 5,
                    "valueOf": (FrozenMap({"__proto__": "deep"}),),
                }
            ),
            "plain": "p",
        }
        env.entities.attach(self, name=name)

    def snapshot(self) -> dict[str, Any]:
        return {**self.values, "label": self.label}


@event_type("frozen.hostile_changed", touches={"frozen_hostile": tuple(_HOSTILE_FIELDS)})
class HostileChanged(DomainEvent):
    entity: str
    constructor: str
    prototype: str
    detail: dict[str, Any]


def build_hostile(env: Environment) -> None:
    entities = [Hostile(env, name=name) for name in (*HOSTILE_NAMES, "host")]

    def script() -> Any:
        for i, entity in enumerate(entities):
            yield env.timeout(1.0)
            deltas = (
                Deltas.build()
                .set(entity.id, "__proto__", f"set-{i}")
                .set(entity.id, "constructor", FrozenMap({"__proto__": i, "~x": (i,)}))
                .put(entity.id, "nested", "__proto__", FrozenMap({"constructor": i}))
                .put(entity.id, "nested", "~__proto__", "tilde")
                .put(entity.id, "nested", "prototype", (FrozenMap({"~x": 1}),))
                .delete(entity.id, "nested", "toString")
                .insert(entity.id, "prototype", 1, FrozenMap({"__proto__": "listed"}))
                .done()
            )
            env.emit(
                HostileChanged(
                    entity=entity.id,
                    constructor="constructor",
                    prototype="prototype",
                    detail={"__proto__": 1, "constructor": 2, "prototype": 3, "~x": 4, "~__proto__": 5},
                    deltas=deltas,
                )
            )
        yield env.timeout(1.0)
        deltas = Deltas.build().delete("host", "nested", "~__proto__").delete("host", "nested", "__proto__").done()
        env.emit(HostileChanged(entity="host", constructor="", prototype="", detail={}, deltas=deltas))

    env.process(script())


class Numbers(Entity, kind="frozen_numbers"):
    state_schema: ClassVar[StateSchema] = StateSchema(
        {
            "scalar": FieldSpec("any"),
            "series": FieldSpec("any", collection="list"),
            "table": FieldSpec("any", collection="map"),
        }
    )

    def __init__(self, env: Environment) -> None:
        self.scalar: Any = float("inf")
        self.series: tuple[Any, ...] = (
            float("-inf"),
            -0.0,
            0.0,
            2**53 - 1,
            -(2**53 - 1),
            5e-324,
            1.7976931348623157e308,
        )
        self.table: FrozenMap = FrozenMap({"nan": float("nan"), "negzero": -0.0, "one": 1, "one_float": 1.0})
        env.entities.attach(self, name="numbers")

    def snapshot(self) -> dict[str, Any]:
        return {"scalar": self.scalar, "series": self.series, "table": self.table, "label": self.label}


@event_type("frozen.numbers_changed", touches={"frozen_numbers": ("scalar", "series", "table")})
class NumbersChanged(DomainEvent):
    note: str


def build_nonfinite(env: Environment) -> None:
    Numbers(env)

    def script() -> Any:
        steps = [
            Deltas.build().set("numbers", "scalar", float("-inf")),
            Deltas.build().set("numbers", "scalar", float("nan")),
            Deltas.build().set("numbers", "scalar", -0.0),
            Deltas.build().set("numbers", "scalar", 0.0),
            Deltas.build().set("numbers", "scalar", 2**53 - 1),
            Deltas.build().insert("numbers", "series", 0, float("nan")),
            Deltas.build().insert("numbers", "series", 1, float("inf")),
            Deltas.build().put("numbers", "table", "inf", float("inf")),
            Deltas.build().put("numbers", "table", "neg", -(2**53 - 1)),
            Deltas.build().remove("numbers", "series", -0.0),
            Deltas.build().set("numbers", "scalar", (float("inf"), -0.0, FrozenMap({"x": float("-inf")}))),
        ]
        for i, step in enumerate(steps):
            yield env.timeout(0.5)
            env.emit(NumbersChanged(note=f"step-{i}", deltas=step.done()))

    env.process(script())


def build_default_seed(env: Environment) -> None:
    build_immediate_release_system(env=env, scenario=Scenario(n_servers=2))


def make(
    name: str, build: Callable[[Environment], object], *, until: float, max_events: int, env: Environment | None = None
) -> Path:
    path = FROZEN / f"{name}.simtrace"
    if path.exists():
        raise SystemExit(f"{path} exists: frozen fixtures are never regenerated")
    generate.record(path, build, until=until, max_events=max_events, seed=11, env=env)
    return path


def main() -> int:
    FROZEN.mkdir(exist_ok=True)
    made = [
        make("hostile_keys", build_hostile, until=10.0, max_events=4),
        make("nonfinite", build_nonfinite, until=10.0, max_events=3),
    ]

    env = Environment(provenance=generate.PROVENANCE)
    while env.seed < 2**53:
        env = Environment(provenance=generate.PROVENANCE)
    made.append(make("default_seed", build_default_seed, until=15.0, max_events=12, env=env))

    # Truncated copies of a complete trace: one cut inside the last chunk record, one without its trailer.
    source = make(
        "_source", build_default_seed, until=12.0, max_events=9, env=Environment(seed=3, provenance=generate.PROVENANCE)
    )
    data = source.read_bytes()
    source.unlink()
    (FROZEN / "trailerless.simtrace").write_bytes(data[: -TRAILER.size])
    trace = Trace.open(FROZEN / "trailerless.simtrace")
    last = trace.index[-1]
    (FROZEN / "truncated.simtrace").write_bytes(data[: last.offset + last.length // 2])
    made += [FROZEN / "trailerless.simtrace", FROZEN / "truncated.simtrace"]

    for path in made:
        print(generate.write_expected(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
