from __future__ import annotations

import pytest

from simulatte.intralogistics.kinematics import ArcProfile, Kinematics, arc_profile, plan_run, rest_to_rest
from simulatte.intralogistics.speed import TrapezoidalProfile

KIN = Kinematics(v_max=1.5, accel=0.8, decel=0.8)


@pytest.mark.parametrize("length", [0.5, 2.8125, 10.0, 100.0])
@pytest.mark.parametrize("limit", [None, 1.0])
def test_rest_to_rest_matches_travel_time(length: float, limit: float | None) -> None:
    profile = TrapezoidalProfile(1.5, 0.8, 0.8)
    cap = KIN.v_max if limit is None else min(KIN.v_max, limit)
    arc = rest_to_rest(length, cap, KIN)
    assert arc.duration == pytest.approx(profile.travel_time(length, speed_limit=limit), rel=1e-12)
    assert arc.v_in == 0.0
    assert arc.v_out == pytest.approx(0.0, abs=1e-12)
    assert arc.position(arc.duration) == pytest.approx(length, abs=1e-9)


def test_rest_to_rest_length_ten() -> None:
    assert rest_to_rest(10.0, 1.5, KIN).duration == pytest.approx(8.541666666666666, rel=1e-12)


def test_arc_profile_with_entry_and_exit_speed() -> None:
    arc = arc_profile(1.0, 0.5, 1.5, 10.0, KIN)
    assert [phase[1] for phase in arc.phases] == [0.8, 0.0, -0.8]
    assert arc.phases[0][0] == pytest.approx(0.625)
    assert arc.phases[1][0] == pytest.approx(5.3125)
    assert arc.phases[2][0] == pytest.approx(1.25)
    assert arc.duration == pytest.approx(7.1875)
    assert arc.v_in == 1.0
    assert arc.v_out == pytest.approx(0.5)
    assert arc.length == 10.0


def test_triangular_profile_has_no_cruise() -> None:
    arc = rest_to_rest(0.5, 1.5, KIN)
    assert [phase[1] for phase in arc.phases] == [0.8, -0.8]


def test_only_deceleration_when_entry_speed_is_the_peak() -> None:
    arc = arc_profile(1.0, 0.0, 1.0, 0.625, KIN)
    assert len(arc.phases) == 1
    assert arc.phases[0] == pytest.approx((1.25, -0.8))
    assert arc.speed(0.0) == 1.0


def test_unequal_accelerations() -> None:
    arc = arc_profile(0.0, 0.0, 10.0, 6.0, Kinematics(10.0, 2.0, 1.0))
    peak = arc.speed(arc.phases[0][0])
    assert peak == pytest.approx(8.0**0.5)
    assert arc.position(arc.duration) == pytest.approx(6.0, abs=1e-9)


@pytest.mark.parametrize(
    ("v_in", "v_out", "length"), [(0.0, 0.0, 10.0), (1.0, 0.5, 10.0), (0.5, 1.0, 3.0), (1.5, 1.5, 4.0)]
)
def test_position_and_time_at_are_inverse(v_in: float, v_out: float, length: float) -> None:
    arc = arc_profile(v_in, v_out, 1.5, length, KIN)
    assert arc.position(0.0) == 0.0
    assert arc.position(arc.duration) == pytest.approx(length, abs=1e-9)
    assert arc.speed(arc.duration) == pytest.approx(v_out, abs=1e-9)
    previous = -1.0
    for i in range(41):
        t = arc.duration * i / 40
        s = arc.position(t)
        assert s >= previous
        previous = s
        assert arc.speed(t) >= 0.0
        assert arc.time_at(s) == pytest.approx(t, abs=1e-6)
    assert arc.time_at(0.0) == 0.0
    assert arc.time_at(length) == pytest.approx(arc.duration, abs=1e-9)


def test_time_at_out_of_range_raises() -> None:
    arc = rest_to_rest(10.0, 1.5, KIN)
    with pytest.raises(ValueError, match="position"):
        arc.time_at(-0.1)
    with pytest.raises(ValueError, match="position"):
        arc.time_at(10.1)
    with pytest.raises(ValueError, match="time"):
        arc.position(-0.1)
    with pytest.raises(ValueError, match="time"):
        arc.speed(arc.duration + 0.1)


def test_time_at_tolerates_rounding_at_the_end() -> None:
    arc = rest_to_rest(10.0, 1.5, KIN)
    assert arc.time_at(10.0 + 1e-12) == arc.duration


def test_zero_length_profile() -> None:
    arc = arc_profile(0.7, 0.7, 1.5, 0.0, KIN)
    assert arc.phases == ()
    assert arc.duration == 0.0
    assert arc.v_out == 0.7
    assert arc.position(0.0) == 0.0
    assert arc.speed(0.0) == 0.7
    assert arc.time_at(0.0) == 0.0


def test_zero_length_at_rest_needs_no_cap() -> None:
    assert rest_to_rest(0.0, 0.0, KIN).phases == ()


def test_time_at_before_a_zero_speed_start() -> None:
    arc = rest_to_rest(10.0, 1.5, KIN)
    assert arc.time_at(0.0) == 0.0


def test_time_at_returns_earliest_time_when_position_is_held() -> None:
    arc = ArcProfile(v_in=0.0, phases=((1.0, 0.0),), length=0.0)
    assert arc.time_at(0.0) == 0.0


def test_speed_never_negative_at_the_end_of_a_deceleration() -> None:
    arc = rest_to_rest(2.8125, 1.5, KIN)
    assert arc.speed(arc.duration) >= 0.0
    assert arc.v_out >= 0.0


@pytest.mark.parametrize(
    ("v_in", "v_out", "length"),
    [(0.0, 1.5, 0.5), (1.5, 0.0, 0.5), (0.0, 0.5, 0.0)],
)
def test_infeasible_speeds_raise(v_in: float, v_out: float, length: float) -> None:
    with pytest.raises(ValueError, match="cannot"):
        arc_profile(v_in, v_out, 1.5, length, KIN)


def test_speed_above_cap_raises() -> None:
    with pytest.raises(ValueError, match="cap"):
        arc_profile(2.0, 0.0, 1.5, 10.0, KIN)
    with pytest.raises(ValueError, match="cap"):
        arc_profile(0.0, 2.0, 1.5, 10.0, KIN)


@pytest.mark.parametrize(
    "kin",
    [Kinematics(1.5, 0.0, 0.8), Kinematics(1.5, 0.8, 0.0)],
)
def test_non_positive_accelerations_raise(kin: Kinematics) -> None:
    with pytest.raises(ValueError, match="positive"):
        arc_profile(0.0, 0.0, 1.5, 10.0, kin)


def test_non_positive_cap_raises() -> None:
    with pytest.raises(ValueError, match="positive"):
        arc_profile(0.0, 0.0, 0.0, 10.0, KIN)


def test_negative_arguments_raise() -> None:
    with pytest.raises(ValueError, match="negative"):
        arc_profile(0.0, 0.0, 1.5, -1.0, KIN)
    with pytest.raises(ValueError, match="negative"):
        arc_profile(-0.1, 0.0, 1.5, 1.0, KIN)


def test_exit_speed_reachable_up_to_rounding() -> None:
    v_in = 0.3
    v_out = (v_in**2 + 2 * 0.8 * 0.5) ** 0.5
    arc = arc_profile(v_in, v_out, 1.5, 0.5, KIN)
    assert arc.position(arc.duration) == pytest.approx(0.5, abs=1e-9)
    assert all(duration >= 0.0 for duration, _ in arc.phases)


def _covered(arc: ArcProfile) -> float:
    """The distance the phases cover, integrated independently of `position`."""
    v, covered = arc.v_in, 0.0
    for duration, accel in arc.phases:
        assert duration >= 0.0
        covered += v * duration + 0.5 * accel * duration * duration
        v += accel * duration
        assert v >= -1e-9
    return covered


@pytest.mark.parametrize("factor", [1 + 0.4e-12, 1 - 0.4e-12])
def test_exit_speed_at_the_edge_of_feasibility_covers_the_length(factor: float) -> None:
    v_in, length = 0.3, 0.5
    reach = (v_in**2 + 2 * 0.8 * length) ** 0.5
    arc = arc_profile(v_in, reach * factor, 1.5, length, KIN)
    assert _covered(arc) == pytest.approx(length, abs=1e-9)
    stop = arc_profile(1.0, 0.0, 1.5, 1.0**2 / (2 * 0.8) * (1 - 0.9e-12), KIN)
    assert _covered(stop) == pytest.approx(stop.length, abs=1e-9)


def _check_run(v0: float, lengths: list[float], run: tuple[ArcProfile, ...]) -> None:
    assert len(run) == len(lengths)
    speed = v0
    for arc, length in zip(run, lengths, strict=True):
        assert arc.length == length
        assert arc.position(arc.duration) == pytest.approx(length, abs=1e-9)
        assert _covered(arc) == pytest.approx(length, abs=1e-9)
        assert arc.v_in == pytest.approx(speed, abs=1e-9)
        speed = arc.v_out
    assert speed == pytest.approx(0.0, abs=1e-9)


def test_single_arc_equals_rest_to_rest() -> None:
    (arc,) = plan_run(0.0, [10.0], [1.5], [0.0, 0.0], KIN)
    assert arc == rest_to_rest(10.0, 1.5, KIN)


def test_split_line_has_the_same_duration() -> None:
    run = plan_run(0.0, [5.0, 5.0], [1.5, 1.5], [0.0, 1.5, 0.0], KIN)
    _check_run(0.0, [5.0, 5.0], run)
    assert [arc.duration for arc in run] == [pytest.approx(4.270833333333333, rel=1e-12)] * 2
    assert sum(arc.duration for arc in run) == pytest.approx(8.541666666666666, rel=1e-12)
    assert rest_to_rest(10.0, 1.5, KIN).duration == pytest.approx(8.541666666666666, rel=1e-12)
    many = plan_run(0.0, [1.0] * 10, [1.5] * 10, [0.0] + [1.5] * 9 + [0.0], KIN)
    _check_run(0.0, [1.0] * 10, many)
    assert sum(arc.duration for arc in many) == pytest.approx(8.541666666666666, abs=1e-12)


def test_speed_limit_change() -> None:
    run = plan_run(0.0, [10.0, 10.0], [1.5, 0.5], [0.0, 1.5, 0.0], KIN)
    _check_run(0.0, [10.0, 10.0], run)
    assert run[0].duration == pytest.approx(8.020833333333334, rel=1e-12)
    assert run[1].duration == pytest.approx(20.3125, rel=1e-12)
    assert run[0].v_out == pytest.approx(0.5)
    assert run[1].v_in == pytest.approx(0.5)


def test_short_arc_is_triangular() -> None:
    (arc,) = plan_run(0.0, [1.0], [1.5], [0.0, 0.0], KIN)
    assert arc.duration == pytest.approx(2 * 0.8**0.5 / 0.8)
    assert len(arc.phases) == 2


def test_entering_at_speed() -> None:
    (arc,) = plan_run(1.5, [5.0], [1.5], [1.5, 0.0], KIN)
    assert [duration for duration, _ in arc.phases] == [
        pytest.approx(2.3958333333333335, rel=1e-12),
        pytest.approx(1.875, rel=1e-12),
    ]
    assert arc.phases[0][1] == 0.0


def test_node_cap_limits_the_pass_speed() -> None:
    run = plan_run(0.0, [10.0, 10.0], [1.5, 1.5], [0.0, 0.5, 0.0], KIN)
    _check_run(0.0, [10.0, 10.0], run)
    assert run[0].v_out == pytest.approx(0.5)
    assert run[1].v_in == pytest.approx(0.5)


def test_cannot_stop_in_the_horizon_raises() -> None:
    with pytest.raises(ValueError, match=r"short by 0\.40"):
        plan_run(1.5, [1.0], [1.5], [1.5, 0.0], KIN)


def test_zero_length_arc_inside_a_run() -> None:
    run = plan_run(0.0, [5.0, 0.0, 5.0], [1.5] * 3, [0.0, 1.5, 1.5, 0.0], KIN)
    _check_run(0.0, [5.0, 0.0, 5.0], run)
    assert run[1].phases == ()
    assert run[1].duration == 0.0
    assert run[1].v_in == run[1].v_out
    assert sum(arc.duration for arc in run) == pytest.approx(8.541666666666666, rel=1e-12)


def test_zero_length_run_at_rest() -> None:
    run = plan_run(0.0, [0.0, 0.0], [1.5, 1.5], [0.0, 0.0, 0.0], KIN)
    assert [arc.phases for arc in run] == [(), ()]


def test_empty_run() -> None:
    assert plan_run(0.0, [], [], [0.0], KIN) == ()
    with pytest.raises(ValueError, match="empty"):
        plan_run(0.5, [], [], [0.0], KIN)


def test_last_node_cap_must_be_zero() -> None:
    with pytest.raises(ValueError, match="last node"):
        plan_run(0.0, [10.0], [1.5], [0.0, 0.5], KIN)


@pytest.mark.parametrize(
    ("v0", "lengths", "arc_caps", "node_caps"),
    [
        (0.0, [1.0, 1.0], [1.5], [0.0, 1.0, 0.0]),
        (0.0, [1.0], [1.5], [0.0]),
        (0.0, [-1.0], [1.5], [0.0, 0.0]),
        (0.0, [1.0], [-1.5], [0.0, 0.0]),
        (0.0, [1.0, 1.0], [1.5, 1.5], [0.0, -1.0, 0.0]),
        (-0.1, [1.0], [1.5], [0.0, 0.0]),
    ],
)
def test_invalid_inputs_raise(v0: float, lengths: list[float], arc_caps: list[float], node_caps: list[float]) -> None:
    with pytest.raises(ValueError):
        plan_run(v0, lengths, arc_caps, node_caps, KIN)


def test_exit_speed_beyond_the_rounding_slack_raises() -> None:
    reach = (0.3**2 + 2 * 0.8 * 0.5) ** 0.5
    with pytest.raises(ValueError, match="cannot"):
        arc_profile(0.3, reach * (1 + 1e-9), 1.5, 0.5, KIN)
