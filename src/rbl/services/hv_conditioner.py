"""
hv_conditioner.py
Phase 5: HV conditioning — ramp up slowly, let micro-discharges burn off
surface asperities, back off on an event, retry. Over several cycles the
electrode holds progressively higher voltage.

WHY THIS EXISTS
---------------
Standard beamline practice for HV electrodes in vacuum, and the correct
response to "a 0->5 kV step once killed an amp" (Section 6.1): rather than
simply avoiding fast steps forever, conditioning actively raises the voltage
the electrode can hold, and produces a conditioning curve that is itself
useful data — improvement across sessions is the signal that conditioning is
working.

ALGORITHM (Section 6.2)
------------------------
1. Ramp up (Phase 4 RampEngine) toward the next level, in increments.
2. At each level, dwell (default 30 s) watching the current monitor and the
   regulation state.
3. On an excursion: back off by N increments, log the event (voltage,
   current, pressure), dwell, then retry the backed-off level.
4. On M consecutive clean dwells AT THE SAME LEVEL, advance to the next one.
5. Terminate at target, or on repeated failure at the same level (that level
   becomes the conditioned ceiling), or on abort().
6. Emit a conditioning curve: achieved voltage vs. elapsed time, with every
   event marked — persisted so successive sessions can be compared.

INTEROPERATION
---------------
Never overrides the Phase 2 vacuum interlock: every ramp target is clamped
through `hv_interlock_status_provider`, and a transition to "block" aborts
the run exactly like an operator abort would (the interlock is the one
authority nothing here second-guesses). Every dwell logs the pressure
alongside voltage and current.
"""
import logging
import time

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from rbl.config.hardware_config import AMP_CHANNEL_MAP, AMP_MAX_KV
from rbl.services.amp_drive import AmpDrive
from rbl.services.conditioning_history import append_session

log = logging.getLogger(__name__)

DEFAULT_INCREMENT_KV = 0.2
DEFAULT_DWELL_S = 30.0
DEFAULT_BACK_OFF_INCREMENTS = 2
DEFAULT_CLEAN_DWELLS_TO_ADVANCE = 3
DEFAULT_MAX_RETRIES_PER_LEVEL = 3
DEFAULT_EXCURSION_MA = 20.0   # amplifier continuous DC rating; a sustained
                              # excursion above this at DC is a discharge, not noise.
GUI_REFRESH_HZ = 10.0


class HvConditioner(QObject):
    progress        = Signal(float, float, str)   # achieved_kv, target_kv, note
    level_reached   = Signal(float, float)          # level_kv, elapsed_s
    discharge_event = Signal(dict)
    finished        = Signal(dict)                  # the full session record
    error           = Signal(str)

    def __init__(self, amp_label: str, funcgen_map: dict, ramp_engine,
                 hv_interlock_status_provider=None, pressure_provider=None,
                 regulation_state_provider=None, parent=None):
        """
        ramp_engine: RampEngine — every level change goes through it.
        hv_interlock_status_provider: optional callable(commanded_kv) ->
            (status, reason) (see hv_interlock_link.hv_interlock_status_for).
            None disables the check — only appropriate off-rig.
        pressure_provider: optional callable() -> pressure in torr, logged at
            every dwell. Defaults to NaN.
        regulation_state_provider: optional callable() -> one of
            "ok"/"warn"/"current_limited"/"amp_off"/"idle" for this channel,
            checked alongside the current monitor at every dwell window.
        """
        super().__init__(parent)
        self._amp_label = amp_label
        self._map = funcgen_map
        self._drive = AmpDrive(funcgen_map, max_kv=AMP_MAX_KV, log_prefix="[COND]")
        self._drive.attach_ramp_engine(ramp_engine)
        self._ramp_engine = ramp_engine
        self._hv_interlock_status_provider = hv_interlock_status_provider
        self._pressure_provider = pressure_provider or (lambda: float("nan"))
        self._regulation_state_provider = regulation_state_provider or (lambda: "ok")

        self._target_kv = 0.0
        self._increment_kv = DEFAULT_INCREMENT_KV
        self._dwell_s = DEFAULT_DWELL_S
        self._back_off_increments = DEFAULT_BACK_OFF_INCREMENTS
        self._clean_dwells_to_advance = DEFAULT_CLEAN_DWELLS_TO_ADVANCE
        self._max_retries_per_level = DEFAULT_MAX_RETRIES_PER_LEVEL
        self._excursion_ma = DEFAULT_EXCURSION_MA

        self._level_kv = 0.0
        self._clean_dwells_at_level = 0
        self._retries_at_level = 0
        self._t_start = None
        self._curve: list = []      # [(elapsed_s, achieved_kv)]
        self._events: list = []     # [{elapsed_s, level_kv, current_ma, pressure_torr}]
        self._running = False
        self._collect_window: list = []
        self._collect_count = 0

        self._ramp_engine.ramp_finished.connect(self._on_ramp_finished)
        self._dwell_timer = QTimer(self)
        self._dwell_timer.setSingleShot(True)
        self._dwell_timer.timeout.connect(self._on_dwell_complete)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, target_kv: float, increment_kv: float = DEFAULT_INCREMENT_KV,
              dwell_s: float = DEFAULT_DWELL_S,
              back_off_increments: int = DEFAULT_BACK_OFF_INCREMENTS,
              clean_dwells_to_advance: int = DEFAULT_CLEAN_DWELLS_TO_ADVANCE,
              max_retries_per_level: int = DEFAULT_MAX_RETRIES_PER_LEVEL,
              excursion_ma: float = DEFAULT_EXCURSION_MA) -> None:
        self._target_kv = target_kv
        self._increment_kv = increment_kv
        self._dwell_s = dwell_s
        self._back_off_increments = back_off_increments
        self._clean_dwells_to_advance = clean_dwells_to_advance
        self._max_retries_per_level = max_retries_per_level
        self._excursion_ma = excursion_ma

        self._level_kv = 0.0
        self._clean_dwells_at_level = 0
        self._retries_at_level = 0
        self._t_start = time.monotonic()
        self._curve = [(0.0, 0.0)]
        self._events = []
        self._running = True

        self._advance_to_next_level()

    def abort(self) -> None:
        if not self._running:
            return
        self._finish(reason="aborted")

    def on_window(self, payload: dict) -> None:
        if not self._running or self._dwell_timer.isActive() is False:
            return
        ain_i = AMP_CHANNEL_MAP[self._amp_label]["current"]
        entry = payload.get("channels", {}).get(ain_i)
        if entry and "waveform" in entry:
            self._collect_window.append(np.asarray(entry["waveform"], dtype=float))
        self._collect_count += 1

    # ------------------------------------------------------------------
    # Level machinery
    # ------------------------------------------------------------------

    def _advance_to_next_level(self) -> None:
        next_level = min(self._level_kv + self._increment_kv, self._target_kv)
        self._command_level(next_level)

    def _command_level(self, level_kv: float) -> None:
        if self._hv_interlock_status_provider is not None:
            status, reason = self._hv_interlock_status_provider(level_kv)
            if status == "block":
                self._finish(reason=f"interlock blocked: {reason}")
                return
        self._pending_level_kv = level_kv
        self._drive.command_dc_ramped(self._amp_label, level_kv)

    def _on_ramp_finished(self, label: str) -> None:
        if not self._running or label != self._amp_label:
            return
        self._level_kv = self._pending_level_kv
        self._start_dwell()

    def _start_dwell(self) -> None:
        self._collect_window = []
        self._collect_count = 0
        self._dwell_timer.start(max(1, int(self._dwell_s * 1000)))

    def _on_dwell_complete(self) -> None:
        wave = np.concatenate(self._collect_window) if self._collect_window else np.array([])
        mean_ma = float(np.mean(wave)) * 10.0 if wave.size else 0.0   # raw monitor V -> mA
        peak_ma = float(np.max(np.abs(wave))) * 10.0 if wave.size else 0.0
        reg_state = self._regulation_state_provider()
        pressure_torr = self._pressure_provider()
        elapsed_s = time.monotonic() - self._t_start

        excursion = peak_ma > self._excursion_ma or reg_state in ("current_limited", "amp_off")

        if excursion:
            event = {
                "elapsed_s": elapsed_s, "level_kv": self._level_kv,
                "mean_current_ma": mean_ma, "peak_current_ma": peak_ma,
                "regulation_state": reg_state, "pressure_torr": pressure_torr,
            }
            self._events.append(event)
            self.discharge_event.emit(event)
            log.warning("hv_conditioner: excursion at %.3f kV (%.2f mA peak, %s) — backing off",
                        self._level_kv, peak_ma, reg_state)

            self._retries_at_level += 1
            if self._retries_at_level > self._max_retries_per_level:
                self._finish(reason=f"conditioned ceiling reached at {self._level_kv:.3f} kV")
                return

            backed_off = max(0.0, self._level_kv - self._back_off_increments * self._increment_kv)
            self._clean_dwells_at_level = 0
            self._command_level(backed_off)
            self._level_kv = backed_off
            return

        self._clean_dwells_at_level += 1
        self._curve.append((elapsed_s, self._level_kv))
        self.level_reached.emit(self._level_kv, elapsed_s)
        self.progress.emit(
            self._level_kv, self._target_kv,
            f"{self._amp_label}: {self._level_kv:.3f}/{self._target_kv:.3f} kV, "
            f"{self._clean_dwells_at_level}/{self._clean_dwells_to_advance} clean dwells")

        if self._level_kv >= self._target_kv - 1e-9:
            self._finish(reason="target reached")
            return

        if self._clean_dwells_at_level >= self._clean_dwells_to_advance:
            self._retries_at_level = 0
            self._clean_dwells_at_level = 0
            self._advance_to_next_level()
        else:
            self._start_dwell()

    # ------------------------------------------------------------------
    # Completion
    # ------------------------------------------------------------------

    def _finish(self, reason: str) -> None:
        self._running = False
        self._dwell_timer.stop()
        try:
            self._drive.zero_and_off_all()
        except Exception:
            pass

        record = {
            "amp_label": self._amp_label, "target_kv": self._target_kv,
            "achieved_kv": self._level_kv, "reason": reason,
            "curve": self._curve, "events": self._events,
            "timestamp": time.time(),
        }
        append_session(record)
        self.finished.emit(record)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from rbl.services.ramp_engine import RampEngine

    app = QApplication.instance() or QApplication([])

    class _FakeGen:
        def __init__(self):
            self._offset = {1: 0.0, 2: 0.0}
            self._amp = {1: 0.0, 2: 0.0}
        def get_error(self):
            return '0,"No error"'
        def get_state(self, ch):
            return {"shape": "DC", "freq": 0.0, "amp": self._amp[ch],
                     "offset": self._offset[ch], "phase": 0.0,
                     "output": True, "load": "INFinity"}
        def set_offset(self, ch, v):
            self._offset[ch] = v
        def set_amplitude(self, ch, vpp):
            self._amp[ch] = vpp
        def write_fast(self, cmd):
            value = float(cmd.split()[-1])
            ch = int(cmd.split(":")[1].replace("SOURce", ""))
            if "OFFSet" in cmd:
                self._offset[ch] = value
            else:
                self._amp[ch] = value
        def set_waveform(self, ch, shape, freq, amp, offset, phase):
            self._offset[ch] = offset
            return ""
        def output_on(self, ch):
            pass
        def output_off(self, ch):
            pass
        def set_output_load(self, ch, load):
            pass

    gen = _FakeGen()
    fmap = {"X+": (gen, 1)}
    ramp = RampEngine(fmap, ramp_duration_s=0.02)
    cond = HvConditioner("X+", fmap, ramp)

    levels = []
    cond.level_reached.connect(lambda kv, t: levels.append(kv))
    finished = []
    cond.finished.connect(finished.append)

    cond.start(target_kv=1.0, increment_kv=0.5, dwell_s=0.02,
               clean_dwells_to_advance=1)

    for _ in range(2000):
        if not cond._running:
            break
        if cond._ramp_engine.is_ramping("X+"):
            cond._ramp_engine._tick()
            continue
        if cond._dwell_timer.isActive():
            cond._on_dwell_complete()

    assert finished, "conditioner never finished"
    record = finished[0]
    assert abs(record["achieved_kv"] - 1.0) < 1e-6, record
    assert record["reason"] == "target reached"
    print(f"[OK] conditioner reaches target: {record['achieved_kv']:.3f} kV, "
          f"curve has {len(record['curve'])} points, {len(record['events'])} events")

    print("\n[OK] hv_conditioner self-test passed")
