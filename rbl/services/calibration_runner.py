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
from datetime import datetime, timezone
from enum import Enum, auto

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from rbl.config.calibration_config import (
    CAL_COLLECT_S, CAL_MAX_KV, CAL_PASSES, CAL_PROFILE, CAL_SETTLE_S,
    LoadCondition, sweep_points,
)
from rbl.config.hardware_config import AMP_AIN_NAMES, AMP_CHANNEL_MAP, AMP_LABELS
from rbl.config.labjack_stream_config import GUI_REFRESH_HZ
from rbl.hardware.amp_monitor import monitor_to_kv, monitor_to_ma
from rbl.hardware.funcgen_safety import _AMP_GAIN
from rbl.services.calibration_writer import config_snapshot, git_commit_hash, new_run_id

log = logging.getLogger(__name__)


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
        self._sequence: list[_StepPoint] = []
        self._seq_idx  = 0
        self._seed     = None
        self._orig_state: dict = {}     # amp_label -> get_state() snapshot

        self._collect_windows: dict = {}   # ain -> [np.ndarray, ...]
        self._collect_window_count = 0
        self._windows_per_collect = max(1, round(CAL_COLLECT_S * GUI_REFRESH_HZ))

        self._run_id  = None
        self._t_start = None

        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.timeout.connect(self._on_settle_elapsed)

        atexit.register(self._atexit_shutdown)

    # ------------------------------------------------------------------
    # Public slots
    # ------------------------------------------------------------------

    def start_sweep(self):
        if self._state != _State.IDLE:
            self._print_err("start_sweep: a run is already in progress")
            return

        self._run_id  = self._run_id_override or new_run_id()
        self._t_start = time.monotonic()
        self._seed    = random.SystemRandom().randrange(2 ** 31)
        self._sequence = self._build_sequence()
        self._seq_idx  = 0

        self._orig_state = {}
        for label, (gen, channel) in self._funcgen_map.items():
            try:
                self._orig_state[label] = gen.get_state(channel)
            except Exception as e:
                self._print_err(f"get_state failed for {label}: {e}")
                self._orig_state[label] = None

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

    # ------------------------------------------------------------------
    # SETTLE
    # ------------------------------------------------------------------

    def _enter_settle(self):
        if self._seq_idx >= len(self._sequence):
            self._finish_success()
            return

        step = self._sequence[self._seq_idx]
        self._state = _State.SETTLE
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
        self._settle_timer.start(int(CAL_SETTLE_S * 1000))

    def _command_setpoint(self, driven_amp: str, commanded_kv: float):
        """Driven channel to its clamped setpoint; the other three to 0.0.

        Every channel is (re)commanded on every setpoint, driven or not —
        an undriven channel that silently kept whatever it held before is
        not the guarantee this sweep is supposed to give.
        """
        clamped = max(-CAL_MAX_KV, min(CAL_MAX_KV, commanded_kv))
        for amp in AMP_LABELS:
            value = clamped if amp == driven_amp else 0.0
            self._command_channel(amp, value)

    def _command_channel(self, amp_label: str, value_kv: float):
        gen, channel = self._funcgen_map[amp_label]
        gen_v = value_kv * 1000.0 / _AMP_GAIN
        print(f"[CAL] {amp_label} ch{channel}: DC {gen_v:+.4f} V "
              f"({value_kv:+.4f} kV)")
        try:
            warn = gen.set_waveform(channel, "DC", 0.0, 0.0, gen_v, 0.0)
            if warn:
                print(f"[CAL] WARN {amp_label} ch{channel}: {warn}")
                log.warning("%s ch%s: %s", amp_label, channel, warn)
            gen.output_on(channel)
        except Exception as e:
            self._print_err(f"{amp_label} ch{channel}: {e}")
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

    def _emit_rows(self, step: "_StepPoint"):
        t_elapsed = time.monotonic() - self._t_start
        ts_iso    = datetime.now(timezone.utc).isoformat()
        gen_v     = step.commanded_kv * 1000.0 / _AMP_GAIN

        for amp in AMP_LABELS:
            for kind in ("voltage", "current"):
                ain = AMP_CHANNEL_MAP[amp][kind]
                arrays = self._collect_windows.get(ain, [])
                n_windows = len(arrays)
                if arrays:
                    samples   = np.concatenate(arrays)
                    mean_v    = float(np.mean(samples))
                    std_v     = float(np.std(samples))
                    min_v     = float(np.min(samples))
                    max_v     = float(np.max(samples))
                    n_samples = int(samples.size)
                else:
                    mean_v = std_v = min_v = max_v = float("nan")
                    n_samples = 0

                if kind == "voltage":
                    converted_value, converted_unit = monitor_to_kv(mean_v), "kV"
                else:
                    converted_value, converted_unit = monitor_to_ma(mean_v), "mA"

                row = {
                    "run_id": self._run_id, "timestamp_iso": ts_iso,
                    "t_elapsed_s": t_elapsed,
                    "pass_index": step.pass_index, "pass_type": step.pass_type,
                    "driven_amp": step.driven_amp,
                    "commanded_kv": step.commanded_kv, "commanded_gen_v": gen_v,
                    "ain": ain, "amp_label": amp, "kind": kind,
                    "mean_v": mean_v, "std_v": std_v,
                    "min_v": min_v, "max_v": max_v,
                    "n_samples": n_samples, "n_windows": n_windows,
                    "converted_value": converted_value,
                    "converted_unit": converted_unit,
                    "stream_profile": CAL_PROFILE,
                }
                self.row_recorded.emit(row)
                if self._writer is not None:
                    try:
                        self._writer.write_row(row)
                    except Exception as e:
                        self._print_err(f"writer.write_row: {e}")

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

        for amp, (gen, channel) in self._funcgen_map.items():
            try:
                gen.set_waveform(channel, "DC", 0.0, 0.0, 0.0, 0.0)
            except Exception as e:
                self._print_err(f"zero {amp}: {e}")
            try:
                gen.output_off(channel)
            except Exception as e:
                self._print_err(f"output_off {amp}: {e}")

        if restore:
            for amp, (gen, channel) in self._funcgen_map.items():
                snap = self._orig_state.get(amp)
                if not snap or "error" in snap:
                    continue
                try:
                    gen.set_waveform(channel, snap["shape"], snap["freq"],
                                      snap["amp"], snap["offset"], snap["phase"])
                    gen.set_output_load(channel, snap.get("load", "INFinity"))
                except Exception as e:
                    self._print_err(f"restore {amp}: {e}")

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
        for amp, (gen, channel) in self._funcgen_map.items():
            try:
                gen.set_waveform(channel, "DC", 0.0, 0.0, 0.0, 0.0)
            except Exception:
                pass
            try:
                gen.output_off(channel)
            except Exception:
                pass

    # ------------------------------------------------------------------

    @staticmethod
    def _print_err(msg: str):
        print(f"[CAL] ERROR: {msg}")
        log.error(msg)
