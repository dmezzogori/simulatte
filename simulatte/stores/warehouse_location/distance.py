import math

from .warehouse_location import WarehouseLocation


def euclidean(location_a: WarehouseLocation, location_b: WarehouseLocation) -> float:
    """
    The euclidean distance between two WarehouseLocations.
    """

    loc_a_x, loc_a_y = location_a.coordinates
    loc_b_x, loc_b_y = location_b.coordinates
    return math.sqrt((loc_a_x - loc_b_x) ** 2 + ((loc_a_y - loc_b_y) ** 2))


def manhattan(location_a: WarehouseLocation, location_b: WarehouseLocation) -> float:
    """
    The manhattan distance between two WarehouseLocations.
    """

    loc_a_x, loc_a_y = location_a.coordinates
    loc_b_x, loc_b_y = location_b.coordinates
    return abs(loc_a_x - loc_a_y) + abs(loc_b_x - loc_b_y)
