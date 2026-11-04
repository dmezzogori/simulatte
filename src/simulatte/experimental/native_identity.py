"""Experimental identity composition for one supplied native production observation.

This pure-Python helper does not query an engine. The caller supplies a fresh
``NativeProduction.state_projection()`` and its model-owned action commitment.
It preserves the complete authority evidence; it cannot prove that a model's
callback describes all future input or authorize result reuse on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any, cast

from simulatte._wire import canonical_pack, freeze, FrozenMap, Wire

__all__ = ["IdentityComposition", "compose_native_identity"]

_TRAJECTORY = "native-trajectory-control-v1"
_REPORTING = "native-reporting-state-v1"
_COMPLETE = "native-reproduction-state-v1"


def _plain(value: Wire) -> Any:
    if isinstance(value, FrozenMap):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class IdentityComposition:
    """Detached immutable snapshot returned by :func:`compose_native_identity`.

    ``canonical_bytes`` is the canonical MessagePack identity envelope, including
    its evidence. ``native_bytes`` is the provider's queried native-state size,
    not an allocation estimate. Retaining this object does not refresh its state.
    """

    _value: FrozenMap = field(repr=False)
    canonical_bytes: bytes = field(repr=False)
    native_bytes: int

    @property
    def size_bytes(self) -> int:
        """Size of the canonical identity envelope, including evidence."""
        return len(self.canonical_bytes)

    def to_dict(self) -> dict[str, Any]:
        """Return a fresh mutable dictionary with independent nested containers."""
        return _plain(self._value)


def _component(schema: str, value: FrozenMap) -> FrozenMap:
    return cast(FrozenMap, freeze(dict(schema=schema, digest=hashlib.sha256(canonical_pack(value)).hexdigest())))


def compose_native_identity(projection: object, *, actions: object) -> IdentityComposition:
    """Compose trajectory/control, reporting and reproduction identities once.

    ``projection`` must be a valid ``native-production-projection-v1`` observation
    from a synchronized live managed backend. ``actions`` is model-owned wire
    data describing realized control actions, not a replacement for ``input_state``.
    Both inputs are copied into canonical immutable values once per call. No
    callback, native query, cross-query cache or binary parser is used here.

    The result uses ``native-identity-set-v1`` with complete authority/preimage
    evidence. It does not change default fingerprints or Coordinator reproduction.
    Structural checks reject unsupported/missing projection fields; this is not
    an independent verifier of an arbitrary or forged provider observation.
    Non-wire values raise ``TypeError`` (out-of-range integers: ``OverflowError``).
    """
    view = freeze(projection)
    if not isinstance(view, FrozenMap) or view.get("schema") != "native-production-projection-v1":
        raise ValueError("Expected native-production-projection-v1 observation")
    required = {"scope", "trajectory", "reporting", "authority", "authority_preimage", "native_bytes"}
    if not required.issubset(view):
        raise ValueError("Missing native production projection fields")
    reporting = view["reporting"]
    if not isinstance(reporting, FrozenMap) or not {"configuration", "catalog", "snapshot"}.issubset(reporting):
        raise ValueError("Missing native production reporting fields")
    native_bytes = view["native_bytes"]
    if type(native_bytes) is not int or native_bytes < 0:
        raise ValueError("native_bytes must be a nonnegative integer")
    scope, authority = view["scope"], view["authority"]
    trajectory = cast(
        FrozenMap, freeze(dict(schema=_TRAJECTORY, scope=scope, native=view["trajectory"], actions=freeze(actions)))
    )
    report = cast(
        FrozenMap,
        freeze(
            dict(
                schema=_REPORTING,
                scope=scope,
                configuration=reporting["configuration"],
                catalog=reporting["catalog"],
                snapshot=reporting["snapshot"],
            )
        ),
    )
    trajectory_id, report_id = _component(_TRAJECTORY, trajectory), _component(_REPORTING, report)
    complete = cast(
        FrozenMap,
        freeze(
            dict(
                schema=_COMPLETE,
                scope=scope,
                authority=authority,
                trajectory_control=trajectory_id,
                reporting=report_id,
            )
        ),
    )
    value = cast(
        FrozenMap,
        freeze(
            dict(
                schema="native-identity-set-v1",
                trajectory_control=trajectory_id,
                reporting=report_id,
                reproduction_state=_component(_COMPLETE, complete),
                authority=authority,
                evidence=dict(
                    trajectory=trajectory,
                    report=report,
                    reproduction=complete,
                    authority_preimage=view["authority_preimage"],
                ),
            )
        ),
    )
    return IdentityComposition(value, canonical_pack(value), native_bytes)
