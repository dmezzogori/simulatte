"""Semantic projection and digest (spec §9.1, §9.2)."""

from __future__ import annotations

import math
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import pytest

from simulatte._wire import FrozenMap, canonical_pack, unpack
from simulatte.builders import build_immediate_release_system
from simulatte.digest import DigestAccumulator, Fingerprint, SemanticDigest, project_event, project_state
from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.environment import Environment
from simulatte.events import Deltas, DomainEvent, event_type
from simulatte.scenario import Scenario


class Dial(Entity, kind="test_digest_dial"):
    state_schema: ClassVar[StateSchema] = StateSchema(
        {"setting": FieldSpec("float"), "note": FieldSpec("str", presentation=True)}
    )

    def __init__(self, env: Environment, *, name: str | None = None, label: str | None = None, note: str = "") -> None:
        self.env = env
        self.setting = 1.0
        self.note = note
        env.entities.attach(self, name=name, label=label)


@event_type(
    "test.digest_turned",
    touches={"test_digest_dial": ("setting", "note", "label")},
    presentation=frozenset({"comment"}),
)
class Turned(DomainEvent):
    dial: str
    to: float
    comment: str = ""


def _turn(env: Environment, dial: Dial, to: float, *, comment: str = "", note: str = "", label: str = "") -> None:
    deltas = Deltas.build().set(dial.id, "setting", to)
    if note:
        deltas.set(dial.id, "note", note)
    if label:
        deltas.set(dial.id, "label", label)
    dial.setting = to
    env.emit(Turned(dial=dial.id, to=to, comment=comment, deltas=deltas.done()))


def _dial_run(*, label: str | None, note: str, comment: str, extra_ops: bool) -> str:
    env = Environment(seed=1)
    digest = SemanticDigest.attach(env)
    dial = Dial(env, name="dial", label=label, note=note)
    env.activate()
    _turn(env, dial, 2.0, comment=comment, note="n1" if extra_ops else "", label="L1" if extra_ops else "")
    _turn(env, dial, 3.5, comment=comment)
    return digest.hexdigest()


def _system_digest(seed: int, *, chatty: bool = False, until: float = 60.0) -> str:
    env = Environment(seed=seed)
    digest = env.enable_digest()
    build_immediate_release_system(env=env, scenario=Scenario(n_servers=3))
    if chatty:
        seen: list[object] = []
        env.bus.subscribe(seen.append, "**")
        env.info("hello", component="test")
        env.warning("careful")
    env.run(until=until)
    return digest.hexdigest()


def test_digest_ignores_logs_and_extra_subscribers() -> None:
    assert _system_digest(5) == _system_digest(5, chatty=True)


def test_digest_ignores_label_changes() -> None:
    base = _dial_run(label=None, note="", comment="", extra_ops=False)
    assert _dial_run(label="Pretty", note="", comment="", extra_ops=False) == base  # initial label
    assert _dial_run(label=None, note="shown", comment="", extra_ops=False) == base  # presentation state field
    assert _dial_run(label=None, note="", comment="free text", extra_ops=False) == base  # presentation payload field
    # set operations on presentation state fields are dropped from the deltas
    assert _dial_run(label=None, note="", comment="", extra_ops=True) == base
    # while a semantic change is visible
    env = Environment(seed=1)
    digest = SemanticDigest.attach(env)
    dial = Dial(env, name="dial")
    env.activate()
    _turn(env, dial, 2.0)
    _turn(env, dial, 3.75)
    assert digest.hexdigest() != base


def test_digest_changes_with_trajectory() -> None:
    assert _system_digest(5) != _system_digest(6)
    assert _system_digest(5, until=60.0) != _system_digest(5, until=80.0)
    assert _system_digest(5) == _system_digest(5)


def test_digest_attach_after_activation_raises() -> None:
    env = Environment()
    env.activate()
    with pytest.raises(RuntimeError, match="after activation"):
        SemanticDigest.attach(env)
    with pytest.raises(RuntimeError, match="after activation"):
        env.enable_digest()


_HASH_SEED_SCRIPT = """
from simulatte.builders import build_lumscor_system
from simulatte.environment import Environment
from simulatte.scenario import Scenario

env = Environment(seed=20260508)
digest = env.enable_digest()
build_lumscor_system(
    env=env, scenario=Scenario(n_servers=4), check_timeout=5.0, wl_norm_level=6.0, allowance_factor=2
)
env.run(until=300)
print(env.fingerprint().digest)
"""


def test_digest_stable_across_hash_seeds() -> None:
    results = set()
    for hash_seed in ("0", "1", "123"):
        completed = subprocess.run(
            [sys.executable, "-c", _HASH_SEED_SCRIPT],
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        results.add(completed.stdout.strip())
    assert len(results) == 1
    (digest,) = results
    assert len(digest) == 64


def test_enable_digest_is_idempotent_and_fingerprint() -> None:
    env = Environment(seed=3)
    assert env.fingerprint() == Fingerprint(digest=None, kpis={})
    first = env.enable_digest()
    assert env.enable_digest() is first
    env.run(until=1)
    assert env.enable_digest() is first  # still the same digest once activated
    assert env.fingerprint() == Fingerprint(digest=first.hexdigest(), kpis={})


def test_prelude_events_are_not_part_of_the_digest() -> None:
    def digest_of(prelude: bool) -> str:
        env = Environment(seed=1)
        digest = SemanticDigest.attach(env)
        dial = Dial(env, name="dial")
        if prelude:
            _turn(env, dial, 1.0)  # emitted before activation: delivered, never projected
            dial.setting = 1.0
        env.activate()
        _turn(env, dial, 2.0)
        return digest.hexdigest()

    # the prelude turn leaves the snapshot unchanged (setting 1.0 -> 1.0), so both runs project the same items
    assert digest_of(True) == digest_of(False)


def test_projection_layout_and_framing() -> None:
    env = Environment(seed=1)
    seen: list[DomainEvent] = []
    digest = SemanticDigest.attach(env)
    dial = Dial(env, name="dial", note="secret")
    env.bus.subscribe(seen.append, "*")
    env.activate()
    _turn(env, dial, 2.0, comment="c", note="n", label="L")
    (turned,) = [e for e in seen if isinstance(e, Turned)]

    state = unpack(project_state(env.initial_state))
    assert state == FrozenMap({"dial": FrozenMap({"$kind": "test_digest_dial", "setting": 1.0})})

    kinds = {"dial": "test_digest_dial"}
    item = unpack(project_event(turned, kinds))
    assert item == (
        0,
        "test.digest_turned",
        1,
        0.0,
        FrozenMap({"dial": "dial", "to": 2.0}),
        (("set", "dial", "setting", 2.0),),
    )

    import hashlib

    expected = hashlib.blake2b(digest_size=32)
    for blob in (project_state(env.initial_state), project_event(turned, kinds)):
        expected.update(len(blob).to_bytes(8, "big"))
        expected.update(blob)
    assert digest.hexdigest() == expected.hexdigest()


def test_creation_projection_drops_presentation_state() -> None:
    env = Environment(seed=1)
    created: list[DomainEvent] = []
    SemanticDigest.attach(env)
    env.activate()
    env.bus.subscribe(created.append, "*")
    Dial(env, name="late", label="Late", note="hidden")
    (event,) = created
    item = unpack(project_event(event))
    assert isinstance(item, tuple)
    assert item[1] == "entity.created"
    assert item[4] == FrozenMap({"entity": "late", "kind": "test_digest_dial"})  # label payload removed
    assert item[5] == (("create", "late", "test_digest_dial", FrozenMap({"setting": 1.0})),)
    assert canonical_pack(item) == project_event(event)


def test_standalone_projection_infers_presentation_fields_of_unknown_entities() -> None:
    env = Environment(seed=1)
    seen: list[DomainEvent] = []
    env.bus.subscribe(seen.append, "*")
    SemanticDigest.attach(env)
    dial = Dial(env, name="dial")
    env.activate()
    _turn(env, dial, 4.0, label="L")
    turned = seen[-1]
    # no kind map: "label" is presentation in every kind, "setting" is not
    item: Any = unpack(project_event(turned))
    assert item[5] == (("set", "dial", "setting", 4.0),)


def test_projection_rolls_the_kind_map_over_create_and_retire() -> None:
    env = Environment(seed=1)
    seen: list[DomainEvent] = []
    env.bus.subscribe(seen.append, "*")
    SemanticDigest.attach(env)
    env.activate()
    dial = Dial(env, name="dial")
    env.entities.retire(dial)
    kinds: dict[str, str] = {}
    project_event(seen[0], kinds)
    assert kinds == {"dial": "test_digest_dial"}
    project_event(seen[1], kinds)
    assert kinds == {}


# ---------------------------------------------------------------------------------------------------------
# Golden: the canonical encoding path (digest and recorded content) must not change for the same run
# ---------------------------------------------------------------------------------------------------------


class GoldenBox(Entity, kind="test_golden_box"):
    state_schema: ClassVar[StateSchema] = StateSchema(
        {
            "level": FieldSpec("float"),
            "items": FieldSpec("any", collection="list"),
            "table": FieldSpec("any", collection="map"),
            "memo": FieldSpec("str", presentation=True),
        }
    )

    def __init__(self, env: Environment, *, name: str | None = None) -> None:
        self.env = env
        self.level = -0.0
        self.items = ["a", 1, (2, 3.5)]
        self.table = {"~x": 1, "__proto__": {"constructor": None}, "é": float("inf"), "z": (True, False)}
        self.memo = "hidden"
        env.entities.attach(self, name=name, label="Box")


@event_type(
    "test.golden_changed",
    touches={"test_golden_box": ("level", "items", "table", "memo", "label")},
    presentation=frozenset({"remark"}),
)
class GoldenChanged(DomainEvent):
    box: str
    constructor: int
    values: tuple[Any, ...]
    extra: Any
    remark: str = ""


@event_type("test.golden_plain", touches={"test_golden_box": ("level", "items", "table")})
class GoldenPlain(DomainEvent):
    box: str
    amount: float
    tags: Any


_GOLDEN_VALUES = (  # fmt: skip
    0,
    127,
    128,
    255,
    256,
    65535,
    65536,
    2**32 - 1,
    2**32,
    2**53 - 1,
    -1,
    -32,
    -33,
    -(2**53 - 1),
    0.0,
    -0.0,
    1e300,
    float("inf"),
    float("-inf"),
    float("nan"),
    math.copysign(float("nan"), -1.0),
    True,
    False,
    None,
    "",
    "x" * 40,
    "ü€😀",
)


def _golden_run(path: Path, content_of: Callable[[Path], str]) -> tuple[str, str]:
    from simulatte.trace import TraceRecorder

    env = Environment(seed=7)
    TraceRecorder(env, path)
    box = GoldenBox(env, name="box")
    env.emit(GoldenPlain(box="box", amount=1.0, tags=None))  # prelude: delivered, recorded, never projected
    env.activate()
    unfrozen: Any = {"z": float("nan"), "a": (1, {"y": 2, "x": 1}), "é": -0.0}  # a FrozenMap built directly
    hostile = {"~a": 1, "__proto__": 2, "constructor": 3, "prototype": {"~": [], "b": FrozenMap({"z": 1, "a": 2})}}
    env.emit(
        GoldenChanged(
            box="box",
            constructor=3,
            values=_GOLDEN_VALUES,
            extra=FrozenMap(unfrozen),
            remark="presentation",
            deltas=Deltas.build()
            .set("box", "level", 2.5)
            .set("box", "memo", "still hidden")
            .set("box", "label", "Box 2")
            .insert("box", "items", 0, hostile)
            .remove("box", "items", "a")
            .move("box", "items", 1, 0)
            .put("box", "table", "new", {"q": (1, 2), "p": None})
            .delete("box", "table", "z")
            .done(),
        )
    )
    env.emit(GoldenPlain(box="box", amount=float("nan"), tags=("t", 2**40, {"k": "v"})))
    env.emit(GoldenPlain(box="box", amount=-0.0, tags=FrozenMap({"b": 1, "a": 2})))
    late = GoldenBox(env, name="late")
    env.emit(GoldenPlain(box="late", amount=3.0, tags=hostile, deltas=Deltas.build().set("late", "level", 3.0).done()))
    env.entities.retire(late)
    env.entities.retire(box)
    env.run()
    env.close()
    digest = env.fingerprint().digest
    assert digest is not None
    return digest, content_of(path)


GOLDEN_SYNTHETIC_DIGEST = "437f67a9b6cb5bdb7c680a83f62bdfe90bd1b17a41bfa77da5da755488eab58a"
GOLDEN_SYNTHETIC_CONTENT = "cc16aa3e5ebbec97c7f7ae113b94f74ba6a441f6d391f97f8e57a0a8f833919a"


def test_golden_synthetic_digest_and_trace_content(tmp_path: Path, trace_content: Callable[[Path], str]) -> None:
    """Edge values (integer widths, signed zero, infinities, NaN payloads, hostile and non-ASCII keys, nested and
    unfrozen maps, presentation fields and ops, prelude, late create and retire) encode to
    pinned bytes; the recorded trace verifies and holds pinned canonical content."""
    from simulatte.trace import Trace

    digest, content = _golden_run(tmp_path / "golden.simtrace", trace_content)
    assert Trace.open(tmp_path / "golden.simtrace").verify() is True
    assert (digest, content) == (GOLDEN_SYNTHETIC_DIGEST, GOLDEN_SYNTHETIC_CONTENT)


def test_fast_path_equals_project_event_with_the_rolled_kind_map(tmp_path: Path) -> None:
    """SemanticDigest's fused encoder yields the bytes of project_event, fed through a DigestAccumulator."""
    from simulatte.trace import TraceRecorder

    env = Environment(seed=7)
    seen: list[DomainEvent] = []
    digest = env.enable_digest()
    TraceRecorder(env, tmp_path / "t.simtrace")  # shares the digest's tails
    env.bus.subscribe(seen.append, "*")
    GoldenBox(env, name="box")
    env.activate()
    env.emit(GoldenPlain(box="box", amount=2.0, tags=(1, {"b": 2, "a": 1}), deltas=Deltas.build().done()))
    env.emit(GoldenChanged(box="box", constructor=1, values=(float("nan"),), extra=None, remark="r"))
    late = GoldenBox(env, name="late")
    env.emit(GoldenPlain(box="late", amount=-0.0, tags=None, deltas=Deltas.build().set("late", "memo", "m").done()))
    env.entities.retire(late)
    env.close()

    replayed = DigestAccumulator()
    replayed.feed_state(env.initial_state)
    for event in seen:
        if event.ordinal is not None:
            replayed.feed(project_event(event, replayed.kinds))
    assert replayed.kinds == {"box": "test_golden_box"}
    assert replayed.hexdigest() == digest.hexdigest()


def test_shared_tail_only_for_events_without_presentation() -> None:
    env = Environment(seed=7)
    digest = env.enable_digest()
    seen: list[DomainEvent] = []
    env.bus.subscribe(lambda event: seen.append(event) if digest.shared_tail(event) is not None else None, "*")
    box = GoldenBox(env, name="box")
    env.activate()
    plain = GoldenPlain(box="box", amount=1.0, tags=None, deltas=Deltas.build().set("box", "level", 1.0).done())
    env.emit(plain)
    assert seen == []  # tails are kept only after share_tails() (a recorder asks for them)

    digest.share_tails()
    env.emit(GoldenPlain(box="box", amount=1.0, tags=None, deltas=Deltas.build().set("box", "level", 1.0).done()))
    env.emit(GoldenChanged(box="box", constructor=1, values=(), extra=None))  # presentation payload field
    env.emit(GoldenPlain(box="box", amount=1.0, tags=None, deltas=Deltas.build().set("box", "memo", "x").done()))
    GoldenBox(env, name="late")  # entity.created: label is presentation
    env.entities.retire(box)
    assert [type(event).__name__ for event in seen] == ["GoldenPlain", "EntityRetired"]
    assert digest.shared_tail(plain) is None  # only the event projected last
    tail = digest.shared_tail(seen[-1])
    assert tail is not None and unpack(b"\x92" + tail) == (
        FrozenMap({"entity": "box", "kind": "test_golden_box"}),
        (("retire", "box"),),
    )


def test_create_projection_accepts_plain_state_maps() -> None:
    from simulatte._wire import freeze
    from simulatte.digest import project_event_parts

    state = {"setting": 1.0, "note": "n", "label": "L"}

    def project(created: Any) -> bytes:
        return project_event_parts(0, "x", 1, 0.0, {}, (("create", "d", "test_digest_dial", created),), kinds={})

    assert project(state) == project(freeze(state)) == project(FrozenMap(state))
    assert unpack(project(state)) == (
        0,
        "x",
        1,
        0.0,
        FrozenMap({}),
        (("create", "d", "test_digest_dial", FrozenMap({"setting": 1.0})),),
    )
