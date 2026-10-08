"""Tests for EEL5000 monitor -> kV/mA conversion. Pure math, no hardware."""
import math

import numpy as np
import pytest

from rbl.config import hardware_config as SC
from rbl.gui import theme
from rbl.hardware.amp_monitor import (
    RailTracker,
    current_status,
    format_kv,
    format_ma,
    is_at_rail,
    monitor_to_kv,
    monitor_to_ma,
    voltage_status,
)


class TestVoltageMonitor:
    """1000:1 -> 1 V at the BNC is 1 kV at the output."""

    @pytest.mark.parametrize("volts,expect_kv", [
        (0.0,   0.0),
        (1.0,   1.0),
        (2.5,   2.5),
        (4.0,   4.0),
        (5.0,   5.0),      # rated maximum
        (-5.0, -5.0),
    ])
    def test_scale(self, volts, expect_kv):
        assert abs(monitor_to_kv(volts) - expect_kv) < 1e-9

    def test_beyond_rating_is_nan(self):
        assert math.isnan(monitor_to_kv(9.0))
        assert math.isnan(monitor_to_kv(-9.0))

    def test_nan_in_nan_out(self):
        assert math.isnan(monitor_to_kv(float("nan")))


class TestCurrentMonitor:
    """1 V at the BNC == 10 mA drawn."""

    @pytest.mark.parametrize("volts,expect_ma", [
        (0.0,    0.0),
        (0.1,    1.0),
        (1.0,   10.0),
        (2.0,   20.0),     # DC rating
        (-2.0, -20.0),
        (10.0, 100.0),     # 4 ms peak rating
    ])
    def test_scale(self, volts, expect_ma):
        assert abs(monitor_to_ma(volts) - expect_ma) < 1e-9

    def test_nan_in_nan_out(self):
        assert math.isnan(monitor_to_ma(float("nan")))


class TestStatus:
    def test_voltage_within_rating_ok(self):
        assert voltage_status(0.0)  == "ok"
        assert voltage_status(5.0)  == "ok"
        assert voltage_status(-5.0) == "ok"

    def test_voltage_over_rating(self):
        assert voltage_status(5.5)  == "over"
        assert voltage_status(float("nan")) == "over"

    def test_current_dc_band_ok(self):
        assert current_status(0.0)   == "ok"
        assert current_status(20.0)  == "ok"
        assert current_status(-20.0) == "ok"

    def test_current_peak_band(self):
        assert current_status(21.0)  == "peak"
        assert current_status(100.0) == "peak"

    def test_current_over(self):
        assert current_status(150.0) == "over"
        assert current_status(float("nan")) == "over"


class TestFormatting:
    def test_kv_above_one(self):
        assert "kV" in format_kv(3.5)

    def test_kv_below_one_shows_volts(self):
        s = format_kv(0.25)
        assert "V" in s and "kV" not in s

    def test_ma_above_one(self):
        assert "mA" in format_ma(15.0)

    def test_ma_below_one_shows_microamps(self):
        assert "µA" in format_ma(0.5)

    def test_nan_dash(self):
        assert "—" in format_kv(float("nan"))
        assert "—" in format_ma(float("nan"))


class TestChannelMapIntegrity:
    """The whole no-interference guarantee, asserted."""

    def test_amp_ains_do_not_overlap_log_amp_ains(self):
        amp  = set(SC.AMP_AIN_NAMES)
        logs = set(SC.LABJACK_CHANNEL_MAP.keys())
        assert not (amp & logs)

    def test_eight_amp_channels(self):
        assert len(SC.AMP_AIN_NAMES) == 8

    def test_twelve_total_channels_no_duplicates(self):
        assert len(SC.ALL_AIN_NAMES) == 12
        assert len(set(SC.ALL_AIN_NAMES)) == 12

    def test_every_amp_has_both_monitors(self):
        for amp in SC.AMP_LABELS:
            assert "voltage" in SC.AMP_CHANNEL_MAP[amp]
            assert "current" in SC.AMP_CHANNEL_MAP[amp]

    def test_every_amp_has_a_color(self):
        for amp in SC.AMP_LABELS:
            assert amp in theme.SLIT_COLORS


class TestRail:
    def test_is_at_rail_threshold_and_nan(self):
        assert is_at_rail(9.9) is True
        assert is_at_rail(-9.95) is True
        assert is_at_rail(9.89) is False
        assert is_at_rail(float("nan")) is False
        arr = is_at_rail(np.array([0.0, 10.0, -10.0, np.nan]))
        assert np.array_equal(arr, [False, True, True, False])

    def test_rail_tracker_measures_a_short_run(self):
        tracker = RailTracker()
        volts = np.array([0.0, 0.0] + [10.0] * 10 + [0.0, 0.0])
        dur = tracker.feed(volts, dt_s=1e-4)
        assert abs(dur - 0.001) < 1e-12

    def test_rail_tracker_joins_a_run_across_windows(self):
        tracker = RailTracker()
        w1 = np.array([0.0] * 10 + [10.0] * 30)
        w2 = np.array([10.0] * 30 + [0.0] * 10)
        tracker.feed(w1, dt_s=1e-4)
        dur2 = tracker.feed(w2, dt_s=1e-4)
        assert abs(dur2 - 0.006) < 1e-12

    def test_rail_tracker_restarts_after_one_sample_below_the_rail(self):
        tracker = RailTracker()
        volts = np.array([10.0] * 30 + [9.0] + [10.0] * 30)
        dur = tracker.feed(volts, dt_s=1e-4)
        assert abs(dur - 0.003) < 1e-12
        tracker.reset()
        w_after = np.array([10.0])
        assert abs(tracker.feed(w_after, dt_s=1e-4) - 1e-4) < 1e-12


class TestContinuousRatingName:
    def test_continuous_rating_has_one_name(self):
        assert SC.AMP_CONTINUOUS_RATING_MA == 20.0
        assert not hasattr(SC, "AMP_MAX_MA_DC")

