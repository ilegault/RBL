"""
calibration_runner.py
Non-blocking, signal/timer-driven state machine that sweeps each deflection
channel through a bipolar DC ladder and records what all 8 EEL5000 monitors
report back at every setpoint.

WHY NOT A FOR-LOOP + time.sleep
--------------------------------
LabJackStreamWorker.window_ready is delivered on the Qt main thread. A
blocking loop of the form

    for setpoint in points:                     # WRONG
        gen.set_waveform(ch, "DC", 0, 0, setpoint, 0)
        time.sleep(1.5)
        record(latest_window)

starves the event loop for the duration of the sleep, so window_ready simply
cannot be delivered while it runs — not "delivered late", not delivered at
all. What gets averaged is stale or nothing, and it presents as "the LabJack
froze". QApplication.processEvents() sprinkled into the loop does not fix
this; it just makes the starvation intermittent instead of total.

Every state transition in this file happens from a slot (on_window) or a
QTimer callback. Nothing here blocks, ever.

STATES
------
IDLE -> [ZERO] -> SETTLE -> COLLECT -> RECORD -> ([ZERO] | SETTLE | DONE),
plus ABORTING.

  ZERO     optional, only in return-to-zero mode (see CAL_ZERO_DWELL_S).
           Commands the driven channel to 0 V and dwells, so the next rung is
           approached from rest rather than from the rung below it. The
           interlock stays armed throughout — an over-current at 0 V
           commanded is a fault whatever the ladder is doing.
  SETTLE   entering it commands the new setpoint (driven channel to its
           value, the other three to 0.0) and arms a QTimer for
           CAL_SETTLE_S. Every window that arrives while here is discarded.
  COLLECT  the settle timer fired. Windows arriving now are accumulated
           (their raw "waveform" arrays, per AIN) until CAL_COLLECT_S worth
           has arrived (window count, since every window_ready payload
           spans exactly 1/GUI_REFRESH_HZ seconds of real time regardless of
           stream profile).
  RECORD   synchronous: build and emit one row per AIN (8 rows) from the
           accumulated windows, then advance the sweep cursor and either
           re-enter SETTLE for the next point or finish.

funcgen_map
-----------
{"X+": (gen, 1), "X-": (gen, 2), "Y+": (gen, 1), "Y-": (gen, 2), ...} —
amp label -> (live DG1022Z instance, channel number). Built by the caller
from the app's existing funcgen<->amp mapping (rbl/hardware/funcgen_safety.py
CHANNEL_ROLE, combined with Beamline.dg_a / dg_b); this runner never
constructs a DG1022Z, never calls openS(), and never touches the LabJack
handle — it only ever calls methods on the driver instances it is handed.

SAFETY
------
  * Every commanded value is clamped to +/-CAL_MAX_KV here, independently of
    the +/-MAX_GEN_VOLTS clamp DG1022Z.set_waveform already applies.
  * Every SCPI write this runner issues goes through DG1022Z.set_waveform /
    output_on / output_off, which themselves query :SYSTem:ERRor? after
    every write (see funcgen_driver.py's _write_checked) — a clean pyvisa
    return does not mean the instrument accepted the command. This runner
    relies on that existing contract rather than re-implementing it, and
    prints every command and every failure to the terminal in addition to
    emitting `error` for the GUI.
  * On finish, on abort, and on any exception: zero volts + outputs OFF on
    all four channels first (unconditionally, best-effort per channel), then
    the pre-run waveform settings are restored — but output is left OFF
    regardless of what the snapshot says, so a restore can never
    re-energize a plate on its own.
  * An atexit hook, registered at construction, repeats the zero+off step
    (never the restore) so a killed interpreter still doesn't leave voltage
    standing — same house pattern as Beamline._emergency_labjack_shutdown.
"""
import atexit
import logging
import math
import random
import time
from dataclasses import dataclass
from enum import Enum, auto

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from rbl.config.calibration_config import (
    CAL_COLLECT_S, CAL_MAX_KV, CAL_PASSES, CAL_SETTLE_S,
    CAL_AC_FREQ_HZ, CAL_AC_SETTLE_S, CAL_AC_COLLECT_S,
    CAL_AC_TRIP_MA, CAL_LOAD_CAP_PF, ac_max_peak_kv, ac_peak_current_ma,
    ac_shape_k,
    CAL_TRIP_HARD_MA, CAL_TRIP_MIN_DURATION_S, CAL_TRIP_CONSEC_WINDOWS,
    CAL_TRIP_BLANK_WINDOWS, CAL_ZERO_DWELL_S,
    DRIFT_LOG_INTERVAL_S, DRIFT_MAX_ATTENDED_H, DRIFT_MAX_UNATTENDED_H,
    LoadCondition, sweep_points, ac_sweep_points,
)
from rbl.config.hardware_config import AMP_AIN_NAMES, AMP_CHANNEL_MAP, AMP_LABELS
from rbl.config.labjack_stream_config import GUI_REFRESH_HZ
from rbl.hardware.ac_metrics import ac_metrics
from rbl.hardware.amp_monitor import (
    monitor_to_kv, monitor_to_ma, ma_to_monitor, ma_unclamped,
)
from rbl.hardware.funcgen_safety import _AMP_GAIN
from rbl.hardware.regulation import regulation_ratio, classify
from rbl.services.amp_drive import AmpDrive
from rbl.services.calibration_writer import (
    config_snapshot, git_commit_hash, new_run_id, now_iso,
)

log = logging.getLogger(__name__)

# No window for this long is treated as a fault (LabJack dropout, dead
# stream) rather than silently recording hours of nothing. Only armed in
# drift mode — a sweep's SETTLE gaps are expected and bounded by
# CAL_SETTLE_S, so a sweep never needs this.
WATCHDOG_S = 5.0


class _State(Enum):
    IDLE     = auto()
    ZERO     = auto()   # return-to-zero dwell before a rung (optional)
    SETTLE   = auto()
    COLLECT  = auto()
    RECORD   = auto()
    DONE     = auto()
    ABORTING = auto()


@dataclass
class _StepPoint:
    driven_amp:   str
    pass_type:    str
    pass_index:   int
    point_index:  int
    commanded_kv: float


class CalibrationRunner(QObject):
    """Drives the sweep. Owns no hardware handles; opens nothing."""

    # (done, total, label) — done/total count setpoints, not AIN rows.
    progress = Signal(int, int, str)
    # One emission per AIN per setpoint (8 per setpoint).
    row_recorded = Signal(dict)
    # CSV path (possibly "" if no writer was supplied).
    finished = Signal(str)
    # User-facing failures. Terminal output happens unconditionally (see
    # module docstring); this signal is for the GUI on top of that.
    error = Signal(str)
    # Amp label whose (current, voltage) pair the stream should switch to.
    # The runner owns no LabJack handle, so it asks; the tab routes this to
    # beamline.set_stream_pair. Emitted only between setpoints, never during
    # a capture — switching the scan list restarts the stream.
    pair_change_requested = Signal(str)
    # (amp_label, measured_ma, limit_ma) — the over-current interlock fired.
    # Separate from `error` so the GUI can present it as a safety event
    # rather than a malfunction: an expected outcome of probing an envelope.
    overcurrent = Signal(str, float, float)
    # Emitted when a zero-dwell begins (return-to-zero mode only).
    # Carries the dwell duration in seconds so the GUI can show a countdown.
    dwell_started = Signal(float)

    def __init__(self, funcgen_map: dict, load_condition: LoadCondition,
                 parent=None, writer=None, run_id: str = None):
        super().__init__(parent)
        self._funcgen_map   = funcgen_map
        self._load_condition = load_condition
        self._writer         = writer   # optional CalibrationWriter-like collaborator
        self._run_id_override = run_id  # lets a caller keep writer/runner run_id in sync
        self.operator_note   = ""       # set by the GUI before start_sweep()

        self._state    = _State.IDLE
        self._mode     = "sweep"        # "sweep" | "drift"
        self._channels: list[str] = list(AMP_LABELS)   # driven channels
        self._sequence: list[_StepPoint] = []
        self._seq_idx  = 0
        self._seed     = None
        self._orig_state: dict = {}     # amp_label -> get_state() snapshot
        self._current_driven: str = None  # tracks which amp is being driven

        self._collect_windows: dict = {}   # ain -> [np.ndarray, ...]
        self._collect_window_count = 0
        # Profile name from the last window actually received. Recorded on
        # every row so the CSV states which stream produced it, rather than
        # which stream the config would like to have produced it.
        self._last_profile: str = None
        self._windows_per_collect = max(1, round(CAL_COLLECT_S * GUI_REFRESH_HZ))

        # AC-mode frequency (applies to both ac_sweep and ac drift channels).
        self._ac_freq_hz = CAL_AC_FREQ_HZ
        # AC-mode waveform. Must track AmpDrive._command_ac's default, because
        # it decides both what gets commanded and how the ladder is capped.
        self._ac_shape = "Triangle"
        # Approach every rung from 0 V instead of from the previous rung.
        # See CAL_ZERO_DWELL_S for why this is worth an extra settle per point.
        self._return_to_zero = False
        # Dwell time at 0 V before each rung in return-to-zero mode.
        # Override via ._zero_dwell_s before calling start_sweep/start_ac_sweep.
        self._zero_dwell_s = CAL_ZERO_DWELL_S
        # When True, the zero-dwell disables the funcgen output instead of
        # commanding 0 V. The output comes back on automatically when
        # _command_setpoint calls command_dc/command_ac (which call output_on).
        self._dwell_output_off = False
        # Per-sample interval from the last window, for the fundamental-bin
        # measurement. Taken from the stream rather than assumed, since the
        # rate differs by profile and the first window after a restart is short.
        self._last_sample_period: float = None

        # Over-current interlock. Armed for every driven mode; the limit is
        # the EEL5000 continuous rating, not its 4 ms transient rating.
        self._trip_ma          = CAL_AC_TRIP_MA
        self._trip_hard_ma     = CAL_TRIP_HARD_MA
        self._tripped          = False   # latches so one event aborts once
        self._use_pair_profile = True    # stream only the driven amp's pair
        # Soft-limit qualification state. Both reset at every setpoint change.
        self._trip_consec      = 0       # consecutive qualifying windows
        self._trip_blank_left  = 0       # windows still exempt from the soft limit

        # Drift-mode-only state.
        self._drift_setpoint_kv = 0.0
        self._drift_end_t       = None
        self._drift_windows_per_log = max(1, round(DRIFT_LOG_INTERVAL_S * GUI_REFRESH_HZ))
        # amp_label -> {"freq_hz": float, "amplitude_kv": float, "phase_deg": float}
        # for AC drift channels; absent means DC.
        self._drift_ac_channels: dict[str, dict] = {}

        self._run_id  = None
        self._t_start = None

        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.timeout.connect(self._on_settle_elapsed)

        self._watchdog_timer = QTimer(self)
        self._watchdog_timer.setSingleShot(True)
        self._watchdog_timer.timeout.connect(self._on_watchdog_timeout)

        self._drive = AmpDrive(funcgen_map, max_kv=CAL_MAX_KV, log_prefix="[CAL]")
        atexit.register(self._atexit_shutdown)

    # ------------------------------------------------------------------
    # Public slots
    # ------------------------------------------------------------------

    def start_sweep(self, channels=None, return_to_zero: bool = False):
        """Start a DC ladder sweep.

        Parameters
        ----------
        channels : list[str] | None
            Subset of AMP_LABELS to sweep, e.g. ["X+", "Y-"].
            None (default) means all four channels.
        """
        if self._state != _State.IDLE:
            self._print_err("start_sweep: a run is already in progress")
            return

        self._channels = list(channels) if channels else list(AMP_LABELS)
        self._mode    = "sweep"
        self._return_to_zero = bool(return_to_zero)
        # Clear per-run interlock and driven-channel state.  _current_driven
        # must start as None so the first step is treated as a channel change
        # and therefore requests its pair; _tripped must clear so a previous
        # run's trip cannot suppress this run's interlock.
        self._tripped         = False
        self._current_driven  = None
        self._run_id  = self._run_id_override or new_run_id()
        self._t_start = time.monotonic()
        self._seed    = random.SystemRandom().randrange(2 ** 31)
        self._sequence = self._build_sequence()
        self._seq_idx  = 0

        self._orig_state = self._drive.snapshot_all()

        if self._writer is not None:
            try:
                self._writer.update_metadata(
                    load_condition=self._load_condition.value,
                    seed=self._seed,
                    operator_note=self.operator_note,
                    funcgen_states=dict(self._orig_state),
                    config_snapshot=config_snapshot(),
                    git_commit_hash=git_commit_hash(),
                )
            except Exception as e:
                self._print_err(f"writer.update_metadata: {e}")

        log.info("[CAL] start_sweep: run_id=%s %d setpoints, load=%s, seed=%s",
                 self._run_id, len(self._sequence), self._load_condition.value, self._seed)
        self._enter_step()

    def start_ac_sweep(self, channels=None, freq_hz: float = None,
                       shape: str = "Triangle",
                       return_to_zero: bool = False):
        """AC sweep at freq_hz, amplitude ramped 0 → max → 0.

        Uses the same state machine as the DC sweep but commands a periodic
        waveform instead of DC, and uses longer settle/collect windows.

        Parameters
        ----------
        channels : list[str] | None
            Subset of AMP_LABELS to sweep.  None means all four channels.
        freq_hz : float | None
            Drive frequency in Hz.  Defaults to CAL_AC_FREQ_HZ when None.
        shape : str
            "Triangle" (default, matching AmpDrive), "Sine" or "Square".
            Recorded in the sidecar and used to cap the ladder — peak current
            for a capacitive load depends on the shape's steepest dV/dt, so
            this is not cosmetic.
        """
        if self._state != _State.IDLE:
            self._print_err("start_ac_sweep: a run is already in progress")
            return

        self._channels = list(channels) if channels else list(AMP_LABELS)
        self._mode    = "ac_sweep"
        self._ac_freq_hz = freq_hz if freq_hz is not None else CAL_AC_FREQ_HZ
        self._ac_shape   = shape or "Triangle"
        self._return_to_zero = bool(return_to_zero)
        # Clear per-run interlock and driven-channel state.  _current_driven
        # must start as None so the first step is treated as a channel change
        # and therefore requests its pair; _tripped must clear so a previous
        # run's trip cannot suppress this run's interlock.
        self._tripped         = False
        self._current_driven  = None
        self._run_id  = self._run_id_override or new_run_id()
        self._t_start = time.monotonic()
        self._seed    = None
        self._sequence = self._build_ac_sequence()
        self._seq_idx  = 0

        # Use AC-specific timing.
        self._windows_per_collect = max(1, round(CAL_AC_COLLECT_S * GUI_REFRESH_HZ))

        self._orig_state = self._drive.snapshot_all()

        if self._writer is not None:
            try:
                self._writer.update_metadata(
                    load_condition=self._load_condition.value,
                    operator_note=self.operator_note,
                    funcgen_states=dict(self._orig_state),
                    config_snapshot=config_snapshot(),
                    git_commit_hash=git_commit_hash(),
                    ac_freq_hz=self._ac_freq_hz,
                )
            except Exception as e:
                self._print_err(f"writer.update_metadata: {e}")

        log.info("[CAL] start_ac_sweep: run_id=%s %d setpoints, freq=%s Hz, load=%s",
                 self._run_id, len(self._sequence), self._ac_freq_hz, self._load_condition.value)
        self._enter_step()

    def start_drift(self, setpoint_kv: float, duration_h: float,
                    ac_channels: "dict[str, dict] | None" = None):
        """Hold one setpoint on all four channels and log every AIN every
        DRIFT_LOG_INTERVAL_S, for duration_h.

        Parameters
        ----------
        setpoint_kv : float
            DC setpoint for DC channels.  Also used as the default peak amplitude
            for AC channels that do not supply their own amplitude_kv.
        duration_h : float
            Run duration in hours.
        ac_channels : dict[str, dict] | None
            Per-channel AC configuration.  Keys are AMP_LABELS ("X+", "X-", …).
            Each value is a dict with the following optional keys:

              freq_hz      (float) — sine frequency; required if the channel is AC.
              amplitude_kv (float) — peak amplitude in kV; defaults to abs(setpoint_kv).
              phase_deg    (float) — phase offset in degrees; defaults to 0.0.

            Channels absent from the dict (or when ac_channels is None entirely)
            are driven as DC at setpoint_kv.

        The load-condition duration guard is enforced HERE, not only in the
        GUI: unattended running (up to DRIFT_MAX_UNATTENDED_H) is permitted
        only with the amplifier DISCONNECTED from the steerer; ON_PLATES is
        capped at DRIFT_MAX_ATTENDED_H. A future headless entry point must
        inherit this rule rather than re-derive it.
        """
        if self._state != _State.IDLE:
            self._print_err("start_drift: a run is already in progress")
            return

        max_allowed = (DRIFT_MAX_ATTENDED_H
                        if self._load_condition is LoadCondition.ON_PLATES
                        else DRIFT_MAX_UNATTENDED_H)
        if duration_h > max_allowed:
            msg = (
                f"Drift run refused: {duration_h:.2f} h exceeds the "
                f"{max_allowed:.2f} h cap for load condition "
                f"{self._load_condition.value}."
            )
            if self._load_condition is LoadCondition.ON_PLATES:
                msg += (
                    f" Runs longer than {DRIFT_MAX_ATTENDED_H:.1f} h are only "
                    "permitted with the amplifier DISCONNECTED from the steerer."
                )
            self._print_err(msg)
            self.error.emit(msg)
            return

        self._mode    = "drift"
        # Clear per-run interlock and driven-channel state.  _current_driven
        # must start as None so the first step is treated as a channel change
        # and therefore requests its pair; _tripped must clear so a previous
        # run's trip cannot suppress this run's interlock.
        self._tripped         = False
        self._current_driven  = None
        self._run_id  = self._run_id_override or new_run_id()
        self._t_start = time.monotonic()
        self._drift_setpoint_kv = max(-CAL_MAX_KV, min(CAL_MAX_KV, setpoint_kv))
        self._drift_end_t = self._t_start + duration_h * 3600.0
        self._drift_ac_channels = dict(ac_channels) if ac_channels else {}

        self._orig_state = self._drive.snapshot_all()

        if self._writer is not None:
            try:
                self._writer.update_metadata(
                    load_condition=self._load_condition.value,
                    operator_note=self.operator_note,
                    funcgen_states=dict(self._orig_state),
                    config_snapshot=config_snapshot(),
                    git_commit_hash=git_commit_hash(),
                    drift_setpoint_kv=self._drift_setpoint_kv,
                    drift_duration_h=duration_h,
                    drift_ac_channels=self._drift_ac_channels or None,
                )
            except Exception as e:
                self._print_err(f"writer.update_metadata: {e}")

        if self._drift_ac_channels:
            ac_summary = ", ".join(
                f"{a}={cfg.get('waveform', 'Triangle')[:3]}"
                f" {cfg.get('amplitude_kv', abs(self._drift_setpoint_kv)):.2f}kV"
                f"@{cfg.get('freq_hz', 0):.0f}Hz"
                f"+{cfg.get('phase_deg', 0):.0f}°"
                for a, cfg in self._drift_ac_channels.items()
            )
        else:
            ac_summary = "DC"
        log.info("[CAL] start_drift: run_id=%s setpoint=%+.3f kV duration=%.2f h load=%s ac=%s",
                 self._run_id, self._drift_setpoint_kv, duration_h,
                 self._load_condition.value, ac_summary)

        try:
            for amp in AMP_LABELS:
                cfg = self._drift_ac_channels.get(amp)
                if cfg is not None:
                    amp_kv    = cfg.get("amplitude_kv", abs(self._drift_setpoint_kv))
                    freq      = cfg.get("freq_hz", self._ac_freq_hz)
                    phase_deg = cfg.get("phase_deg", 0.0)
                    waveform  = cfg.get("waveform", "Triangle")
                    self._command_channel_ac(amp, amp_kv, freq, phase_deg, waveform)
                else:
                    self._command_channel(amp, self._drift_setpoint_kv)
        except Exception as e:
            self._print_err(f"start_drift: {e}")
            self.error.emit(str(e))
            self._finish(aborted=True)
            return

        # Enter SETTLE first so the profile switch (requested by the GUI
        # just before start_drift is called) has time to complete before we
        # start accumulating windows.  Windows that arrive during SETTLE are
        # silently discarded — they may carry the wrong channel layout from
        # the previous profile.  The watchdog is armed only after SETTLE fires
        # (in _on_settle_elapsed) so the transition time is not counted.
        self._state = _State.SETTLE
        self._collect_windows = {ain: [] for ain in AMP_AIN_NAMES}
        self._collect_window_count = 0
        self._trip_consec     = 0
        self._trip_blank_left = CAL_TRIP_BLANK_WINDOWS
        self._settle_timer.start(int(CAL_SETTLE_S * 1000))

    def abort(self):
        if self._state in (_State.IDLE, _State.DONE, _State.ABORTING):
            return
        log.info("[CAL] abort() requested")
        self._state = _State.ABORTING
        self._settle_timer.stop()
        self._finish(aborted=True)

    def _check_overcurrent(self, payload: dict) -> bool:
        """True if the driven channel over-current interlock fired this window.

        Works on the window's magnitude envelope, not its mean.  On an AC
        setpoint the mean current of a symmetric waveform is ~0 by
        construction, so a mean-based interlock would read a comfortable zero
        while the amplifier was drawing 40 mA peak every cycle.

        Two thresholds, because a short and a step transient are not the same
        event and must not be treated the same way:

        HARD (CAL_TRIP_HARD_MA) — one sample over it is enough.  Nothing in a
        well-posed sweep goes there; a short, an arc or a failing amplifier
        does, and waiting to confirm those costs hardware.

        SOFT (self._trip_ma, the continuous rating) — qualified before it
        counts.  The sweep profile streams at 50 kS/s, so one sample is 20 us
        and a bare peak test trips on excursions shorter than the amplifier's
        own 4 ms transient rating and too brief to appear on the live plot.
        A window only counts if at least CAL_TRIP_MIN_DURATION_S of samples
        are over the limit, and the interlock only fires after
        CAL_TRIP_CONSEC_WINDOWS such windows in a row.  A clean window resets
        the count.  The first CAL_TRIP_BLANK_WINDOWS windows after a setpoint
        is commanded are exempt entirely — charging 1200 pF to a new voltage
        draws inrush by definition, and that is not a fault.

        Checked on EVERY window regardless of state, including SETTLE. The
        inrush from a step change lands during SETTLE — the one window the
        collection path deliberately throws away — so a COLLECT-only interlock
        would be blind to exactly the moment most likely to trip it.
        """
        if self._tripped or not self._current_driven:
            return False
        ain = AMP_CHANNEL_MAP.get(self._current_driven, {}).get("current")
        if not ain:
            return False
        entry = (payload.get("channels") or {}).get(ain)
        if entry is None:
            return False   # this AIN is not in the active scan list
        wave = entry.get("waveform")
        if wave is None or len(wave) == 0:
            return False

        wave = np.abs(np.asarray(wave, dtype=float))
        n = wave.size
        # ma_unclamped, not monitor_to_ma: the latter returns NaN above the
        # plausibility ceiling, and NaN fails every `>` test — a dead short
        # railing the monitor would read as "no over-current".
        peak_ma = abs(ma_unclamped(float(np.nanmax(wave))))
        if math.isnan(peak_ma):
            return False   # channel not producing data this window

        # --- HARD limit: instant, unconditional -------------------------
        if peak_ma > self._trip_hard_ma:
            return self._trip(peak_ma, self._trip_hard_ma, "hard", 0.0)

        # --- SOFT limit: qualified in time, then across windows ----------
        # Blanking is consumed even when the window is clean, so the exemption
        # is a fixed number of windows after the step, not a free pass that
        # lasts until the first excursion.
        blanked = self._trip_blank_left > 0
        if blanked:
            self._trip_blank_left -= 1

        if peak_ma <= self._trip_ma:
            self._trip_consec = 0
            return False

        # Duration above the limit, from the sample count.  Prefer the exact
        # per-sample interval the worker measured at eStreamStart; the fallback
        # derives it from the window length, which is right only for a
        # full-size window.  The first window after a stream restart is
        # deliberately short (settling scans trimmed), so dividing a fixed
        # 1/GUI_REFRESH_HZ by its length would overstate dt and inflate every
        # duration measured in it.
        dt_s = payload.get("sample_period") or ((1.0 / GUI_REFRESH_HZ) / n)
        trip_v = abs(ma_to_monitor(self._trip_ma))
        # np.count_nonzero on a NaN-containing array: NaN > x is False, so
        # dropouts simply do not count toward the duration. That is the
        # conservative direction for a soft limit.
        over_s = float(np.count_nonzero(wave > trip_v)) * dt_s

        if over_s < CAL_TRIP_MIN_DURATION_S:
            # A spike, not a load. Do not advance the consecutive counter and
            # do not reset it either: a genuine over-current that straddles a
            # window boundary should not be forgiven by one marginal window.
            return False

        if blanked:
            log.warning("[CAL] over-current ignored during step blanking: %s %.1f mA peak, "
                        "%.1f ms over %.0f mA",
                        self._current_driven, peak_ma, over_s * 1e3, self._trip_ma)
            return False

        self._trip_consec += 1
        if self._trip_consec < CAL_TRIP_CONSEC_WINDOWS:
            log.warning("[CAL] over-current window %d/%d: %s %.1f mA peak, "
                        "%.1f ms over %.0f mA",
                        self._trip_consec, CAL_TRIP_CONSEC_WINDOWS,
                        self._current_driven, peak_ma, over_s * 1e3, self._trip_ma)
            return False

        return self._trip(peak_ma, self._trip_ma, "sustained", over_s)

    def _trip(self, peak_ma: float, limit_ma: float, kind: str,
              over_s: float) -> bool:
        """Stop the hardware, THEN tell anyone who is listening.

        Order matters and is the whole point of this being its own method.
        `overcurrent` is connected to a GUI slot that opens a modal dialog,
        and a modal dialog spins a nested Qt event loop — so everything after
        the emit would not run until the operator clicked OK.  Emitting first
        meant the settle timer kept firing, the state machine kept advancing,
        and the amplifier kept being commanded to the NEXT (higher) rung of
        the ladder while the "run stopped" warning sat on screen.

        So: latch, stop the timers, leave the state machine, zero and turn off
        every output — and only once the hardware is safe, emit.  By the time
        any slot runs, there is nothing left running for it to block.
        """
        self._tripped = True
        self._state = _State.ABORTING
        self._settle_timer.stop()
        self._watchdog_timer.stop()

        detail = (f", sustained {over_s * 1e3:.1f} ms" if over_s > 0 else "")
        msg = (f"OVER-CURRENT ({kind}): {self._current_driven} drew "
               f"{peak_ma:.1f} mA peak, limit {limit_ma:.1f} mA{detail} — "
               f"aborting and zeroing.")
        self._print_err(msg)

        # Hardware safe first. _shutdown zeroes every channel and turns every
        # output off; _finish then emits `finished` for the GUI.
        try:
            self._finish(aborted=True)
        finally:
            # Emitted last, and unconditionally: even if the shutdown path
            # raised, the operator must be told what happened.
            self.overcurrent.emit(self._current_driven, peak_ma, limit_ma)
            self.error.emit(msg)
        return True

    def on_window(self, payload: dict):
        # Record which profile is really live before anything else can return
        # early — a window discarded during SETTLE still tells us what the
        # stream is, and a row may be written before the next one arrives.
        profile = payload.get("profile")
        if profile:
            self._last_profile = profile
        sp = payload.get("sample_period")
        if sp:
            self._last_sample_period = float(sp)
        # Interlock first, and before the state guard: an over-current during
        # SETTLE is still an over-current.
        if self._state in (_State.ZERO, _State.SETTLE,
                           _State.COLLECT, _State.RECORD):
            try:
                if self._check_overcurrent(payload):
                    return
            except Exception as e:
                self._print_err(f"_check_overcurrent: {e}")
        if self._state != _State.COLLECT:
            return   # SETTLE discards; IDLE/DONE/ABORTING ignore stragglers
        try:
            self._accumulate(payload)
            self._collect_window_count += 1
            if self._mode == "drift":
                self._watchdog_timer.start(int(WATCHDOG_S * 1000))   # reset
                if self._collect_window_count >= self._drift_windows_per_log:
                    self._record_drift_row()
            else:
                if self._collect_window_count >= self._windows_per_collect:
                    self._record_and_advance()
        except Exception as e:
            self._print_err(f"on_window: {e}")
            self.error.emit(str(e))
            self._finish(aborted=True)

    # ------------------------------------------------------------------
    # Sequence construction
    # ------------------------------------------------------------------

    def _build_sequence(self) -> list:
        """One channel driven at a time; for each selected channel, all passes."""
        seq = []
        pass_counter = 0
        for amp in self._channels:
            for pass_type in CAL_PASSES:
                pts = sweep_points(pass_type, seed=self._seed)
                for point_idx, kv in enumerate(pts):
                    seq.append(_StepPoint(
                        driven_amp=amp, pass_type=pass_type,
                        pass_index=pass_counter, point_index=point_idx,
                        commanded_kv=kv,
                    ))
                pass_counter += 1
        return seq

    def _build_ac_sequence(self) -> list:
        """AC sweep: amplitude ramp for each selected channel (up then down).

        The ladder is capped at the amplitude the current limit allows at this
        run's frequency (see calibration_config.ac_max_peak_kv).  Above roughly
        530 Hz on a ~1200 pF load that cap bites before the amplifier's 5 kV
        rating does, so an uncapped high-frequency run would simply climb until
        the interlock stopped it — producing a trip instead of a measurement.
        """
        seq = []
        # The cap depends on the SHAPE, not just the frequency: peak current is
        # set by the steepest dV/dt, and a triangle's is 4fV while a sine's is
        # 2*pi*fV. Passing the shape stops a triangle run being capped as if it
        # were a sine, which held the ladder ~57% below the real current wall.
        ceiling = ac_max_peak_kv(self._ac_freq_hz, shape=self._ac_shape)
        pts = ac_sweep_points(ceiling)
        top = max(pts) if pts else 0.0
        if ceiling < CAL_MAX_KV - 1e-9:
            log.info("[CAL] AC ladder capped at %.1f kV pk for %.0f Hz %s "
                     "(current limit %.0f mA, assumed C=%.0f pF -> %.1f mA at top rung); "
                     "amplifier rating is %.1f kV. "
                     "This is a PREDICTION from an assumed capacitance; the "
                     "recorded current is measured, not derived from it.",
                     top, self._ac_freq_hz, self._ac_shape, self._trip_ma,
                     CAL_LOAD_CAP_PF, ac_peak_current_ma(self._ac_freq_hz, top, shape=self._ac_shape),
                     CAL_MAX_KV)
        for pass_idx, amp in enumerate(self._channels):
            for point_idx, peak_kv in enumerate(pts):
                seq.append(_StepPoint(
                    driven_amp=amp, pass_type="ac",
                    pass_index=pass_idx, point_index=point_idx,
                    commanded_kv=peak_kv,
                ))
        return seq

    # ------------------------------------------------------------------
    # SETTLE
    # ------------------------------------------------------------------

    def _enter_step(self):
        """Begin the next sequence point, via a zero dwell if enabled.

        In return-to-zero mode every rung is approached from rest instead of
        from the rung below it, so the amplifier never changes internal
        operating range while the load is already charged. See
        CAL_ZERO_DWELL_S. Drift has a single setpoint and no ladder, so it
        never takes this path.
        """
        if self._seq_idx >= len(self._sequence):
            self._finish_success()
            return
        if not self._return_to_zero or self._mode == "drift":
            self._enter_settle()
            return

        step = self._sequence[self._seq_idx]
        self._state = _State.ZERO
        try:
            # Silence the driven channel for the dwell. Other channels are
            # handled in _enter_settle on a driven-channel change, as before.
            if self._dwell_output_off:
                self._drive.output_off(step.driven_amp)
            else:
                self._command_channel(step.driven_amp, 0.0)
        except Exception as e:
            self._print_err(f"_enter_step zero: {e}")
            self.error.emit(str(e))
            self._finish(aborted=True)
            return
        # The interlock stays armed through the dwell: an over-current while
        # commanding 0 V is a fault whatever the ladder is doing.
        self._trip_consec     = 0
        self._trip_blank_left = CAL_TRIP_BLANK_WINDOWS
        self.dwell_started.emit(self._zero_dwell_s)
        self._settle_timer.start(int(self._zero_dwell_s * 1000))

    def _on_zero_elapsed(self):
        if self._state != _State.ZERO:
            return   # stale timer fire after an abort; ignore
        self._enter_settle()

    def _enter_settle(self):
        if self._seq_idx >= len(self._sequence):
            self._finish_success()
            return

        step = self._sequence[self._seq_idx]
        self._state = _State.SETTLE

        # When the driven channel changes (new pass or new amp), zero the
        # previous driven channel and set the new non-driven channels to 0.
        # This happens once per pass, not at every setpoint.
        if step.driven_amp != self._current_driven:
            try:
                for amp in AMP_LABELS:
                    if amp != step.driven_amp:
                        self._command_channel(amp, 0.0)
            except Exception as e:
                self._print_err(f"_enter_settle zero others: {e}")
                self.error.emit(str(e))
                self._finish(aborted=True)
                return
            self._current_driven = step.driven_amp
            # Re-point the stream at the newly driven amplifier's pair.  Only
            # here, on a driven-channel change, because switching the scan
            # list costs a stream stop/restart; doing it per setpoint would
            # add that cost 50+ times per pass for no benefit.  The SETTLE
            # window started below covers the restart, and windows arriving
            # during SETTLE are discarded — which is exactly right, since
            # some of them carry the previous amplifier's channel layout.
            if self._use_pair_profile:
                self.pair_change_requested.emit(step.driven_amp)

        try:
            self._command_setpoint(step.driven_amp, step.commanded_kv)
        except Exception as e:
            self._print_err(f"_enter_settle: {e}")
            self.error.emit(str(e))
            self._finish(aborted=True)
            return

        self.progress.emit(
            self._seq_idx, len(self._sequence),
            f"{step.driven_amp} {step.pass_type} {step.commanded_kv:+.2f} kV",
        )
        self._collect_windows = {ain: [] for ain in AMP_AIN_NAMES}
        self._collect_window_count = 0
        # New rung: forgive the step inrush and start the consecutive-window
        # count from zero. Set AFTER _command_setpoint so the blanking covers
        # the windows that follow the command, not ones that preceded it.
        self._trip_consec     = 0
        self._trip_blank_left = CAL_TRIP_BLANK_WINDOWS
        settle_s = CAL_AC_SETTLE_S if self._mode == "ac_sweep" else CAL_SETTLE_S
        self._settle_timer.start(int(settle_s * 1000))

    def _command_setpoint(self, driven_amp: str, commanded_kv: float):
        """Driven channel to its clamped setpoint; others zeroed once per pass.

        Non-driven channels are zeroed at the start of each pass (see
        _enter_settle) rather than re-commanded at every setpoint — sending
        SCPI writes to all four generators at every step slows the sweep and
        is unnecessary: a channel sitting at DC 0 V stays there.

        In AC mode, commanded_kv is the peak amplitude (non-negative).
        """
        if self._mode == "ac_sweep":
            self._command_channel_ac(driven_amp, commanded_kv,
                                     waveform=self._ac_shape)
        else:
            clamped = max(-CAL_MAX_KV, min(CAL_MAX_KV, commanded_kv))
            self._command_channel(driven_amp, clamped)

    def _command_channel(self, amp_label: str, value_kv: float):
        try:
            self._drive.command_dc(amp_label, value_kv)
        except Exception as e:
            self._print_err(f"{amp_label}: {e}")
            raise

    def _command_channel_ac(self, amp_label: str, peak_kv: float,
                            freq_hz: float = None, phase_deg: float = 0.0,
                            waveform: str = "Triangle"):
        """Command an AC waveform with the given peak amplitude (in kV).

        freq_hz   — defaults to self._ac_freq_hz (set once per ac_sweep run).
        phase_deg — 0 is the normal sense; 180 inverts the waveform.
        waveform  — "Triangle" (default), "Sine", or "Square".
        """
        if freq_hz is None:
            freq_hz = self._ac_freq_hz
        try:
            if waveform == "Sine":
                self._drive.command_sine(amp_label, peak_kv, freq_hz, phase_deg)
            elif waveform == "Square":
                self._drive.command_square(amp_label, peak_kv, freq_hz, phase_deg)
            else:  # Triangle (default)
                self._drive.command_triangle(amp_label, peak_kv, freq_hz, phase_deg)
        except Exception as e:
            self._print_err(f"{amp_label}: {e}")
            raise

    def _on_settle_elapsed(self):
        # One timer serves both dwells, so it dispatches on the current state.
        if self._state == _State.ZERO:
            self._on_zero_elapsed()
            return
        if self._state != _State.SETTLE:
            return   # a stale timer fire after an abort/exception; ignore
        self._state = _State.COLLECT
        if self._mode == "drift":
            # Arm watchdog now that we are actually collecting drift data.
            self._watchdog_timer.start(int(WATCHDOG_S * 1000))

    # ------------------------------------------------------------------
    # COLLECT / RECORD
    # ------------------------------------------------------------------

    def _accumulate(self, payload: dict):
        channels = payload.get("channels", {})
        for ain in AMP_AIN_NAMES:
            entry = channels.get(ain)
            if entry is None or "waveform" not in entry:
                continue
            self._collect_windows.setdefault(ain, []).append(entry["waveform"])

    def _record_and_advance(self):
        step = self._sequence[self._seq_idx]
        try:
            self._state = _State.RECORD
            self._emit_rows(step)
        except Exception as e:
            self._print_err(f"_record_and_advance: {e}")
            self.error.emit(str(e))
            self._finish(aborted=True)
            return

        self._seq_idx += 1
        self._enter_step()

    def _window_stats(self, ain: str) -> tuple:
        """(mean_v, std_v, rms_v, min_v, max_v, abs_p999_v, n_samples,
        n_windows) over every raw sample accumulated for *ain* since the last
        reset.

        ON PEAKS, AND WHY THERE ARE TWO OF THEM.  min_v and max_v are the
        single most extreme samples in the whole collect period — ONE sample
        each.  At 250 Hz with a 2 s collect that period holds 500 cycles and
        100 000 samples, so max_v is the largest of 500 peaks, chosen by an
        extreme-value statistic with no averaging behind it.  That makes it
        the most noise-sensitive number in the row: it can only ever be
        biased upward, it creeps up as the collect window lengthens (more
        samples = deeper into the noise tail), and a single 20 us ADC glitch
        moves it ~25% while the waveform itself is unchanged.

        abs_p999_v is the 99.9th percentile of |x| — the level the signal
        exceeds 0.1% of the time.  Displacing it takes 100 bad samples out of
        100 000, not one.  It is a near-exact peak estimator for every shape
        this rig drives: |x| is uniform on [0, A] for a triangle so p99.9 =
        0.999 A, and for a sine and a square it lands within 0.01% of A.

        Keep both.  abs_p999_v is what the amplitude actually was;  max_v is
        the worst single excursion, which is the right question after a trip
        and the wrong one when measuring a level.  They agree on a clean
        waveform and diverge exactly when something odd happened, so the gap
        between them is itself the diagnostic.

        std_v and rms_v are BOTH recorded because they answer different
        questions and only coincide when the mean is zero:

            rms_v = sqrt(mean(x^2))            RMS about zero — the true RMS
            std_v = sqrt(mean((x - mean)^2))   RMS about the mean — the AC part

        They are related by  rms^2 = mean^2 + std^2.  On a DC setpoint the
        signal is nearly all mean, so std_v is just the noise and rms_v is
        essentially the DC level.  On an AC setpoint the mean is ~0 by
        construction, so the two converge and either one is the waveform's RMS.
        Recording only std_v (as this did) silently discards the DC term, which
        is the wrong number the moment a waveform sits on an offset.
        """
        arrays = self._collect_windows.get(ain, [])
        n_windows = len(arrays)
        nan = float("nan")
        if not arrays:
            return nan, nan, nan, nan, nan, nan, 0, 0
        samples = np.concatenate(arrays)
        return (float(np.mean(samples)), float(np.std(samples)),
                float(np.sqrt(np.mean(np.square(samples)))),
                float(np.min(samples)), float(np.max(samples)),
                float(np.percentile(np.abs(samples), 99.9)),
                int(samples.size), n_windows)

    def _drive_freq_hz(self, amp: str) -> float:
        """Drive frequency commanded on *amp*, or 0.0 if it is being held DC.

        0.0 is the DC answer, and ac_metrics treats it as such — the
        fundamental fields come back NaN and everything else is still measured,
        so one code path serves both modes.
        """
        if self._mode == "ac_sweep":
            return float(self._ac_freq_hz)
        if self._mode == "drift":
            cfg = self._drift_ac_channels.get(amp)
            if cfg:
                return float(cfg.get("freq_hz", self._ac_freq_hz) or 0.0)
        return 0.0

    def _fundamental_stats(self, ain: str, freq_hz: float) -> tuple:
        """(fund_amp_v, fund_phase_rad, crest, n_cycles) for *ain*.

        The measurement that answers "how much current is actually flowing" on
        an AC setpoint.  Mean is ~0 there by construction, and peak is
        dominated by the monitor's noise floor, so neither reports the drive.
        Asking what the monitor is doing AT THE COMMANDED FREQUENCY rejects
        everything else — on this rig's ~1.4 mA rms current-monitor noise, a
        peak reading biases +181% where this biases +0.2%.

        Read directly off the amplifier's own monitor samples.  Nothing here
        assumes or back-solves a load capacitance.
        """
        nan = float("nan")
        arrays = self._collect_windows.get(ain, [])
        if not arrays or not self._last_sample_period:
            return nan, nan, nan, 0
        m = ac_metrics(np.concatenate(arrays),
                       1.0 / self._last_sample_period, freq_hz)
        return (m["fund_amp"], m["fund_phase"], m["crest"], m["n_cycles"])

    def _make_row(self, pass_index: int, pass_type: str, driven_amp: str,
                  commanded_kv: float, amp: str, kind: str,
                  t_elapsed: float, ts_iso: str) -> dict:
        ain = AMP_CHANNEL_MAP[amp][kind]
        (mean_v, std_v, rms_v, min_v, max_v, abs_p999_v,
         n_samples, n_windows) = self._window_stats(ain)
        gen_v = commanded_kv * 1000.0 / _AMP_GAIN

        freq_hz = self._drive_freq_hz(amp)
        fund_v, fund_phase, crest, n_cycles = self._fundamental_stats(ain, freq_hz)

        # converted_value is the headline number for this row, so on an AC
        # setpoint it must not be the mean: a symmetric drive has a mean of ~0
        # however hard the amplifier is working, which is why AC current rows
        # read as approximately nothing. Use the fundamental amplitude there —
        # measured at the commanded frequency, so noise outside that bin is
        # rejected — and say so in converted_unit rather than letting two
        # different quantities share one label.
        ac = freq_hz > 0 and math.isfinite(fund_v)
        source_v = fund_v if ac else mean_v
        if kind == "voltage":
            converted_value = monitor_to_kv(source_v)
            converted_unit  = "kV_pk_fund" if ac else "kV"
        else:
            converted_value = monitor_to_ma(source_v)
            converted_unit  = "mA_pk_fund" if ac else "mA"

        regulation_state, regulation_reason = self._regulation_state_for(
            amp, commanded_kv, freq_hz, ac)

        return {
            "run_id": self._run_id, "timestamp_iso": ts_iso,
            "t_elapsed_s": t_elapsed,
            "pass_index": pass_index, "pass_type": pass_type,
            "driven_amp": driven_amp,
            "commanded_kv": commanded_kv, "commanded_gen_v": gen_v,
            "ain": ain, "amp_label": amp, "kind": kind,
            "mean_v": mean_v, "std_v": std_v, "rms_v": rms_v,
            "min_v": min_v, "max_v": max_v, "abs_p999_v": abs_p999_v,
            # Fundamental-bin measurement at the commanded drive frequency.
            # NaN on DC setpoints, where there is no fundamental to speak of.
            "fund_v": fund_v, "fund_phase_rad": fund_phase,
            "crest": crest, "n_cycles": n_cycles,
            "n_samples": n_samples, "n_windows": n_windows,
            "converted_value": converted_value,
            "converted_unit": converted_unit,
            # The profile the stream ACTUALLY delivered, taken from the last
            # window's own payload — not the profile this feature asks for.
            # Those are different: sweeps run on CAL_SWEEP_PROFILE (AMP_PAIR)
            # while this field was hardcoded to CAL_PROFILE ("WAVEFORM"), so
            # every pair-mode sweep recorded a profile it never ran under, and
            # with it an implied 12.5 kS/s that was really 50 kS/s.
            "stream_profile": self._last_profile or "",
            # Phase 6 (regulation.py): "ok"/"current_limited"/"amp_off"/"idle"
            # for THIS amp at THIS setpoint. A row taken while the amplifier
            # was not following its commanded input is not deleted — it is
            # flagged, so the user decides whether to keep or discard it.
            "regulation_state": regulation_state,
            "regulation_reason": regulation_reason,
        }

    def _regulation_state_for(self, amp: str, commanded_kv: float,
                               freq_hz: float, ac: bool) -> tuple:
        """Joint voltage+current classification for one amp at one setpoint
        (rbl/hardware/regulation.py). Needs BOTH monitors regardless of which
        `kind` row is being built, so this reads both AINs directly rather
        than reusing whichever one `_make_row` already had in hand."""
        ain_v = AMP_CHANNEL_MAP[amp]["voltage"]
        ain_i = AMP_CHANNEL_MAP[amp]["current"]
        mean_v, *_ = self._window_stats(ain_v)
        mean_i, *_ = self._window_stats(ain_i)
        if ac:
            fund_v, _, _, _ = self._fundamental_stats(ain_v, freq_hz)
            fund_i, _, _, _ = self._fundamental_stats(ain_i, freq_hz)
            source_v = fund_v if math.isfinite(fund_v) else mean_v
            source_i = fund_i if math.isfinite(fund_i) else mean_i
        else:
            source_v, source_i = mean_v, mean_i

        measured_kv = monitor_to_kv(source_v)
        measured_ma = abs(ma_unclamped(source_i))
        if math.isnan(measured_kv) or math.isnan(measured_ma):
            return "idle", "no data collected for this amp at this setpoint"

        v_ratio = regulation_ratio(measured_kv, commanded_kv)
        return classify(v_ratio, measured_ma, self._trip_ma, commanded_kv)

    def _emit_row(self, row: dict):
        self.row_recorded.emit(row)
        if self._writer is not None:
            try:
                self._writer.write_row(row)
            except Exception as e:
                self._print_err(f"writer.write_row: {e}")

    def _emit_rows(self, step: "_StepPoint"):
        """One row per streamed monitor for this setpoint.

        A pair-mode sweep streams only the driven amplifier's two AINs, so the
        other six monitors have no samples at all.  Writing them anyway
        produced six all-NaN rows for every two real ones — 75% of a sweep CSV
        was padding that said nothing, and worse, it looked like a measurement
        that had failed rather than a channel that was never sampled.

        The driven amplifier's rows are ALWAYS written, even if empty: no data
        on the channel being driven is a fault worth seeing in the CSV, not
        something to silently omit.
        """
        t_elapsed = time.monotonic() - self._t_start
        ts_iso    = now_iso()
        for amp in AMP_LABELS:
            for kind in ("voltage", "current"):
                row = self._make_row(
                    step.pass_index, step.pass_type, step.driven_amp,
                    step.commanded_kv, amp, kind, t_elapsed, ts_iso,
                )
                if row["n_windows"] == 0 and amp != step.driven_amp:
                    continue   # not in the active scan list this pass
                self._emit_row(row)

    # ------------------------------------------------------------------
    # Drift mode
    # ------------------------------------------------------------------

    def _record_drift_row(self):
        try:
            self._state = _State.RECORD
            t_elapsed = time.monotonic() - self._t_start
            total_s   = self._drift_end_t - self._t_start
            remain_s  = max(0.0, self._drift_end_t - time.monotonic())
            if self._drift_ac_channels:
                ac_note = " | AC: " + ", ".join(
                    f"{a} {cfg.get('waveform', 'Triangle')[:3]}"
                    f" {cfg.get('amplitude_kv', abs(self._drift_setpoint_kv)):.2f}kV"
                    f"@{cfg.get('freq_hz', 0):.0f}Hz"
                    f"+{cfg.get('phase_deg', 0.0):.0f}°"
                    for a, cfg in self._drift_ac_channels.items()
                )
            else:
                ac_note = ""
            self.progress.emit(
                int(t_elapsed), int(total_s),
                f"Drift {self._drift_setpoint_kv:+.3f} kV{ac_note} — "
                f"{t_elapsed / 3600:.2f} h elapsed, {remain_s / 3600:.2f} h remaining",
            )
            ts_iso = now_iso()
            for amp in AMP_LABELS:
                for kind in ("voltage", "current"):
                    self._emit_row(self._make_row(
                        0, "drift", "ALL", self._drift_setpoint_kv,
                        amp, kind, t_elapsed, ts_iso,
                    ))
        except Exception as e:
            self._print_err(f"_record_drift_row: {e}")
            self.error.emit(str(e))
            self._finish(aborted=True)
            return

        self._collect_windows = {ain: [] for ain in AMP_AIN_NAMES}
        self._collect_window_count = 0

        if time.monotonic() >= self._drift_end_t:
            self._finish_success()
            return
        self._state = _State.COLLECT

    def _on_watchdog_timeout(self):
        if self._mode != "drift" or self._state not in (_State.COLLECT, _State.RECORD):
            return   # a stale timer fire after finish/abort; ignore
        msg = f"watchdog: no window arrived for {WATCHDOG_S:.0f}s — treating as a fault"
        self._print_err(msg)
        self.error.emit(msg)
        self._emit_fault_row(msg)
        self._finish(aborted=True)

    def _emit_fault_row(self, reason: str):
        """One row noting a fault (currently only the drift watchdog).

        The CSV schema has no dedicated "reason" column (see calibration_
        writer.CSV_COLUMNS — fixed since Phase 4); this repurposes the
        free-text `converted_unit` column to carry it on a row that is
        otherwise clearly not real data (kind="fault", every numeric stat
        NaN). Documented in docs/calibration.md.
        """
        t_elapsed = (time.monotonic() - self._t_start) if self._t_start else float("nan")
        row = {
            "run_id": self._run_id, "timestamp_iso": now_iso(),
            "t_elapsed_s": t_elapsed,
            "pass_index": -1, "pass_type": self._mode, "driven_amp": "WATCHDOG_FAULT",
            "commanded_kv": self._drift_setpoint_kv, "commanded_gen_v": float("nan"),
            "ain": "", "amp_label": "", "kind": "fault",
            "mean_v": float("nan"), "std_v": float("nan"),
            "rms_v": float("nan"),
            "min_v": float("nan"), "max_v": float("nan"),
            "abs_p999_v": float("nan"),
            "fund_v": float("nan"), "fund_phase_rad": float("nan"),
            "crest": float("nan"), "n_cycles": 0,
            "n_samples": 0, "n_windows": 0,
            "converted_value": float("nan"), "converted_unit": reason,
            "stream_profile": self._last_profile or "",
        }
        self._emit_row(row)

    # ------------------------------------------------------------------
    # Finish / abort / shutdown
    # ------------------------------------------------------------------

    def _write_run_note(self):
        """Run-level context into the JSON sidecar, not into every row.

        Mode, waveform, drive frequency and the stream profile are constant for
        a whole run, so repeating them on every row would be 50+ identical
        copies of the same four facts. They belong once, at the top, where a
        reader looks to find out what the file IS before reading what it says.
        """
        if self._writer is None:
            return
        ac = self._mode == "ac_sweep"
        note = {
            "mode": self._mode,
            "stream_profile": self._last_profile or "",
            "waveform_shape": self._ac_shape if ac else "DC",
            "drive_freq_hz": float(self._ac_freq_hz) if ac else 0.0,
            "step_approach": ("return_to_zero" if self._return_to_zero
                              else "ascending"),
            "zero_dwell_s": (self._zero_dwell_s if self._return_to_zero else 0.0),
            "zero_method": ("output_off" if self._dwell_output_off else "command_zero") if self._return_to_zero else "n/a",
            "collect_s": (CAL_AC_COLLECT_S if ac else CAL_COLLECT_S),
            "settle_s": (CAL_AC_SETTLE_S if ac else CAL_SETTLE_S),
            "trip_ma": self._trip_ma,
            "trip_hard_ma": self._trip_hard_ma,
            "current_measurement": (
                "Fundamental-bin (single-bin DFT over whole cycles) at "
                "drive_freq_hz, measured directly from the amplifier CURRENT "
                "monitor samples. converted_value carries this on AC rows "
                "(unit mA_pk_fund / kV_pk_fund) and the window mean on DC rows "
                "(unit mA / kV). No load capacitance is assumed or "
                "back-solved anywhere in the measurement path."
            ),
        }
        if ac:
            note["ladder_cap_kv"] = ac_max_peak_kv(self._ac_freq_hz,
                                                   shape=self._ac_shape)
            note["ladder_cap_basis"] = (
                f"PREDICTION ONLY, from assumed C={CAL_LOAD_CAP_PF:.0f} pF and "
                f"I_pk = {ac_shape_k(self._ac_shape):.3f}*f*C*V_pk for shape "
                f"{self._ac_shape}. Sizes the ladder before the run; never used "
                f"to interpret a measurement."
            )
        try:
            self._writer.update_metadata(run_note=note)
        except Exception as e:
            self._print_err(f"writer.update_metadata(run_note): {e}")

    def _finish_success(self):
        log.info("[CAL] sweep complete: %d setpoints", len(self._sequence))
        self._state = _State.DONE
        csv_path = self._shutdown(restore=True)
        self.finished.emit(csv_path or "")

    def _finish(self, aborted: bool):
        csv_path = self._shutdown(restore=True)
        self._state = _State.IDLE if aborted else _State.DONE
        self.finished.emit(csv_path or "")

    def _shutdown(self, restore: bool):
        """Always zero + outputs OFF on every channel, then (optionally)
        restore the pre-run waveform settings with output left OFF."""
        self._settle_timer.stop()
        self._watchdog_timer.stop()

        # Written here rather than at start so it can record the profile the
        # stream ACTUALLY ran under, which is not known until a window arrives.
        # Before the hardware shutdown and the writer close, both of which can
        # raise — the note is cheap and a file without it is harder to read.
        self._write_run_note()

        self._drive.zero_and_off_all()

        if restore:
            self._drive.restore_all(self._orig_state)

        csv_path = None
        if self._writer is not None:
            try:
                csv_path = self._writer.close()
            except Exception as e:
                self._print_err(f"writer.close: {e}")
        return csv_path

    def _atexit_shutdown(self):
        """Best-effort, never raises — same pattern as
        Beamline._emergency_labjack_shutdown. Zero + off only; no restore."""
        try:
            self._drive.zero_and_off_all()
        except Exception:
            pass

    # ------------------------------------------------------------------

    @staticmethod
    def _print_err(msg: str):
        log.error("[CAL] ERROR: %s", msg)
