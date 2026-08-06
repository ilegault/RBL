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
IDLE -> SETTLE -> COLLECT -> RECORD -> (SETTLE | DONE), plus ABORTING.

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
import random
import time
from dataclasses import dataclass
from enum import Enum, auto

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from rbl.config.calibration_config import (
    CAL_COLLECT_S, CAL_MAX_KV, CAL_PASSES, CAL_PROFILE, CAL_SETTLE_S,
    CAL_AC_FREQ_HZ, CAL_AC_SETTLE_S, CAL_AC_COLLECT_S, CAL_STEP_KV,
    DRIFT_LOG_INTERVAL_S, DRIFT_MAX_ATTENDED_H, DRIFT_MAX_UNATTENDED_H,
    LoadCondition, sweep_points, ac_sweep_points,
)
from rbl.config.hardware_config import AMP_AIN_NAMES, AMP_CHANNEL_MAP, AMP_LABELS
from rbl.config.labjack_stream_config import GUI_REFRESH_HZ
from rbl.hardware.amp_monitor import monitor_to_kv, monitor_to_ma
from rbl.hardware.funcgen_safety import _AMP_GAIN
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
        self._sequence: list[_StepPoint] = []
        self._seq_idx  = 0
        self._seed     = None
        self._orig_state: dict = {}     # amp_label -> get_state() snapshot
        self._current_driven: str = None  # tracks which amp is being driven

        self._collect_windows: dict = {}   # ain -> [np.ndarray, ...]
        self._collect_window_count = 0
        self._windows_per_collect = max(1, round(CAL_COLLECT_S * GUI_REFRESH_HZ))

        # Drift-mode-only state.
        self._drift_setpoint_kv = 0.0
        self._drift_end_t       = None
        self._drift_windows_per_log = max(1, round(DRIFT_LOG_INTERVAL_S * GUI_REFRESH_HZ))

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

    def start_sweep(self):
        if self._state != _State.IDLE:
            self._print_err("start_sweep: a run is already in progress")
            return

        self._mode    = "sweep"
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

        print(f"[CAL] start_sweep: run_id={self._run_id} "
              f"{len(self._sequence)} setpoints, "
              f"load={self._load_condition.value}, seed={self._seed}")
        self._enter_settle()

    def start_ac_sweep(self):
        """AC sweep: sine wave at CAL_AC_FREQ_HZ, amplitude ramped 0 → max → 0.

        Uses the same state machine as the DC sweep but commands a sine
        waveform instead of DC, and uses longer settle/collect windows.
        """
        if self._state != _State.IDLE:
            self._print_err("start_ac_sweep: a run is already in progress")
            return

        self._mode    = "ac_sweep"
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
                    ac_freq_hz=CAL_AC_FREQ_HZ,
                )
            except Exception as e:
                self._print_err(f"writer.update_metadata: {e}")

        print(f"[CAL] start_ac_sweep: run_id={self._run_id} "
              f"{len(self._sequence)} setpoints, "
              f"freq={CAL_AC_FREQ_HZ} Hz, "
              f"load={self._load_condition.value}")
        self._enter_settle()

    def start_drift(self, setpoint_kv: float, duration_h: float):
        """Hold one setpoint on all four channels and log every AIN every
        DRIFT_LOG_INTERVAL_S, for duration_h.

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
        self._run_id  = self._run_id_override or new_run_id()
        self._t_start = time.monotonic()
        self._drift_setpoint_kv = max(-CAL_MAX_KV, min(CAL_MAX_KV, setpoint_kv))
        self._drift_end_t = self._t_start + duration_h * 3600.0

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
                )
            except Exception as e:
                self._print_err(f"writer.update_metadata: {e}")

        print(f"[CAL] start_drift: run_id={self._run_id} "
              f"setpoint={self._drift_setpoint_kv:+.3f} kV "
              f"duration={duration_h:.2f} h load={self._load_condition.value}")

        try:
            for amp in AMP_LABELS:
                self._command_channel(amp, self._drift_setpoint_kv)
        except Exception as e:
            self._print_err(f"start_drift: {e}")
            self.error.emit(str(e))
            self._finish(aborted=True)
            return

        self._state = _State.COLLECT
        self._collect_windows = {ain: [] for ain in AMP_AIN_NAMES}
        self._collect_window_count = 0
        self._watchdog_timer.start(int(WATCHDOG_S * 1000))

    def abort(self):
        if self._state in (_State.IDLE, _State.DONE, _State.ABORTING):
            return
        print("[CAL] abort() requested")
        self._state = _State.ABORTING
        self._settle_timer.stop()
        self._finish(aborted=True)

    def on_window(self, payload: dict):
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
        """One channel driven at a time; for each channel, all passes."""
        seq = []
        pass_counter = 0
        for amp in AMP_LABELS:
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
        """AC sweep: amplitude ramp for each channel (up then down)."""
        seq = []
        pts = ac_sweep_points()
        for pass_idx, amp in enumerate(AMP_LABELS):
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
            self._command_channel_ac(driven_amp, commanded_kv)
        else:
            clamped = max(-CAL_MAX_KV, min(CAL_MAX_KV, commanded_kv))
            self._command_channel(driven_amp, clamped)

    def _command_channel(self, amp_label: str, value_kv: float):
        try:
            self._drive.command_dc(amp_label, value_kv)
        except Exception as e:
            self._print_err(f"{amp_label}: {e}")
            raise

    def _command_channel_ac(self, amp_label: str, peak_kv: float):
        """Command a sine wave with the given peak amplitude (in kV)."""
        try:
            self._drive.command_sine(amp_label, peak_kv, CAL_AC_FREQ_HZ)
        except Exception as e:
            self._print_err(f"{amp_label}: {e}")
            raise

    def _on_settle_elapsed(self):
        if self._state != _State.SETTLE:
            return   # a stale timer fire after an abort/exception; ignore
        self._state = _State.COLLECT

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
        self._enter_settle()

    def _window_stats(self, ain: str) -> tuple:
        """(mean_v, std_v, min_v, max_v, n_samples, n_windows) over every
        raw sample accumulated for *ain* since the last reset."""
        arrays = self._collect_windows.get(ain, [])
        n_windows = len(arrays)
        if not arrays:
            return float("nan"), float("nan"), float("nan"), float("nan"), 0, 0
        samples = np.concatenate(arrays)
        return (float(np.mean(samples)), float(np.std(samples)),
                float(np.min(samples)), float(np.max(samples)),
                int(samples.size), n_windows)

    def _make_row(self, pass_index: int, pass_type: str, driven_amp: str,
                  commanded_kv: float, amp: str, kind: str,
                  t_elapsed: float, ts_iso: str) -> dict:
        ain = AMP_CHANNEL_MAP[amp][kind]
        mean_v, std_v, min_v, max_v, n_samples, n_windows = self._window_stats(ain)
        gen_v = commanded_kv * 1000.0 / _AMP_GAIN
        if kind == "voltage":
            converted_value, converted_unit = monitor_to_kv(mean_v), "kV"
        else:
            converted_value, converted_unit = monitor_to_ma(mean_v), "mA"
        return {
            "run_id": self._run_id, "timestamp_iso": ts_iso,
            "t_elapsed_s": t_elapsed,
            "pass_index": pass_index, "pass_type": pass_type,
            "driven_amp": driven_amp,
            "commanded_kv": commanded_kv, "commanded_gen_v": gen_v,
            "ain": ain, "amp_label": amp, "kind": kind,
            "mean_v": mean_v, "std_v": std_v,
            "min_v": min_v, "max_v": max_v,
            "n_samples": n_samples, "n_windows": n_windows,
            "converted_value": converted_value,
            "converted_unit": converted_unit,
            "stream_profile": CAL_PROFILE,
        }

    def _emit_row(self, row: dict):
        self.row_recorded.emit(row)
        if self._writer is not None:
            try:
                self._writer.write_row(row)
            except Exception as e:
                self._print_err(f"writer.write_row: {e}")

    def _emit_rows(self, step: "_StepPoint"):
        t_elapsed = time.monotonic() - self._t_start
        ts_iso    = now_iso()
        for amp in AMP_LABELS:
            for kind in ("voltage", "current"):
                self._emit_row(self._make_row(
                    step.pass_index, step.pass_type, step.driven_amp,
                    step.commanded_kv, amp, kind, t_elapsed, ts_iso,
                ))

    # ------------------------------------------------------------------
    # Drift mode
    # ------------------------------------------------------------------

    def _record_drift_row(self):
        try:
            self._state = _State.RECORD
            t_elapsed = time.monotonic() - self._t_start
            ts_iso    = now_iso()
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
            "min_v": float("nan"), "max_v": float("nan"),
            "n_samples": 0, "n_windows": 0,
            "converted_value": float("nan"), "converted_unit": reason,
            "stream_profile": CAL_PROFILE,
        }
        self._emit_row(row)

    # ------------------------------------------------------------------
    # Finish / abort / shutdown
    # ------------------------------------------------------------------

    def _finish_success(self):
        print(f"[CAL] sweep complete: {len(self._sequence)} setpoints")
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
        print(f"[CAL] ERROR: {msg}")
        log.error(msg)
