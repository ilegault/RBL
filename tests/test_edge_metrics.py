"""
Unit tests for rbl.hardware.edge_metrics — no Qt, no hardware.
"""
import math

import numpy as np
import pytest

from rbl.hardware.edge_metrics import (
    current_tail_duration_s,
    figure_of_merit,
    flat_top_creep_pct,
    overshoot_pct,
    peak_current_ma,
    rise_time_s,
    settling_time_s,
)

FS = 100_000.0
T = np.arange(0, 0.5, 1.0 / FS)
V_FINAL = 500.0
TAU = 5e-5


class TestOvershoot:
    def test_clean_first_order_rise_has_no_overshoot(self):
        v = V_FINAL * (1 - np.exp(-T / TAU))
        assert overshoot_pct(v, V_FINAL) == pytest.approx(0.0, abs=1.0)

    def test_overshooting_rise_is_positive(self):
        v = V_FINAL * (1.05 - 0.05 * np.exp(-T / TAU)) * (1 - np.exp(-T / TAU))
        assert overshoot_pct(v, V_FINAL) > 0

    def test_zero_final_is_nan(self):
        assert math.isnan(overshoot_pct([1.0, 2.0], 0.0))

    def test_empty_trace_is_nan(self):
        assert math.isnan(overshoot_pct([], 500.0))


class TestSettlingTime:
    def test_settles_quickly_for_a_clean_rise(self):
        v = V_FINAL * (1 - np.exp(-T / TAU))
        settle = settling_time_s(T, v, V_FINAL, tolerance_pct=1.0)
        assert 0 < settle < 0.01

    def test_never_settling_is_nan(self):
        v = np.full_like(T, V_FINAL * 1.5)
        assert math.isnan(settling_time_s(T, v, V_FINAL, tolerance_pct=1.0))

    def test_within_tolerance_from_the_start_returns_first_sample_time(self):
        v = np.full_like(T, V_FINAL)
        assert settling_time_s(T, v, V_FINAL, tolerance_pct=1.0) == pytest.approx(T[0])

    def test_tighter_tolerance_never_settles_shorter_tolerance_may(self):
        v = V_FINAL * (1 - np.exp(-T / TAU)) + 0.02 * V_FINAL
        loose = settling_time_s(T, v, V_FINAL, tolerance_pct=5.0)
        tight = settling_time_s(T, v, V_FINAL, tolerance_pct=0.5)
        assert loose < tight or math.isnan(tight)


class TestFlatTopCreep:
    def test_undercompensated_creeps_positive(self):
        v = (V_FINAL * (1 - np.exp(-T / TAU))
             + 0.02 * V_FINAL * (1 - np.exp(-T / 0.1)))
        assert flat_top_creep_pct(T, v, V_FINAL) > 0

    def test_overcompensated_creeps_negative(self):
        v = (V_FINAL * (1 - np.exp(-T / TAU))
             - 0.02 * V_FINAL * (1 - np.exp(-T / 0.1)))
        assert flat_top_creep_pct(T, v, V_FINAL) < 0

    def test_zero_final_is_nan(self):
        assert math.isnan(flat_top_creep_pct(T, T, 0.0))


class TestRiseTime:
    def test_positive_for_a_rising_step(self):
        v = V_FINAL * (1 - np.exp(-T / TAU))
        assert rise_time_s(T, v, V_FINAL) > 0

    def test_zero_final_is_nan(self):
        assert math.isnan(rise_time_s(T, T, 0.0))


class TestCurrentMetrics:
    def test_peak_current_is_the_worst_magnitude(self):
        i = 20.0 * np.exp(-T / TAU) + 0.5
        assert peak_current_ma(i) == pytest.approx(20.5, abs=0.1)

    def test_empty_trace_is_nan(self):
        assert math.isnan(peak_current_ma([]))

    def test_tail_duration_positive_for_a_decaying_pulse(self):
        i = 20.0 * np.exp(-T / TAU) + 0.5
        tail = current_tail_duration_s(T, i, baseline_ma=0.5)
        assert 0 < tail < 0.01

    def test_flat_trace_has_zero_tail(self):
        i = np.full_like(T, 0.5)
        assert current_tail_duration_s(T, i, baseline_ma=0.5) == 0.0

    def test_never_settling_current_is_nan(self):
        i = np.full_like(T, 20.0)
        assert math.isnan(current_tail_duration_s(T, i, baseline_ma=0.0))


class TestFigureOfMerit:
    def test_clean_trial_scores_lower_than_a_poor_one(self):
        good = figure_of_merit(0.5, 0.0001, 0.2)
        bad = figure_of_merit(15.0, 0.05, 8.0)
        assert good < bad

    def test_nan_components_are_dropped_not_poisoning(self):
        score = figure_of_merit(1.0, float("nan"), 2.0)
        assert score == pytest.approx(3.0)

    def test_all_nan_is_nan(self):
        assert math.isnan(figure_of_merit(float("nan"), float("nan"), float("nan")))
