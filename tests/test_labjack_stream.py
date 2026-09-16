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
    AMP_CHANNELS,
    DEFAULT_SINGLE_CHANNEL,
    LOGAMP_CHANNELS,
    MAX_AGG_RATE_BY_RES,
    STREAM_PROFILES,
    T7_AGGREGATE_CEILING_HZ,
    channel_choices,
    is_single_channel,
    resolution_index,
    window_samples,
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


class TestFioStateStreamConfig:
    def test_fio_state_in_full_scan_list_only(self):
        full_sl = STREAM_PROFILES["FULL"]["scan_list"]
        assert "FIO_STATE" in full_sl
        for name, prof in STREAM_PROFILES.items():
            if name != "FULL":
                assert "FIO_STATE" not in prof["scan_list"], (
                    f"FIO_STATE must not be in {name} scan list"
                )

    def test_full_profile_rate_and_window_samples(self):
        prof = STREAM_PROFILES["FULL"]
        assert prof["per_channel_rate_hz"] == 7_500
        assert window_samples("FULL") == 750
        assert len(prof["scan_list"]) == 13
        assert len(prof["scan_list"]) * prof["per_channel_rate_hz"] == 97_500

    def test_full_profile_description_mentions_3_75x(self):
        desc = STREAM_PROFILES["FULL"]["description"]
        assert "3.75" in desc
        assert "4x" not in desc and "~4×" not in desc


class TestScanAddressResolver:
    def test_ain_addresses_arithmetic(self):
        from rbl.hardware.labjack_stream_worker import channel_address
        assert channel_address("AIN0") == 0
        assert channel_address("AIN6") == 12
        assert channel_address("AIN13") == 26

    def test_ain_addresses_without_ljm(self, monkeypatch):
        from rbl.hardware import labjack_stream_worker as worker_mod
        monkeypatch.setattr(worker_mod, "_LJM_AVAILABLE", False)
        monkeypatch.setattr(worker_mod, "_ljm", None)
        assert worker_mod.channel_address("AIN0") == 0
        assert worker_mod.channel_address("AIN7") == 14

    def test_fio_state_address_with_ljm(self, monkeypatch):
        from rbl.hardware import labjack_stream_worker as worker_mod
        fake_ljm = type("FakeLJM", (), {
            "nameToAddress": staticmethod(lambda name: (2500, 0))
        })
        monkeypatch.setattr(worker_mod, "_LJM_AVAILABLE", True)
        monkeypatch.setattr(worker_mod, "_ljm", fake_ljm)
        assert worker_mod.channel_address("FIO_STATE") == 2500

    def test_fio_state_address_raises_without_ljm(self, monkeypatch):
        from rbl.hardware import labjack_stream_worker as worker_mod
        monkeypatch.setattr(worker_mod, "_LJM_AVAILABLE", False)
        monkeypatch.setattr(worker_mod, "_ljm", None)
        with pytest.raises(RuntimeError, match="labjack-ljm is not available"):
            worker_mod.channel_address("FIO_STATE")


class TestFioStatePayload:
    def test_fio_state_transitions_recovered(self):
        w = _worker("FULL")
        win = window_samples("FULL")
        sl = list(STREAM_PROFILES["FULL"]["scan_list"])
        data = np.zeros((win, len(sl)), dtype=float)
        fio_idx = sl.index("FIO_STATE")

        # Set transition sequence
        # scans 0..99: word 28 (0b00011100)
        # scans 100..499: word 24 (0b00011000)
        # scans 500..end: word 16 (0b00010000)
        data[0:100, fio_idx] = 28.0
        data[100:500, fio_idx] = 24.0
        data[500:, fio_idx] = 16.0

        payload = w._build_payload(sl, data, win, 1.0, sample_period=1.0 / 7500.0)
        fio_entry = payload["channels"]["FIO_STATE"]
        assert fio_entry is not None
        assert fio_entry["first"] == 28
        assert fio_entry["last"] == 16
        assert fio_entry["transitions"] == [(100, 24), (500, 16)]

    def test_fio_state_constant_word(self):
        w = _worker("FULL")
        win = window_samples("FULL")
        sl = list(STREAM_PROFILES["FULL"]["scan_list"])
        data = np.zeros((win, len(sl)), dtype=float)
        fio_idx = sl.index("FIO_STATE")
        data[:, fio_idx] = 31.0

        payload = w._build_payload(sl, data, win, 1.0, sample_period=1.0 / 7500.0)
        fio_entry = payload["channels"]["FIO_STATE"]
        assert fio_entry is not None
        assert fio_entry["first"] == 31
        assert fio_entry["last"] == 31
        assert fio_entry["transitions"] == []

    def test_fio_state_none_in_other_profiles(self):
        for prof_name in ("WAVEFORM", "AMP_PAIR", "SINGLE_FAST", "SINGLE_HIRES"):
            w = _worker(prof_name)
            win = window_samples(prof_name)
            sl = list(STREAM_PROFILES[prof_name]["scan_list"])
            data = np.zeros((win, len(sl)), dtype=float)
            payload = w._build_payload(sl, data, win, 0.1)
            assert "FIO_STATE" in payload["channels"], f"FIO_STATE missing from {prof_name}"
            assert payload["channels"]["FIO_STATE"] is None, (
                f"FIO_STATE must be None in {prof_name}"
            )

    def test_window_payload_helper_fio_state(self):
        from tests.payloads import window_payload
        # Default: None
        p1 = window_payload({}, profile="FULL")
        assert p1["channels"]["FIO_STATE"] is None

        # With integer
        p2 = window_payload({}, profile="FULL", fio_state=25)
        assert p2["channels"]["FIO_STATE"] == {
            "first": 25,
            "last": 25,
            "transitions": [],
        }

        # With transition series as scan list
        p3 = window_payload({}, profile="FULL", fio_state=[25] * 10 + [20] * 40 + [15] * 25)
        assert p3["channels"]["FIO_STATE"]["first"] == 25
        assert p3["channels"]["FIO_STATE"]["last"] == 15
        assert p3["channels"]["FIO_STATE"]["transitions"] == [(10, 20), (50, 15)]

        # With transition series as tuple list
        trans = [(0, 25), (10, 20), (50, 15)]
        p4 = window_payload({}, profile="FULL", fio_state=trans)
        assert p4["channels"]["FIO_STATE"]["first"] == 25
        assert p4["channels"]["FIO_STATE"]["last"] == 15
        assert p4["channels"]["FIO_STATE"]["transitions"] == [(10, 20), (50, 15)]


class TestConsumersTolerateFioState:
    def test_amp_trace_push_tolerates_fio_state(self):
        from rbl.hardware.amp_trace import AmpTraceBuilder
        builder = AmpTraceBuilder(history_samples=1000)
        payload = {
            "profile": "FULL",
            "window_samples": 750,
            "t": 1.0,
            "sample_period": 1.0 / 7500.0,
            "channels": {
                "AIN7": {"waveform": np.ones(750), "peak": 1.0, "rms": 1.0, "pk_pk": 0.0},
                "FIO_STATE": {"first": 28, "last": 28, "transitions": []},
            },
        }
        builder.push(payload["channels"])
        traces = builder.traces(payload)
        assert isinstance(traces, dict)

    def test_calibration_runner_tolerates_fio_state(self):
        from rbl.config.calibration_config import LoadCondition
        from rbl.services.calibration_runner import CalibrationRunner
        runner = CalibrationRunner(funcgen_map={}, load_condition=LoadCondition.DISCONNECTED)
        payload = {
            "profile": "FULL",
            "window_samples": 750,
            "t": 1.0,
            "sample_period": 1.0 / 7500.0,
            "channels": {
                "AIN12": {"waveform": np.zeros(750), "peak": 0.0, "rms": 0.0, "pk_pk": 0.0},
                "AIN13": {"waveform": np.zeros(750), "peak": 0.0, "rms": 0.0, "pk_pk": 0.0},
                "FIO_STATE": {"first": 28, "last": 28, "transitions": []},
            },
        }
        runner.on_window(payload)

    def test_load_characterizer_tolerates_fio_state(self):
        from rbl.config.calibration_config import LoadCondition
        from rbl.services.load_characterizer import LoadCharacterizer
        char = LoadCharacterizer(funcgen_map={}, load_condition=LoadCondition.DISCONNECTED)
        payload = {
            "profile": "FULL",
            "window_samples": 750,
            "t": 1.0,
            "sample_period": 1.0 / 7500.0,
            "channels": {
                "AIN12": {"waveform": np.zeros(750)},
                "FIO_STATE": {"first": 28, "last": 28, "transitions": []},
            },
        }
        char.on_window(payload)

    def test_dynamic_adjustment_tolerates_fio_state(self):
        from rbl.services.dynamic_adjustment import DynamicAdjustmentTrial
        trial = DynamicAdjustmentTrial(amp_label="X+", funcgen_map={}, pot_position="5.0")
        payload = {
            "profile": "FULL",
            "window_samples": 750,
            "t": 1.0,
            "sample_period": 1.0 / 7500.0,
            "channels": {
                "AIN13": {"waveform": np.zeros(750)},
                "FIO_STATE": {"first": 28, "last": 28, "transitions": []},
            },
        }
        trial.on_window(payload)



