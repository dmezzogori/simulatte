from __future__ import annotations


class Seconds:
    multiplier = 1
    value: int

    def __init__(self, value: int | Seconds = 0) -> None:
        if not isinstance(value, int):
            value = value.value
        self.value = value

    def __repr__(self) -> str:
        return f"{self.value} {self.__class__.__name__}"

    def __mul__(self, other) -> Seconds:
        prev_cls = self.__class__.mro()[1]
        if prev_cls in (Seconds, object):
            return other * self.multiplier
        return other * self.multiplier * prev_cls()

    def __rmul__(self, other) -> Seconds:
        return self.__mul__(other)


class Minutes(Seconds):
    multiplier = 60


class Hours(Minutes):
    multiplier = 60


class Days(Hours):
    multiplier = 24
