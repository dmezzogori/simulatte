"""The entities of :class:`~simulatte.intralogistics.reservation.ReservationController`: one ``segment`` per segment
and one ``zone`` per zone or corridor (spec §11.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from simulatte._wire import wire_float
from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.intralogistics.network import Corridor, Segment, Zone

if TYPE_CHECKING:
    from collections.abc import Sequence

    from simulatte.environment import Environment

__all__ = ["Container", "SegmentBinding", "ZoneBinding"]

Container = Zone | Corridor


class SegmentBinding(Entity, kind="segment"):
    """The entity of a :class:`~simulatte.intralogistics.network.Segment` (spec §11.1).

    Its id is the segment id. ``source`` and ``target`` are the node ids in sorted order; ``forward`` lists the AGVs
    holding the segment from ``source`` to ``target`` and ``backward`` the others, both in grant order.
    """

    state_schema: ClassVar[StateSchema] = StateSchema(
        {
            "source": FieldSpec("str"),
            "target": FieldSpec("str"),
            "length": FieldSpec("float"),
            "forward": FieldSpec("str", collection="list"),
            "backward": FieldSpec("str", collection="list"),
        }
    )

    def __init__(self, env: Environment, segment: Segment) -> None:
        self.segment = segment
        self.forward: list[str] = []
        self.backward: list[str] = []
        env.entities.attach(self, name=segment.id)

    def snapshot(self) -> dict[str, Any]:
        """Current entity state: the end nodes, the length and the holders of each direction."""
        segment = self.segment
        return {
            "source": segment.a.id,
            "target": segment.b.id,
            "length": wire_float(segment.length),
            "forward": list(self.forward),
            "backward": list(self.backward),
            "label": self.label,
        }


class ZoneBinding(Entity, kind="zone"):
    """The entity of an authored zone (``rule`` ``exclusive``) or a derived corridor (``same_direction``).

    ``capacity`` is null for a corridor; ``members`` are node and segment ids; ``reserved_by`` lists the holders in
    grant order; ``orientation`` is the orientation of a corridor's oriented holders, or null.
    """

    state_schema: ClassVar[StateSchema] = StateSchema(
        {
            "rule": FieldSpec("str"),
            "capacity": FieldSpec("int", nullable=True),
            "members": FieldSpec("str", collection="list"),
            "reserved_by": FieldSpec("str", collection="list"),
            "orientation": FieldSpec("str", nullable=True),
        }
    )

    def __init__(self, env: Environment, container: Container, members: Sequence[str]) -> None:
        self.container = container
        self.rule = "same_direction" if isinstance(container, Corridor) else "exclusive"
        self.capacity: int | None = None if isinstance(container, Corridor) else container.capacity
        self.members = list(members)
        self.reserved_by: list[str] = []
        self.orientation: str | None = None
        env.entities.attach(self, name=container.id)

    def snapshot(self) -> dict[str, Any]:
        """Current entity state: rule, capacity, members, holders and orientation."""
        return {
            "rule": self.rule,
            "capacity": self.capacity,
            "members": list(self.members),
            "reserved_by": list(self.reserved_by),
            "orientation": self.orientation,
            "label": self.label,
        }
