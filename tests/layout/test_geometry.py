from fractions import Fraction
import math

import pytest

from simulatte.layout.geometry import Rectangle, point_distance_squared, segment_intersection, transform
from simulatte.layout.types import Point


def test_transforms_and_circular_clearance():
    assert transform(Point(2, 3), Point(10, 20), 90) == Point(7, 22)
    rectangle = Rectangle.create("box", Point(0, 0), 2, 2, 0)
    assert rectangle.blocks_point(Point(2, 1), 1)
    assert not rectangle.blocks_point(Point(1.8, 1.8), 1)
    assert rectangle.blocks_segment(Point(-3, 0), Point(3, 0), 0)
    assert rectangle.blocks_segment(Point(-3, 2), Point(3, 2), 1)
    assert not rectangle.blocks_segment(
        Point(-3, math.nextafter(2, math.inf)), Point(3, math.nextafter(2, math.inf)), 1
    )
    assert point_distance_squared(Point(0, 0), Point(1, 1)) == 2


def test_exact_intersection_and_materialized_rectangle():
    assert segment_intersection(Point(0, 0), Point(3, 3), Point(0, 2), Point(3, 0)) == (Fraction(6, 5), Fraction(6, 5))
    with pytest.raises(ValueError, match="overlap"):
        segment_intersection(Point(0, 0), Point(3, 0), Point(1, 0), Point(4, 0))
    rect = Rectangle.create("rotated", Point(1, 2), 2, 3, 37)
    assert rect == Rectangle.create("rotated", Point(1, 2), 2, 3, 37)
    assert rect.blocks_point(Point(1, 2), 0)
    assert all(rect.blocks_point(p, 0) for p in rect.vertices)


@pytest.mark.parametrize(
    "rotation,expected", [(0, (2, 3)), (90, (-3, 2)), (180, (-2, -3)), (270, (3, -2)), (450, (-3, 2))]
)
def test_quadrants(rotation, expected):
    assert transform(Point(2, 3), Point(0, 0), rotation) == Point(*expected)


@pytest.mark.parametrize(
    "a,b,c,d,expected",
    [
        ((0, 0), (1, 0), (1, 0), (2, 0), (1, 0)),
        ((0, 0), (1, 0), (2, 0), (3, 0), None),
        ((0, 0), (1, 0), (0, 1), (1, 1), None),
        ((0, 0), (1, 0), (2, -1), (2, 1), None),
        ((1, 0), (0, 0), (1, 0), (2, 0), (1, 0)),
    ],
)
def test_intersection_boundaries(a, b, c, d, expected):
    assert segment_intersection(*(Point(*p) for p in (a, b, c, d))) == expected


def test_distance_predicates_are_exact_and_closed():
    box = Rectangle.create("b", Point(0, 0), 2, 2, 0)
    assert box.point_distance_squared(Point(2, 2)) == 2
    assert box.segment_distance_squared(Point(3, 2), Point(4, 2)) == 5
    assert box.segment_distance_squared(Point(-2, 1), Point(2, 1)) == 0
    assert box.segment_distance_squared(Point(-2, -1), Point(2, -1)) == 0
    assert box.segment_distance_squared(Point(1, 1), Point(3, 3)) == 0
    assert box.segment_distance_squared(Point(-3, 0), Point(0, 0)) == 0
    assert box.segment_distance_squared(Point(2, 2), Point(2, 2)) == 2
    assert box.blocks_point(Point(2, 0), 1)
    assert box.blocks_point(Point(math.nextafter(2, 0), 0), 1)
    assert not box.blocks_point(Point(math.nextafter(2, math.inf), 0), 1)
    a, b = Point(1e16, 0), Point(1e16, 1)
    assert math.hypot(a.x, a.y) == math.hypot(b.x, b.y)
    assert point_distance_squared(Point(0, 0), a) < point_distance_squared(Point(0, 0), b)


def test_transform_precision_diagnostics():
    from simulatte.layout.geometry import materialize

    with pytest.raises(ValueError, match="precision"):
        Rectangle.create("tiny", Point(1e16, 0), 1, 1, 0)
    with pytest.raises(ValueError, match="precision"):
        transform(Point(1e308, 0), Point(1e308, 0), 0)
    with pytest.raises(ValueError, match="precision"):
        materialize((Fraction(10) ** 400, Fraction(0)))
