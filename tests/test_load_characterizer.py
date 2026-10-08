"""
Tests for rbl.services.load_characterizer.LoadCharacterizer.

No hardware: a FakeGen stands in for DG1022Z (mirrors CalibrationRunner's
test convention), and synthetic window payloads are injected directly via
on_window(). QTimer slots are invoked directly rather than by running the Qt
event loop, matching tests/test_calibration_runner.py's convention.
"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timedelta

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from rbl.config.calibration_config import LoadCondition
from rbl.hardware.amp_monitor import ma_to_monitor
from rbl.services.load_characterizer import (
    CLAMP_FREQ_LADDER_HZ,
    CLAMP_RATIO_THRESHOLD,
    GUI_REFRESH_HZ,
    MODE_C_FREQ_HZ,
    MODE_C_LADDER_KV,
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


def good_vacuum():
    """A pressure the HV interlock permits every rung at."""
    return 1e-7


def _begin_collect(lc):
    """Skip the settle delay and return the step now being collected.

    The one place these tests reach for the settle callback and the current
    step; the real path waits `settle_s` on a QTimer.
    """
    lc._on_settle_elapsed()
    return lc._current_step()


def _result_files():
    from rbl.config import paths
    d = paths.CHARACTERIZATION_DIR
    return sorted(d.glob("*.json")) if d.exists() else []


def _run_mode_a_sine(lc, c_true_pf=1200.0):
    """Drive a complete synthetic Mode A run at one frequency to completion."""
    lc.start_mode_a("X+", freq_list=[1000.0])
    fs = 50_000.0
    step = _begin_collect(lc)
    n = int(fs * step.collect_s)
    t = np.arange(n) / fs
    v_pk_v = step.peak_kv * 1000.0
    v_raw = (v_pk_v / 1000.0) * np.sin(2 * np.pi * step.freq_hz * t)
    i_ma = (2 * np.pi * step.freq_hz * c_true_pf * 1e-12 * v_pk_v
            * np.cos(2 * np.pi * step.freq_hz * t) * 1e3)
    i_raw = ma_to_monitor(i_ma)
    windows = max(1, round(step.collect_s * GUI_REFRESH_HZ))
    chunk = n // windows
    for w in range(windows):
        lc.on_window({"sample_period": 1.0 / fs,
                      "channels": {"AIN13": {"waveform": v_raw[w*chunk:(w+1)*chunk]},
                                   "AIN12": {"waveform": i_raw[w*chunk:(w+1)*chunk]}}})


class TestResultFiles:
    """Ticket 37: runs write characterization results tagged with the amplifier."""

    def test_a_completed_mode_a_run_writes_exactly_one_result(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES)
        _run_mode_a_sine(lc)
        (path,) = _result_files()
        rec = json.loads(path.read_text(encoding="utf-8"))
        assert rec["method"] == "impedance_sweep"
        assert rec["plate_position"] == "X+"
        assert rec["values"]["c_pf"] == pytest.approx(1200.0, abs=50.0)
        assert rec["values"]["g_us"] == pytest.approx(0.0, abs=1e-6)
        assert len(rec["points"]) == 1
        assert not rec.get("aborted")

    def test_the_serial_is_the_one_assigned_to_the_plate(self, qapp, funcgen_map):
        from rbl.config import amplifier_assignments as aa
        now = datetime(2026, 10, 7, 12, 0, 0)
        aa.record_assignment({"X+": "S-123", "X-": "S-2", "Y+": "S-3", "Y-": "S-4"},
                             now - timedelta(days=5), "initial")
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES,
                               now_fn=lambda: now)
        _run_mode_a_sine(lc)
        (path,) = _result_files()
        rec = json.loads(path.read_text(encoding="utf-8"))
        assert rec["amplifier_serial"] == "S-123"
        assert rec["when"] == now.isoformat()

    def test_with_no_assignment_the_serial_is_unassigned(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES)
        _run_mode_a_sine(lc)
        (path,) = _result_files()
        assert json.loads(path.read_text(encoding="utf-8"))["amplifier_serial"] == "unassigned"

    def test_the_load_condition_is_recorded(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.CABLE_ONLY)
        _run_mode_a_sine(lc)
        (path,) = _result_files()
        assert json.loads(path.read_text(encoding="utf-8"))["load_condition"] == "CABLE_ONLY"

    def test_an_aborted_run_writes_an_aborted_result_that_is_never_the_newest(
            self, qapp, funcgen_map):
        from rbl.config import characterization_history as ch
        now = datetime(2026, 10, 7, 12, 0, 0)
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES,
                               now_fn=lambda: now)
        lc.start_mode_a("X+", freq_list=[1000.0])
        lc.abort()
        (path,) = _result_files()
        rec = json.loads(path.read_text(encoding="utf-8"))
        assert rec["aborted"] is True
        assert "c_pf" not in rec["values"]
        assert ch.newest("X+", "ON_PLATES", now + timedelta(days=1)) is None

    def test_a_completed_mode_c_run_is_a_charge_integral_ladder_result(
            self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES, pressure_provider=good_vacuum)
        lc.start_mode_c("X+", ladder_kv=[MODE_C_PEAK_KV])     # one rung
        fs = 1_000_000.0
        step = _begin_collect(lc)
        n = int(fs * step.collect_s)
        period = int(fs / MODE_C_FREQ_HZ)
        v = MODE_C_PEAK_KV * np.sign(np.sin(2 * np.pi * MODE_C_FREQ_HZ * np.arange(n) / fs))
        q = 1200.0 * 1e-12 * 2 * MODE_C_PEAK_KV * 1000.0
        tau = 300e-6      # a 20 us edge here would be a 120 mA hard trip (ticket 44)
        i_ma = np.zeros(n)
        for k, pos in enumerate(np.arange(period // 2, n, period // 2)):
            idx = np.arange(pos, min(pos + int(10 * tau * fs), n))
            i_ma[idx] += (1.0 if k % 2 == 0 else -1.0) * (q / tau) * np.exp(
                -(idx - pos) / (tau * fs)) * 1e3
        windows = max(1, round(step.collect_s * GUI_REFRESH_HZ))
        chunk = n // windows
        for w in range(windows):
            i_chunk = ma_to_monitor(i_ma)[w*chunk:(w+1)*chunk]
            lc.on_window({"sample_period": 1.0 / fs,
                          "channels": {"AIN13": {"waveform": v[w*chunk:(w+1)*chunk]},
                                       "AIN12": {"waveform": i_chunk}}})
        (path,) = _result_files()
        rec = json.loads(path.read_text(encoding="utf-8"))
        assert rec["method"] == "charge_integral_ladder"
        assert rec["values"]["c_pf"] == pytest.approx(1200.0, rel=0.3)

    def test_mode_b_leakage_runs_write_no_capacitance_result(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES)
        lc.start_mode_b("X+", ladder_kv=[1.0], leak_threshold_ua=50.0)
        step = _begin_collect(lc)
        _feed_windows(lc, {"AIN12": 0.0},
                      n_windows=max(1, round(step.collect_s * GUI_REFRESH_HZ)))
        assert _result_files() == []

    def test_the_retired_single_record_store_is_never_written(self, qapp, funcgen_map):
        from rbl.config import load_calibration_store as store
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES)
        _run_mode_a_sine(lc)
        assert _result_files()
        assert not store.STORE_PATH.exists()


def _feed_rung(lc, step, c_pf=1200.0, tau_s=300e-6, fs=500_000.0, leak_ma=0.0,
               swing_fraction=1.0, rail_samples=0, rail_volts=10.0):
    """Feed one complete synthetic Mode C rung: a +/-step.peak_kv square wave
    whose edges each deliver exactly C x dV, decaying with time constant tau.

    tau defaults to 150 sample intervals. Two things bound it: a sampled step
    edge makes the trapezoid integral read high by about half a sample's share
    (dt / 2 tau), so a shorter tau would put that quantisation in the result;
    and the ladder's hard trip (CAL_TRIP_HARD_MA) treats any sample over 60 mA
    as a short, so the edge of the 5 kV rung (12 uC into 1200 pF) has to be
    spread over at least ~220 us to be a legal edge at all.
    """
    n = int(fs * step.collect_s)
    period = int(fs / MODE_C_FREQ_HZ)
    # swing_fraction < 1: an amplifier that does not follow its input swings
    # less AND delivers proportionally less charge, so C itself is unchanged.
    peak_kv = step.peak_kv * swing_fraction
    v = peak_kv * np.sign(np.sin(2 * np.pi * MODE_C_FREQ_HZ * np.arange(n) / fs))
    q = c_pf * 1e-12 * 2 * peak_kv * 1000.0
    i_ma = np.full(n, float(leak_ma))
    for k, pos in enumerate(np.arange(period // 2, n, period // 2)):
        idx = np.arange(pos, min(pos + int(12 * tau_s * fs), n))
        i_ma[idx] += (1.0 if k % 2 == 0 else -1.0) * (q / tau_s) * np.exp(
            -(idx - pos) / (tau_s * fs)) * 1e3
    i_mon = ma_to_monitor(i_ma)
    if rail_samples > 0:
        for k, pos in enumerate(np.arange(period // 2, n, period // 2)):
            sign = 1.0 if k % 2 == 0 else -1.0
            i_mon[pos:min(pos + rail_samples, n)] = sign * rail_volts
    windows = max(1, round(step.collect_s * GUI_REFRESH_HZ))
    chunk = n // windows
    for w in range(windows):
        i_chunk = i_mon[w*chunk:(w+1)*chunk]
        lc.on_window({"sample_period": 1.0 / fs,
                      "channels": {"AIN13": {"waveform": v[w*chunk:(w+1)*chunk]},
                                   "AIN12": {"waveform": i_chunk}}})


def _run_ladder(lc, ladder_kv=None, per_rung=None, **kw):
    """Run rungs until the ladder finishes (completed or aborted).

    `per_rung` maps a rung index to extra _feed_rung keyword arguments.
    """
    finished = []
    lc.finished.connect(finished.append)
    lc.start_mode_c("X+", ladder_kv=ladder_kv)
    total = len(ladder_kv) if ladder_kv else len(MODE_C_LADDER_KV)
    for i in range(total):
        if finished:
            break
        step = _begin_collect(lc)
        _feed_rung(lc, step, **{**kw, **(per_rung or {}).get(i, {})})


def _square_commands(funcgen_map):
    gen, _ch = funcgen_map["X+"]
    return [c for c in gen.calls if c[0] == "set_waveform" and c[2] == "Square"]


def _only_result():
    (path,) = _result_files()
    return json.loads(path.read_text(encoding="utf-8"))


def _feed_clamp_step(lc, step, v_peak_kv, i_fund_ma, fs=50_000.0):
    """One clamp-test step: a triangle voltage of peak `v_peak_kv` and a current
    whose FUNDAMENTAL is exactly `i_fund_ma` (a sine at the drive frequency)."""
    n = int(fs * step.collect_s)
    t = np.arange(n) / fs
    v = v_peak_kv * (2.0 / np.pi) * np.arcsin(np.sin(2 * np.pi * step.freq_hz * t))
    i_raw = ma_to_monitor(i_fund_ma * np.sin(2 * np.pi * step.freq_hz * t + 1.2))
    windows = max(1, round(step.collect_s * GUI_REFRESH_HZ))
    chunk = n // windows
    for w in range(windows):
        lc.on_window({"sample_period": 1.0 / fs,
                      "channels": {"AIN13": {"waveform": v[w*chunk:(w+1)*chunk]},
                                   "AIN12": {"waveform": i_raw[w*chunk:(w+1)*chunk]}}})


def _run_clamp(lc, freqs, v_peak_of, i_fund_of, peak_kv=1.0):
    """Run the clamp test, feeding each step from the two functions of frequency."""
    finished = []
    lc.finished.connect(finished.append)
    lc.start_clamp_test("X+", peak_kv=peak_kv, freq_list=freqs)
    for _ in freqs:
        if finished:
            break
        step = _begin_collect(lc)
        _feed_clamp_step(lc, step, v_peak_of(step.freq_hz), i_fund_of(step.freq_hz))


def _triangle_freqs(funcgen_map):
    gen, _ch = funcgen_map["X+"]
    return [c[3] for c in gen.calls if c[0] == "set_waveform" and c[2] == "Triangle"]


class TestClampTest:
    """Ticket 45: raise the frequency until the amplifier stops following its input."""

    @staticmethod
    def make(funcgen_map):
        return LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES,
                                 pressure_provider=good_vacuum)

    def test_the_default_ladder_and_threshold(self):
        assert CLAMP_FREQ_LADDER_HZ == [250, 500, 750, 1000, 1500, 2000, 2500, 3000, 4000, 5000]
        assert CLAMP_RATIO_THRESHOLD == 0.95

    def test_it_stops_at_the_first_step_that_stops_following(self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        _run_clamp(lc, CLAMP_FREQ_LADDER_HZ,
                   v_peak_of=lambda f: 1.0 if f <= 1500 else 0.8,
                   i_fund_of=lambda f: 0.01 * f)
        rec = _only_result()
        assert rec["method"] == "clamp_test"
        assert rec["values"]["clamp_freq_hz"] == 2000
        assert rec["values"]["peak_kv"] == 1.0
        assert not rec.get("aborted")
        # 2500 Hz was never commanded
        assert _triangle_freqs(funcgen_map) == [250, 500, 750, 1000, 1500, 2000]
        assert len(rec["points"]) == 6

    def test_clamp_ma_is_the_current_fundamental_at_the_stopping_step(
            self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        _run_clamp(lc, CLAMP_FREQ_LADDER_HZ,
                   v_peak_of=lambda f: 1.0 if f <= 1500 else 0.8,
                   i_fund_of=lambda f: 0.01 * f)
        assert _only_result()["values"]["clamp_ma"] == pytest.approx(20.0, abs=1e-6)

    def test_a_healthy_triangle_reads_a_ratio_of_one(self, qapp, funcgen_map):
        # The fundamental of a triangle is 8/pi^2 of its peak; comparing it with
        # the peak would read 0.81 on a perfectly healthy amplifier.
        lc = self.make(funcgen_map)
        points = []
        lc.point_measured.connect(points.append)
        _run_clamp(lc, [500.0], v_peak_of=lambda f: 1.0, i_fund_of=lambda f: 5.0)
        assert points[0]["regulation_ratio"] == pytest.approx(1.0, abs=2e-3)

    def test_a_ratio_just_under_the_threshold_stops_and_just_over_does_not(
            self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        points = []
        lc.point_measured.connect(points.append)
        _run_clamp(lc, [250.0, 500.0, 750.0],
                   v_peak_of=lambda f: {250.0: 0.96, 500.0: 0.94}.get(f, 1.0),
                   i_fund_of=lambda f: 1.0)
        assert [p["freq_hz"] for p in points] == [250.0, 500.0]

    def test_if_the_ratio_never_drops_the_clamp_is_not_reached(self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        _run_clamp(lc, [250.0, 500.0], v_peak_of=lambda f: 1.0, i_fund_of=lambda f: 1.0)
        rec = _only_result()
        assert rec["values"]["clamp_ma"] is None
        assert rec["values"]["clamp_freq_hz"] is None
        assert rec["values"]["clamp_at_rail"] is False
        assert not rec.get("aborted")
        assert lc.clamp_result["reached"] is False
        assert lc.clamp_result["at_rail"] is False

    def test_the_clamp_result_is_available_to_the_tab(self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        _run_clamp(lc, [250.0, 500.0], v_peak_of=lambda f: 1.0 if f < 500 else 0.5,
                   i_fund_of=lambda f: 0.02 * f)
        assert lc.clamp_result == {"reached": True, "clamp_ma": pytest.approx(10.0, abs=1e-6),
                                   "clamp_freq_hz": 500.0, "peak_kv": 1.0, "at_rail": False}

    def test_a_clamp_at_the_rail_is_recorded_as_at_rail(self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        _run_clamp(lc, [500.0],
                   v_peak_of=lambda f: 0.8,
                   i_fund_of=lambda f: 20.0)
        rec = _only_result()
        assert lc.clamp_result["at_rail"] is True
        assert rec["values"]["clamp_at_rail"] is True

    def test_a_clamp_below_the_rail_is_not_at_rail(self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        _run_clamp(lc, [500.0],
                   v_peak_of=lambda f: 0.8,
                   i_fund_of=lambda f: 10.0)
        assert lc.clamp_result["at_rail"] is False
        assert lc.clamp_result["clamp_ma"] == pytest.approx(10.0, abs=0.2)

    def test_a_rail_held_for_6_ms_ends_the_clamp_test_with_hard_trip(self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        lc.start_clamp_test("X+")
        _begin_collect(lc)
        wave = np.zeros(100)
        wave[10:70] = 10.0  # 60 samples at dt=1e-4 -> 6 ms at rail
        lc.on_window({"sample_period": 1e-4,
                      "channels": {"AIN12": {"waveform": wave},
                                   "AIN13": {"waveform": np.zeros(100)}}})
        rec = _only_result()
        assert rec["aborted"] is True and rec["abort_rule"] == "hard_trip"
        assert rec["points"] == []

    def test_no_voltage_reading_is_an_abort_not_a_silent_pass(self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        lc.start_clamp_test("X+", freq_list=[250.0, 500.0])
        step = _begin_collect(lc)
        _feed_windows(lc, {"AIN12": 0.1},          # current only: no voltage channel
                      n_windows=max(1, round(step.collect_s * GUI_REFRESH_HZ)))
        rec = _only_result()
        assert rec["aborted"] is True and rec["abort_rule"] == "no_data"

    def test_an_operator_abort_writes_an_aborted_result(self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        lc.start_clamp_test("X+")
        lc.abort()
        rec = _only_result()
        assert rec["aborted"] is True and rec["abort_rule"] == "operator"
        assert "clamp_ma" not in rec["values"]

    def test_the_clamp_result_never_hides_the_capacitance(self, qapp, funcgen_map):
        # A clamp result is ON_PLATES too, but carries no capacitance.
        from rbl.config import characterization_history as ch
        lc = self.make(funcgen_map)
        _run_clamp(lc, [250.0], v_peak_of=lambda f: 1.0, i_fund_of=lambda f: 1.0)
        assert ch.newest_on_plates_c_pf(
            "X+", datetime.now().astimezone() + timedelta(days=1)) is None


class TestModeCLadder:
    """Ticket 43: one run steps through the rungs and reports each."""

    def test_the_default_ladder_is_half_a_kilovolt_to_five(self):
        assert MODE_C_LADDER_KV == [0.5, 1.0, 2.0, 3.0, 4.0, 5.0]

    def test_six_rungs_each_recover_the_capacitance(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES,
                               pressure_provider=good_vacuum)
        points = []
        lc.point_measured.connect(points.append)
        _run_ladder(lc)
        assert [p["rung_kv"] for p in points] == [0.5, 1.0, 2.0, 3.0, 4.0, 5.0]
        for p in points:
            assert p["c_pf_mean"] == pytest.approx(1200.0, rel=0.3)

    def test_the_written_result_is_the_mean_of_the_rungs(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES,
                               pressure_provider=good_vacuum)
        points = []
        lc.point_measured.connect(points.append)
        _run_ladder(lc)
        (path,) = _result_files()
        rec = json.loads(path.read_text(encoding="utf-8"))
        assert rec["method"] == "charge_integral_ladder"
        assert len(rec["points"]) == 6
        assert rec["values"]["c_pf"] == pytest.approx(
            sum(p["c_pf_mean"] for p in points) / 6, abs=1e-9)
        assert not rec.get("aborted")

    def test_each_rung_reports_the_edge_charge_as_c_times_the_step(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES,
                               pressure_provider=good_vacuum)
        points = []
        lc.point_measured.connect(points.append)
        _run_ladder(lc, ladder_kv=[0.5, 2.0, 5.0])
        for p in points:
            # C [pF] x dV [kV] x 1e-3 = charge in uC
            assert p["edge_charge_uc"] == pytest.approx(
                1200.0 * 2 * p["rung_kv"] * 1e-3, rel=0.05)
            assert p["measured_swing_kv"] == pytest.approx(2 * p["rung_kv"], rel=0.01)

    @pytest.mark.parametrize("tau_s, lower_bound", [(20e-6, True), (300e-6, False)])
    def test_a_narrow_edge_peak_is_flagged_as_a_lower_bound(
            self, qapp, funcgen_map, tau_s, lower_bound):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES,
                               pressure_provider=good_vacuum)
        points = []
        lc.point_measured.connect(points.append)
        # 200 pF keeps even the 20 us edge under the 60 mA hard trip.
        _run_ladder(lc, ladder_kv=[1.0], tau_s=tau_s, c_pf=200.0)
        (p,) = points
        assert p["edge_peak_is_lower_bound"] is lower_bound
        # time above half the peak of an exponential decay is tau x ln 2
        assert p["edge_duration_us"] == pytest.approx(tau_s * np.log(2) * 1e6, rel=0.15)

    def test_the_edge_peak_current_is_reported(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES,
                               pressure_provider=good_vacuum)
        points = []
        lc.point_measured.connect(points.append)
        _run_ladder(lc, ladder_kv=[1.0], tau_s=300e-6)
        q = 1200.0 * 1e-12 * 2 * 1.0 * 1000.0
        assert points[0]["edge_peak_ma"] == pytest.approx(q / 300e-6 * 1e3, rel=0.05)

    def test_an_edge_whose_current_leads_the_detected_voltage_jump_is_not_biased(
            self, qapp, funcgen_map):
        # In the synthetic trace the voltage jump is detected a sample after the
        # current onset (as it can be on the bench). With the baseline taken
        # from the samples just before the detected jump, that largest sample
        # leaked into the baseline and C read about a third low.
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES,
                               pressure_provider=good_vacuum)
        points = []
        lc.point_measured.connect(points.append)
        _run_ladder(lc, ladder_kv=[1.0], tau_s=80e-6)
        assert points[0]["c_pf_mean"] == pytest.approx(1200.0, rel=0.05)

    def test_leakage_between_edges_is_reported_in_microamps(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES,
                               pressure_provider=good_vacuum)
        points = []
        lc.point_measured.connect(points.append)
        _run_ladder(lc, ladder_kv=[1.0], leak_ma=0.02)
        assert points[0]["inter_edge_leak_ua"] == pytest.approx(20.0, rel=0.1)
        # and the capacitance is not disturbed by a steady leak
        assert points[0]["c_pf_mean"] == pytest.approx(1200.0, rel=0.3)

    def test_no_edges_still_reports_every_new_key(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES, pressure_provider=good_vacuum)
        points = []
        lc.point_measured.connect(points.append)
        lc.start_mode_c("X+", ladder_kv=[1.0])
        step = _begin_collect(lc)
        _feed_windows(lc, {"AIN13": 1.0, "AIN12": 0.0},
                      n_windows=max(1, round(step.collect_s * GUI_REFRESH_HZ)))
        (p,) = points
        assert p["rung_kv"] == 1.0 and p["n_edges"] == 0
        for key in ("measured_swing_kv", "edge_peak_ma", "edge_duration_us",
                    "edge_charge_uc"):
            assert np.isnan(p[key])
        assert p["edge_peak_is_lower_bound"] is False

    def test_every_rung_is_driven_as_a_square_wave_at_the_edge_frequency(
            self, qapp, funcgen_map):
        # The charge-integral method needs sharp edges; a sine has none.
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES, pressure_provider=good_vacuum)
        _run_ladder(lc, ladder_kv=[0.5, 1.0])
        gen, _ch = funcgen_map["X+"]
        # The run ends by zeroing the output (a "DC" call); the rungs themselves
        # are the AC calls.
        waves = [(c[2], c[3], c[4]) for c in gen.calls
                 if c[0] == "set_waveform" and c[2] != "DC"]
        assert [(w[0], w[1]) for w in waves] == [("Square", MODE_C_FREQ_HZ)] * 2
        assert waves[1][2] == pytest.approx(2 * waves[0][2])    # 1.0 kV is twice 0.5 kV

    def test_progress_counts_the_rungs(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES, pressure_provider=good_vacuum)
        seen = []
        lc.progress.connect(lambda done, total, label: seen.append((done, total)))
        _run_ladder(lc, ladder_kv=[0.5, 1.0, 2.0])
        assert seen == [(0, 3), (1, 3), (2, 3)]

    def test_a_railed_edge_marks_its_rung_edge_at_rail_and_the_ladder_continues(
            self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES,
                               pressure_provider=good_vacuum)
        points = []
        lc.point_measured.connect(points.append)
        _run_ladder(lc, ladder_kv=[0.5, 1.0],
                    per_rung={0: {"rail_samples": 15, "rail_volts": 10.0},
                              1: {"c_pf": 1650.0}})
        assert len(points) == 2
        assert points[0]["edge_at_rail"] is True
        assert lc.abort_rule is None

    def test_an_edge_below_the_rail_is_not_edge_at_rail(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES,
                               pressure_provider=good_vacuum)
        points = []
        lc.point_measured.connect(points.append)
        _run_ladder(lc, ladder_kv=[0.5, 1.0],
                    per_rung={0: {"rail_samples": 15, "rail_volts": 8.0},
                              1: {"c_pf": 1550.0}})
        assert len(points) == 2
        assert points[0]["edge_at_rail"] is False


class TestModeCLadderAborts:
    """Ticket 44: any of four rules ends the ladder; the interlock gates each rung."""

    @staticmethod
    def make(funcgen_map, provider=good_vacuum):
        return LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES,
                                 pressure_provider=provider)

    def test_a_clean_ladder_completes_six_rungs_with_no_abort_rule(self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        points = []
        lc.point_measured.connect(points.append)
        _run_ladder(lc)
        assert len(points) == 6
        rec = _only_result()
        assert not rec.get("aborted")
        assert "abort_rule" not in rec
        assert "c_pf" in rec["values"]

    def test_a_changed_capacitance_ends_the_ladder_and_the_next_rung_is_never_commanded(
            self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        _run_ladder(lc, per_rung={2: {"c_pf": 1500.0}})        # 1.25 x the first rung
        rec = _only_result()
        assert rec["aborted"] is True
        assert rec["abort_rule"] == "c_changed"
        assert rec["abort_rung_kv"] == 2.0
        assert len(rec["points"]) == 3
        assert "c_pf" not in rec["values"]                       # no headline C
        assert len(_square_commands(funcgen_map)) == 3           # 0.5, 1 and 2 kV only

    def test_a_ten_percent_change_is_tolerated(self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        points = []
        lc.point_measured.connect(points.append)
        _run_ladder(lc, ladder_kv=[0.5, 1.0], per_rung={1: {"c_pf": 1200.0 * 1.08}})
        assert len(points) == 2
        assert not _only_result().get("aborted")

    def test_leakage_between_edges_ends_the_ladder(self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        _run_ladder(lc, per_rung={1: {"leak_ma": 0.1}})          # 100 uA
        rec = _only_result()
        assert rec["abort_rule"] == "leakage"
        assert rec["abort_rung_kv"] == 1.0
        assert len(rec["points"]) == 2

    def test_a_swing_below_90_percent_of_commanded_ends_the_ladder(self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        _run_ladder(lc, per_rung={1: {"swing_fraction": 0.85}})
        rec = _only_result()
        assert rec["abort_rule"] == "not_following"
        assert rec["abort_rung_kv"] == 1.0
        assert len(rec["points"]) == 2

    def test_a_swing_at_95_percent_is_fine(self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        _run_ladder(lc, ladder_kv=[0.5, 1.0], per_rung={1: {"swing_fraction": 0.95}})
        assert not _only_result().get("aborted")

    def test_a_rung_with_no_edges_at_all_counts_as_not_following(self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        lc.start_mode_c("X+", ladder_kv=[1.0, 2.0])
        step = _begin_collect(lc)
        _feed_windows(lc, {"AIN13": 0.0, "AIN12": 0.0},
                      n_windows=max(1, round(step.collect_s * GUI_REFRESH_HZ)))
        rec = _only_result()
        assert rec["abort_rule"] == "not_following"
        assert rec["abort_rung_kv"] == 1.0

    def test_a_rail_held_for_6_ms_ends_the_ladder_before_the_point_is_emitted(
            self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        points, finished = [], []
        lc.point_measured.connect(points.append)
        lc.finished.connect(finished.append)
        lc.start_mode_c("X+", ladder_kv=[0.5, 1.0])
        step = _begin_collect(lc)
        wave = np.zeros(100)
        wave[10:70] = 10.0  # 60 samples at dt=1e-4 -> 6 ms at rail
        lc.on_window({"sample_period": 1e-4,
                      "channels": {"AIN12": {"waveform": wave},
                                   "AIN13": {"waveform": np.zeros(100)}}})
        assert finished and points == []
        rec = _only_result()
        assert rec["abort_rule"] == "hard_trip"
        assert rec["abort_rung_kv"] == 0.5
        assert rec["points"] == []
        assert len(_square_commands(funcgen_map)) == 1
        assert step.peak_kv == 0.5

    def test_a_negative_rail_held_for_6_ms_also_trips(self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        lc.start_mode_c("X+", ladder_kv=[0.5])
        _begin_collect(lc)
        wave = np.zeros(100)
        wave[10:70] = -10.0  # 60 samples at dt=1e-4 -> 6 ms at negative rail
        lc.on_window({"sample_period": 1e-4,
                      "channels": {"AIN12": {"waveform": wave},
                                   "AIN13": {"waveform": np.zeros(100)}}})
        assert _only_result()["abort_rule"] == "hard_trip"

    def test_a_150_us_rail_on_every_edge_is_not_a_trip(self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        finished = []
        lc.finished.connect(finished.append)
        lc.start_mode_c("X+", ladder_kv=[0.5])
        step = _begin_collect(lc)
        fs = 500_000.0
        n = int(fs * step.collect_s)
        period = int(fs / MODE_C_FREQ_HZ)
        peak_kv = step.peak_kv
        v = peak_kv * np.sign(np.sin(2 * np.pi * MODE_C_FREQ_HZ * np.arange(n) / fs))
        q = 1200.0 * 1e-12 * 2 * peak_kv * 1000.0
        tau_s = 300e-6
        i_ma = np.zeros(n)
        for k, pos in enumerate(np.arange(period // 2, n, period // 2)):
            idx = np.arange(pos, min(pos + int(12 * tau_s * fs), n))
            i_ma[idx] += (1.0 if k % 2 == 0 else -1.0) * (q / tau_s) * np.exp(
                -(idx - pos) / (tau_s * fs)) * 1e3
        i_mon = ma_to_monitor(i_ma)
        rail_samples = int(150e-6 * fs)
        for k, pos in enumerate(np.arange(period // 2, n, period // 2)):
            sign = 1.0 if k % 2 == 0 else -1.0
            i_mon[pos:min(pos + rail_samples, n)] = sign * 10.0
        windows = max(1, round(step.collect_s * GUI_REFRESH_HZ))
        chunk = n // windows
        for w in range(windows):
            lc.on_window({"sample_period": 1.0 / fs,
                          "channels": {"AIN13": {"waveform": v[w*chunk:(w+1)*chunk]},
                                       "AIN12": {"waveform": i_mon[w*chunk:(w+1)*chunk]}}})
        assert finished
        assert lc.abort_rule is None
        rec = _only_result()
        assert not rec.get("aborted") and rec.get("abort_rule") is None

    def test_a_pressure_the_interlock_refuses_stops_the_ladder_before_that_rung(
            self, qapp, funcgen_map):
        readings = iter([1e-7, 1e-7, 1e-7, 2e-4])               # outgassing as HV climbs
        lc = self.make(funcgen_map, provider=lambda: next(readings))
        points = []
        lc.point_measured.connect(points.append)
        _run_ladder(lc)
        rec = _only_result()
        assert rec["abort_rule"] == "interlock"
        assert rec["abort_rung_kv"] == 3.0
        assert len(rec["points"]) == 3
        assert len(points) == 3
        assert len(_square_commands(funcgen_map)) == 3           # 3 kV never commanded

    def test_an_unknown_pressure_blocks_the_first_rung(self, qapp, funcgen_map):
        # No data is not good vacuum: the default provider reports NaN.
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES)
        lc.start_mode_c("X+")
        rec = _only_result()
        assert rec["abort_rule"] == "interlock"
        assert rec["abort_rung_kv"] == 0.5
        assert _square_commands(funcgen_map) == []

    def test_a_ceiling_below_the_rung_blocks_it(self, qapp, funcgen_map):
        # 7e-5 torr permits 1 kV: the 0.5 and 1 kV rungs run, 2 kV does not.
        lc = self.make(funcgen_map, provider=lambda: 7e-5)
        _run_ladder(lc)
        rec = _only_result()
        assert rec["abort_rule"] == "interlock"
        assert rec["abort_rung_kv"] == 2.0
        assert len(rec["points"]) == 2

    def test_an_ended_ladder_still_emits_finished_and_zeroes_the_output(
            self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        finished = []
        lc.finished.connect(finished.append)
        _run_ladder(lc, per_rung={1: {"leak_ma": 0.1}})
        assert len(finished) == 1
        gen, _ch = funcgen_map["X+"]
        assert gen.calls[-1][2] == "DC"                          # zeroed last

    def test_the_rule_that_stopped_it_is_available_to_the_tab(self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        _run_ladder(lc, per_rung={1: {"leak_ma": 0.1}})
        assert lc.abort_rule == "leakage"

    def test_the_first_matching_rule_in_order_is_reported(self, qapp, funcgen_map):
        # Rung 2 both changes C and leaks: c_changed comes first.
        lc = self.make(funcgen_map)
        _run_ladder(lc, per_rung={1: {"c_pf": 1500.0, "leak_ma": 0.1}})
        assert _only_result()["abort_rule"] == "c_changed"

    def test_an_operator_abort_is_recorded_as_such(self, qapp, funcgen_map):
        lc = self.make(funcgen_map)
        lc.start_mode_c("X+")
        lc.abort()
        assert _only_result()["abort_rule"] == "operator"


class TestModeAStartsFromTheMeasuredLoad:
    def test_a_measured_plate_sizes_its_first_amplitude_from_its_own_capacitance(
            self, qapp, funcgen_map):
        from rbl.config import characterization_history as ch
        ch.write_result(
            {"plate_position": "X+", "amplifier_serial": "unassigned",
             "load_condition": "ON_PLATES", "method": "impedance_sweep",
             "values": {"c_pf": 1000.0, "g_us": 0.0}},
            datetime.now().astimezone() - timedelta(days=1))
        gen, _ = funcgen_map["X+"]
        LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES).start_mode_a(
            "X+", freq_list=[1000.0])                       # measured: 1000 pF
        LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES).start_mode_a(
            "X-", freq_list=[1000.0])                       # unmeasured: sizing assumption
        sines = {c[1]: c[4] for c in gen.calls if c[0] == "set_waveform" and c[2] == "Sine"}
        # Channel 1 is X+, channel 2 is X-: 3000 pF is three times 1000 pF, so
        # the same target current needs a third of the amplitude.
        assert sines[1] == pytest.approx(3 * sines[2], rel=1e-6)


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
        i_raw = ma_to_monitor(i_ma_physical)

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
        # I = 2*pi*f*C*V -> should land near MODE_A_TARGET_MA at SIZING_ASSUMPTION_PF
        # (nothing is measured for X+ here).
        import math

        from rbl.config.calibration_config import SIZING_ASSUMPTION_PF
        i_ma = 2 * math.pi * 1000.0 * (SIZING_ASSUMPTION_PF * 1e-12) * (v * 1000.0) * 1e3
        assert i_ma == pytest.approx(4.0, rel=0.05)

    def test_unknown_amp_label_raises(self, qapp, funcgen_map):
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES)
        with pytest.raises(ValueError):
            lc.start_mode_a("Q+")

    def test_finished_emits_and_persists_measurement(
        self, qapp, funcgen_map
    ):
        # Persisted as a characterization result file (ticket 37), not into
        # the retired single-record store.
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES)
        finished = []
        lc.finished.connect(finished.append)
        lc.start_mode_a("X+", freq_list=[1000.0])
        step = _begin_collect(lc)
        _feed_windows(lc, {"AIN13": 1.0, "AIN12": 0.1},
                      n_windows=max(1, round(step.collect_s * GUI_REFRESH_HZ)))
        assert finished
        files = _result_files()
        assert len(files) == 1, "expected the measurement to be persisted"
        rec = json.loads(files[0].read_text(encoding="utf-8"))
        assert rec["method"] == "impedance_sweep"
        assert rec["plate_position"] == "X+"
        assert rec["load_condition"] == "ON_PLATES"
        assert rec["values"]["c_pf"] == rec["values"]["c_pf"]   # a number


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
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES, pressure_provider=good_vacuum)
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
        tau_s = 300e-6    # a 20 us edge here would be a 120 mA hard trip (ticket 44)
        i_ma = np.zeros(n)
        edges = np.arange(period_samples // 2, n, period_samples // 2)
        for k, pos in enumerate(edges):
            sign = 1.0 if (k % 2 == 0) else -1.0
            width = int(10 * tau_s * fs)
            idx = np.arange(pos, min(pos + width, n))
            peak_a = q_true_c / tau_s
            i_ma[idx] += sign * (peak_a * np.exp(-(idx - pos) / (tau_s * fs))) * 1e3
        v_raw = v_square
        i_raw = ma_to_monitor(i_ma)

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
        lc = LoadCharacterizer(funcgen_map, LoadCondition.ON_PLATES, pressure_provider=good_vacuum)
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
