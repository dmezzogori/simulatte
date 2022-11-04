from math import sqrt


def euclidean(*, x1: float, x2: float, y1: float, y2: float) -> float:
    """
    The euclidean distance between two points.
    """
    return sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)


def manhattan(*, x1: float, x2: float, y1: float, y2: float) -> float:
    """
    The manhattan distance between two points.
    """
    return abs(x1 - x2) + abs(y1 - y2)
