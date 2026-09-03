"""
ramp_engine.py
Slew-limited, multi-channel amplitude/offset ramp service. A caller never
writes a setpoint directly to a channel that should ramp; it writes a
TARGET here, and this service walks the amplitude or DC offset toward it in
steps sized from the measured SCPI round-trip time.

WHY THIS EXISTS
---------------
Explicitly NOT to protect the amplifier from the shutoff transient — Section
1.5 of docs/AMP_ENVELOPE_AND_HV_SAFETY_PLAN.md shows that transient is
harmless (35 mA for ~90-170 us, a fraction of a percent of the 100 mA/4 ms
burst budget). Built instead because:

  1. Discharge initiation is dV/dt-sensitive. Streamer formation in vacuum is
     more likely on a fast edge than a slow ramp — the plausible mechanism
     behind the historical Y+ shutdown on a 0->5 kV step.
  2. It is the substrate Phase 5 (HV conditioning) cannot exist without.
  3. Phase 2's interlock needs a graceful shutdown path when it trips.
  4. Phase 1 Mode B needs to walk a DC ladder without stepping.

THE SUBTLE PART
----------------
`DG1022Z.set_waveform()` uses `:SOURce{ch}:APPLy:...`, which reconfigures the
channel and restarts the phase generator. Calling it mid-ramp would create
exactly the discontinuity a ramp exists to avoid, so every ramp step below
goes through `set_amplitude`/`set_offset`/`write_fast` instead (see
`funcgen_driver.py`) — never `set_waveform`.

SCPI latency (Section 1.9) means a ramp is latency-bound, not physics-bound:
the load settles in tens of microseconds, the command path takes tens of
milliseconds. `write_fast()` skips the per-write `:SYSTem:ERRor?` poll for
every step except the LAST, which goes through the checked `set_amplitude`/
`set_offset` call so a failure anywhere in the ramp is still caught, just not
per-step — a 25-step ramp on four checked writes would otherwise cost
seconds of wall time it does not have.

UNITS — deliberately generator volts, not kV
---------------------------------------------
This engine ramps whatever `set_offset`/`set_amplitude` take: raw generator
volts (DC offset) or Vpp (AC amplitude), the same units the driver itself
uses. It does not know about the EEL5000's 1000x gain or "plate kV" at all.
That conversion is the CALLER's job — `AmpDrive.command_dc_ramped()` and
`command_ac_amplitude_ramped()` convert plate kV to generator volts before
calling `retarget()`, exactly as `AmpDrive.command_dc()` already does for an
unramped command. Keeping the gain conversion out of this module is what
lets `FuncGenControlMixin` reuse the identical engine for GUI-originated
raw-volt edits (Section 5.4) without inventing a second ramp implementation.
"""
import logging
import time

from PySide6.QtCore import QObject, QTimer, Signal

from rbl.config.calibration_config import CAL_LOAD_CAP_PF
from rbl.hardware.funcgen_driver import MAX_AMP_VPP, MAX_GEN_VOLTS

log = logging.getLogger(__name__)

# Target wall-clock time for a ramp to complete. The physics-derived step
# size (current budget / (C * 2*pi*f_bw), Section 5.3) works out to ~19-38 mV
# of PLATE voltage for this load — impractically small over a ~20 ms USB
# round trip, so in practice the step is sized from this duration instead and
# the resulting predicted peak current is logged so it is never silently
# large.
DEFAULT_RAMP_DURATION_S = 1.0

# Used only before the first real round-trip measurement for a channel.
FALLBACK_ROUND_TRIP_S = 0.02

# Order writes so pair members are adjacent, minimising the per-step
# differential across a plate pair (SCPI writes are serial, so channels are
# up to one step out of sync at any instant during a lockstep ramp).
_CHANNEL_ORDER = ("X+", "X-", "Y+", "Y-")

_DONE_EPS_V = 1e-9

_CLAMP = {
    "offset": (-MAX_GEN_VOLTS, MAX_GEN_VOLTS),
    "amplitude": (-MAX_AMP_VPP, MAX_AMP_VPP),
}


class RampEngine(QObject):
    ramp_started  = Signal(str)              # label
    ramp_progress = Signal(str, float, float)   # label, current_v, target_v
    ramp_finished = Signal(str)
    ramp_failed   = Signal(str, str)

    def __init__(self, funcgen_map: dict, ramp_duration_s: float = DEFAULT_RAMP_DURATION_S,
                 load_pf_for_label=None, plate_gain_for_label=None, parent=None):
        """
        funcgen_map: {label: (DG1022Z, channel_int)}. A live reference — if
            the caller mutates this SAME dict in place as generators connect
            or disconnect, the engine sees the update without re-construction.
        load_pf_for_label: optional callable(label) -> load capacitance in pF,
            used only for the predicted-peak-current log line. Defaults to
            the global CAL_LOAD_CAP_PF for every label.
        plate_gain_for_label: optional callable(label) -> volts-to-plate-volts
            gain, used only for that same log line (e.g. the EEL5000's 1000x).
            Defaults to 1.0 (log reflects generator volts directly) — pass
            the real amplifier gain when ramping an amp-label channel.
        """
        super().__init__(parent)
        self._map = funcgen_map
        self._ramp_duration_s = ramp_duration_s
        self._load_pf_for_label = load_pf_for_label or (lambda label: CAL_LOAD_CAP_PF)
        self._plate_gain_for_label = plate_gain_for_label or (lambda label: 1.0)
        self._round_trip_s: dict[str, float] = {}   # label -> measured seconds
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        # label -> {"mode": "offset"|"amplitude", "target_v", "current_v", "step_v"}
        self._ramps: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Public state — Phase 6's regulation detector reads this to suspend
    # itself during a ramp instead of firing a false alarm on the legitimate
    # lag between commanded and measured values.
    # ------------------------------------------------------------------

    def is_ramping(self, label: str) -> bool:
        return label in self._ramps

    def ramping_labels(self) -> set:
        return set(self._ramps.keys())

    def current_step_value(self, label: str):
        """The interim generator-volts value most recently commanded for
        `label`, or None if it is not currently ramping. Compare a live
        measurement against THIS during a ramp, never against the final
        target — the measured value legitimately lags the target while a
        ramp is in progress."""
        ramp = self._ramps.get(label)
        return ramp["current_v"] if ramp else None

    # ------------------------------------------------------------------
    # Commanding a target
    # ------------------------------------------------------------------

    def retarget(self, label: str, target_v: float, mode: str = "offset") -> None:
        """Set or update the target for `label`, in generator volts (offset
        mode) or Vpp (amplitude mode). Does not queue: if a ramp is already
        running for this label, its target is updated in place and the ramp
        continues from wherever it currently is — queuing would produce
        surprising behaviour the operator cannot predict.
        """
        if label not in self._map:
            self.ramp_failed.emit(label, f"unknown channel {label!r}")
            return
        if mode not in _CLAMP:
            raise ValueError(f"mode must be 'offset' or 'amplitude', got {mode!r}")
        lo, hi = _CLAMP[mode]
        target_v = max(lo, min(hi, target_v))

        if label not in self._round_trip_s:
            self._measure_round_trip(label)

        ramp = self._ramps.get(label)
        if ramp is None:
            start_v = self._read_current_value(label, mode)
            ramp = {"mode": mode, "target_v": target_v,
                    "current_v": start_v, "step_v": 0.0}
            self._ramps[label] = ramp
            self.ramp_started.emit(label)
        else:
            ramp["target_v"] = target_v
            ramp["mode"] = mode

        self._recompute_step(label)
        self._log_predicted_current(label)

        if not self._timer.isActive():
            interval_ms = max(1, int(self._round_trip_s[label] * 1000))
            self._timer.start(interval_ms)

    def abort(self) -> None:
        """Stop every ramp immediately without commanding anything further.

        This bypasses the ramp entirely and is the correct response to a
        genuine fault: a fast collapse is the lesser risk once something has
        already gone wrong. It does NOT zero or disable outputs — callers
        needing a hard stop use AmpDrive.zero_and_off_all() directly, which
        stays completely outside the ramp path (see
        docs/AMP_ENVELOPE_AND_HV_SAFETY_PLAN.md Section 5.3).
        """
        self._timer.stop()
        self._ramps.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _measure_round_trip(self, label: str) -> None:
        gen, _ = self._map[label]
        t0 = time.perf_counter()
        try:
            gen.get_error()
            self._round_trip_s[label] = max(time.perf_counter() - t0, 0.001)
        except Exception:
            self._round_trip_s[label] = FALLBACK_ROUND_TRIP_S

    def _read_current_value(self, label: str, mode: str) -> float:
        gen, channel = self._map[label]
        try:
            state = gen.get_state(channel)
            if "error" in state:
                return 0.0
            return state["offset"] if mode == "offset" else state["amp"]
        except Exception:
            return 0.0

    def _recompute_step(self, label: str) -> None:
        ramp = self._ramps[label]
        round_trip_s = self._round_trip_s.get(label, FALLBACK_ROUND_TRIP_S)
        n_steps = max(1, round(self._ramp_duration_s / round_trip_s))
        ramp["step_v"] = (ramp["target_v"] - ramp["current_v"]) / n_steps

    def _log_predicted_current(self, label: str) -> None:
        ramp = self._ramps[label]
        round_trip_s = self._round_trip_s.get(label, FALLBACK_ROUND_TRIP_S)
        load_pf = self._load_pf_for_label(label)
        gain = self._plate_gain_for_label(label)
        step_plate_v = abs(ramp["step_v"]) * gain
        i_pred_ma = (load_pf * 1e-12) * step_plate_v / max(round_trip_s, 1e-6) * 1000.0
        msg = (f"[RAMP] {label}: {ramp['current_v']:.4f} -> {ramp['target_v']:.4f} V, "
               f"step {ramp['step_v']:+.5f} V / {round_trip_s * 1000:.1f} ms, "
               f"predicted peak current {i_pred_ma:.3f} mA")
        print(msg)
        log.info(msg)

    def _tick(self) -> None:
        finished = []
        worst_step_v = 0.0
        # Canonical order first (pair members adjacent), then anything else.
        ordered = [lbl for lbl in _CHANNEL_ORDER if lbl in self._ramps]
        ordered += [lbl for lbl in self._ramps if lbl not in _CHANNEL_ORDER]

        for label in ordered:
            ramp = self._ramps[label]
            worst_step_v = max(worst_step_v, abs(ramp["step_v"]))
            self._step_one(label, ramp)
            if abs(ramp["target_v"] - ramp["current_v"]) < _DONE_EPS_V:
                finished.append(label)

        if worst_step_v > 0:
            log.debug("[RAMP] tick worst-case per-step differential: %.5f V", worst_step_v)

        for label in finished:
            del self._ramps[label]
            self.ramp_finished.emit(label)

        if not self._ramps:
            self._timer.stop()

    def _step_one(self, label: str, ramp: dict) -> None:
        gen, channel = self._map[label]
        remaining = ramp["target_v"] - ramp["current_v"]
        step = ramp["step_v"]
        is_last = step == 0.0 or abs(remaining) <= abs(step) + 1e-12
        next_v = ramp["target_v"] if is_last else ramp["current_v"] + step
        lo, hi = _CLAMP[ramp["mode"]]
        next_v = max(lo, min(hi, next_v))

        try:
            if ramp["mode"] == "offset":
                if is_last:
                    gen.set_offset(channel, next_v)
                else:
                    gen.write_fast(f":SOURce{int(channel)}:VOLTage:OFFSet {next_v}")
            else:
                if is_last:
                    gen.set_amplitude(channel, next_v)
                else:
                    gen.write_fast(f":SOURce{int(channel)}:VOLTage {next_v}")
        except Exception as e:
            log.error("ramp step failed for %s: %s", label, e)
            print(f"[RAMP] ERROR {label}: {e}")
            self.ramp_failed.emit(label, str(e))
            ramp["current_v"] = next_v   # don't retry the same failing step forever
            return

        ramp["current_v"] = next_v
        self.ramp_progress.emit(label, ramp["current_v"], ramp["target_v"])


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    class _FakeGen:
        def __init__(self):
            self.calls = []
            self._offset = {1: 0.0, 2: 0.0}
            self._amp = {1: 0.0, 2: 0.0}

        def get_error(self):
            return '0,"No error"'

        def get_state(self, ch):
            return {"shape": "DC", "freq": 0.0, "amp": self._amp[ch],
                     "offset": self._offset[ch], "phase": 0.0,
                     "output": True, "load": "INFinity"}

        def set_offset(self, ch, v):
            self.calls.append(("set_offset", ch, v))
            self._offset[ch] = v

        def set_amplitude(self, ch, vpp):
            self.calls.append(("set_amplitude", ch, vpp))
            self._amp[ch] = vpp

        def write_fast(self, cmd):
            self.calls.append(("write_fast", cmd))
            value = float(cmd.split()[-1])
            if "OFFSet" in cmd:
                self._offset[1] = value
            else:
                self._amp[1] = value

    gen = _FakeGen()
    fmap = {"X+": (gen, 1), "X-": (gen, 2)}
    engine = RampEngine(fmap, ramp_duration_s=0.05)

    finished_labels: list[str] = []
    engine.ramp_finished.connect(finished_labels.append)

    engine.retarget("X+", 3.0, mode="offset")
    assert engine.is_ramping("X+")
    assert engine.current_step_value("X+") is not None
    print(f"[OK] retarget starts a ramp: current_step_value={engine.current_step_value('X+')}")

    # Drive the timer manually rather than running the Qt event loop, matching
    # this repo's convention of invoking Qt-timer slots directly in tests.
    for _ in range(200):
        if not engine.is_ramping("X+"):
            break
        engine._tick()
    assert "X+" in finished_labels
    assert abs(gen._offset[1] - 3.0) < 1e-6, gen._offset[1]
    print(f"[OK] ramp reaches target: {gen._offset[1]:.4f} V")

    # Retarget mid-ramp continues from the current position, does not queue.
    engine.retarget("X+", 1.0, mode="offset")
    engine.retarget("X+", 4.0, mode="offset")   # second call updates target in place
    assert engine._ramps["X+"]["target_v"] == 4.0
    print("[OK] retarget mid-ramp updates target in place rather than queuing")

    for _ in range(200):
        if not engine.is_ramping("X+"):
            break
        engine._tick()
    assert abs(gen._offset[1] - 4.0) < 1e-6, gen._offset[1]
    print(f"[OK] retargeted ramp reaches new target: {gen._offset[1]:.4f} V")

    # abort() bypasses the ramp entirely.
    engine.retarget("X-", 5.0, mode="offset")
    assert engine.is_ramping("X-")
    engine.abort()
    assert not engine.is_ramping("X-")
    assert not engine.ramping_labels()
    print("[OK] abort() clears all ramps immediately")

    print("\n[OK] ramp_engine self-test passed")
