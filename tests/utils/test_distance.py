from simulatte.utils.distance import euclidean, manhattan


def test_euclidean():
    x1, y1 = 0, 0
    x2, y2 = 1, 1

    assert euclidean(x1=x1, y1=y1, x2=x2, y2=y2) == 2**0.5


def test_manhattan():
    x1, y1 = 0, 0
    x2, y2 = 1, 1

    assert manhattan(x1=x1, y1=y1, x2=x2, y2=y2) == 2
