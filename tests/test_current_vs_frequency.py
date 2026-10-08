"""
Ticket 41: the current-vs-frequency chart model and its margin verdict.

Plain numbers in, plain numbers out - the chart model has no Qt and no state.
"""
import math

import pytest

from rbl.hardware import raster_plan
from rbl.hardware.raster_plan import current_vs_frequency


def _one(c_pf, kv, f, shape="triangle"):
    return current_vs_frequency(
        {"X+": c_pf}, {"X": kv}, {"X": f}, shape)


def test_triangle_1600pf_2kv_500hz_is_6p4_ma_and_green():
    r = _one(1600.0, 2.0, 500.0)
    f, ma = r["operating_point"]["X+"]
    assert f == 500.0
    assert ma == pytest.approx(6.4, abs=1e-9)
    assert r["verdict"]["X+"] == "green"


@pytest.mark.parametrize("c_pf, expected", [
    (2499.75, "green"),    # 9.999 mA
    (2500.0, "amber"),     # exactly 10.0 mA
    (4000.0, "amber"),     # exactly 16.0 mA
    (4000.25, "red"),      # 16.001 mA
])
def test_margin_boundaries_at_1kv_1khz(c_pf, expected):
    # triangle: I = 4 * 1000 Hz * C * 1 kV, i.e. C/250 mA per 1000 pF steps.
    assert _one(c_pf, 1.0, 1000.0)["verdict"]["X+"] == expected


def test_unmeasured_plate_has_no_series_or_operating_point():
    r = current_vs_frequency(
        {"X+": 1600.0, "X-": None}, {"X": 2.0}, {"X": 500.0}, "triangle")
    assert "X-" not in r["series"]
    assert "X-" not in r["operating_point"]
    assert r["verdict"]["X-"] == "not_measured"
    assert r["verdict"]["X+"] == "green"


def test_sine_is_2pi_over_4_times_triangle():
    tri = _one(1600.0, 2.0, 500.0, "triangle")["operating_point"]["X+"][1]
    sin = _one(1600.0, 2.0, 500.0, "sine")["operating_point"]["X+"][1]
    assert sin == pytest.approx(tri * 2 * math.pi / 4)


def test_levels_are_only_the_continuous_rating():
    assert _one(1600.0, 2.0, 500.0)["levels"] == {
        "continuous_ma": 20.0}


def test_frequency_grid_and_series_follow_i_equals_k_f_c_v():
    r = _one(1600.0, 2.0, 500.0)
    f = r["freq_hz"]
    assert len(f) == 200
    assert f[0] == pytest.approx(10.0) and f[-1] == pytest.approx(10_000.0)
    assert all(b > a for a, b in zip(f, f[1:]))
    for fi, ma in zip(f, r["series"]["X+"]):
        assert ma == pytest.approx(4 * fi * 1600.0 * 2.0 / 1e6)


def test_each_plate_uses_its_own_axis_amplitude_and_frequency():
    r = current_vs_frequency(
        {"X+": 1000.0, "Y+": 1000.0},
        {"X": 1.0, "Y": 2.0}, {"X": 100.0, "Y": 200.0}, "triangle")
    assert r["operating_point"]["X+"] == (100.0, pytest.approx(0.4))
    assert r["operating_point"]["Y+"] == (200.0, pytest.approx(1.6))


def test_margin_constants_are_half_and_80_percent_of_continuous():
    assert raster_plan.MARGIN_AMBER_MA == 10.0
    assert raster_plan.MARGIN_RED_MA == 16.0


def test_unknown_shape_is_refused():
    with pytest.raises(ValueError):
        _one(1600.0, 2.0, 500.0, "wobble")


def test_planner_continuous_level_is_the_rating_not_the_soft_trip(monkeypatch):
    monkeypatch.setattr(raster_plan, "CAL_AC_TRIP_MA", 19.0)
    r = current_vs_frequency({"X+": 1600.0}, {"X": 2.0}, {"X": 500.0}, "triangle")
    assert r["levels"]["continuous_ma"] == 20.0

