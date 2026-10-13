"""Replication-level statistics: t intervals, paired differences and Welch averaging (design 5, D98, D99).

Pure Python and standard library only. Observations are one scalar per replication;
samples inside a run are never treated as observations.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Any, TypeGuard

__all__ = ["Estimate", "Paired", "bonferroni", "paired", "summarize", "t_quantile", "welch"]


def _check_level(level: float) -> float:
    if isinstance(level, bool) or not isinstance(level, (int, float)) or not 0 < level < 1:
        raise ValueError(f"level must be in (0, 1), got {level!r}")
    return float(level)


def _finite(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"values must be finite numbers, got {value!r}")
    return float(value)


# Student t quantile ------------------------------------------------------------


def _continued_fraction(a: float, b: float, x: float) -> float:
    """Lentz evaluation of the incomplete beta continued fraction (Numerical Recipes ``betacf``)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 100_000):
        m2 = 2 * m
        step = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + step * d
        c = 1.0 + step / c
        d = 1.0 / (d if abs(d) > tiny else tiny)
        h *= d * (c if abs(c) > tiny else tiny)
        step = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + step * d
        c = 1.0 + step / c
        d = 1.0 / (d if abs(d) > tiny else tiny)
        change = d * (c if abs(c) > tiny else tiny)
        h *= change
        if abs(change - 1.0) < 1e-15:
            return h
    raise RuntimeError("incomplete beta continued fraction did not converge")  # pragma: no cover


def _incomplete_beta(a: float, b: float, x: float) -> float:
    """The regularized incomplete beta function ``I_x(a, b)``."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _continued_fraction(a, b, x) / a
    return 1.0 - front * _continued_fraction(b, a, 1.0 - x) / b


def _upper_tail(t: float, df: int) -> float:
    """``P(T > t)`` for ``t >= 0``."""
    return 0.5 * _incomplete_beta(df / 2.0, 0.5, df / (df + t * t))


def t_quantile(p: float, df: int) -> float:
    """The Student t quantile at probability ``p`` for ``df`` degrees of freedom, to about 1e-12."""
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 < p < 1:
        raise ValueError(f"t_quantile: p must be in (0, 1), got {p!r}")
    if isinstance(df, bool) or not isinstance(df, int) or df < 1:
        raise ValueError(f"t_quantile: df must be a positive integer, got {df!r}")
    if p == 0.5:
        return 0.0
    if p < 0.5:
        return -t_quantile(1.0 - p, df)
    target = 1.0 - p
    low, high = 0.0, 1.0
    while _upper_tail(high, df) > target:
        low, high = high, high * 2.0
        if high > 1e300:  # pragma: no cover - a tail below the float resolution of p
            break
    while high - low > 1e-13 * max(1.0, high):
        middle = (low + high) / 2.0
        if _upper_tail(middle, df) > target:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


# Estimates ---------------------------------------------------------------------


@dataclass(frozen=True)
class Estimate:
    """Mean and t interval of replication-level values.

    ``state`` is ``ok`` (n >= 2, sd > 0), ``degenerate`` (n >= 2, all values equal: a
    zero-width interval) or ``insufficient`` (n < 2: the single value or nothing, no interval).
    """

    n: int
    mean: float | None
    sd: float | None
    half_width: float | None
    lower: float | None
    upper: float | None
    state: str
    level: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "mean": self.mean,
            "sd": self.sd,
            "half_width": self.half_width,
            "lower": self.lower,
            "upper": self.upper,
            "state": self.state,
            "level": self.level,
        }


def summarize(values: Iterable[float], *, level: float = 0.95) -> Estimate:
    """Mean and two-sided t interval of ``values`` at ``level``."""
    level = _check_level(level)
    data = [_finite(value) for value in values]
    n = len(data)
    if n < 2:
        return Estimate(n, data[0] if data else None, None, None, None, None, "insufficient", level)
    if min(data) == max(data):
        # Identical values: rounding in the mean would otherwise leave a ~1e-16 sd and an ``ok`` interval.
        return Estimate(n, data[0], 0.0, 0.0, data[0], data[0], "degenerate", level)
    mean = math.fsum(data) / n
    sd = math.sqrt(math.fsum((value - mean) ** 2 for value in data) / (n - 1))
    half_width = t_quantile(1.0 - (1.0 - level) / 2.0, n - 1) * sd / math.sqrt(n)
    return Estimate(n, mean, sd, half_width, mean - half_width, mean + half_width, "ok", level)


def bonferroni(level: float, m: int) -> float:
    """The per-interval level that keeps ``m`` simultaneous intervals at family level ``level``."""
    level = _check_level(level)
    if isinstance(m, bool) or not isinstance(m, int) or m < 1:
        raise ValueError(f"m must be a positive integer, got {m!r}")
    return 1.0 - (1.0 - level) / m


@dataclass(frozen=True)
class Paired:
    """Per-index differences ``b - a`` with their estimate; ``missing`` lists unpaired indices."""

    differences: dict[int, float]
    n: int
    estimate: Estimate
    missing: list[int]


def _usable(value: Any) -> TypeGuard[float]:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def paired(a: Mapping[int, float | None], b: Mapping[int, float | None], *, level: float = 0.95) -> Paired:
    """Paired differences ``b - a`` by replication index only.

    An index present on one side only, or whose value is None or non-finite on either
    side, is listed in ``missing`` and excluded.
    """
    differences: dict[int, float] = {}
    missing = []
    for index in sorted(set(a) | set(b)):
        first, second = a.get(index), b.get(index)
        if _usable(first) and _usable(second):
            differences[index] = float(second) - float(first)
        else:
            missing.append(index)
    return Paired(differences, len(differences), summarize(differences.values(), level=level), missing)


# Welch's moving average ----------------------------------------------------------


def welch(
    series: Sequence[Sequence[tuple[float, float]]], *, window: int, grid: Sequence[float]
) -> list[tuple[float, float]]:
    """Average replications on ``grid``, then smooth with a centred moving average of ``window`` points.

    Each replication is sampled with previous-value interpolation; a grid point before its
    first sample is skipped for that replication, and a grid point no replication covers is
    dropped. At the edges the moving average uses the available points.
    """
    if isinstance(window, bool) or not isinstance(window, int) or window < 1 or window % 2 == 0:
        raise ValueError(f"window must be an odd positive integer, got {window!r}")
    sampled = []
    for replication in series:
        times = [float(t) for t, _ in replication]
        values = [_finite(value) for _, value in replication]
        sampled.append((times, values))
    averages: list[tuple[float, float]] = []
    for time in grid:
        found = []
        for times, values in sampled:
            position = bisect_right(times, time)
            if position:
                found.append(values[position - 1])
        if found:
            averages.append((float(time), math.fsum(found) / len(found)))
    half = window // 2
    return [
        (
            time,
            math.fsum(value for _, value in averages[max(0, i - half) : i + half + 1])
            / len(averages[max(0, i - half) : i + half + 1]),
        )
        for i, (time, _) in enumerate(averages)
    ]
