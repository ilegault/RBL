"""Tests for EEL5000 monitor -> kV/mA conversion. Pure math, no hardware."""
import math

import pytest

from rbl.hardware.amp_monitor import (
    monitor_to_kv, monitor_to_ma, format_kv, format_ma,
    voltage_status, current_status,
)
from rbl.hardware import slit_config as SC


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
            assert amp in SC.AMP_COLORS
