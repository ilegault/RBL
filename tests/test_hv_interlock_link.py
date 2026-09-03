"""
Unit tests for rbl.state.hv_interlock_link.chamber_pressure_torr — the pure
extraction step that turns a VacuumState into the single number
hv_interlock.interlock_status() checks against.
"""
import math

from rbl.hardware.vgc083_driver import VgcReading
from rbl.hardware.xgs600_driver import XgsChannel, XgsReading
from rbl.state.hv_interlock_link import chamber_pressure_torr
from rbl.state.snapshots import VacuumState


def _vgc(pressure, channel="IG"):
    return VgcReading(channel=channel, pressure=pressure, raw="", state="OK")


def _xgs(pressure, label="A1"):
    channel = XgsChannel(index=0, slot=0, board="HFIG", label=label, sensor_code="I1")
    return XgsReading(channel=channel, pressure=pressure, raw="", state="OK")


class TestChamberPressureTorr:
    def test_no_gauges_connected_is_nan(self):
        state = VacuumState(timestamp=0.0)
        assert math.isnan(chamber_pressure_torr(state))

    def test_single_vgc_reading(self):
        state = VacuumState(timestamp=0.0, vgc_readings=[_vgc(1e-6)], vgc_connected=True)
        assert chamber_pressure_torr(state) == 1e-6

    def test_single_xgs_reading(self):
        state = VacuumState(timestamp=0.0, xgs_readings=[_xgs(2e-5)], xgs_connected=True)
        assert chamber_pressure_torr(state) == 2e-5

    def test_takes_the_worst_reading_across_both_gauges(self):
        state = VacuumState(
            timestamp=0.0,
            xgs_readings=[_xgs(1e-6), _xgs(5e-4)], xgs_connected=True,
            vgc_readings=[_vgc(2e-6)], vgc_connected=True,
        )
        assert chamber_pressure_torr(state) == 5e-4

    def test_disconnected_gauge_is_ignored_even_with_stale_readings_present(self):
        state = VacuumState(
            timestamp=0.0,
            xgs_readings=[_xgs(9.0)], xgs_connected=False,   # stale/garbage, gauge is off
            vgc_readings=[_vgc(1e-6)], vgc_connected=True,
        )
        assert chamber_pressure_torr(state) == 1e-6

    def test_non_numeric_channels_are_skipped_not_treated_as_zero(self):
        state = VacuumState(
            timestamp=0.0,
            vgc_readings=[_vgc(None), _vgc(3e-5)], vgc_connected=True,
        )
        assert chamber_pressure_torr(state) == 3e-5

    def test_all_channels_non_numeric_is_nan(self):
        state = VacuumState(timestamp=0.0, vgc_readings=[_vgc(None)], vgc_connected=True)
        assert math.isnan(chamber_pressure_torr(state))


class TestChamberPressureGaugeSelection:
    """Gauge selection: only readings matching selected_keys are used."""

    def test_selected_key_filters_to_single_gauge(self):
        state = VacuumState(
            timestamp=0.0,
            xgs_readings=[_xgs(1e-6, label="IG1"), _xgs(5e-4, label="IG2")],
            xgs_connected=True,
        )
        # Select only IG1 — the good gauge
        assert chamber_pressure_torr(state, {"xgs600:IG1"}) == 1e-6

    def test_selected_key_filters_cross_instrument(self):
        state = VacuumState(
            timestamp=0.0,
            xgs_readings=[_xgs(9e-1, label="IG1")], xgs_connected=True,
            vgc_readings=[_vgc(2e-6, channel="IG")], vgc_connected=True,
        )
        # Select only the VGC IG — ignore the high XGS reading
        assert chamber_pressure_torr(state, {"vgc083:IG"}) == 2e-6

    def test_empty_selection_returns_nan(self):
        state = VacuumState(
            timestamp=0.0,
            vgc_readings=[_vgc(1e-6)], vgc_connected=True,
        )
        assert math.isnan(chamber_pressure_torr(state, set()))

    def test_none_selection_uses_all(self):
        state = VacuumState(
            timestamp=0.0,
            xgs_readings=[_xgs(1e-6)], xgs_connected=True,
            vgc_readings=[_vgc(5e-4)], vgc_connected=True,
        )
        # None = legacy mode, worst-case across all
        assert chamber_pressure_torr(state, None) == 5e-4

    def test_selected_key_no_match_returns_nan(self):
        state = VacuumState(
            timestamp=0.0,
            vgc_readings=[_vgc(1e-6, channel="CG1")], vgc_connected=True,
        )
        assert math.isnan(chamber_pressure_torr(state, {"vgc083:IG"}))
