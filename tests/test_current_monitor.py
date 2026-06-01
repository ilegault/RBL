"""Tests for rbl.hardware.current_monitor — pure math, no hardware."""
import math
import pytest
import numpy as np

from rbl.hardware.current_monitor import (
    voltage_to_current,
    format_current,
    beam_centering,
    RollingBuffer,
)


# ── voltage_to_current ────────────────────────────────────────────────────────

class TestVoltageToCurrentModel0_6V:
    """Ascending 0-6 V model (v_at_1nA=0, v_at_1mA=6)."""

    def test_lower_endpoint_is_1nA(self):
        assert abs(voltage_to_current(0.0, 0.0, 6.0) - 1e-9) < 1e-12

    def test_midpoint_is_1uA(self):
        assert abs(voltage_to_current(3.0, 0.0, 6.0) - 1e-6) < 1e-9

    def test_upper_endpoint_is_1mA(self):
        assert abs(voltage_to_current(6.0, 0.0, 6.0) - 1e-3) < 1e-6

    def test_below_range_is_nan(self):
        assert math.isnan(voltage_to_current(-1.0, 0.0, 6.0))

    def test_above_range_is_nan(self):
        assert math.isnan(voltage_to_current(7.0, 0.0, 6.0))

    def test_just_within_lower_tolerance(self):
        # 0.5 V below minimum is still accepted
        result = voltage_to_current(-0.4, 0.0, 6.0)
        assert not math.isnan(result)

    def test_just_outside_lower_tolerance(self):
        result = voltage_to_current(-0.6, 0.0, 6.0)
        assert math.isnan(result)

    def test_monotonic_ascending(self):
        voltages = np.linspace(0.1, 5.9, 50)
        currents = [voltage_to_current(v, 0.0, 6.0) for v in voltages]
        for i in range(len(currents) - 1):
            assert currents[i] < currents[i + 1]


class TestVoltageToCurrentModel9_3V:
    """Descending 9-3 V model (v_at_1nA=9, v_at_1mA=3)."""

    def test_v9_is_1nA(self):
        assert abs(voltage_to_current(9.0, 9.0, 3.0) - 1e-9) < 1e-12

    def test_v6_is_1uA(self):
        assert abs(voltage_to_current(6.0, 9.0, 3.0) - 1e-6) < 1e-9

    def test_v3_is_1mA(self):
        assert abs(voltage_to_current(3.0, 9.0, 3.0) - 1e-3) < 1e-6

    def test_below_min_is_nan(self):
        assert math.isnan(voltage_to_current(20.0, 9.0, 3.0))

    def test_above_max_is_nan(self):
        assert math.isnan(voltage_to_current(-1.0, 9.0, 3.0))

    def test_monotonic_descending(self):
        voltages = np.linspace(3.1, 8.9, 50)
        currents = [voltage_to_current(v, 9.0, 3.0) for v in voltages]
        for i in range(len(currents) - 1):
            assert currents[i] > currents[i + 1], f"not descending at {voltages[i]}"


# ── format_current ────────────────────────────────────────────────────────────

class TestFormatCurrent:
    def test_nA_range(self):
        s = format_current(1e-9)
        assert "nA" in s

    def test_uA_range(self):
        s = format_current(1e-6)
        assert "µA" in s or "uA" in s or "µ" in s

    def test_mA_range(self):
        s = format_current(1e-3)
        assert "mA" in s

    def test_nan_shows_dash(self):
        s = format_current(float("nan"))
        assert "—" in s or "-" in s or s.strip() == ""

    def test_none_shows_dash(self):
        s = format_current(None)
        assert "—" in s or "-" in s

    def test_boundary_1uA_is_not_nA(self):
        s = format_current(1e-6)
        assert "nA" not in s

    def test_boundary_1mA_is_not_uA(self):
        s = format_current(1e-3)
        assert "µA" not in s and "uA" not in s

    def test_small_nA_values(self):
        s = format_current(5e-10)
        assert "nA" in s

    def test_mid_uA_values(self):
        s = format_current(50e-6)
        assert "µA" in s or "uA" in s or "µ" in s


# ── beam_centering ────────────────────────────────────────────────────────────

class TestBeamCentering:
    def test_equal_currents_is_zero(self):
        assert abs(beam_centering(1e-6, 1e-6)) < 1e-9

    def test_plus_dominant_is_positive(self):
        assert beam_centering(2e-6, 1e-6) > 0

    def test_minus_dominant_is_negative(self):
        assert beam_centering(1e-6, 2e-6) < 0

    def test_antisymmetric(self):
        a = beam_centering(3e-6, 1e-6)
        b = beam_centering(1e-6, 3e-6)
        assert abs(a + b) < 1e-12

    def test_all_on_plus_is_plus1(self):
        assert abs(beam_centering(1e-6, 0.0) - 1.0) < 1e-9

    def test_all_on_minus_is_minus1(self):
        assert abs(beam_centering(0.0, 1e-6) + 1.0) < 1e-9

    def test_zero_total_is_nan(self):
        assert math.isnan(beam_centering(0.0, 0.0))

    def test_nan_input_plus(self):
        assert math.isnan(beam_centering(float("nan"), 1e-6))

    def test_nan_input_minus(self):
        assert math.isnan(beam_centering(1e-6, float("nan")))

    def test_result_bounded(self):
        for ratio in [0.1, 0.5, 2.0, 10.0]:
            r = beam_centering(ratio * 1e-6, 1e-6)
            assert -1.0 <= r <= 1.0


# ── RollingBuffer ─────────────────────────────────────────────────────────────

class TestRollingBuffer:
    def test_empty_snapshot(self):
        buf = RollingBuffer(10)
        t, v = buf.snapshot()
        assert len(t) == 0
        assert len(v) == 0

    def test_single_append(self):
        buf = RollingBuffer(10)
        buf.append(1.0, 42.0)
        t, v = buf.snapshot()
        assert len(t) == 1
        assert t[0] == 1.0
        assert v[0] == 42.0

    def test_capacity_capped(self):
        buf = RollingBuffer(100)
        for i in range(150):
            buf.append(float(i), float(i * 2))
        t, v = buf.snapshot()
        assert len(t) == 100

    def test_oldest_samples_overwritten(self):
        buf = RollingBuffer(10)
        for i in range(20):
            buf.append(float(i), float(i))
        t, v = buf.snapshot()
        assert t[0] == 10.0
        assert t[-1] == 19.0

    def test_snapshot_sorted_by_time(self):
        buf = RollingBuffer(5)
        for i in [4, 1, 3, 2, 0]:
            buf.append(float(i), float(i))
        t, v = buf.snapshot()
        assert list(t) == sorted(t)

    def test_latest_returns_most_recent(self):
        buf = RollingBuffer(50)
        for i in range(30):
            buf.append(float(i), float(i * 3))
        t_last, v_last = buf.latest()
        assert t_last == 29.0
        assert v_last == 87.0

    def test_latest_empty_is_nan(self):
        buf = RollingBuffer(10)
        t, v = buf.latest()
        assert math.isnan(t)
        assert math.isnan(v)

    def test_thread_safety(self):
        """Multiple threads append concurrently; no exception should be raised."""
        import threading
        buf = RollingBuffer(200)
        errors = []

        def writer(start):
            try:
                for i in range(50):
                    buf.append(float(start + i), float(start + i))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i * 100,)) for i in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert not errors

    def test_values_match_times(self):
        buf = RollingBuffer(50)
        for i in range(50):
            buf.append(float(i), float(i ** 2))
        t, v = buf.snapshot()
        for ti, vi in zip(t, v):
            assert abs(vi - ti ** 2) < 1e-9
