"""
Unit tests for rbl.hardware.hv_interlock — no Qt, no hardware.
"""
import pytest

from rbl.hardware.hv_interlock import (
    max_permitted_kv, interlock_status, HV_PRESSURE_ABSOLUTE_LOCKOUT_TORR,
)


class TestMaxPermittedKv:
    @pytest.mark.parametrize("pressure_torr,expected_kv", [
        (1e-6, 5.0),
        (2e-5, 3.0),
        (7e-5, 1.0),
        (5e-4, 0.0),
        (1e-3, 0.0),
    ])
    def test_ladder(self, pressure_torr, expected_kv):
        assert max_permitted_kv(pressure_torr) == expected_kv

    def test_lockout_boundary_is_inclusive(self):
        assert max_permitted_kv(HV_PRESSURE_ABSOLUTE_LOCKOUT_TORR) == 0.0


class TestInterlockStatus:
    def test_ok_within_ceiling(self):
        status, _ = interlock_status(1e-6, 5.0)
        assert status == "ok"

    def test_warn_above_ceiling_below_lockout(self):
        status, _ = interlock_status(2e-5, 5.0)
        assert status == "warn"

    def test_block_at_absolute_lockout(self):
        status, _ = interlock_status(1e-3, 0.5)
        assert status == "block"

    def test_block_at_absolute_lockout_even_with_zero_commanded(self):
        status, _ = interlock_status(2e-3, 0.0)
        assert status == "block"

    def test_stale_reading_blocks_regardless_of_pressure_value(self):
        status, reason = interlock_status(1e-6, 5.0, pressure_known=False)
        assert status == "block"
        assert "stale" in reason or "unavailable" in reason

    def test_stale_reading_blocks_even_zero_command(self):
        status, _ = interlock_status(1e-6, 0.0, pressure_known=False)
        assert status == "block"
