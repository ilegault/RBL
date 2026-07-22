"""
Tests for the LabJack T7 stream configuration and stream worker, focused on the
single-channel streaming feature (SINGLE_FAST / SINGLE_HIRES profiles).

No hardware and no LJM library are required: the config is pure data and the
worker's de-interleave / payload math is exercised directly via _build_payload
(constructed with __new__ so QThread.__init__ / LJM are never touched).
"""
import numpy as np
import pytest

from rbl.config import labjack_stream_config as CFG
from rbl.config.labjack_stream_config import (
    STREAM_PROFILES, AMP_CHANNELS, LOGAMP_CHANNELS,
    window_samples, resolution_index, is_single_channel, channel_choices,
    MAX_AGG_RATE_BY_RES, T7_AGGREGATE_CEILING_HZ, DEFAULT_SINGLE_CHANNEL,
)
from rbl.hardware.labjack_stream_worker import LabJackStreamWorker


# ---------------------------------------------------------------------------
# Config: profile inventory & invariants
# ---------------------------------------------------------------------------

class TestStreamConfig:
    def test_single_channel_profiles_exist(self):
        assert "SINGLE_FAST" in STREAM_PROFILES
        assert "SINGLE_HIRES" in STREAM_PROFILES

    def test_single_flags(self):
        assert is_single_channel("SINGLE_FAST")
        assert is_single_channel("SINGLE_HIRES")
        assert not is_single_channel("FULL")
        assert not is_single_channel("WAVEFORM")

    def test_choices_are_amp_monitors_only(self):
        # Feature scope: single-channel targets are the 8 HV amp monitors only.
        for name in ("SINGLE_FAST", "SINGLE_HIRES"):
            choices = channel_choices(name)
            assert choices, f"{name} has no channel_choices"
            assert set(choices).issubset(set(AMP_CHANNELS))
            # No log amps offered as targets.
            assert not (set(choices) & set(LOGAMP_CHANNELS))

    def test_multichannel_profiles_have_no_choices(self):
        assert channel_choices("FULL") == []
        assert channel_choices("WAVEFORM") == []

    def test_default_single_channel_is_a_valid_choice(self):
        assert DEFAULT_SINGLE_CHANNEL in channel_choices("SINGLE_FAST")
        assert DEFAULT_SINGLE_CHANNEL in AMP_CHANNELS

    def test_fast_profile_is_max_rate(self):
        prof = STREAM_PROFILES["SINGLE_FAST"]
        assert prof["per_channel_rate_hz"] == T7_AGGREGATE_CEILING_HZ
        assert resolution_index("SINGLE_FAST") == 1
        assert window_samples("SINGLE_FAST") == 10_000   # 100 kS/s / 10 Hz GUI

    def test_hires_profile_trades_rate_for_resolution(self):
        assert resolution_index("SINGLE_HIRES") == 8
        # Rate must be low enough that res index 8 can actually deliver it.
        rate = STREAM_PROFILES["SINGLE_HIRES"]["per_channel_rate_hz"]
        assert rate <= MAX_AGG_RATE_BY_RES[8]
        assert window_samples("SINGLE_HIRES") == rate // CFG.GUI_REFRESH_HZ

    def test_every_profile_rate_fits_its_resolution_index(self):
        for name, prof in STREAM_PROFILES.items():
            agg = len(prof["scan_list"]) * prof["per_channel_rate_hz"]
            res = resolution_index(name)
            assert agg <= T7_AGGREGATE_CEILING_HZ
            assert agg <= MAX_AGG_RATE_BY_RES[res], (
                f"{name}: {agg} S/s exceeds res-{res} ceiling"
            )

    def test_resolution_index_defaults_to_module_constant(self):
        # A profile without an explicit resolution_index falls back to default.
        assert resolution_index("WAVEFORM") == CFG.STREAM_RESOLUTION_INDEX


# ---------------------------------------------------------------------------
# Worker: single-channel payload building
# ---------------------------------------------------------------------------

def _worker(profile_name):
    """A worker instance for payload tests, bypassing QThread/LJM init."""
    w = LabJackStreamWorker.__new__(LabJackStreamWorker)
    w._profile_name = profile_name
    return w


class TestSingleChannelPayload:
    def test_target_channel_is_live_others_paused(self):
        w = _worker("SINGLE_FAST")
        win = window_samples("SINGLE_FAST")
        target = "AIN11"                      # X- voltage monitor
        data = np.full((win, 1), 2.5)
        payload = w._build_payload([target], data, win, 0.5)

        # Target present with a full waveform + scalars.
        entry = payload["channels"][target]
        assert entry is not None
        assert len(entry["waveform"]) == win
        assert entry["peak"] == pytest.approx(2.5)
        assert entry["pk_pk"] == pytest.approx(0.0)
        assert entry["rms"] == pytest.approx(2.5)

        # Every other amp channel and every log amp is paused (None).
        for ain in AMP_CHANNELS:
            if ain != target:
                assert payload["channels"][ain] is None
        for ain in LOGAMP_CHANNELS:
            assert payload["channels"][ain] is None

    def test_current_target_supported(self):
        # A current monitor is a valid single-channel target and carries a
        # waveform just like a voltage monitor.
        w = _worker("SINGLE_HIRES")
        win = window_samples("SINGLE_HIRES")
        target = "AIN6"                       # Y- current monitor
        data = np.full((win, 1), 0.8)
        payload = w._build_payload([target], data, win, 0.1)
        assert payload["channels"][target] is not None
        assert len(payload["channels"][target]["waveform"]) == win

    def test_multichannel_payload_unchanged(self):
        # The FULL profile still returns every channel populated (no regression).
        w = _worker("FULL")
        win = window_samples("FULL")
        sl = list(STREAM_PROFILES["FULL"]["scan_list"])
        data = np.full((win, len(sl)), 1.0)
        payload = w._build_payload(sl, data, win, 0.0)
        for ain in AMP_CHANNELS:
            assert payload["channels"][ain] is not None
        for ain in LOGAMP_CHANNELS:
            assert payload["channels"][ain] is not None


class TestWorkerConstruction:
    def test_channel_override_stored(self):
        # A real construction records the override for run() to use later.
        w = LabJackStreamWorker(handle=1, profile_name="SINGLE_FAST",
                                channel_override="AIN9")
        assert w._channel_override == "AIN9"
        assert w._profile_name == "SINGLE_FAST"

    def test_channel_override_defaults_none(self):
        w = LabJackStreamWorker(handle=1, profile_name="FULL")
        assert w._channel_override is None
