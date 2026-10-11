"""Materialized float64 geometry with exact rational topology and clearance predicates."""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

from .errors import LayoutError
from .types import Point, normalize_rotation, number

ExactPoint = tuple[Fraction, Fraction]


def exact(point: Point) -> ExactPoint:
    return Fraction(point.x), Fraction(point.y)


def materialize(point: ExactPoint) -> Point:
    try:
        return Point(float(point[0]), float(point[1]))
    except (OverflowError, LayoutError) as exc:
        raise LayoutError("geometry precision: coordinate exceeds finite float64 range") from exc


def _sub(a: ExactPoint, b: ExactPoint) -> ExactPoint:
    return a[0] - b[0], a[1] - b[1]


def _cross(a: ExactPoint, b: ExactPoint) -> Fraction:
    return a[0] * b[1] - a[1] * b[0]


def _dot(a: ExactPoint, b: ExactPoint) -> Fraction:
    return a[0] * b[0] + a[1] * b[1]


def _transform(point: ExactPoint, center: Point, rotation: float) -> Point:
    angle = normalize_rotation(rotation)
    x, y = point
    if angle == 0:
        a, b = x, y
    elif angle == 90:
        a, b = -y, x
    elif angle == 180:
        a, b = -x, -y
    elif angle == 270:
        a, b = y, -x
    else:
        radians = math.radians(angle)
        c, s = Fraction(math.cos(radians)), Fraction(math.sin(radians))
        a, b = x * c - y * s, x * s + y * c
    return materialize((a + Fraction(center.x), b + Fraction(center.y)))


def transform(point: Point, center: Point, rotation: float) -> Point:
    """Rotate once, then translate, with exact signed permutations at quadrants."""
    return _transform(exact(point), center, rotation)


def point_distance_squared(a: Point, b: Point) -> Fraction:
    delta = _sub(exact(a), exact(b))
    return _dot(delta, delta)


def _point_segment_distance(p: ExactPoint, a: ExactPoint, b: ExactPoint) -> Fraction:
    d, v = _sub(b, a), _sub(p, a)
    length = _dot(d, d)
    t = min(Fraction(1), max(Fraction(0), _dot(v, d) / length)) if length else Fraction(0)
    delta = _sub(v, (t * d[0], t * d[1]))
    return _dot(delta, delta)


def _intersection(a: ExactPoint, b: ExactPoint, c: ExactPoint, d: ExactPoint) -> ExactPoint | None:
    ab, cd, ac = _sub(b, a), _sub(d, c), _sub(c, a)
    cross = _cross(ab, cd)
    if cross:
        t, u = _cross(ac, cd) / cross, _cross(ac, ab) / cross
        if 0 <= t <= 1 and 0 <= u <= 1:
            return a[0] + t * ab[0], a[1] + t * ab[1]
        return None
    if _cross(ac, ab) or _cross(ac, cd):
        return None
    # Lexicographic order also orders points along every collinear segment.
    lo, hi = max(min(a, b), min(c, d)), min(max(a, b), max(c, d))
    if lo < hi:
        raise LayoutError("positive-length collinear overlap")
    return lo if lo == hi else None


def segment_intersection(a: Point, b: Point, c: Point, d: Point) -> ExactPoint | None:
    """Return the exact crossing; positive-length collinear overlap is an error."""
    return _intersection(exact(a), exact(b), exact(c), exact(d))


def _segments_touch(a: ExactPoint, b: ExactPoint, c: ExactPoint, d: ExactPoint) -> bool:
    try:
        return _intersection(a, b, c, d) is not None
    except LayoutError:
        return True


@dataclass(frozen=True)
class Rectangle:
    """An authoritative stored convex quadrilateral; owner is a placement id or None."""

    id: str
    vertices: tuple[Point, Point, Point, Point]
    owner: str | None = None

    @classmethod
    def create(
        cls, id: str, center: Point, width: float, depth: float, rotation: float, *, owner: str | None = None
    ) -> Rectangle:
        w = Fraction(number(width, "width", positive=True)) / 2
        h = Fraction(number(depth, "depth", positive=True)) / 2
        vertices = tuple(_transform(p, center, rotation) for p in ((-w, -h), (w, -h), (w, h), (-w, h)))
        q = tuple(map(exact, vertices))
        if any(_cross(_sub(q[(i + 1) % 4], q[i]), _sub(q[(i + 2) % 4], q[(i + 1) % 4])) <= 0 for i in range(4)):
            raise LayoutError(f"rectangle {id!r}: geometry precision collapsed its stored convex footprint")
        return cls(id, (vertices[0], vertices[1], vertices[2], vertices[3]), owner)

    def _contains(self, point: ExactPoint) -> bool:
        q = tuple(map(exact, self.vertices))
        return all(_cross(_sub(q[(i + 1) % 4], q[i]), _sub(point, q[i])) >= 0 for i in range(4))

    def point_distance_squared(self, point: Point) -> Fraction:
        p = exact(point)
        if self._contains(p):
            return Fraction(0)
        q = tuple(map(exact, self.vertices))
        return min(_point_segment_distance(p, q[i], q[(i + 1) % 4]) for i in range(4))

    def segment_distance_squared(self, source: Point, target: Point) -> Fraction:
        a, b = exact(source), exact(target)
        if self._contains(a) or self._contains(b):
            return Fraction(0)
        q = tuple(map(exact, self.vertices))
        distances = []
        for i in range(4):
            c, d = q[i], q[(i + 1) % 4]
            if _segments_touch(a, b, c, d):
                return Fraction(0)
            distances.extend(
                (
                    _point_segment_distance(a, c, d),
                    _point_segment_distance(b, c, d),
                    _point_segment_distance(c, a, b),
                    _point_segment_distance(d, a, b),
                )
            )
        return min(distances)

    def blocks_point(self, point: Point, clearance: float) -> bool:
        return self.point_distance_squared(point) <= Fraction(number(clearance, "clearance", nonnegative=True)) ** 2

    def blocks_segment(self, source: Point, target: Point, clearance: float) -> bool:
        return (
            self.segment_distance_squared(source, target)
            <= Fraction(number(clearance, "clearance", nonnegative=True)) ** 2
        )
