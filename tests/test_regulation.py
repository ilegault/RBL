"""
Unit tests for rbl.hardware.regulation — no Qt, no hardware.
"""
import math

import pytest

from rbl.hardware.regulation import classify, regulation_ratio


class TestRegulationRatio:
    def test_healthy_ratio(self):
        assert regulation_ratio(2.0, 4.0) == pytest.approx(0.5)

    def test_near_zero_commanded_is_nan(self):
        assert math.isnan(regulation_ratio(1.0, 0.0))


class TestClassify:
    def test_idle_when_commanded_near_zero(self):
        state, _ = classify(v_ratio=float("nan"), i_measured_ma=0.0,
                             i_limit_ma=20.0, commanded_kv=0.0)
        assert state == "idle"

    def test_ok_when_following(self):
        state, _ = classify(v_ratio=0.98, i_measured_ma=4.0, i_limit_ma=20.0,
                             commanded_kv=2.0)
        assert state == "ok"

    def test_amp_off_when_both_monitors_are_zero(self):
        state, _ = classify(v_ratio=0.01, i_measured_ma=0.1, i_limit_ma=20.0,
                             commanded_kv=3.0)
        assert state == "amp_off"

    def test_current_limited_when_voltage_low_current_at_limit(self):
        state, reason = classify(v_ratio=0.2, i_measured_ma=19.5, i_limit_ma=20.0,
                                  commanded_kv=5.0)
        assert state == "current_limited"
        assert "invalid" in reason

    def test_classification_is_sign_independent(self):
        state, _ = classify(v_ratio=-0.98, i_measured_ma=-4.0, i_limit_ma=20.0,
                             commanded_kv=-2.0)
        assert state == "ok"

    def test_below_arm_threshold_is_idle_even_if_ratio_looks_bad(self):
        state, _ = classify(v_ratio=0.0, i_measured_ma=0.0, i_limit_ma=20.0,
                             commanded_kv=0.05, arm_threshold_kv=0.1)
        assert state == "idle"
