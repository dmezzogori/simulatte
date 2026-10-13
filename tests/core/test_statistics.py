"""Pure-Python statistics: t quantiles, replication-level intervals, paired differences and Welch averaging."""

import math

import pytest

from simulatte.statistics import _incomplete_beta, Estimate, bonferroni, paired, summarize, t_quantile, welch

REFERENCE = {
    1: 12.7062047362,
    2: 4.3026527297,
    5: 2.5705818366,
    10: 2.2281388520,
    30: 2.0422724563,
    60: 2.0002978220,
    120: 1.9799303982,
}


@pytest.mark.parametrize(("df", "expected"), REFERENCE.items())
def test_t_quantile_matches_reference_values(df, expected):
    assert t_quantile(0.975, df) == pytest.approx(expected, abs=1e-6)


def test_t_quantile_approaches_the_normal_limit_and_is_symmetric():
    assert t_quantile(0.975, 10**6) == pytest.approx(1.9599639845, abs=1e-5)
    assert t_quantile(0.5, 7) == 0.0
    assert t_quantile(0.025, 10) == pytest.approx(-t_quantile(0.975, 10), abs=1e-12)
    assert t_quantile(0.9995, 3) == pytest.approx(12.9239, abs=1e-4)
    # Closed form for df 2: t = (2p - 1) / sqrt(2 p (1 - p)).
    assert t_quantile(1 - 1e-9, 2) == pytest.approx((1 - 2e-9) / math.sqrt(2 * (1 - 1e-9) * 1e-9), rel=1e-6)


def test_t_quantile_next_to_the_median_and_beta_bounds():
    # x = df / (df + t^2) rounds to 1 below t of about 1e-8, which bounds the resolution next to the median.
    assert 0 < t_quantile(0.5 + 2**-52, 5) < 1e-7
    assert _incomplete_beta(2.0, 3.0, 0.0) == 0.0 and _incomplete_beta(2.0, 3.0, 1.0) == 1.0
    assert _incomplete_beta(2.0, 3.0, 0.5) == pytest.approx(0.6875)  # I_x(2, 3) at x = 1/2


@pytest.mark.parametrize(
    ("p", "df"), [(0.0, 5), (1.0, 5), (-0.1, 5), (float("nan"), 5), (0.9, 0), (0.9, -3), (0.9, 2.5), (0.9, True)]
)
def test_t_quantile_rejects_invalid_arguments(p, df):
    with pytest.raises(ValueError, match="t_quantile"):
        t_quantile(p, df)


def test_summarize_states():
    ok = summarize([1.0, 2.0])
    assert ok.state == "ok" and ok.n == 2 and ok.mean == 1.5 and ok.level == 0.95
    assert ok.sd == pytest.approx(math.sqrt(0.5))
    assert ok.half_width == pytest.approx(REFERENCE[1] * math.sqrt(0.5) / math.sqrt(2), abs=1e-6)
    assert ok.half_width is not None
    assert ok.lower == pytest.approx(1.5 - ok.half_width) and ok.upper == pytest.approx(1.5 + ok.half_width)

    degenerate = summarize([3.0, 3.0])
    assert (degenerate.state, degenerate.n, degenerate.mean, degenerate.sd) == ("degenerate", 2, 3.0, 0.0)
    assert (degenerate.half_width, degenerate.lower, degenerate.upper) == (0.0, 3.0, 3.0)

    # Equal values whose mean rounds away from them are still degenerate, not a ~1e-16 interval.
    repeated = summarize([0.1] * 3)
    assert (repeated.state, repeated.mean, repeated.sd, repeated.half_width) == ("degenerate", 0.1, 0.0, 0.0)
    assert summarize([0.1, 0.1, 0.1000001]).state == "ok"

    single = summarize([3.0])
    assert (single.state, single.n, single.mean) == ("insufficient", 1, 3.0)
    assert (single.sd, single.half_width, single.lower, single.upper) == (None, None, None, None)

    empty = summarize([])
    assert (empty.state, empty.n, empty.mean, empty.half_width) == ("insufficient", 0, None, None)


def test_summarize_level_and_known_interval():
    values = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
    estimate = summarize(values, level=0.9)
    assert estimate.level == 0.9 and estimate.mean == 5.0
    assert estimate.sd == pytest.approx(math.sqrt(32 / 7))
    # t(0.95, 7) = 1.894578605
    assert estimate.sd is not None
    assert estimate.half_width == pytest.approx(1.894578605 * estimate.sd / math.sqrt(8), abs=1e-6)
    assert summarize(iter(values)).n == 8


@pytest.mark.parametrize("bad", [[1.0, float("nan")], [float("inf"), 1.0], [1.0, None], ["x"]])
def test_summarize_rejects_non_finite_values(bad):
    with pytest.raises(ValueError, match="finite"):
        summarize(bad)


@pytest.mark.parametrize("level", [0.0, 1.0, 1.5, float("nan")])
def test_levels_are_open_unit_interval(level):
    with pytest.raises(ValueError, match="level"):
        summarize([1.0, 2.0], level=level)
    with pytest.raises(ValueError, match="level"):
        bonferroni(level, 2)


def test_estimate_to_dict_is_plain_data():
    assert summarize([3.0]).to_dict() == {
        "n": 1,
        "mean": 3.0,
        "sd": None,
        "half_width": None,
        "lower": None,
        "upper": None,
        "state": "insufficient",
        "level": 0.95,
    }
    assert isinstance(summarize([1.0, 2.0]), Estimate)


def test_paired_lists_missing_indices_and_insufficient_single_pair():
    result = paired({0: 1.0, 1: 2.0, 3: 5.0}, {0: 2.0, 2: 7.0, 3: 4.0})
    assert result.differences == {0: 1.0, 3: -1.0}
    assert result.missing == [1, 2]
    assert result.n == 2 and result.estimate.n == 2 and result.estimate.mean == 0.0

    single = paired({0: 1.0, 1: 2.0}, {1: 5.0})
    assert single.differences == {1: 3.0} and single.missing == [0]
    assert single.estimate.state == "insufficient" and single.estimate.mean == 3.0
    assert single.estimate.half_width is None and single.estimate.lower is None

    assert paired({}, {}).estimate.n == 0


def test_paired_pairs_by_index_not_by_order_and_skips_unusable_values():
    nan = float("nan")
    result = paired({2: 1.0, 0: 2.0, 1: nan, 4: None}, {0: 5.0, 1: 1.0, 2: 3.0, 4: 1.0, 5: 1.0}, level=0.9)
    assert result.differences == {0: 3.0, 2: 2.0}
    assert result.missing == [1, 4, 5]
    assert result.estimate.level == 0.9 and result.estimate.mean == 2.5


def test_bonferroni_lowers_the_level():
    assert bonferroni(0.95, 1) == pytest.approx(0.95)
    assert bonferroni(0.95, 3) == pytest.approx(1 - 0.05 / 3)
    assert bonferroni(0.95, 3) > 0.95
    with pytest.raises(ValueError, match="m"):
        bonferroni(0.95, 0)
    with pytest.raises(ValueError, match="m"):
        bonferroni(0.95, 1.5)  # ty: ignore[invalid-argument-type]


def test_welch_averages_on_the_grid_then_smooths():
    a = [(0.0, 0.0), (2.0, 10.0)]
    b = [(0.0, 2.0), (2.0, 4.0)]
    grid = [0.0, 1.0, 2.0, 3.0, 4.0]
    # Previous-value samples: a = 0 0 10 10 10, b = 2 2 4 4 4, mean = 1 1 7 7 7.
    assert welch([a, b], window=1, grid=grid) == [(0.0, 1.0), (1.0, 1.0), (2.0, 7.0), (3.0, 7.0), (4.0, 7.0)]
    # Window 3, shorter at the edges: 1, 3, 5, 7, 7.
    assert welch([a, b], window=3, grid=grid) == [(0.0, 1.0), (1.0, 3.0), (2.0, 5.0), (3.0, 7.0), (4.0, 7.0)]


def test_welch_skips_grid_points_before_a_replications_first_sample():
    late = [(2.0, 8.0)]
    early = [(0.0, 2.0)]
    assert welch([early, late], window=1, grid=[0.0, 1.0, 2.0]) == [(0.0, 2.0), (1.0, 2.0), (2.0, 5.0)]
    assert welch([late], window=1, grid=[0.0, 1.0, 2.0]) == [(2.0, 8.0)]
    assert welch([], window=1, grid=[0.0]) == []
    assert welch([[]], window=1, grid=[0.0]) == []


@pytest.mark.parametrize("window", [0, 2, -1, 1.5, True])
def test_welch_window_must_be_odd_positive_int(window):
    with pytest.raises(ValueError, match="window"):
        welch([[(0.0, 1.0)]], window=window, grid=[0.0])


def test_welch_rejects_non_finite_values():
    with pytest.raises(ValueError, match="finite"):
        welch([[(0.0, float("nan"))]], window=1, grid=[0.0])
