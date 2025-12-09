import math
from typing import Protocol


class HasCoordinates(Protocol):
    coordinates: tuple[float, float]


def euclidean(location_a: HasCoordinates, location_b: HasCoordinates) -> float:
    """
    The euclidean distance between two WarehouseLocations.
    """

    loc_a_x, loc_a_y = location_a.coordinates
    loc_b_x, loc_b_y = location_b.coordinates
    return math.sqrt((loc_a_x - loc_b_x) ** 2 + ((loc_a_y - loc_b_y) ** 2))


def manhattan(location_a: HasCoordinates, location_b: HasCoordinates) -> float:
    """
    The manhattan distance between two WarehouseLocations.
    """

    loc_a_x, loc_a_y = location_a.coordinates
    loc_b_x, loc_b_y = location_b.coordinates
    return abs(loc_a_x - loc_a_y) + abs(loc_b_x - loc_b_y)
