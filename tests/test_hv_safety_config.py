"""
Unit tests for rbl.config.hv_safety_config — no Qt, no hardware.
"""
from rbl.config.hv_safety_config import (
    GAUGE_STALE_TIMEOUT_S,
    HV_PRESSURE_ABSOLUTE_LOCKOUT_TORR,
    HV_PRESSURE_LIMITS,
)


def test_ladder_is_sorted_by_decreasing_permitted_voltage():
    assert HV_PRESSURE_LIMITS == sorted(HV_PRESSURE_LIMITS, key=lambda t: -t[0])


def test_ladder_never_exceeds_amplifier_rating():
    assert all(kv <= 5.0 for kv, _ in HV_PRESSURE_LIMITS)


def test_lockout_is_looser_than_every_ladder_requirement():
    for _, required_below in HV_PRESSURE_LIMITS:
        assert required_below <= HV_PRESSURE_ABSOLUTE_LOCKOUT_TORR


def test_stale_timeout_is_positive():
    assert GAUGE_STALE_TIMEOUT_S > 0
