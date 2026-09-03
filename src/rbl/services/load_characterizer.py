"""
load_characterizer.py
Phase 1: per-channel load capacitance and leakage conductance, measured on
demand instead of assumed from `calibration_config.CAL_LOAD_CAP_PF`'s
one-off analysis.

WHY THIS EXISTS
---------------
`CAL_LOAD_CAP_PF` is a single global number, hand-derived once from two AC
sweeps. It cannot tell the four channels apart, and it cannot answer whether
a channel is leaky — which is exactly the question behind the historical
Y-axis fault history (Appendix A of the plan). This service produces the
measurement every later phase consumes, per channel, on demand:

  Mode A - Impedance sweep (primary). Sine sweep, per-frequency amplitude
           chosen to land drawn current in a 2-6 mA band (several times the
           rig's ~1.4 mA noise floor at every point), complex admittance via
           the lock-in fundamentals (rbl.hardware.load_model).
  Mode B - DC leakage vs. voltage. A ramped DC ladder with a long dwell per
           rung, aborting the ladder on rising leakage — a discharge
           starting, not something to climb further into.
  Mode C - Charge integral (cross-check). Low-frequency square wave; the
           charge under each edge gives C independent of the monitor's
           bandwidth (rbl.hardware.load_model.capacitance_from_charge).
           Streams BOTH the voltage and current monitor for the driven
           channel (AMP_PAIR), not current alone as Section 2.3 originally
           suggests: the measured voltage swing at each edge is the ground
           truth delta_v for the charge integral, more robust than assuming
           the commanded peak_kv was reached exactly (an amplitude clamp or
           warning would otherwise go unnoticed). The cost is a lower
           per-channel sample rate (AMP_PAIR's 50 kS/s vs. a single-channel
           100 kS/s) — still comfortably above what a 10 Hz edge needs.

SHAPE
-----
A QObject service in the shape of CalibrationRunner: `progress`,
`point_measured`, `finished`, `error` signals, plus an `abort()` method. Runs
ONE channel at a time and produces per-point admittance/leakage records
rather than CalibrationRunner's raw per-monitor CSV row.

SAFETY
------
Every commanded amplitude is clamped through `ac_max_peak_kv()` and
`funcgen_safety.peak_status()` before it reaches the hardware — no
exceptions, matching Section 2.3's explicit requirement. DC ladder steps go
through the Phase 4 ramp engine rather than a step, per Section 2.3 Mode B.
"""
import csv
import logging
import time
from dataclasses import dataclass
from enum import Enum, auto

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from rbl.config.calibration_config import (
    CAL_LOAD_CAP_PF,
    CAL_MAX_KV,
    ac_max_peak_kv,
)
from rbl.config.hardware_config import (
    AMP_CHANNEL_MAP,
    AMP_MAX_KV,
    CURRENT_MONITOR_MA_PER_VOLT,
    VOLTAGE_MONITOR_KV_PER_VOLT,
)
from rbl.config.load_calibration_store import save_measurement
from rbl.hardware.ac_metrics import fundamental, phase_difference_deg
from rbl.hardware.amp_monitor import ma_unclamped, monitor_to_kv
from rbl.hardware.funcgen_safety import _AMP_GAIN, peak_status
from rbl.hardware.load_model import admittance_from_fundamentals, capacitance_from_charge
from rbl.services.amp_drive import AmpDrive
from rbl.services.calibration_writer import now_iso

log = logging.getLogger(__name__)

# --- Mode A: impedance sweep -------------------------------------------------
MODE_A_FREQ_LADDER_HZ = [200.0, 300.0, 450.0, 650.0, 950.0, 1400.0, 2000.0, 2500.0, 3000.0]
MODE_A_TARGET_MA = 4.0     # middle of the 2-6 mA band Section 2.3 asks for
MODE_A_SETTLE_S = 1.0
MODE_A_COLLECT_S = 1.0

# --- Mode B: DC leakage vs voltage -------------------------------------------
MODE_B_LADDER_KV = [0.5, 1.0, 2.0, 3.0, 4.0, 5.0]
MODE_B_DWELL_S = 10.0                    # >= 10 s for ~1.4 uA sensitivity (Section 1.8)
MODE_B_LEAK_THRESHOLD_UA_DEFAULT = 50.0  # abort the ladder above this

# --- Mode C: charge integral --------------------------------------------------
MODE_C_FREQ_HZ = 10.0
MODE_C_PEAK_KV = 1.0
MODE_C_STREAM_S = 5.0        # collection window before post-processing edges
MODE_C_EDGE_WINDOW_PRE_S = 200e-6
MODE_C_EDGE_WINDOW_POST_S = 2e-3
MODE_C_MIN_EDGES = 20        # short of the plan's 100, but enough for a real number

GUI_REFRESH_HZ = 10.0   # matches labjack_stream_config.GUI_REFRESH_HZ


class Mode(Enum):
    A = "A"
    B = "B"
    C = "C"


class _State(Enum):
    IDLE = auto()
    SETTLE = auto()
    COLLECT = auto()
    DONE = auto()
    ABORTING = auto()


@dataclass
class _Step:
    freq_hz: float
    peak_kv: float
    settle_s: float
    collect_s: float


class LoadCharacterizer(QObject):
    progress = Signal(int, int, str)     # done, total, label
    point_measured = Signal(dict)
    finished = Signal(str)               # CSV path, possibly ""
    error = Signal(str)

    def __init__(self, funcgen_map: dict, load_condition, parent=None,
                 output_dir=None, ramp_engine=None, pressure_provider=None):
        """
        funcgen_map: {amp_label: (DG1022Z, channel_int)}.
        load_condition: calibration_config.LoadCondition — tagged on every
            stored result (Section 2.4): the app must be able to tell a
            DISCONNECTED sweep from an ON_PLATES one apart at a glance.
        ramp_engine: optional RampEngine for Mode B's ladder; a private one
            is constructed if not given.
        pressure_provider: optional callable() -> pressure in torr, recorded
            alongside each Mode B point (Section 2.3). Returns NaN if absent.
        """
        super().__init__(parent)
        self._map = funcgen_map
        self._load_condition = load_condition
        self._drive = AmpDrive(funcgen_map, max_kv=AMP_MAX_KV, log_prefix="[LDC]")
        self._ramp_engine = ramp_engine
        self._pressure_provider = pressure_provider or (lambda: float("nan"))
        self._output_dir = output_dir

        self._mode: Mode = None
        self._amp_label: str = None
        self._state = _State.IDLE
        self._steps: list = []
        self._step_index = 0
        self._points: list = []
        self._c_est_pf = CAL_LOAD_CAP_PF
        self._collect_windows: dict = {}
        self._last_sample_period = None
        self._leak_threshold_ua = MODE_B_LEAK_THRESHOLD_UA_DEFAULT
        self._csv_rows: list = []

        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.timeout.connect(self._on_settle_elapsed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_mode_a(self, amp_label: str, freq_list: list = None) -> None:
        self._start_common(Mode.A, amp_label)
        freqs = freq_list or MODE_A_FREQ_LADDER_HZ
        self._c_est_pf = CAL_LOAD_CAP_PF
        self._steps = [
            _Step(freq_hz=f, peak_kv=self._mode_a_amplitude_for(f),
                  settle_s=MODE_A_SETTLE_S, collect_s=MODE_A_COLLECT_S)
            for f in freqs
        ]
        self._advance()

    def start_mode_b(self, amp_label: str, ladder_kv: list = None,
                      leak_threshold_ua: float = None) -> None:
        self._start_common(Mode.B, amp_label)
        self._leak_threshold_ua = (leak_threshold_ua if leak_threshold_ua is not None
                                    else MODE_B_LEAK_THRESHOLD_UA_DEFAULT)
        self._steps = [
            _Step(freq_hz=0.0, peak_kv=v, settle_s=0.0, collect_s=MODE_B_DWELL_S)
            for v in (ladder_kv or MODE_B_LADDER_KV)
        ]
        self._advance()

    def start_mode_c(self, amp_label: str) -> None:
        self._start_common(Mode.C, amp_label)
        self._steps = [_Step(freq_hz=MODE_C_FREQ_HZ, peak_kv=MODE_C_PEAK_KV,
                              settle_s=1.0, collect_s=MODE_C_STREAM_S)]
        self._advance()

    def abort(self) -> None:
        if self._state in (_State.IDLE, _State.DONE):
            return
        self._state = _State.ABORTING
        self._settle_timer.stop()
        self._finish(aborted=True)

    def on_window(self, payload: dict) -> None:
        """Wire to Beamline.raw_window_ready, same contract as
        CalibrationRunner.on_window: the raw, unconverted per-AIN waveform."""
        sp = payload.get("sample_period")
        if sp:
            self._last_sample_period = float(sp)
        if self._state != _State.COLLECT:
            return
        channels = payload.get("channels", {})
        for ain in self._collect_windows:
            entry = channels.get(ain)
            if entry and "waveform" in entry:
                self._collect_windows[ain].append(np.asarray(entry["waveform"], dtype=float))
        self._collect_window_count += 1
        target = self._current_step().collect_s * GUI_REFRESH_HZ
        if self._collect_window_count >= max(1, round(target)):
            self._on_collect_complete()

    # ------------------------------------------------------------------
    # Shared step machinery
    # ------------------------------------------------------------------

    def _start_common(self, mode: Mode, amp_label: str) -> None:
        if amp_label not in self._map:
            raise ValueError(f"unknown amp label {amp_label!r}")
        self._mode = mode
        self._amp_label = amp_label
        self._step_index = 0
        self._points = []
        self._csv_rows = []
        self._state = _State.IDLE

    def _current_step(self) -> _Step:
        return self._steps[self._step_index]

    def _advance(self) -> None:
        if self._step_index >= len(self._steps):
            self._finish(aborted=False)
            return
        step = self._current_step()
        self.progress.emit(self._step_index, len(self._steps),
                            f"{self._mode.value} {self._amp_label} step {self._step_index + 1}/{len(self._steps)}")
        self._command_step(step)
        ain_v = AMP_CHANNEL_MAP[self._amp_label]["voltage"]
        ain_i = AMP_CHANNEL_MAP[self._amp_label]["current"]
        self._collect_windows = {ain_v: [], ain_i: []}
        self._collect_window_count = 0
        self._state = _State.SETTLE
        self._settle_timer.start(max(1, int(step.settle_s * 1000)))

    def _command_step(self, step: _Step) -> None:
        if self._mode in (Mode.A, Mode.C):
            status, peak = peak_status("Sine", step.peak_kv * 2.0 * 1000.0 / _AMP_GAIN, 0.0)
            if status == "block":
                raise RuntimeError(f"amplitude {step.peak_kv} kV at {step.freq_hz} Hz blocked by peak interlock")
            self._drive.command_sine(self._amp_label, step.peak_kv, step.freq_hz)
        else:
            if self._ramp_engine is not None:
                self._drive.attach_ramp_engine(self._ramp_engine)
                self._drive.command_dc_ramped(self._amp_label, step.peak_kv)
            else:
                self._drive.command_dc(self._amp_label, step.peak_kv)

    def _on_settle_elapsed(self) -> None:
        self._state = _State.COLLECT

    def _on_collect_complete(self) -> None:
        try:
            step = self._current_step()
            if self._mode == Mode.A:
                point = self._finish_mode_a_point(step)
            elif self._mode == Mode.B:
                point = self._finish_mode_b_point(step)
            else:
                point = self._finish_mode_c_point(step)
        except Exception as e:
            self._print_err(f"point at step {self._step_index}: {e}")
            self.error.emit(str(e))
            self._finish(aborted=True)
            return

        self._points.append(point)
        self.point_measured.emit(point)
        self._csv_rows.append(point)

        if self._mode == Mode.B and point.get("leak_ua", 0.0) > self._leak_threshold_ua:
            log.warning("load_characterizer: leakage %.2f uA exceeds threshold %.2f uA — aborting ladder",
                        point["leak_ua"], self._leak_threshold_ua)
            self._finish(aborted=True)
            return

        self._step_index += 1
        self._advance()

    # ------------------------------------------------------------------
    # Mode A — impedance sweep
    # ------------------------------------------------------------------

    def _mode_a_amplitude_for(self, freq_hz: float) -> float:
        """V_pk so drawn current lands near MODE_A_TARGET_MA, given the
        current best capacitance estimate. Clamped through ac_max_peak_kv()
        and the peak interlock before use — no exceptions (Section 2.3)."""
        import math
        c_farad = self._c_est_pf * 1e-12
        if freq_hz <= 0 or c_farad <= 0:
            return 0.0
        v_pk_kv = (MODE_A_TARGET_MA * 1e-3) / (2 * math.pi * freq_hz * c_farad) / 1000.0
        ceiling = min(CAL_MAX_KV, ac_max_peak_kv(freq_hz, load_pf=self._c_est_pf))
        return max(0.0, min(v_pk_kv, ceiling))

    def _finish_mode_a_point(self, step: _Step) -> dict:
        ain_v = AMP_CHANNEL_MAP[self._amp_label]["voltage"]
        ain_i = AMP_CHANNEL_MAP[self._amp_label]["current"]
        fs = 1.0 / self._last_sample_period if self._last_sample_period else 0.0
        v_wave = np.concatenate(self._collect_windows[ain_v]) if self._collect_windows[ain_v] else np.array([])
        i_wave = np.concatenate(self._collect_windows[ain_i]) if self._collect_windows[ain_i] else np.array([])

        v_amp, v_phase = fundamental(v_wave, fs, step.freq_hz) if v_wave.size else (float("nan"), float("nan"))
        i_amp, i_phase = fundamental(i_wave, fs, step.freq_hz) if i_wave.size else (float("nan"), float("nan"))
        v_fund_kv = monitor_to_kv(v_amp)
        i_fund_ma = ma_unclamped(i_amp) if i_amp == i_amp else float("nan")
        phase_deg = phase_difference_deg(i_phase, v_phase)

        result = admittance_from_fundamentals(i_fund_ma, v_fund_kv, phase_deg, step.freq_hz)
        if result["c_pf"] == result["c_pf"] and result["c_pf"] > 0:   # not NaN
            self._c_est_pf = result["c_pf"]   # refine for the next frequency

        return {
            "mode": "A", "amp_label": self._amp_label, "freq_hz": step.freq_hz,
            "commanded_peak_kv": step.peak_kv, "v_fund_kv": v_fund_kv,
            "i_fund_ma": i_fund_ma, "phase_deg": phase_deg,
            "c_pf": result["c_pf"], "g_us": result["g_us"],
            "loss_tangent": result["loss_tangent"],
            "load_condition": self._load_condition.value,
            "timestamp_iso": now_iso(),
        }

    # ------------------------------------------------------------------
    # Mode B — DC leakage vs voltage
    # ------------------------------------------------------------------

    def _finish_mode_b_point(self, step: _Step) -> dict:
        ain_i = AMP_CHANNEL_MAP[self._amp_label]["current"]
        i_wave = np.concatenate(self._collect_windows[ain_i]) if self._collect_windows[ain_i] else np.array([])
        if i_wave.size == 0:
            mean_ma, sem_ma = float("nan"), float("nan")
        else:
            # Vectorised mA conversion — ma_unclamped() is written for a
            # single scalar (its NaN check is math.isnan), so it is applied
            # here as the linear scale factor it actually is rather than
            # element-by-element.
            i_ma_wave = i_wave * CURRENT_MONITOR_MA_PER_VOLT
            mean_ma = float(np.mean(i_ma_wave))
            sem_ma = float(np.std(i_ma_wave) / np.sqrt(i_wave.size))
        return {
            "mode": "B", "amp_label": self._amp_label, "commanded_kv": step.peak_kv,
            "mean_current_ma": mean_ma, "leak_ua": abs(mean_ma) * 1000.0,
            "stderr_ua": sem_ma * 1000.0,
            "pressure_torr": self._pressure_provider(),
            "load_condition": self._load_condition.value,
            "timestamp_iso": now_iso(),
        }

    # ------------------------------------------------------------------
    # Mode C — charge integral cross-check
    # ------------------------------------------------------------------

    def _finish_mode_c_point(self, step: _Step) -> dict:
        ain_v = AMP_CHANNEL_MAP[self._amp_label]["voltage"]
        ain_i = AMP_CHANNEL_MAP[self._amp_label]["current"]
        v_wave = np.concatenate(self._collect_windows[ain_v]) if self._collect_windows[ain_v] else np.array([])
        i_wave = np.concatenate(self._collect_windows[ain_i]) if self._collect_windows[ain_i] else np.array([])
        dt = self._last_sample_period or 0.0

        c_values = []
        if v_wave.size > 2 and dt > 0:
            # Vectorised conversion — see _finish_mode_b_point's note on why
            # ma_unclamped()/monitor_to_kv() aren't applied elementwise here.
            v_kv = v_wave * VOLTAGE_MONITOR_KV_PER_VOLT
            i_ma = (i_wave * CURRENT_MONITOR_MA_PER_VOLT if i_wave.size == v_wave.size
                    else np.full_like(v_wave, np.nan))
            # Edge detection on the (known) commanded square wave: a jump in
            # voltage of at least half the commanded swing. Phase cannot be
            # predicted over SCPI (Section 1.9), so edges are found from the
            # MEASURED trace, never assumed from a commanded phase.
            threshold_kv = step.peak_kv * 0.5
            diffs = np.diff(v_kv)
            edge_idx = np.flatnonzero(np.abs(diffs) > threshold_kv) + 1
            n_pre = max(1, int(MODE_C_EDGE_WINDOW_PRE_S / dt))
            n_post = max(1, int(MODE_C_EDGE_WINDOW_POST_S / dt))
            for idx in edge_idx:
                lo, hi = idx - n_pre, idx + n_post
                if lo < 0 or hi >= i_ma.size:
                    continue
                baseline = float(np.mean(i_ma[max(0, idx - 2 * n_pre):idx]))
                delta_v_kv = float(v_kv[min(idx + n_post, v_kv.size - 1)] - v_kv[max(idx - n_pre, 0)])
                if delta_v_kv == 0:
                    continue
                c_pf = capacitance_from_charge(i_ma[lo:hi], dt, baseline, delta_v_kv)
                if c_pf == c_pf:   # not NaN
                    c_values.append(c_pf)

        n_edges = len(c_values)
        c_pf_mean = float(np.mean(c_values)) if n_edges else float("nan")
        c_pf_std = float(np.std(c_values)) if n_edges else float("nan")
        return {
            "mode": "C", "amp_label": self._amp_label, "n_edges": n_edges,
            "c_pf_mean": c_pf_mean, "c_pf_std": c_pf_std,
            "load_condition": self._load_condition.value,
            "timestamp_iso": now_iso(),
        }

    # ------------------------------------------------------------------
    # Completion / persistence
    # ------------------------------------------------------------------

    def _finish(self, aborted: bool) -> None:
        self._state = _State.DONE if not aborted else _State.IDLE
        self._settle_timer.stop()
        try:
            self._drive.zero_and_off_all()
        except Exception:
            pass

        csv_path = self._write_csv() if self._csv_rows else ""

        if self._mode == Mode.A and not aborted and self._points:
            c_values = [p["c_pf"] for p in self._points if p["c_pf"] == p["c_pf"]]
            g_values = [p["g_us"] for p in self._points if p["g_us"] == p["g_us"]]
            if c_values:
                save_measurement(
                    self._amp_label, c_pf=float(np.mean(c_values)),
                    g_us=float(np.mean(g_values)) if g_values else 0.0,
                    load_condition=self._load_condition.value, method="impedance_sweep",
                )
        elif self._mode == Mode.C and not aborted and self._points:
            point = self._points[-1]
            if point["c_pf_mean"] == point["c_pf_mean"]:
                save_measurement(
                    self._amp_label, c_pf=point["c_pf_mean"], g_us=0.0,
                    load_condition=self._load_condition.value, method="charge_integral",
                )

        self.finished.emit(csv_path)

    def _write_csv(self):
        if self._output_dir is None:
            return ""
        from pathlib import Path
        out_dir = Path(self._output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"load_char_{self._mode.value}_{self._amp_label}_{time.strftime('%Y%m%dT%H%M%S')}.csv"
        fieldnames = sorted({k for row in self._csv_rows for k in row})
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self._csv_rows)
        return str(path)

    @staticmethod
    def _print_err(msg: str):
        print(f"[LDC] ERROR: {msg}")
        log.error(msg)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from rbl.config.calibration_config import LoadCondition

    app = QApplication.instance() or QApplication([])

    class _FakeGen:
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

    fake = _FakeGen()
    fmap = {"X+": (fake, 1), "X-": (fake, 2), "Y+": (fake, 1), "Y-": (fake, 2)}
    lc = LoadCharacterizer(fmap, LoadCondition.ON_PLATES)

    points = []
    lc.point_measured.connect(points.append)
    finished = []
    lc.finished.connect(finished.append)

    lc.start_mode_a("X+", freq_list=[1000.0])
    assert lc._state == _State.SETTLE
    lc._on_settle_elapsed()
    assert lc._state == _State.COLLECT

    # Synthetic 1200 pF capacitor response at the commanded amplitude/frequency.
    fs = 50_000.0
    step = lc._current_step()
    n = int(fs * step.collect_s)
    t = np.arange(n) / fs
    v_pk_v = step.peak_kv * 1000.0   # plate volts
    # Payloads carry RAW MONITOR VOLTS (the unconverted ADC reading), not
    # physical units — 1 V monitor = 1 kV plate (voltage) or 10 mA (current).
    v_raw_wave = (v_pk_v / 1000.0) * np.sin(2 * np.pi * step.freq_hz * t)          # = kV numerically
    i_ma_physical = (2 * np.pi * step.freq_hz * (1200e-12) * v_pk_v
                      * np.cos(2 * np.pi * step.freq_hz * t) * 1e3)
    i_raw_wave = i_ma_physical / 10.0                                              # monitor volts

    windows = max(1, round(step.collect_s * GUI_REFRESH_HZ))
    chunk = n // windows
    for w in range(windows):
        payload = {
            "sample_period": 1.0 / fs,
            "channels": {
                "AIN13": {"waveform": v_raw_wave[w*chunk:(w+1)*chunk]},
                "AIN12": {"waveform": i_raw_wave[w*chunk:(w+1)*chunk]},
            },
        }
        lc.on_window(payload)

    assert points, "expected a Mode A point to be measured"
    p = points[0]
    assert abs(p["c_pf"] - 1200.0) < 50.0, p
    print(f"[OK] Mode A recovers C = {p['c_pf']:.1f} pF from a synthetic 1200 pF load")
    assert finished
    print("[OK] Mode A sweep with one frequency finishes cleanly")

    # --- Mode B: leakage ladder, abort on threshold -------------------------
    lc_b = LoadCharacterizer(fmap, LoadCondition.ON_PLATES)
    b_points = []
    lc_b.point_measured.connect(b_points.append)
    b_finished = []
    lc_b.finished.connect(b_finished.append)
    lc_b.start_mode_b("X+", ladder_kv=[1.0, 2.0, 3.0], leak_threshold_ua=50.0)
    lc_b._on_settle_elapsed()

    def _feed_mode_b(mean_ma, n_windows=2, n=1000):
        step = lc_b._current_step()
        for _ in range(max(1, round(step.collect_s * GUI_REFRESH_HZ))):
            payload = {"sample_period": 1e-4,
                       "channels": {"AIN12": {"waveform": np.full(100, mean_ma / 10.0)}}}
            lc_b.on_window(payload)

    _feed_mode_b(0.0)     # healthy: near-zero leakage at 1 kV
    assert b_points and abs(b_points[0]["leak_ua"]) < 1.0
    print(f"[OK] Mode B healthy point: leak={b_points[0]['leak_ua']:.3f} uA")

    lc_b._on_settle_elapsed()
    _feed_mode_b(0.01)    # 10 uA — still healthy at 2 kV
    assert len(b_points) == 2
    print("[OK] Mode B ladder advances past a healthy point")

    lc_b._on_settle_elapsed()
    _feed_mode_b(5.0)     # 5 mA = 5000 uA >> 50 uA threshold -> discharge onset
    assert b_finished, "expected the ladder to abort on a leakage excursion"
    assert len(b_points) == 3
    print(f"[OK] Mode B aborts the ladder on leakage excursion: "
          f"{b_points[-1]['leak_ua']:.1f} uA > {lc_b._leak_threshold_ua} uA threshold")

    # --- Mode C: charge integral recovers a known capacitance ---------------
    lc_c = LoadCharacterizer(fmap, LoadCondition.ON_PLATES)
    c_points = []
    lc_c.point_measured.connect(c_points.append)
    lc_c.start_mode_c("X+")
    lc_c._on_settle_elapsed()

    fs_c = 1_000_000.0
    step_c = lc_c._current_step()
    n_c = int(fs_c * step_c.collect_s)
    period_samples = int(fs_c / MODE_C_FREQ_HZ)
    v_square_kv = MODE_C_PEAK_KV * np.sign(np.sin(2 * np.pi * MODE_C_FREQ_HZ * np.arange(n_c) / fs_c))
    # Exponential current spikes at each edge, integrating to C * deltaV.
    c_true_pf = 1200.0
    delta_v_v = 2 * MODE_C_PEAK_KV * 1000.0
    q_true_c = c_true_pf * 1e-12 * delta_v_v
    tau_s = 20e-6
    i_ma = np.zeros(n_c)
    edge_positions = np.arange(period_samples // 2, n_c, period_samples // 2)
    for k, pos in enumerate(edge_positions):
        sign = 1.0 if (k % 2 == 0) else -1.0
        width = int(10 * tau_s * fs_c)
        idx = np.arange(pos, min(pos + width, n_c))
        peak_a = q_true_c / tau_s
        i_ma[idx] += sign * (peak_a * np.exp(-(idx - pos) / (tau_s * fs_c))) * 1e3
    v_raw_c = v_square_kv
    i_raw_c = i_ma / 10.0

    windows_c = max(1, round(step_c.collect_s * GUI_REFRESH_HZ))
    chunk_c = n_c // windows_c
    for w in range(windows_c):
        payload = {"sample_period": 1.0 / fs_c,
                   "channels": {"AIN13": {"waveform": v_raw_c[w*chunk_c:(w+1)*chunk_c]},
                                "AIN12": {"waveform": i_raw_c[w*chunk_c:(w+1)*chunk_c]}}}
        lc_c.on_window(payload)

    assert c_points, "expected a Mode C point"
    pc = c_points[0]
    assert pc["n_edges"] > 0, pc
    print(f"[OK] Mode C found {pc['n_edges']} edges, C = {pc['c_pf_mean']:.1f} "
          f"+/- {pc['c_pf_std']:.1f} pF (true {c_true_pf} pF)")

    print("\n[OK] load_characterizer self-test passed")
