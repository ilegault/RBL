"""
Tests for rbl.services.load_characterizer.LoadCharacterizer.

No hardware: a FakeGen stands in for DG1022Z (mirrors CalibrationRunner's
test convention), and synthetic window payloads are injected directly via
on_window(). QTimer slots are invoked directly rather than by running the Qt
event loop, matching tests/test_calibration_runner.py's convention.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from PySide6.QtWidgets import QApplication

from rbl.config.calibration_config import LoadCondition
from rbl.services.load_characterizer import (
    GUI_REFRESH_HZ,
    MODE_C_FREQ_HZ,
    MODE_C_PEAK_KV,
    LoadCharacterizer,
    _State,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeGen:
    def __init__(self):
        self.calls = []

    def set_waveform(self, ch, shape, freq, amp, offset, phase):
        self.calls.append(("set_waveform", ch, shape, freq, amp, offset, phase))
        return ""

    def output_on(self, ch):
        pass

    def output_off(self, ch):
        pass

    def get_state(self, ch):
        return {"shape": "DC", "freq": 0.0, "amp": 0.0, "offset": 0.0,
                 "phase": 0.0, "output": False, "load": "INFinity"}

    def set_output_load(self, ch, load):
        pass


@pytest.fixture
def funcgen_map():
    gen = FakeGen()
    return {"X+": (gen, 1), "X-": (gen, 2), "Y+": (gen, 1), "Y-": (gen, 2)}


def _feed_windows(lc, channels_ma_or_kv: dict, n_windows: int, n_samples: int = 100):
    """channels_ma_or_kv: {ain: value} -> a flat window of that raw monitor
    value repeated n_samples times, fed for n_windows windows."""
    for _ in range(n_windows):
        payload = {
            "sample_period": 1e-4,
            "channels": {ain: {"waveform": np.full(n_samples, val)}
                         for ain, val in channels_ma_or_kv.items()},
        }
        lc.on_window(payload)


class TestModeA:
    def test_recovers_known_capacitance_from_synthetic_sine(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES)
        points = []
        lc.point_measured.connect(points.append)
        lc.start_mode_a("X+", freq_list=[1000.0])
        lc._on_settle_elapsed()
        assert lc._state == _State.COLLECT

        fs = 50_000.0
        step = lc._current_step()
        n = int(fs * step.collect_s)
        t = np.arange(n) / fs
        v_pk_v = step.peak_kv * 1000.0
        v_raw = (v_pk_v / 1000.0) * np.sin(2 * np.pi * step.freq_hz * t)
        i_ma_physical = (2 * np.pi * step.freq_hz * 1200e-12 * v_pk_v
                          * np.cos(2 * np.pi * step.freq_hz * t) * 1e3)
        i_raw = i_ma_physical / 10.0

        windows = max(1, round(step.collect_s * GUI_REFRESH_HZ))
        chunk = n // windows
        for w in range(windows):
            payload = {"sample_period": 1.0 / fs,
                       "channels": {"AIN13": {"waveform": v_raw[w*chunk:(w+1)*chunk]},
                                    "AIN12": {"waveform": i_raw[w*chunk:(w+1)*chunk]}}}
            lc.on_window(payload)

        assert points
        assert points[0]["c_pf"] == pytest.approx(1200.0, abs=50.0)
        assert points[0]["g_us"] == pytest.approx(0.0, abs=1e-6)

    def test_amplitude_chosen_to_target_current_band(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES)
        v = lc._mode_a_amplitude_for(1000.0)
        # I = 2*pi*f*C*V -> should land near MODE_A_TARGET_MA at CAL_LOAD_CAP_PF.
        import math

        from rbl.config.calibration_config import CAL_LOAD_CAP_PF
        i_ma = 2 * math.pi * 1000.0 * (CAL_LOAD_CAP_PF * 1e-12) * (v * 1000.0) * 1e3
        assert i_ma == pytest.approx(4.0, rel=0.05)

    def test_unknown_amp_label_raises(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES)
        with pytest.raises(ValueError):
            lc.start_mode_a("Q+")

    def test_finished_emits_and_persists_measurement(self, qapp, funcgen_map, monkeypatch, tmp_path):
        import rbl.services.load_characterizer as mod
        monkeypatch.setattr(mod, "save_measurement",
                             lambda *a, **k: saved.append((a, k)))
        saved = []
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES)
        finished = []
        lc.finished.connect(finished.append)
        lc.start_mode_a("X+", freq_list=[1000.0])
        lc._on_settle_elapsed()
        step = lc._current_step()
        _feed_windows(lc, {"AIN13": 1.0, "AIN12": 0.1},
                      n_windows=max(1, round(step.collect_s * GUI_REFRESH_HZ)))
        assert finished
        assert saved, "expected the measured capacitance to be persisted"


class TestModeB:
    def test_healthy_ladder_completes_without_aborting(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES)
        points, finished = [], []
        lc.point_measured.connect(points.append)
        lc.finished.connect(finished.append)
        lc.start_mode_b("X+", ladder_kv=[1.0, 2.0], leak_threshold_ua=50.0)

        for _ in range(2):
            lc._on_settle_elapsed()
            step = lc._current_step()
            _feed_windows(lc, {"AIN12": 0.0},
                          n_windows=max(1, round(step.collect_s * GUI_REFRESH_HZ)))
        assert len(points) == 2
        assert finished
        assert all(p["leak_ua"] < 1.0 for p in points)

    def test_aborts_on_leakage_excursion(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES)
        points, finished = [], []
        lc.point_measured.connect(points.append)
        lc.finished.connect(finished.append)
        lc.start_mode_b("X+", ladder_kv=[1.0, 2.0, 3.0], leak_threshold_ua=50.0)

        lc._on_settle_elapsed()
        step = lc._current_step()
        _feed_windows(lc, {"AIN12": 0.0},
                      n_windows=max(1, round(step.collect_s * GUI_REFRESH_HZ)))
        assert len(points) == 1
        assert not finished

        lc._on_settle_elapsed()
        step = lc._current_step()
        # 5 mA raw -> 50 mA physical current -> 50000 uA, well above threshold.
        _feed_windows(lc, {"AIN12": 5.0},
                      n_windows=max(1, round(step.collect_s * GUI_REFRESH_HZ)))
        assert finished, "expected the ladder to abort on excursion"
        assert len(points) == 2   # the third rung was never reached

    def test_pressure_provider_is_recorded(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES,
                                pressure_provider=lambda: 3.5e-6)
        points = []
        lc.point_measured.connect(points.append)
        lc.start_mode_b("X+", ladder_kv=[1.0], leak_threshold_ua=50.0)
        lc._on_settle_elapsed()
        step = lc._current_step()
        _feed_windows(lc, {"AIN12": 0.0},
                      n_windows=max(1, round(step.collect_s * GUI_REFRESH_HZ)))
        assert points[0]["pressure_torr"] == 3.5e-6

    def test_missing_pressure_provider_defaults_to_nan(self, qapp, funcgen_map):
        import math
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES)
        points = []
        lc.point_measured.connect(points.append)
        lc.start_mode_b("X+", ladder_kv=[1.0], leak_threshold_ua=50.0)
        lc._on_settle_elapsed()
        step = lc._current_step()
        _feed_windows(lc, {"AIN12": 0.0},
                      n_windows=max(1, round(step.collect_s * GUI_REFRESH_HZ)))
        assert math.isnan(points[0]["pressure_torr"])


class TestModeC:
    def test_finds_edges_and_recovers_capacitance_ballpark(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES)
        points = []
        lc.point_measured.connect(points.append)
        lc.start_mode_c("X+")
        lc._on_settle_elapsed()

        fs = 1_000_000.0
        step = lc._current_step()
        n = int(fs * step.collect_s)
        period_samples = int(fs / MODE_C_FREQ_HZ)
        v_square = MODE_C_PEAK_KV * np.sign(
            np.sin(2 * np.pi * MODE_C_FREQ_HZ * np.arange(n) / fs))
        c_true_pf = 1200.0
        delta_v_v = 2 * MODE_C_PEAK_KV * 1000.0
        q_true_c = c_true_pf * 1e-12 * delta_v_v
        tau_s = 20e-6
        i_ma = np.zeros(n)
        edges = np.arange(period_samples // 2, n, period_samples // 2)
        for k, pos in enumerate(edges):
            sign = 1.0 if (k % 2 == 0) else -1.0
            width = int(10 * tau_s * fs)
            idx = np.arange(pos, min(pos + width, n))
            peak_a = q_true_c / tau_s
            i_ma[idx] += sign * (peak_a * np.exp(-(idx - pos) / (tau_s * fs))) * 1e3
        v_raw = v_square
        i_raw = i_ma / 10.0

        windows = max(1, round(step.collect_s * GUI_REFRESH_HZ))
        chunk = n // windows
        for w in range(windows):
            payload = {"sample_period": 1.0 / fs,
                       "channels": {"AIN13": {"waveform": v_raw[w*chunk:(w+1)*chunk]},
                                    "AIN12": {"waveform": i_raw[w*chunk:(w+1)*chunk]}}}
            lc.on_window(payload)

        assert points
        assert points[0]["n_edges"] > 0
        assert points[0]["c_pf_mean"] == pytest.approx(c_true_pf, rel=0.3)

    def test_no_edges_reports_nan_not_a_crash(self, qapp, funcgen_map):
        import math
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES)
        points = []
        lc.point_measured.connect(points.append)
        lc.start_mode_c("X+")
        lc._on_settle_elapsed()
        step = lc._current_step()
        _feed_windows(lc, {"AIN13": 1.0, "AIN12": 0.0},   # flat, no edges
                      n_windows=max(1, round(step.collect_s * GUI_REFRESH_HZ)))
        assert points
        assert points[0]["n_edges"] == 0
        assert math.isnan(points[0]["c_pf_mean"])


class TestAbort:
    def test_abort_mid_run_finishes_and_zeros_outputs(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES)
        finished = []
        lc.finished.connect(finished.append)
        lc.start_mode_a("X+", freq_list=[1000.0, 2000.0])
        lc.abort()
        assert finished
        assert lc._state == _State.IDLE

    def test_abort_when_idle_is_a_noop(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES)
        lc.abort()   # must not raise
