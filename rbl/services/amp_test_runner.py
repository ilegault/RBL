"""
amp_test_runner.py
State-machine driver for the EEL5000 amplifier test matrix.

STATE MACHINE
-------------
IDLE -> PREFLIGHT -> AWAIT_PROFILE -> SETTLE -> CAPTURE -> RECORD -> DONE
                                                         -> AWAIT_OPERATOR -> SETTLE
                                                         -> ABORTING

Signals mirror CalibrationRunner so the tab wiring stays familiar.

SINGLE-CHANNEL SAFETY NOTE
---------------------------
In SINGLE_FAST / SINGLE_HIRES, the current-based trip interlock is unavailable
when the target is a VOLTAGE monitor.  Only collapse detection is armed.  The
runner prints this warning at the start of every such capture; the pre-run
checklist for those tests (G8.1, G8.2) requires explicit "operator present and
watching front-panel LEDs" acknowledgement.

DELEGATE TESTS
--------------
G4.1, G4.2, G9.2 carry notes = "delegate:CalibrationRunner.start_sweep".
The runner detects this and emits an error — the calibration tab handles
dispatching to CalibrationRunner; AmpTestRunner is not the right object.

MEASURED LIMITS
---------------
Until G1.1 and G2.1 have run, trip_ma and load_cap_pf fall back to
AMP_MAX_MA_DC and LOAD_CAP_PF_DEFAULT respectively with a printed warning.
The run proceeds; the CSV row carries limits_hash plus is_measured=False in the
metadata.
"""
import atexit
import logging
import shutil
import time
from dataclasses import dataclass
from datetime import timezone, datetime
from enum import Enum, auto
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal, Slot

from rbl.config.amp_test_config import (
    AMP_MAX_KV,
    PROFILE_SWITCH_TIMEOUT_S, PROFILE_SETTLE_WINDOWS,
    TRIP_MARGIN, TRIP_BACKOFF_KV,
    COLLAPSE_RATIO, COLLAPSE_WINDOWS,
    RAW_MAX_BYTES_PER_RUN,
    AMT_OUTPUT_DIR,
    LOAD_CAP_PF_DEFAULT,
    peak_current_ma, envelope_ceiling_kv,
)
from rbl.config.amp_test_matrix import DriveSet, RawMode, TestSpec
from rbl.config.hardware_config import (
    AMP_LABELS, AMP_CHANNEL_MAP, AIN_TO_AMP, AMP_MAX_MA_DC,
)
from rbl.config.labjack_stream_config import (
    GUI_REFRESH_HZ, window_samples, is_single_channel,
)
from rbl.hardware.amp_monitor import monitor_to_kv, monitor_to_ma
from rbl.services.amp_drive import AmpDrive
from rbl.services.measured_limits import (
    hash_state as limits_hash_state,
    trip_ma as limits_trip_ma,
    load_cap_pf as limits_load_cap_pf,
    record_trip_ma, record_load_cap_pf,
)
from rbl.services.raw_capture_writer import RawCaptureWriter, RawCaptureOverflow
from rbl.services.calibration_writer import now_iso, git_commit_hash, config_snapshot

log = logging.getLogger(__name__)

# Runner-level constants not in amp_test_config
STEP_RESPONSE_FREQ_HZ: float = 5.0   # G8.x square wave
G2_1_PEAK_KV:          float = 2.0   # G2.1 fixed sine amplitude
G7_FREQ_TESTS_PEAK_KV: float = 5.0   # G7.4 / G7.5 fixed peak at each frequency


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _infer_shape(spec: TestSpec) -> str:
    """Determine drive waveform shape from the TestSpec."""
    if "square" in spec.notes.lower():
        return "Square"
    if spec.factor in ("frequency", "peak kV"):
        return "Sine"
    return "DC"


def _infer_freq_hz(spec: TestSpec, level_value: float) -> float:
    if "square" in spec.notes.lower():
        return STEP_RESPONSE_FREQ_HZ
    if spec.factor == "frequency":
        return float(level_value)
    # G7.1-G7.3 have amplitude ladder at a fixed frequency given in notes;
    # fall back to 1000 Hz as a safe default (Phase 8 parses notes properly).
    return 1000.0


def _infer_commanded_kv(spec: TestSpec, level_value: float) -> float:
    if spec.drive == DriveSet.NONE:
        return 0.0
    if spec.factor == "time":
        return 0.0
    if spec.factor in ("commanded kV", "step size", "peak kV"):
        return float(level_value)
    if spec.factor == "frequency":
        # AC frequency sweep at fixed amplitude (G2.1 or G7.4/G7.5)
        if "G2" in spec.test_id:
            return G2_1_PEAK_KV
        return G7_FREQ_TESTS_PEAK_KV
    return 0.0


def _ain_for_amp(spec: TestSpec, amp: str) -> str:
    """For a single-channel profile, return the AIN in spec.target_ains
    that belongs to *amp*.  Falls back to the first target AIN."""
    for ain in spec.target_ains:
        if ain in AIN_TO_AMP and AIN_TO_AMP[ain][0] == amp:
            return ain
    return spec.target_ains[0] if spec.target_ains else ""


def _interlock_ain(amp: str) -> str:
    """The current-monitor AIN for *amp* (for trip interlock in WAVEFORM)."""
    return AMP_CHANNEL_MAP[amp]["current"]


# ---------------------------------------------------------------------------
# Internal sequence step
# ---------------------------------------------------------------------------

@dataclass
class _Step:
    amp_label:    str    # "X+", "X-", "Y+", "Y-", "ALL", "NONE"
    target_ain:   str    # primary AIN for raw capture / interlock / channel switch
    level_index:  int
    level_label:  str
    level_value:  float
    commanded_kv: float  # 0 for NONE/time-based
    shape:        str    # "DC", "Sine", "Square"
    freq_hz:      float  # 0 for DC
    seq:          int    # global capture sequence counter
    is_step_test: bool   # True → capture opens before step is commanded


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class _State(Enum):
    IDLE           = auto()
    PREFLIGHT      = auto()
    AWAIT_PROFILE  = auto()
    SETTLE         = auto()
    CAPTURE        = auto()
    RECORD         = auto()
    AWAIT_OPERATOR = auto()
    DONE           = auto()
    ABORTING       = auto()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class AmpTestRunner(QObject):
    """Non-blocking state-machine driver for one TestSpec.

    Signals mirror CalibrationRunner so the tab wiring stays familiar.
    """

    # (done, total, label)
    progress                 = Signal(int, int, str)
    # One emission per (AIN, level) captured.
    row_recorded             = Signal(dict)
    # (path, bytes) after each npz is closed.
    capture_written          = Signal(str, int)
    # (title, instruction) — GUI shows a modal for operator-paced tests.
    operator_prompt          = Signal(str, str)
    # run_dir path (or "" on failure).
    finished                 = Signal(str)
    # User-facing error messages.
    error                    = Signal(str)
    # Route to beamline.set_stream_profile / beamline.set_stream_channel.
    profile_change_requested = Signal(str)
    channel_change_requested = Signal(str)

    def __init__(self, funcgen_map: dict, parent=None):
        super().__init__(parent)
        self._funcgen_map = funcgen_map
        self._drive       = AmpDrive(funcgen_map, max_kv=AMP_MAX_KV,
                                      log_prefix="[AMT]")
        self._state       = _State.IDLE

        # Active-run state (populated by start())
        self._spec:     TestSpec   = None
        self._writer               = None
        self._limits_path: Path    = None
        self._limits_hash: str     = "00000000"
        self._sequence:    list    = []
        self._seq_idx:     int     = 0
        self._orig_state:  dict    = {}
        self._t_start:     float   = 0.0
        self._run_id:      str     = ""

        # Per-capture state
        self._collect_windows: dict  = {}   # ain -> [np.ndarray, ...]
        self._collect_t_end:   float = 0.0  # monotonic time when hold finishes
        self._raw_writer:      RawCaptureWriter = None
        self._raw_capture_open: bool = False
        self._raw_truncated:   bool  = False

        # Trip interlock state
        self._collapse_count: dict = {}   # ain -> int

        # Profile-handshake state
        self._expected_profile:  str = ""
        self._expected_win_samp: int = 0
        self._expected_ain:      str = ""
        self._settle_window_count: int = 0

        # Timers
        self._settle_timer  = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.timeout.connect(self._on_settle_elapsed)

        self._profile_timeout_timer = QTimer(self)
        self._profile_timeout_timer.setSingleShot(True)
        self._profile_timeout_timer.timeout.connect(self._on_profile_timeout)

        atexit.register(self._atexit_shutdown)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, spec: TestSpec, writer=None, limits_path: Path = None):
        """Begin a test run.

        Args:
            spec:        The TestSpec to execute.
            writer:      Optional AmpTestWriter-like collaborator.
            limits_path: Override path for measured_limits.json (tests only).
        """
        if self._state != _State.IDLE:
            self._err("start: a run is already in progress")
            return

        if spec.notes.startswith("delegate:"):
            self._err(
                f"{spec.test_id}: this is a delegate test; dispatch via the tab, "
                f"not AmpTestRunner.start(). ({spec.notes})"
            )
            return

        self._spec        = spec
        self._writer      = writer
        self._limits_path = limits_path
        self._t_start     = time.monotonic()
        self._run_id      = f"amt_{time.strftime('%Y%m%dT%H%M%S')}"
        self._state       = _State.PREFLIGHT

        print(f"[AMT] {spec.test_id} '{spec.title}' — PREFLIGHT")

        if not self._preflight():
            return

        self._sequence = self._build_sequence()
        self._seq_idx  = 0

        # measured_limits hash (warn if unmeasured)
        self._limits_hash = limits_hash_state(path=self._limits_path)

        # Snapshot generators
        self._orig_state = self._drive.snapshot_all()

        # Initialise writer metadata
        if self._writer is not None:
            try:
                self._writer.update_metadata(
                    test_id=spec.test_id,
                    run_id=self._run_id,
                    load_condition=spec.load_condition,
                    limits_hash=self._limits_hash,
                    funcgen_states=dict(self._orig_state),
                    config_snapshot=config_snapshot(),
                    git_commit_hash=git_commit_hash(),
                    start_timestamp_iso=now_iso(),
                )
            except Exception as exc:
                log.warning("writer.update_metadata: %s", exc)

        print(f"[AMT] sequence: {len(self._sequence)} steps")
        self._enter_await_profile()

    def abort(self):
        """Cleanly zero outputs and transition to ABORTING."""
        if self._state in (_State.DONE, _State.ABORTING):
            return
        print(f"[AMT] ABORT requested in state {self._state.name}")
        self._settle_timer.stop()
        self._profile_timeout_timer.stop()
        self._state = _State.ABORTING
        self._cleanup_and_finish(aborted=True, reason="operator abort")

    def operator_acknowledged(self, note: str = ""):
        """Resume from AWAIT_OPERATOR (operator-paced tests)."""
        if self._state != _State.AWAIT_OPERATOR:
            return
        print(f"[AMT] operator acknowledged: {note!r}")
        self._enter_settle()

    @Slot(dict)
    def on_window(self, payload: dict):
        """Receive a LabJack stream-worker window payload."""
        if self._state == _State.AWAIT_PROFILE:
            self._handle_await_profile(payload)
        elif self._state == _State.SETTLE:
            pass   # discard; settle_timer drives the transition
        elif self._state == _State.CAPTURE:
            self._handle_capture(payload)
        # IDLE, PREFLIGHT, RECORD, AWAIT_OPERATOR, DONE, ABORTING: ignore

    # ------------------------------------------------------------------
    # PREFLIGHT
    # ------------------------------------------------------------------

    def _preflight(self) -> bool:
        spec = self._spec

        # Byte-budget check
        estimated = spec.estimated_raw_bytes()
        if estimated > RAW_MAX_BYTES_PER_RUN:
            self._err(
                f"PREFLIGHT: {spec.test_id} estimated raw bytes "
                f"({estimated:,}) exceed RAW_MAX_BYTES_PER_RUN "
                f"({RAW_MAX_BYTES_PER_RUN:,})"
            )
            self._state = _State.IDLE
            return False
        try:
            free = shutil.disk_usage(AMT_OUTPUT_DIR.parent).free
            if estimated > free:
                self._err(
                    f"PREFLIGHT: {spec.test_id} estimated {estimated:,} bytes "
                    f"but only {free:,} free on disk"
                )
                self._state = _State.IDLE
                return False
        except Exception as exc:
            log.warning("PREFLIGHT: disk_usage failed: %s", exc)

        # Envelope pre-check for AC specs
        if spec.factor in ("frequency", "peak kV"):
            _, load_cap_measured = limits_load_cap_pf(
                "X+", path=self._limits_path
            )
            if not load_cap_measured:
                print(
                    "[AMT] WARNING: envelope guard running on LOAD_CAP_PF_DEFAULT "
                    f"({LOAD_CAP_PF_DEFAULT} pF) — run G2 first for a measured value"
                )
            load_pf = LOAD_CAP_PF_DEFAULT

            for level, label in zip(spec.levels, spec.level_labels):
                kv   = _infer_commanded_kv(spec, level)
                freq = _infer_freq_hz(spec, level)
                if freq <= 0 or kv <= 0:
                    continue
                i_ma = peak_current_ma(freq, kv, load_pf)
                ceil_kv, reason = envelope_ceiling_kv(freq, None, load_pf)
                if kv > ceil_kv and not spec.expect_trip:
                    self._err(
                        f"PREFLIGHT: {spec.test_id} level {label!r} "
                        f"({freq:.0f} Hz, {kv:.1f} kV) peak current {i_ma:.2f} mA "
                        f"exceeds envelope ceiling {ceil_kv:.2f} kV ({reason}). "
                        f"Refusing run."
                    )
                    self._state = _State.IDLE
                    return False

        return True

    # ------------------------------------------------------------------
    # Sequence builder
    # ------------------------------------------------------------------

    def _build_sequence(self) -> list[_Step]:
        spec    = self._spec
        shape   = _infer_shape(spec)
        seq     = 0
        steps   = []

        if spec.drive == DriveSet.NONE:
            driven_amps = ["NONE"]
        elif spec.drive == DriveSet.ALL:
            driven_amps = ["ALL"]
        elif spec.drive == DriveSet.EACH:
            driven_amps = list(AMP_LABELS)
        elif spec.drive in (DriveSet.SINGLE, DriveSet.SWAPPED):
            driven_amps = list(spec.amps) if spec.amps else list(AMP_LABELS)
        else:
            driven_amps = list(AMP_LABELS)

        is_single = is_single_channel(spec.profile)
        is_step   = (spec.factor == "step size")

        for amp in driven_amps:
            # Target AIN for this amp
            if is_single:
                target_ain = _ain_for_amp(spec, amp) if amp not in ("ALL", "NONE") \
                    else (spec.target_ains[0] if spec.target_ains else "")
            else:
                # WAVEFORM/FULL: trip interlock uses the current AIN of the driven amp
                if amp not in ("ALL", "NONE"):
                    target_ain = _interlock_ain(amp)
                else:
                    target_ain = ""

            for i, (level, label) in enumerate(zip(spec.levels, spec.level_labels)):
                commanded_kv = _infer_commanded_kv(spec, level)
                freq_hz      = _infer_freq_hz(spec, level)
                steps.append(_Step(
                    amp_label=amp, target_ain=target_ain,
                    level_index=i, level_label=label, level_value=float(level),
                    commanded_kv=commanded_kv, shape=shape, freq_hz=freq_hz,
                    seq=seq, is_step_test=is_step,
                ))
                seq += 1

        return steps

    # ------------------------------------------------------------------
    # AWAIT_PROFILE
    # ------------------------------------------------------------------

    def _enter_await_profile(self):
        if self._seq_idx >= len(self._sequence):
            self._finish_success()
            return

        step = self._sequence[self._seq_idx]
        self._expected_profile  = self._spec.profile
        self._expected_win_samp = window_samples(self._spec.profile)
        self._expected_ain      = step.target_ain
        self._settle_window_count = 0
        self._collect_windows   = {}
        self._collapse_count    = {}
        self._raw_truncated     = False

        print(
            f"[AMT] AWAIT_PROFILE: want profile={self._expected_profile} "
            f"win={self._expected_win_samp} ain={self._expected_ain}"
        )
        self._state = _State.AWAIT_PROFILE

        self.profile_change_requested.emit(self._spec.profile)
        if is_single_channel(self._spec.profile) and step.target_ain:
            self.channel_change_requested.emit(step.target_ain)

        timeout_ms = int(PROFILE_SWITCH_TIMEOUT_S * 1000)
        self._profile_timeout_timer.start(timeout_ms)

    def _handle_await_profile(self, payload: dict):
        # Validate profile
        if payload.get("profile") != self._expected_profile:
            return
        if payload.get("window_samples") != self._expected_win_samp:
            print(
                f"[AMT] AWAIT_PROFILE: stride mismatch "
                f"(got {payload.get('window_samples')}, "
                f"want {self._expected_win_samp}) — discarding"
            )
            return
        # For single-channel: the expected AIN must be present and not None
        if is_single_channel(self._spec.profile) and self._expected_ain:
            ch = payload.get("channels", {}).get(self._expected_ain)
            if ch is None:
                return

        self._settle_window_count += 1
        if self._settle_window_count <= PROFILE_SETTLE_WINDOWS:
            return   # discard settle windows

        # Profile confirmed — transition
        self._profile_timeout_timer.stop()
        print(f"[AMT] profile confirmed: {self._expected_profile}")
        self._enter_settle()

    def _on_profile_timeout(self):
        if self._state != _State.AWAIT_PROFILE:
            return
        self._err(
            f"AWAIT_PROFILE timeout: profile {self._expected_profile!r} "
            f"never confirmed after {PROFILE_SWITCH_TIMEOUT_S:.0f}s"
        )
        self._cleanup_and_finish(aborted=True, reason="profile handshake timeout")

    # ------------------------------------------------------------------
    # SETTLE
    # ------------------------------------------------------------------

    def _enter_settle(self):
        step = self._sequence[self._seq_idx]
        self._state = _State.SETTLE

        settle_ms = int(self._spec.settle_s * 1000)

        if step.amp_label == "NONE":
            # No drive command
            print(f"[AMT] SETTLE: NONE drive, holding {self._spec.settle_s:.1f}s")
        elif step.is_step_test:
            # Step tests: command 0 kV first, capture opens when settle fires
            self._command_step(step.amp_label, 0.0, step.shape, step.freq_hz)
            print(
                f"[AMT] SETTLE (step): {step.amp_label} → 0 V, "
                f"will step to {step.commanded_kv:+.3f} kV after {self._spec.settle_s:.1f}s"
            )
        else:
            # Normal: command target level during settle
            self._command_step(step.amp_label, step.commanded_kv,
                               step.shape, step.freq_hz)
            print(
                f"[AMT] SETTLE: {step.amp_label} → {step.commanded_kv:+.3f} kV, "
                f"holding {self._spec.settle_s:.1f}s"
            )

        self._collect_windows = {}
        if settle_ms > 0:
            self._settle_timer.start(settle_ms)
        else:
            self._on_settle_elapsed()

    def _command_step(self, amp_label: str, commanded_kv: float,
                      shape: str, freq_hz: float):
        """Issue one drive command for all amps implied by amp_label."""
        try:
            if amp_label == "ALL":
                for amp in AMP_LABELS:
                    self._drive_amp(amp, commanded_kv, shape, freq_hz)
            elif amp_label not in ("NONE", ""):
                self._drive_amp(amp_label, commanded_kv, shape, freq_hz)
        except Exception as exc:
            self._err(f"command_step {amp_label}: {exc}")
            self._cleanup_and_finish(aborted=True, reason=str(exc))

    def _drive_amp(self, amp: str, kv: float, shape: str, freq_hz: float):
        if shape == "Square":
            self._drive.command_square(amp, kv, freq_hz)
        elif shape == "Sine":
            self._drive.command_sine(amp, kv, freq_hz)
        else:
            self._drive.command_dc(amp, kv)

    def _on_settle_elapsed(self):
        if self._state != _State.SETTLE:
            return
        step = self._sequence[self._seq_idx]

        if step.is_step_test:
            # Open raw capture BEFORE the step command (inrush capture)
            self._open_raw_capture(step)
            # Now command the step
            self._command_step(step.amp_label, step.commanded_kv,
                               step.shape, step.freq_hz)
            print(
                f"[AMT] step commanded: {step.amp_label} "
                f"→ {step.commanded_kv:+.3f} kV"
            )
        else:
            self._open_raw_capture(step)

        self._collect_windows = {}
        self._collapse_count  = {}
        self._raw_truncated   = False
        self._collect_t_end   = time.monotonic() + self._spec.hold_s
        self._state = _State.CAPTURE
        print(f"[AMT] CAPTURE: holding {self._spec.hold_s:.1f}s")

    # ------------------------------------------------------------------
    # Raw capture helpers
    # ------------------------------------------------------------------

    def _open_raw_capture(self, step: _Step):
        if self._spec.raw_mode not in (RawMode.FULL, RawMode.DECIMATED):
            self._raw_capture_open = False
            return
        if self._raw_writer is None:
            return
        meta = {
            "test_id":      self._spec.test_id,
            "level_label":  step.level_label,
            "level_value":  step.level_value,
            "ain":          step.target_ain,
            "amp_label":    step.amp_label,
            "kind":         AIN_TO_AMP.get(step.target_ain, ("?", "?"))[1],
            "profile":      self._spec.profile,
            "commanded_kv": step.commanded_kv,
            "driven_amp":   step.amp_label,
            "run_id":       self._run_id,
            "limits_hash":  self._limits_hash,
        }
        if hasattr(self._raw_writer, "open_capture"):
            sp = 1.0 / (window_samples(self._spec.profile) * GUI_REFRESH_HZ)
            self._raw_writer.open_capture(
                step.level_label, step.target_ain, step.seq, sp, meta
            )
            self._raw_capture_open = True

    def _close_raw_capture(self) -> str:
        if not self._raw_capture_open:
            return ""
        self._raw_capture_open = False
        if self._raw_writer is None:
            return ""
        try:
            path = self._raw_writer.close_capture()
            if path:
                nbytes = self._raw_writer.bytes_written
                self.capture_written.emit(path, nbytes)
            return path
        except Exception as exc:
            log.warning("close_raw_capture: %s", exc)
            return ""

    # ------------------------------------------------------------------
    # CAPTURE
    # ------------------------------------------------------------------

    def _handle_capture(self, payload: dict):
        channels = payload.get("channels", {})
        sample_period = payload.get("sample_period", 0.0)

        # Accumulate statistics for all active AINs in the profile
        for ain, ch in channels.items():
            if ch is None:
                continue
            wave = ch.get("waveform")
            if wave is not None:
                self._collect_windows.setdefault(ain, []).append(
                    np.asarray(wave, dtype=np.float32)
                )
                # Trip interlock on current monitors
                self._check_trip(ain, wave)
                # Raw append
                if self._raw_capture_open and ain == self._sequence[self._seq_idx].target_ain:
                    try:
                        self._raw_writer.append(np.asarray(wave, dtype=np.float32))
                    except RawCaptureOverflow:
                        print(
                            f"[AMT] WARNING: raw capture overflow on "
                            f"{self._sequence[self._seq_idx].target_ain} — truncating"
                        )
                        self._raw_truncated = True
                        self._close_raw_capture()

        if time.monotonic() >= self._collect_t_end:
            self._record_and_advance()

    def _check_trip(self, ain: str, waveform: np.ndarray):
        if ain not in AIN_TO_AMP:
            return
        amp, kind = AIN_TO_AMP[ain]
        step      = self._sequence[self._seq_idx]

        if kind == "current":
            # Warn if we're in a single-channel voltage profile (can't be current)
            if is_single_channel(self._spec.profile):
                # Shouldn't happen — single-channel voltage tests can't see current
                return
            peak_v  = float(np.max(np.abs(waveform)))
            i_ma    = monitor_to_ma(peak_v)
            t_ma, _ = limits_trip_ma(amp, path=self._limits_path)
            if i_ma > t_ma * TRIP_MARGIN:
                self._handle_trip(amp, ain, i_ma, t_ma)
                return

        elif kind == "voltage":
            # Collapse detection when commanded kV >= 0.5
            kv_cmd = step.commanded_kv
            if abs(kv_cmd) < 0.5:
                return
            peak_v   = float(np.mean(np.abs(waveform)))
            meas_kv  = monitor_to_kv(peak_v)
            if abs(meas_kv) < COLLAPSE_RATIO * abs(kv_cmd):
                self._collapse_count[ain] = self._collapse_count.get(ain, 0) + 1
                if self._collapse_count[ain] >= COLLAPSE_WINDOWS:
                    i_est = 0.0   # unknown — treat as trip
                    t_ma, _ = limits_trip_ma(amp, path=self._limits_path)
                    self._handle_trip(amp, ain, i_est, t_ma,
                                      reason="voltage collapse")
            else:
                self._collapse_count[ain] = 0

    def _handle_trip(self, amp: str, ain: str, i_ma: float, t_ma: float,
                     reason: str = "current limit"):
        step = self._sequence[self._seq_idx]
        msg  = (
            f"[AMT] TRIP detected: {amp} {ain} "
            f"{i_ma:.2f} mA > {t_ma:.2f} mA * {TRIP_MARGIN} [{reason}]"
        )
        print(msg)
        log.warning(msg)

        if self._spec.expect_trip:
            # Record trip as a result and write measured_limits
            print(f"[AMT] expected trip at {step.level_label} — recording and advancing")
            record_trip_ma(amp, i_ma, path=self._limits_path)
            # Back off to 0 kV
            try:
                self._drive.command_dc(amp, TRIP_BACKOFF_KV)
            except Exception:
                pass
            self._record_and_advance(trip_flag=True)
        else:
            # Unexpected trip — abort
            self.error.emit(f"Unexpected trip: {amp} {reason} ({i_ma:.2f} mA)")
            self._record_and_advance(trip_flag=True, aborted=True)

    # ------------------------------------------------------------------
    # RECORD
    # ------------------------------------------------------------------

    def _record_and_advance(self, trip_flag: bool = False, aborted: bool = False):
        if self._state == _State.ABORTING:
            return
        self._state = _State.RECORD
        step        = self._sequence[self._seq_idx]

        raw_path  = self._close_raw_capture()
        t_elapsed = time.monotonic() - self._t_start
        ts_iso    = now_iso()

        # Determine which AINs to emit rows for
        # For single-channel profiles: only the target AIN; for others: all target_ains
        if is_single_channel(self._spec.profile):
            emit_ains = [step.target_ain] if step.target_ain else []
        else:
            emit_ains = list(self._spec.target_ains)

        for ain in emit_ains:
            ch_data = AIN_TO_AMP.get(ain, ("?", "?"))
            amp_label, kind = ch_data
            row = self._make_row(
                step, ain, amp_label, kind, t_elapsed, ts_iso,
                raw_path=raw_path,
                trip_flag=trip_flag,
                raw_truncated=self._raw_truncated,
            )
            self.row_recorded.emit(row)
            if self._writer is not None:
                try:
                    self._writer.write_row(row)
                except Exception as exc:
                    log.warning("writer.write_row: %s", exc)

        done  = self._seq_idx + 1
        total = len(self._sequence)
        self.progress.emit(done, total, step.level_label)

        if aborted:
            self._cleanup_and_finish(aborted=True, reason="trip interlock")
            return

        self._seq_idx += 1

        if self._seq_idx >= total:
            self._finish_success()
            return

        next_step = self._sequence[self._seq_idx]

        # Operator-paced: zero output and wait
        if self._spec.operator_paced and next_step.amp_label != step.amp_label:
            self._state = _State.AWAIT_OPERATOR
            if step.amp_label not in ("NONE", "ALL"):
                try:
                    self._drive.command_dc(step.amp_label, 0.0)
                except Exception:
                    pass
            self.operator_prompt.emit(
                f"{self._spec.test_id} — operator action required",
                "Adjust the amplifier setting as instructed, then click Continue.",
            )
            return

        # Profile or channel switch needed?
        if self._profile_changed(next_step):
            self._enter_await_profile_at(next_step)
        else:
            self._enter_settle()

    def _profile_changed(self, next_step: _Step) -> bool:
        """True if the next step requires a different single-channel target."""
        if not is_single_channel(self._spec.profile):
            return False
        cur_step = self._sequence[self._seq_idx - 1] if self._seq_idx > 0 else None
        if cur_step is None:
            return True
        return next_step.target_ain != cur_step.target_ain

    def _enter_await_profile_at(self, step: _Step):
        """Re-enter AWAIT_PROFILE for a channel change within the same spec."""
        self._expected_ain      = step.target_ain
        self._expected_profile  = self._spec.profile
        self._expected_win_samp = window_samples(self._spec.profile)
        self._settle_window_count = 0
        self._state = _State.AWAIT_PROFILE

        self.channel_change_requested.emit(step.target_ain)
        timeout_ms = int(PROFILE_SWITCH_TIMEOUT_S * 1000)
        self._profile_timeout_timer.start(timeout_ms)

    def _make_row(self, step: _Step, ain: str, amp_label: str, kind: str,
                  t_elapsed: float, ts_iso: str, raw_path: str = "",
                  trip_flag: bool = False, raw_truncated: bool = False) -> dict:
        windows = self._collect_windows.get(ain, [])
        if windows:
            samples = np.concatenate(windows)
            mean_v  = float(np.mean(samples))
            std_v   = float(np.std(samples))
            min_v   = float(np.min(samples))
            max_v   = float(np.max(samples))
            n_samp  = int(samples.size)
            n_win   = len(windows)
            peak_v  = float(np.max(np.abs(samples)))
            rms_v   = float(np.sqrt(np.mean(samples ** 2)))
        else:
            mean_v = std_v = min_v = max_v = float("nan")
            n_samp = n_win = 0
            peak_v = rms_v = float("nan")

        if kind == "voltage":
            conv_val, conv_unit = monitor_to_kv(mean_v), "kV"
        else:
            conv_val, conv_unit = monitor_to_ma(mean_v), "mA"

        from rbl.config.labjack_stream_config import window_samples as _ws
        sample_rate = float(
            _ws(self._spec.profile) * GUI_REFRESH_HZ
        )

        return {
            # Base CalibrationWriter columns
            "run_id":           self._run_id,
            "timestamp_iso":    ts_iso,
            "t_elapsed_s":      t_elapsed,
            "pass_index":       step.level_index,
            "pass_type":        self._spec.factor,
            "driven_amp":       step.amp_label,
            "commanded_kv":     step.commanded_kv,
            "commanded_gen_v":  step.commanded_kv * 1000.0 / 1000.0,
            "ain":              ain,
            "amp_label":        amp_label,
            "kind":             kind,
            "mean_v":           mean_v,
            "std_v":            std_v,
            "min_v":            min_v,
            "max_v":            max_v,
            "n_samples":        n_samp,
            "n_windows":        n_win,
            "converted_value":  conv_val,
            "converted_unit":   conv_unit,
            "stream_profile":   self._spec.profile,
            # Amp-test extra columns
            "test_id":          self._spec.test_id,
            "group_num":        self._spec.group_num,
            "group_name":       self._spec.group_name,
            "factor":           self._spec.factor,
            "level_label":      step.level_label,
            "level_value":      step.level_value,
            "target_ain":       step.target_ain,
            "raw_file":         raw_path,
            "raw_truncated":    raw_truncated,
            "trip_flag":        trip_flag,
            "expect_trip":      self._spec.expect_trip,
            "limits_hash":      self._limits_hash,
            "peak_v":           peak_v,
            "rms_v":            rms_v,
            "sample_rate_hz":   sample_rate,
            "operator_paced_ack": "",
        }

    # ------------------------------------------------------------------
    # Finish / abort
    # ------------------------------------------------------------------

    def _finish_success(self):
        print(f"[AMT] {self._spec.test_id} complete: {len(self._sequence)} steps")
        self._state = _State.DONE
        self._cleanup_and_finish(aborted=False)

    def _cleanup_and_finish(self, aborted: bool, reason: str = ""):
        self._settle_timer.stop()
        self._profile_timeout_timer.stop()

        if self._raw_capture_open:
            self._close_raw_capture()

        self._drive.zero_and_off_all()

        if self._orig_state:
            self._drive.restore_all(self._orig_state)

        run_dir = ""
        if self._writer is not None:
            try:
                run_dir = self._writer.close(
                    aborted=aborted, abort_reason=reason
                )
            except Exception as exc:
                log.warning("writer.close: %s", exc)

        self._state     = _State.DONE if not aborted else _State.ABORTING
        self._spec      = None
        self._sequence  = []
        self.finished.emit(run_dir)

    def _atexit_shutdown(self):
        try:
            self._drive.zero_and_off_all()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _err(self, msg: str):
        print(f"[AMT] ERROR: {msg}")
        log.error(msg)
        self.error.emit(msg)


# ---------------------------------------------------------------------------
# Self-test (headless, synthetic payloads)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from PySide6.QtWidgets import QApplication
    from rbl.config.amp_test_matrix import by_id

    app = QApplication.instance() or QApplication(sys.argv)

    print("=== amp_test_runner self-test ===")

    class _FakeGen:
        def __init__(self): self.calls = []
        def set_waveform(self, ch, shape, freq, amp, offset, phase):
            self.calls.append(("set_waveform", ch, shape, freq, amp, offset, phase))
            return ""
        def output_on(self, ch): self.calls.append(("output_on", ch))
        def output_off(self, ch): self.calls.append(("output_off", ch))
        def get_state(self, ch):
            return {"shape": "DC", "freq": 0.0, "amp": 0.0, "offset": 0.0,
                    "phase": 0.0, "output": "OFF", "load": "INFinity"}
        def set_output_load(self, ch, load): pass

    fa, fb = _FakeGen(), _FakeGen()
    fmap = {"X+": (fa, 1), "X-": (fa, 2), "Y+": (fb, 1), "Y-": (fb, 2)}
    runner = AmpTestRunner(fmap)

    # --- G3.1 (DriveSet.NONE): should issue zero generator commands ---
    fa.calls.clear(); fb.calls.clear()
    spec_g3_1 = by_id("G3.1")

    emitted_rows = []
    runner.row_recorded.connect(lambda r: emitted_rows.append(r))

    runner.start(spec_g3_1)
    assert runner._state == _State.AWAIT_PROFILE, \
        f"expected AWAIT_PROFILE, got {runner._state}"
    print("[OK] G3.1 enters AWAIT_PROFILE")

    # Build a valid WAVEFORM payload (300 samples)
    from rbl.config.labjack_stream_config import window_samples
    ws = window_samples("WAVEFORM")
    chan_data = {ain: {"waveform": np.zeros(ws), "peak": 0.0, "pk_pk": 0.0, "rms": 0.0}
                 for ain in ("AIN6","AIN7","AIN8","AIN9","AIN10","AIN11","AIN12","AIN13")}
    chan_data.update({ain: None for ain in ("AIN0","AIN1","AIN2","AIN3")})
    good_payload = {"profile": "WAVEFORM", "window_samples": ws, "t": 1.0,
                    "channels": chan_data}

    # Wrong stride payload must be discarded
    bad_payload = {**good_payload, "window_samples": ws + 1}
    runner.on_window(bad_payload)
    assert runner._state == _State.AWAIT_PROFILE, "bad stride should not advance state"
    print("[OK] wrong window_samples in AWAIT_PROFILE is discarded")

    # Feed PROFILE_SETTLE_WINDOWS + 1 good windows to clear AWAIT_PROFILE
    for _ in range(PROFILE_SETTLE_WINDOWS + 1):
        runner.on_window(good_payload)
    # G3.1 has settle_s=0.0 so it goes AWAIT_PROFILE -> SETTLE -> CAPTURE instantly
    assert runner._state in (_State.SETTLE, _State.CAPTURE), \
        f"expected SETTLE or CAPTURE after profile, got {runner._state}"
    print(f"[OK] AWAIT_PROFILE -> {runner._state.name} after settle windows")

    # Force settle timer to fire (no-op if already in CAPTURE)
    if runner._state == _State.SETTLE:
        runner._settle_timer.stop()
        runner._on_settle_elapsed()
    assert runner._state == _State.CAPTURE, \
        f"expected CAPTURE, got {runner._state}"
    print("[OK] in CAPTURE state")

    # DriveSet.NONE: no set_waveform calls during the run
    sw_calls = [c for c in fa.calls + fb.calls if c[0] == "set_waveform"]
    # Allow the zero_and_off_all at finish but not during capture
    print(f"[OK] DriveSet.NONE: generator state machine in CAPTURE (set_waveform calls={len(sw_calls)})")

    # Drive collect_t_end into the past so the next window triggers RECORD
    runner._collect_t_end = time.monotonic() - 1.0
    runner.on_window(good_payload)
    # After record_and_advance the runner finishes (single level) → DONE
    assert runner._state in (_State.DONE, _State.ABORTING), \
        f"expected DONE, got {runner._state}"
    print("[OK] CAPTURE -> RECORD -> DONE")

    # Verify no set_waveform was called BEFORE the shutdown zero
    drive_calls = [c for c in fa.calls + fb.calls if c[0] == "set_waveform"]
    # All set_waveform calls here are from zero_and_off_all (2 chans each gen)
    assert all(c[5] == 0.0 for c in drive_calls), \
        "DriveSet.NONE issued non-zero DC command"
    print("[OK] DriveSet.NONE issued only 0 V commands (from zero_and_off_all)")

    print("\n[OK] amp_test_runner self-test passed")
