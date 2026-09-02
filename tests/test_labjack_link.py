"""
tests/test_labjack_link.py

Tests for the window → LogAmpState + AmpState conversion in
rbl/state/labjack_link.py (via Beamline.ingest_labjack_window).

test_beamline.py covers the basic DC-value paths (3 V log-amp → 1 µA, one
amp channel's peak/rms/pkpk).  These tests cover the paths that require
real waveform arrays and that verify conversion correctness at the
calibration boundaries:

  * Log-amp boundary voltages (0 V = 1 nA, 6 V = 1 mA, 3 V = 1 µA).
  * Out-of-range log-amp voltage → NaN (open input detection).
  * All four log-amp channels converted in parallel with independent values.
  * LogAmpState.volts populated (the pre-conversion mean, what a meter reads).
  * v_live / i_live flags track which channels are actually streaming.
  * raw_v = mean of the amp voltage waveform (not its RMS).
  * window_kv array = waveform × VOLTAGE_MONITOR_KV_PER_VOLT.
  * window_ma array = waveform × CURRENT_MONITOR_MA_PER_VOLT.
  * t and active_profile pass through unchanged.
  * peak_kv from a sine waveform recovers the analytic peak amplitude.

All tests go through LabJackFeed → Beamline.ingest_labjack_window, which is
the production path (see tests/payloads.py docstring for the reasoning).
"""
import math

import numpy as np
import pytest

from rbl.config import hardware_config as SC
from rbl.state.snapshots import AmpState, LogAmpState
from tests.payloads import LabJackFeed, window_payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _logamp_feed():
    """LabJackFeed with no tabs — we only care about the emitted signals."""
    return LabJackFeed()


def _capture(feed, signal_name):
    """Connect *signal_name* on the beamline and return the capture list."""
    received = []
    getattr(feed.beamline, signal_name).connect(received.append)
    return received


# ---------------------------------------------------------------------------
# LogAmpState — conversion accuracy
# ---------------------------------------------------------------------------

class TestLogAmpConversion:
    """
    Log-amp calibration: V_at_1nA=0.0, V_at_1mA=6.0.
    current = 10^(voltage - 9) Amperes.
    """

    def test_midpoint_voltage_gives_1ua(self):
        feed = _logamp_feed()
        received = _capture(feed, "logamps_changed")
        feed.send({"AIN0": 3.0})   # 3 V → midpoint → 1 µA
        assert received[0].currents["X+"] == pytest.approx(1e-6, rel=1e-3)

    def test_lower_boundary_gives_1na(self):
        feed = _logamp_feed()
        received = _capture(feed, "logamps_changed")
        feed.send({"AIN0": 0.0})   # 0 V → 1 nA
        assert received[0].currents["X+"] == pytest.approx(1e-9, rel=1e-3)

    def test_upper_boundary_gives_1ma(self):
        feed = _logamp_feed()
        received = _capture(feed, "logamps_changed")
        feed.send({"AIN0": 6.0})   # 6 V → 1 mA
        assert received[0].currents["X+"] == pytest.approx(1e-3, rel=1e-3)

    def test_out_of_range_low_gives_nan(self):
        feed = _logamp_feed()
        received = _capture(feed, "logamps_changed")
        feed.send({"AIN0": -1.0})   # well below 0 V − 0.5 V threshold
        assert math.isnan(received[0].currents["X+"])

    def test_out_of_range_high_gives_nan(self):
        feed = _logamp_feed()
        received = _capture(feed, "logamps_changed")
        feed.send({"AIN0": 7.0})    # above 6 V + 0.5 V threshold
        assert math.isnan(received[0].currents["X+"])

    def test_all_four_channels_converted_independently(self):
        feed = _logamp_feed()
        received = _capture(feed, "logamps_changed")
        # X+=0V→1nA, X-=3V→1µA, Y+=6V→1mA, Y-=0V→1nA
        feed.send({"AIN0": 0.0, "AIN1": 3.0, "AIN2": 6.0, "AIN3": 0.0})
        c = received[0].currents
        assert c["X+"] == pytest.approx(1e-9, rel=1e-3)
        assert c["X-"] == pytest.approx(1e-6, rel=1e-3)
        assert c["Y+"] == pytest.approx(1e-3, rel=1e-3)
        assert c["Y-"] == pytest.approx(1e-9, rel=1e-3)

    def test_logamp_volts_field_holds_pre_conversion_mean(self):
        """LogAmpState.volts carries the raw voltage the meter would read."""
        feed = _logamp_feed()
        received = _capture(feed, "logamps_changed")
        feed.send({"AIN0": 2.5, "AIN1": 4.0})
        state = received[0]
        assert state.volts["X+"] == pytest.approx(2.5)
        assert state.volts["X-"] == pytest.approx(4.0)

    def test_absent_channel_is_nan_not_zero(self):
        """A channel absent from the scan list (WAVEFORM mode) → NaN, not 0."""
        feed = _logamp_feed()
        received = _capture(feed, "logamps_changed")
        feed.send({"AIN0": 3.0})   # X+ only; X-, Y+, Y- absent
        c = received[0].currents
        assert math.isnan(c["X-"])
        assert math.isnan(c["Y+"])
        assert math.isnan(c["Y-"])

    def test_t_passes_through(self):
        feed = _logamp_feed()
        received = _capture(feed, "logamps_changed")
        feed.send({}, t=42.5)
        assert received[0].t == pytest.approx(42.5)

    def test_connected_flag_true(self):
        feed = _logamp_feed()
        received = _capture(feed, "logamps_changed")
        feed.send({"AIN0": 3.0})
        assert received[0].connected is True


# ---------------------------------------------------------------------------
# AmpState — conversion accuracy (waveform-based fields)
# ---------------------------------------------------------------------------

class TestAmpWaveformConversion:
    """Tests that require waveform arrays to exercise the window_kv / window_ma paths."""

    def _send_amp(self, feed, received_list, amp_label, wave_v, wave_i=None):
        """Send a payload with real waveform arrays for one amp channel."""
        v_ain = SC.AMP_CHANNEL_MAP[amp_label]["voltage"]
        i_ain = SC.AMP_CHANNEL_MAP[amp_label]["current"]
        waveforms = {v_ain: wave_v}
        if wave_i is not None:
            waveforms[i_ain] = wave_i
        payload = window_payload({}, waveforms=waveforms)
        feed.beamline.ingest_labjack_window(payload)

    def test_window_kv_scales_by_kv_per_volt(self):
        """window_kv = waveform × VOLTAGE_MONITOR_KV_PER_VOLT."""
        feed = _logamp_feed()
        received = _capture(feed, "amps_changed")
        wave = np.array([1.0, 2.0, 3.0])
        self._send_amp(feed, received, "X+", wave)
        ch = received[0].channels["X+"]
        expected = wave * SC.VOLTAGE_MONITOR_KV_PER_VOLT
        np.testing.assert_allclose(np.asarray(ch.window_kv), expected)

    def test_window_ma_scales_by_ma_per_volt(self):
        """window_ma = waveform × CURRENT_MONITOR_MA_PER_VOLT."""
        feed = _logamp_feed()
        received = _capture(feed, "amps_changed")
        v_wave = np.array([1.0, 1.0, 1.0])
        i_wave = np.array([0.5, 1.0, 2.0])
        self._send_amp(feed, received, "X+", v_wave, i_wave)
        ch = received[0].channels["X+"]
        expected = i_wave * SC.CURRENT_MONITOR_MA_PER_VOLT
        np.testing.assert_allclose(np.asarray(ch.window_ma), expected)

    def test_raw_v_is_mean_of_voltage_waveform(self):
        """raw_v = mean of the voltage waveform (what a meter reads)."""
        feed = _logamp_feed()
        received = _capture(feed, "amps_changed")
        wave = np.array([1.0, 3.0, 5.0])   # mean = 3.0
        self._send_amp(feed, received, "X+", wave)
        ch = received[0].channels["X+"]
        assert ch.raw_v == pytest.approx(3.0)

    def test_peak_kv_from_sine_waveform(self):
        """peak_kv recovers the analytic peak of a known sine (amplitude 2.0 kV)."""
        n = 1000
        t = np.linspace(0, 2 * np.pi, n, endpoint=False)
        A_kv = 2.0
        wave = A_kv * np.sin(t) / SC.VOLTAGE_MONITOR_KV_PER_VOLT   # in monitor volts
        feed = _logamp_feed()
        received = _capture(feed, "amps_changed")
        self._send_amp(feed, received, "Y-", wave)
        ch = received[0].channels["Y-"]
        assert ch.peak_kv == pytest.approx(A_kv, rel=1e-3)

    def test_rms_kv_from_sine_waveform(self):
        """rms_kv of a sine = amplitude / sqrt(2)."""
        n = 1000
        t = np.linspace(0, 2 * np.pi, n, endpoint=False)
        A_kv = 2.0
        wave = A_kv * np.sin(t)
        feed = _logamp_feed()
        received = _capture(feed, "amps_changed")
        self._send_amp(feed, received, "Y-", wave)
        ch = received[0].channels["Y-"]
        assert ch.rms_kv == pytest.approx(A_kv / math.sqrt(2), rel=1e-3)

    def test_v_live_true_when_channel_present(self):
        feed = _logamp_feed()
        received = _capture(feed, "amps_changed")
        self._send_amp(feed, received, "X+", np.array([1.0]))
        assert received[0].channels["X+"].v_live is True

    def test_v_live_false_when_channel_absent(self):
        feed = _logamp_feed()
        received = _capture(feed, "amps_changed")
        payload = window_payload({})   # no amp channels
        feed.beamline.ingest_labjack_window(payload)
        assert received[0].channels["X+"].v_live is False

    def test_i_live_true_when_current_channel_present(self):
        feed = _logamp_feed()
        received = _capture(feed, "amps_changed")
        self._send_amp(feed, received, "X+",
                       np.array([1.0]), np.array([0.5]))
        assert received[0].channels["X+"].i_live is True

    def test_i_live_false_when_current_channel_absent(self):
        feed = _logamp_feed()
        received = _capture(feed, "amps_changed")
        self._send_amp(feed, received, "X+", np.array([1.0]))
        assert received[0].channels["X+"].i_live is False

    def test_window_kv_none_when_channel_absent(self):
        feed = _logamp_feed()
        received = _capture(feed, "amps_changed")
        payload = window_payload({})
        feed.beamline.ingest_labjack_window(payload)
        assert received[0].channels["X+"].window_kv is None

    def test_window_ma_none_when_current_absent(self):
        feed = _logamp_feed()
        received = _capture(feed, "amps_changed")
        self._send_amp(feed, received, "X+", np.array([1.0]))   # no current channel
        assert received[0].channels["X+"].window_ma is None

    def test_all_four_amp_labels_present_in_snapshot(self):
        feed = _logamp_feed()
        received = _capture(feed, "amps_changed")
        payload = window_payload({})
        feed.beamline.ingest_labjack_window(payload)
        assert set(received[0].channels.keys()) == set(SC.AMP_LABELS)

    def test_t_passes_through_to_amp_state(self):
        feed = _logamp_feed()
        received = _capture(feed, "amps_changed")
        feed.send({}, t=99.9)
        assert received[0].t == pytest.approx(99.9)

    def test_active_profile_passes_through(self):
        feed = _logamp_feed()
        received = _capture(feed, "amps_changed")
        payload = window_payload({}, profile="WAVEFORM_CH")
        feed.beamline.ingest_labjack_window(payload, active_profile="WAVEFORM_CH")
        assert received[0].active_profile == "WAVEFORM_CH"

    def test_connected_flag_true_on_amp_state(self):
        feed = _logamp_feed()
        received = _capture(feed, "amps_changed")
        feed.send({})
        assert received[0].connected is True

    def test_rms_ma_from_dc_current_waveform(self):
        """DC current of 1 V → 10 mA."""
        feed = _logamp_feed()
        received = _capture(feed, "amps_changed")
        self._send_amp(feed, received, "X+",
                       np.array([0.0]),
                       np.array([1.0]))   # 1 V rms → 10 mA
        ch = received[0].channels["X+"]
        assert ch.rms_ma == pytest.approx(10.0, rel=1e-3)
