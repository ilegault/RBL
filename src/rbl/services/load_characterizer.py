"""
load_characterizer.py
Phase 1: per-channel load capacitance and leakage conductance, measured on
demand instead of assumed from a single global guess.

WHY THIS EXISTS
---------------
The old global capacitance guess was a single number, hand-derived once from
two AC sweeps. It could not tell the four channels apart, and it cannot answer whether
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
  Clamp test - a triangle at a fixed amplitude with the frequency raised step
           by step until the voltage fundamental falls below 95 % of the
           command: the first such step's current fundamental is the clamp
           current at which this amplifier stops following its input.
  Mode C - Charge integral voltage ladder. A low-frequency square wave
           stepped through MODE_C_LADDER_KV (0.5 to 5 kV), reporting C and the
           edge spike (peak, duration, charge) and the leakage between edges
           at every rung; the headline C is the mean over the rungs. Driven
           as a SQUARE wave (it was commanded as a sine, which has no edges
           for the analysis to find). The
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

THE MODE C LADDER STOPS ON ANY OF FOUR RULES
--------------------------------------------
Characterization pushes toward the limits on purpose, and its data is only
valid while the amplifier follows its input, so (docs/adr/0006: characterization
and calibration runs keep their aborts) the ladder ends when:
  hard_trip      stays at the monitor rail (HARD_TRIP_RAIL_S, 5 ms) on any
                 window including SETTLE (a dead short; an edge charging the
                 load leaves the rail in under 1.5 ms);
  c_changed      a rung's C differs from the first rung's by more than 10 %
                 (the load is not a linear capacitor here: incipient discharge);
  leakage        current between edges above the Mode B threshold (50 uA);
  not_following  measured swing under 90 % of the commanded 2 x rung_kv, or no
                 edges at all (the amplifier is limiting).
Before each rung the HV interlock must permit that voltage at the present
pressure (unknown pressure blocks: no data is not good vacuum), rule
"interlock". An ended ladder keeps the rungs it completed and the rule in its
result file, and writes no headline C. A square edge charging the load pulls the
monitor to the rail for under 1.5 ms; holding the rail for 5 ms is a dead short,
not capacitive inrush.

WHAT IS KEPT
------------
A finished or aborted Mode A / Mode C run is written as a characterization
result (rbl/config/characterization_history.py): one file per run, never
overwritten, tagged with the SERIAL of the amplifier the current assignment
puts on that plate ("unassigned" if none), the load condition, the method and
the time. The old single-record store (load_calibration_store) is no longer
written: it overwrote the previous measurement and knew nothing about which
physical amplifier produced it. An aborted run is kept with `aborted: true`
and no capacitance - the abort is a finding - and is never returned as the
newest result. Mode B (leakage) writes only its CSV.

SAFETY
------
Every commanded amplitude is clamped through `ac_max_peak_kv()` and
`funcgen_safety.peak_status()` before it reaches the hardware — no
exceptions, matching Section 2.3's explicit requirement. DC ladder steps go
through the Phase 4 ramp engine rather than a step, per Section 2.3 Mode B.
"""
import csv
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from rbl.config import amplifier_assignments, characterization_history
from rbl.config.calibration_config import (
    CAL_MAX_KV,
    HARD_TRIP_RAIL_S,
    SIZING_ASSUMPTION_PF,
    ac_max_peak_kv,
    resolve_load_pf,
)
from rbl.config.hardware_config import (
    AMP_CHANNEL_MAP,
    AMP_MAX_KV,
    CURRENT_MONITOR_MA_PER_VOLT,
    VOLTAGE_MONITOR_KV_PER_VOLT,
)
from rbl.hardware.ac_metrics import fundamental, phase_difference_deg
from rbl.hardware.amp_monitor import RailTracker, is_at_rail, ma_unclamped, monitor_to_kv
from rbl.hardware.funcgen_safety import _AMP_GAIN, peak_status
from rbl.hardware.hv_interlock import interlock_status
from rbl.hardware.load_model import admittance_from_fundamentals, capacitance_from_charge
from rbl.hardware.regulation import regulation_ratio
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
# One run steps through these rungs (peak kV of the +/- square wave): if C is
# really independent of voltage it reads the same at every rung, and a change
# with voltage means discharge or corona - the reason to climb rather than
# measure once at 1 kV.
MODE_C_LADDER_KV = [0.5, 1.0, 2.0, 3.0, 4.0, 5.0]
# An edge whose current stays above half its peak for less than this is faster
# than the monitor and sampling resolve: its measured peak is only a lower
# bound on the true one (its charge, an integral, is still right).
EDGE_LOWER_BOUND_US = 100.0
# --- Clamp test: where does the amplifier stop following its input? ------------
# A triangle at a fixed amplitude, frequency raised step by step. The current an
# amplifier can supply is limited (LIMIT mode clamps it and the output stops
# following the input), and the current a capacitive load draws grows with
# frequency, so the first frequency at which the output falls short of the
# command is where the real limit sits - measured, not read from the manual.
CLAMP_FREQ_LADDER_HZ = [250, 500, 750, 1000, 1500, 2000, 2500, 3000, 4000, 5000]
# measured / commanded voltage fundamental below this = no longer following.
CLAMP_RATIO_THRESHOLD = 0.95
# The fundamental of a symmetric triangle of peak V is (8 / pi^2) V. The voltage
# monitor's lock-in fundamental must be compared with THAT, not with the peak:
# against the peak a perfectly healthy amplifier reads 0.81 and the test would
# "clamp" at the first step.
_TRIANGLE_FUNDAMENTAL_FRACTION = 8.0 / (math.pi ** 2)

# --- Mode C ladder abort rules (see LoadCharacterizer._ladder_rule) ----------
# A rung's C may differ from the first rung's by at most this fraction; more
# means the load is not a linear capacitor at this voltage (incipient discharge).
MODE_C_MAX_C_CHANGE = 0.10
# The measured swing must reach this fraction of the commanded 2 x rung_kv; less
# means the amplifier stopped following its input (it is limiting), and a
# capacitance computed from that rung would be a number from a broken drive.
MODE_C_MIN_SWING_FRACTION = 0.90
MODE_C_STREAM_S = 5.0        # collection window before post-processing edges
MODE_C_EDGE_WINDOW_PRE_S = 200e-6
MODE_C_EDGE_WINDOW_POST_S = 2e-3
MODE_C_MIN_EDGES = 20        # short of the plan's 100, but enough for a real number

GUI_REFRESH_HZ = 10.0   # matches labjack_stream_config.GUI_REFRESH_HZ


class Mode(Enum):
    A = "A"
    B = "B"
    C = "C"
    CLAMP = "CLAMP"


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
                 output_dir=None, ramp_engine=None, pressure_provider=None,
                 now_fn=None):
        """
        funcgen_map: {amp_label: (DG1022Z, channel_int)}.
        load_condition: calibration_config.LoadCondition — tagged on every
            stored result (Section 2.4): the app must be able to tell a
            DISCONNECTED sweep from an ON_PLATES one apart at a glance.
        ramp_engine: optional RampEngine for Mode B's ladder; a private one
            is constructed if not given.
        pressure_provider: optional callable() -> pressure in torr, recorded
            alongside each Mode B point (Section 2.3). Returns NaN if absent.
        now_fn: optional callable() -> datetime for the time stamped on the
            result file; defaults to the local, timezone-aware now. Tests
            pass a fixed time.
        """
        super().__init__(parent)
        self._map = funcgen_map
        self._load_condition = load_condition
        self._drive = AmpDrive(funcgen_map, max_kv=AMP_MAX_KV, log_prefix="[LDC]")
        self._ramp_engine = ramp_engine
        self._pressure_provider = pressure_provider or (lambda: float("nan"))
        self._output_dir = output_dir
        self._now_fn = now_fn or (lambda: datetime.now().astimezone())

        self._mode: Mode = None
        self._amp_label: str = None
        self._state = _State.IDLE
        self._steps: list = []
        self._step_index = 0
        self._points: list = []
        self._c_est_pf = SIZING_ASSUMPTION_PF
        self._collect_windows: dict = {}
        self._last_sample_period = None
        self._leak_threshold_ua = MODE_B_LEAK_THRESHOLD_UA_DEFAULT
        self._csv_rows: list = []
        self._abort_rule: str | None = None   # why the run ended early, if it did
        self._abort_rung_kv: float | None = None
        self._clamp_result: dict | None = None
        self._rail_tracker = RailTracker()

        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.timeout.connect(self._on_settle_elapsed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_mode_a(self, amp_label: str, freq_list: list = None) -> None:
        self._start_common(Mode.A, amp_label)
        freqs = freq_list or MODE_A_FREQ_LADDER_HZ
        # The first amplitude is sized from this plate's newest measured
        # capacitance if there is one, else the named sizing assumption.
        self._c_est_pf = resolve_load_pf(None, amp_label, self._now_fn())
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

    def start_clamp_test(self, amp_label: str, peak_kv: float = 1.0,
                         freq_list: list = None) -> None:
        """Triangle at `peak_kv`, frequency stepped up until the output stops
        following. Stops at the first step whose voltage-fundamental ratio is
        below CLAMP_RATIO_THRESHOLD; that step's current fundamental is the
        clamp current."""
        self._start_common(Mode.CLAMP, amp_label)
        self._clamp_result = None
        self._steps = [
            _Step(freq_hz=float(f), peak_kv=peak_kv,
                  settle_s=MODE_A_SETTLE_S, collect_s=MODE_A_COLLECT_S)
            for f in (freq_list or CLAMP_FREQ_LADDER_HZ)
        ]
        self._advance()

    def start_mode_c(self, amp_label: str, ladder_kv: list = None) -> None:
        self._start_common(Mode.C, amp_label)
        rungs = ladder_kv or MODE_C_LADDER_KV
        self._steps = [_Step(freq_hz=MODE_C_FREQ_HZ, peak_kv=kv,
                              settle_s=1.0, collect_s=MODE_C_STREAM_S)
                       for kv in rungs]
        self._advance()

    @property
    def clamp_result(self):
        """After a clamp test: {"reached", "clamp_ma", "clamp_freq_hz", "peak_kv", "at_rail"},
        else None. `reached` False means the ratio never fell below the threshold.
        `at_rail` is True when any raw current sample touched the monitor rail (>=9.9 V)."""
        return self._clamp_result

    @property
    def abort_rule(self):
        """Why the run ended early ("hard_trip", "c_changed", "leakage",
        "not_following", "interlock", "operator", "error"), or None."""
        return self._abort_rule

    def abort(self) -> None:
        if self._state in (_State.IDLE, _State.DONE):
            return
        self._state = _State.ABORTING
        self._settle_timer.stop()
        self._finish(aborted=True, rule="operator")

    def on_window(self, payload: dict) -> None:
        """Wire to Beamline.raw_window_ready, same contract as
        CalibrationRunner.on_window: the raw, unconverted per-AIN waveform."""
        sp = payload.get("sample_period")
        if sp:
            self._last_sample_period = float(sp)
        # The hard trip is checked on EVERY window of a ladder, SETTLE included:
        # the inrush from stepping to a new voltage lands during SETTLE, the one
        # window collection throws away.
        if (self._mode in (Mode.C, Mode.CLAMP)
                and self._state in (_State.SETTLE, _State.COLLECT)
                and self._hard_tripped(payload)):
            self._finish(aborted=True, rule="hard_trip",
                         rung_kv=self._current_step().peak_kv)
            return
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
        self._abort_rule = None
        self._abort_rung_kv = None
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
        if self._mode == Mode.C and not self._interlock_permits(step.peak_kv):
            self._finish(aborted=True, rule="interlock", rung_kv=step.peak_kv)
            return
        self.progress.emit(
            self._step_index, len(self._steps),
            f"{self._mode.value} {self._amp_label} "
            f"step {self._step_index + 1}/{len(self._steps)}")
        self._command_step(step)
        ain_v = AMP_CHANNEL_MAP[self._amp_label]["voltage"]
        ain_i = AMP_CHANNEL_MAP[self._amp_label]["current"]
        self._rail_tracker.reset()
        self._collect_windows = {ain_v: [], ain_i: []}
        self._collect_window_count = 0
        self._state = _State.SETTLE
        self._settle_timer.start(max(1, int(step.settle_s * 1000)))

    def _command_step(self, step: _Step) -> None:
        if self._mode in (Mode.A, Mode.C, Mode.CLAMP):
            shape = {Mode.A: "Sine", Mode.C: "Square", Mode.CLAMP: "Triangle"}[self._mode]
            status, peak = peak_status(shape, step.peak_kv * 2.0 * 1000.0 / _AMP_GAIN, 0.0)
            if status == "block":
                raise RuntimeError(
                    f"amplitude {step.peak_kv} kV at {step.freq_hz} Hz blocked by peak interlock")
            # Mode C is a charge integral across SHARP edges, so it is driven
            # with a square wave; a sine has no edges for the analysis to find.
            if self._mode == Mode.A:
                self._drive.command_sine(self._amp_label, step.peak_kv, step.freq_hz)
            elif self._mode == Mode.CLAMP:
                self._drive.command_triangle(self._amp_label, step.peak_kv, step.freq_hz)
            else:
                self._drive.command_square(self._amp_label, step.peak_kv, step.freq_hz)
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
            elif self._mode == Mode.CLAMP:
                point = self._finish_clamp_point(step)
            else:
                point = self._finish_mode_c_point(step)
        except Exception as e:
            self._print_err(f"point at step {self._step_index}: {e}")
            self.error.emit(str(e))
            self._finish(aborted=True, rule="error")
            return

        self._points.append(point)
        self.point_measured.emit(point)
        self._csv_rows.append(point)

        if self._mode == Mode.B and point.get("leak_ua", 0.0) > self._leak_threshold_ua:
            log.warning(
                "load_characterizer: leakage %.2f uA exceeds threshold %.2f uA — aborting ladder",
                point["leak_ua"], self._leak_threshold_ua)
            self._finish(aborted=True, rule="leakage")
            return

        if self._mode == Mode.CLAMP:
            ratio = point["regulation_ratio"]
            if ratio != ratio:
                # No voltage reading at all: neither "following" nor "clamped".
                self._finish(aborted=True, rule="no_data", rung_kv=step.peak_kv)
                return
            if ratio < CLAMP_RATIO_THRESHOLD:
                self._clamp_result = {
                    "reached": True, "clamp_ma": point["i_fund_ma"],
                    "clamp_freq_hz": step.freq_hz, "peak_kv": step.peak_kv,
                    "at_rail": point["at_rail"]}
                self._finish(aborted=False)
                return

        if self._mode == Mode.C:
            rule = self._ladder_rule(point)
            if rule:
                log.warning("load_characterizer: Mode C ladder ended at %.3g kV: %s",
                            step.peak_kv, rule)
                self._finish(aborted=True, rule=rule, rung_kv=step.peak_kv)
                return

        self._step_index += 1
        self._advance()

    def _finish_clamp_point(self, step: _Step) -> dict:
        ain_v = AMP_CHANNEL_MAP[self._amp_label]["voltage"]
        ain_i = AMP_CHANNEL_MAP[self._amp_label]["current"]
        fs = 1.0 / self._last_sample_period if self._last_sample_period else 0.0
        v_wave = (np.concatenate(self._collect_windows[ain_v])
                  if self._collect_windows[ain_v] else np.array([]))
        i_wave = (np.concatenate(self._collect_windows[ain_i])
                  if self._collect_windows[ain_i] else np.array([]))
        v_amp, _vp = (fundamental(v_wave, fs, step.freq_hz)
                      if v_wave.size else (float("nan"), float("nan")))
        i_amp, _ip = (fundamental(i_wave, fs, step.freq_hz)
                      if i_wave.size else (float("nan"), float("nan")))
        v_fund_kv = monitor_to_kv(v_amp)
        i_fund_ma = ma_unclamped(i_amp) if i_amp == i_amp else float("nan")
        commanded_fund_kv = step.peak_kv * _TRIANGLE_FUNDAMENTAL_FRACTION
        at_rail = bool(np.any(is_at_rail(i_wave))) if i_wave.size else False
        return {
            "mode": "CLAMP", "amp_label": self._amp_label, "freq_hz": step.freq_hz,
            "commanded_peak_kv": step.peak_kv,
            "v_fund_kv": v_fund_kv, "i_fund_ma": i_fund_ma,
            "regulation_ratio": regulation_ratio(v_fund_kv, commanded_fund_kv),
            "at_rail": at_rail,
            "load_condition": self._load_condition.value,
            "timestamp_iso": now_iso(),
        }

    def _ladder_rule(self, point: dict):
        """The first abort rule a finished rung trips, or None.

        In order: c_changed, leakage, not_following (hard_trip is checked per
        window in on_window, interlock before each rung in _advance). The first
        matching rule is the one reported.
        """
        c_first = self._points[0]["c_pf_mean"]
        c = point["c_pf_mean"]
        if (len(self._points) > 1 and c_first == c_first and c == c
                and c_first > 0
                and abs(c - c_first) / c_first > MODE_C_MAX_C_CHANGE):
            return "c_changed"
        leak = point["inter_edge_leak_ua"]
        if leak == leak and leak > self._leak_threshold_ua:
            return "leakage"
        swing = point["measured_swing_kv"]
        # No edges at all means no swing was measured: the amplifier did not
        # produce the commanded swing, whatever the reason.
        if swing != swing or swing < MODE_C_MIN_SWING_FRACTION * 2.0 * point["rung_kv"]:
            return "not_following"
        return None

    def _hard_tripped(self, payload: dict) -> bool:
        """True if the driven channel stays at the rail for HARD_TRIP_RAIL_S.

        Feeds the raw (signed, un-abs'd) window to RailTracker. A railed
        monitor (>= ~9.9 V) held for 5 ms or more indicates a dead short,
        not ordinary capacitive edge inrush which leaves the rail in under
        1.5 ms.
        """
        ain = AMP_CHANNEL_MAP[self._amp_label]["current"]
        entry = (payload.get("channels") or {}).get(ain)
        wave = None if entry is None else entry.get("waveform")
        if wave is None or len(wave) == 0:
            return False
        arr = np.asarray(wave, dtype=float)
        dt_s = payload.get("sample_period") or ((1.0 / GUI_REFRESH_HZ) / arr.size)
        return self._rail_tracker.feed(arr, dt_s) >= HARD_TRIP_RAIL_S

    def _interlock_permits(self, rung_kv: float) -> bool:
        """May this rung's voltage be commanded at the present pressure?

        A pressure the provider cannot give (NaN: gauge absent or stale) is
        UNKNOWN, not good: no data is not good vacuum.
        """
        pressure = float(self._pressure_provider())
        status, reason = interlock_status(
            pressure, rung_kv, pressure_known=math.isfinite(pressure))
        if status != "ok":
            log.warning("load_characterizer: HV interlock refuses %.3g kV: %s",
                        rung_kv, reason)
        return status == "ok"

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
        v_wave = (np.concatenate(self._collect_windows[ain_v])
                  if self._collect_windows[ain_v] else np.array([]))
        i_wave = (np.concatenate(self._collect_windows[ain_i])
                  if self._collect_windows[ain_i] else np.array([]))

        v_amp, v_phase = (fundamental(v_wave, fs, step.freq_hz)
                           if v_wave.size else (float("nan"), float("nan")))
        i_amp, i_phase = (fundamental(i_wave, fs, step.freq_hz)
                           if i_wave.size else (float("nan"), float("nan")))
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
        i_wave = (np.concatenate(self._collect_windows[ain_i])
                  if self._collect_windows[ain_i] else np.array([]))
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
        v_wave = (np.concatenate(self._collect_windows[ain_v])
                  if self._collect_windows[ain_v] else np.array([]))
        i_wave = (np.concatenate(self._collect_windows[ain_i])
                  if self._collect_windows[ain_i] else np.array([]))
        dt = self._last_sample_period or 0.0

        c_values: list = []
        swings: list = []
        peaks: list = []
        durations: list = []
        charges: list = []
        edge_at_rail = False
        leak_ua = float("nan")
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
            edge_idx = [int(i) for i in np.flatnonzero(np.abs(diffs) > threshold_kv) + 1]
            n_pre = max(1, int(MODE_C_EDGE_WINDOW_PRE_S / dt))
            n_post = max(1, int(MODE_C_EDGE_WINDOW_POST_S / dt))
            between_edges = np.ones(i_ma.size, dtype=bool)
            for idx in edge_idx:
                between_edges[max(0, idx - n_pre):idx + n_post] = False
            for idx in edge_idx:
                lo, hi = idx - n_pre, idx + n_post
                if lo < 0 or hi >= i_ma.size:
                    continue
                # The baseline comes from the stretch BEFORE the integration
                # window, not the stretch just before the detected voltage
                # jump: the current onset can lead that jump by a sample or
                # two, and the first sample of an edge is its largest. One such
                # sample in the baseline average shifted C by about a third.
                base_lo = max(0, idx - 2 * n_pre)
                if lo - base_lo < 1:
                    continue
                baseline = float(np.mean(i_ma[base_lo:lo]))
                # Not reflowed: splitting this expression across lines changes
                # how mypy resolves the two ndarray.__getitem__ overloads and
                # doubles a pre-existing stub-typing error (see PR #32).
                delta_v_kv = float(v_kv[min(idx + n_post, v_kv.size - 1)] - v_kv[max(idx - n_pre, 0)])  # noqa: E501
                if delta_v_kv == 0:
                    continue
                c_pf = capacitance_from_charge(i_ma[lo:hi], dt, baseline, delta_v_kv)
                if c_pf == c_pf:   # not NaN
                    c_values.append(c_pf)
                    swings.append(abs(delta_v_kv))
                    peak, duration_s, charge_c = self._edge_spike(i_ma[lo:hi] - baseline, dt)
                    peaks.append(peak)
                    durations.append(duration_s)
                    charges.append(charge_c)
                    if bool(np.any(is_at_rail(i_wave[lo:hi]))):
                        edge_at_rail = True
            if between_edges.any():
                leak_ua = float(np.mean(np.abs(i_ma[between_edges]))) * 1e3

        n_edges = len(c_values)
        c_pf_mean = float(np.mean(c_values)) if n_edges else float("nan")
        c_pf_std = float(np.std(c_values)) if n_edges else float("nan")

        def mean_or_nan(values):
            return float(np.mean(values)) if values else float("nan")

        edge_duration_us = mean_or_nan(durations) * 1e6
        return {
            "mode": "C", "amp_label": self._amp_label, "n_edges": n_edges,
            "c_pf_mean": c_pf_mean, "c_pf_std": c_pf_std,
            "rung_kv": step.peak_kv,
            "measured_swing_kv": mean_or_nan(swings),
            "edge_peak_ma": mean_or_nan(peaks),
            "edge_duration_us": edge_duration_us,
            "edge_charge_uc": mean_or_nan(charges) * 1e6,
            "inter_edge_leak_ua": leak_ua,
            # NaN duration (no edges) is not "short": there is no peak to qualify.
            "edge_peak_is_lower_bound": bool(edge_duration_us < EDGE_LOWER_BOUND_US),
            "edge_at_rail": bool(edge_at_rail),
            "load_condition": self._load_condition.value,
            "timestamp_iso": now_iso(),
        }

    @staticmethod
    def _edge_spike(above_baseline_ma, dt: float):
        """(peak mA, time above half the peak in s, charge in coulombs) for one edge.

        `above_baseline_ma` is the current across one edge with the pre-edge
        baseline removed. The duration is the contiguous stretch around the
        peak that stays above half of it, so noise elsewhere in the window
        cannot lengthen it.
        """
        a = np.abs(above_baseline_ma)
        k = int(np.argmax(a))
        peak = float(a[k])
        half = peak / 2.0
        lo = k
        while lo > 0 and a[lo - 1] >= half:
            lo -= 1
        hi = k
        while hi < a.size - 1 and a[hi + 1] >= half:
            hi += 1
        charge_c = abs(float(np.trapezoid(above_baseline_ma * 1e-3, dx=dt)))
        return peak, (hi - lo + 1) * dt, charge_c

    # ------------------------------------------------------------------
    # Completion / persistence
    # ------------------------------------------------------------------

    def _finish(self, aborted: bool, rule: str = None, rung_kv: float = None) -> None:
        if aborted:
            self._abort_rule = rule
            self._abort_rung_kv = rung_kv
        self._state = _State.DONE if not aborted else _State.IDLE
        self._settle_timer.stop()
        try:
            self._drive.zero_and_off_all()
        except Exception:
            pass

        csv_path = self._write_csv() if self._csv_rows else ""

        self._write_characterization_result(aborted)

        self.finished.emit(csv_path)

    def _write_characterization_result(self, aborted: bool) -> None:
        """Keep this run as a characterization result file (never overwritten).

        Modes A and C produce capacitance results; Mode B (leakage) does not.
        An aborted run is written too, with `aborted: true` and no `c_pf`: an
        abort is itself a finding, and `characterization_history` never returns
        it as the newest result, so nothing plans from it.
        """
        if self._mode == Mode.A:
            method = "impedance_sweep"
        elif self._mode == Mode.C:
            method = "charge_integral_ladder"
        elif self._mode == Mode.CLAMP:
            method = "clamp_test"
        else:
            return

        values: dict = {}
        if not aborted:
            if self._mode == Mode.CLAMP:
                # clamp_ma is None (JSON null) when the ratio never fell below
                # the threshold: a finding in its own right - the clamp lies
                # above the top of the frequency ladder.
                reached = self._clamp_result or {
                    "reached": False, "clamp_ma": None, "clamp_freq_hz": None,
                    "peak_kv": self._steps[-1].peak_kv,
                    "at_rail": False}
                self._clamp_result = reached
                values = {"clamp_ma": reached["clamp_ma"],
                          "clamp_freq_hz": reached["clamp_freq_hz"],
                          "peak_kv": reached["peak_kv"],
                          "clamp_at_rail": reached.get("at_rail", False)}
            elif self._mode == Mode.A:
                c_values = [p["c_pf"] for p in self._points if p["c_pf"] == p["c_pf"]]
                g_values = [p["g_us"] for p in self._points if p["g_us"] == p["g_us"]]
                if not c_values:
                    return
                values = {"c_pf": float(np.mean(c_values)),
                          "g_us": float(np.mean(g_values)) if g_values else 0.0}
            else:
                # The headline C is the mean over the rungs that found edges.
                # Mode C does not measure conductance, so none is invented.
                rung_c = [p["c_pf_mean"] for p in self._points
                          if p["c_pf_mean"] == p["c_pf_mean"]]      # drop NaN
                if not rung_c:
                    return
                values = {"c_pf": float(np.mean(rung_c))}

        now = self._now_fn()
        assignment = amplifier_assignments.current_assignment(now) or {}
        result = {
            "plate_position": self._amp_label,
            "amplifier_serial": assignment.get(
                self._amp_label, characterization_history.UNASSIGNED),
            "load_condition": self._load_condition.value,
            "method": method,
            "values": values,
            "points": list(self._points),
        }
        if aborted:
            result["aborted"] = True
            result["abort_rule"] = self._abort_rule
            if self._abort_rung_kv is not None:
                result["abort_rung_kv"] = self._abort_rung_kv
        try:
            characterization_history.write_result(result, now)
        except Exception as e:      # the run's CSV and signals must still complete
            self._print_err(f"could not write the characterization result: {e}")
            self.error.emit(f"Could not save the characterization result: {e}")

    def _write_csv(self):
        if self._output_dir is None:
            return ""
        from pathlib import Path
        out_dir = Path(self._output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%S")
        path = out_dir / f"load_char_{self._mode.value}_{self._amp_label}_{stamp}.csv"
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
    v_raw_wave = (v_pk_v / 1000.0) * np.sin(2 * np.pi * step.freq_hz * t)   # = kV numerically
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
    v_square_kv = MODE_C_PEAK_KV * np.sign(
        np.sin(2 * np.pi * MODE_C_FREQ_HZ * np.arange(n_c) / fs_c))
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
