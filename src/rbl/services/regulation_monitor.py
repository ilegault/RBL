"""
regulation_monitor.py
Live, debounced regulation-fault detector: feeds each stream window's
commanded/measured voltage and current through `rbl.hardware.regulation` and
raises `fault_detected` only once a real fault is confirmed — never on a
single noisy window, and never during a legitimate ramp.

WHY THIS EXISTS
---------------
`rbl.hardware.regulation.classify()` is pure math: one window in, one
verdict out. Wiring it straight to a UI would fire on every noisy sample,
and worse, on every single Phase 4 ramp — the measured value legitimately
lags the target while a ramp is in progress, and that lag looks exactly
like "current_limited" to a one-shot classifier. This service is the
stateful layer that turns "instantaneous verdict" into "confirmed fault":
debounced, and blind to a ramp's ordinary transient.

THREE GUARDS — omit any and it cries wolf (Section 7.3)
---------------------------------------------------------
1. Arm threshold — handled inside `classify()` itself: below a minimum
   commanded amplitude the ratio is undefined and the verdict is "idle".
2. Debounce — require `debounce_windows` consecutive windows in the SAME
   fault state before firing, reusing the
   `CalibrationRunner._check_overcurrent`/`CAL_TRIP_CONSEC_WINDOWS` pattern.
3. Ramp coupling — evaluation is suspended entirely while the attached
   RampEngine reports the channel as ramping, plus `post_ramp_blank_windows`
   more windows after the ramp finishes (mirroring
   `CalibrationRunner`'s `CAL_TRIP_BLANK_WINDOWS`), so the tail of settling
   right after a ramp stops ticking cannot fire a false alarm either.
"""
import logging

from PySide6.QtCore import QObject, Signal

from rbl.hardware.regulation import regulation_ratio, classify, DEFAULT_ARM_THRESHOLD_KV

log = logging.getLogger(__name__)

DEFAULT_DEBOUNCE_WINDOWS = 2          # mirrors CAL_TRIP_CONSEC_WINDOWS
DEFAULT_POST_RAMP_BLANK_WINDOWS = 2   # mirrors CAL_TRIP_BLANK_WINDOWS

_FAULT_STATES = ("amp_off", "current_limited")


class RegulationMonitor(QObject):
    fault_detected = Signal(str, str, str)   # label, state, reason — debounced, fires once
    state_changed  = Signal(str, str)        # label, state — every non-suspended evaluation

    def __init__(self, ramp_engine=None, debounce_windows: int = DEFAULT_DEBOUNCE_WINDOWS,
                 post_ramp_blank_windows: int = DEFAULT_POST_RAMP_BLANK_WINDOWS,
                 arm_threshold_kv: float = DEFAULT_ARM_THRESHOLD_KV, parent=None):
        """
        ramp_engine: optional object exposing `is_ramping(label) -> bool`
            (RampEngine satisfies this). None disables the ramp-coupling
            guard entirely — only appropriate where nothing ever ramps.
        """
        super().__init__(parent)
        self._ramp_engine = ramp_engine
        self._debounce_windows = debounce_windows
        self._post_ramp_blank_windows = post_ramp_blank_windows
        self._arm_threshold_kv = arm_threshold_kv
        self._consec = {}        # label -> (state, count)
        self._blank_left = {}    # label -> windows remaining to ignore post-ramp
        self._was_ramping = {}   # label -> True while last seen mid-ramp
        self._fired = {}         # label -> state already reported (fire once, not every window)

    def reset(self, label: str = None) -> None:
        """Clear all debounce/blank/fired state for one label, or every
        label if none is given. Call this when starting a fresh run so a
        fault confirmed in a previous run cannot re-fire without a new
        qualifying sequence of windows."""
        stores = (self._consec, self._blank_left, self._was_ramping, self._fired)
        if label is None:
            for store in stores:
                store.clear()
        else:
            for store in stores:
                store.pop(label, None)

    def evaluate(self, label: str, measured_kv: float, commanded_kv: float,
                 i_measured_ma: float, i_limit_ma: float) -> str:
        """Feed one stream window's measurement for `label`. Returns the
        instantaneous classification for live display; `fault_detected` only
        fires once the debounce guard (Section 7.3, guard 2) confirms it."""
        if self._ramp_engine is not None and self._ramp_engine.is_ramping(label):
            self._was_ramping[label] = True
            self._consec.pop(label, None)
            return "idle"

        if self._was_ramping.pop(label, False):
            self._blank_left[label] = self._post_ramp_blank_windows

        if self._blank_left.get(label, 0) > 0:
            self._blank_left[label] -= 1
            self._consec.pop(label, None)
            return "idle"

        v_ratio = regulation_ratio(measured_kv, commanded_kv)
        state, reason = classify(v_ratio, i_measured_ma, i_limit_ma, commanded_kv,
                                  arm_threshold_kv=self._arm_threshold_kv)
        self.state_changed.emit(label, state)

        if state not in _FAULT_STATES:
            self._consec.pop(label, None)
            self._fired.pop(label, None)
            return state

        prev_state, prev_count = self._consec.get(label, (None, 0))
        count = prev_count + 1 if prev_state == state else 1
        self._consec[label] = (state, count)

        if count >= self._debounce_windows and self._fired.get(label) != state:
            self._fired[label] = state
            self.fault_detected.emit(label, state, reason)

        return state


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    class _FakeRamp:
        def __init__(self):
            self.ramping = set()
        def is_ramping(self, label):
            return label in self.ramping

    ramp = _FakeRamp()
    monitor = RegulationMonitor(ramp_engine=ramp, debounce_windows=2,
                                 post_ramp_blank_windows=2)
    faults = []
    monitor.fault_detected.connect(lambda *a: faults.append(a))

    # A single bad window must not fire — debounce guard.
    monitor.evaluate("X+", measured_kv=0.01, commanded_kv=3.0,
                      i_measured_ma=0.1, i_limit_ma=20.0)
    assert not faults
    print("[OK] a single bad window does not fire (debounce)")

    # A second consecutive bad window confirms it.
    monitor.evaluate("X+", measured_kv=0.01, commanded_kv=3.0,
                      i_measured_ma=0.1, i_limit_ma=20.0)
    assert faults and faults[0][0] == "X+" and faults[0][1] == "amp_off"
    print("[OK] two consecutive bad windows confirm amp_off")

    # It does not re-fire every subsequent window for the same fault.
    monitor.evaluate("X+", measured_kv=0.01, commanded_kv=3.0,
                      i_measured_ma=0.1, i_limit_ma=20.0)
    assert len(faults) == 1
    print("[OK] does not re-fire on every window once confirmed")

    # A ramp in progress suspends evaluation entirely — no false alarm.
    faults.clear()
    ramp.ramping.add("Y+")
    for _ in range(5):
        monitor.evaluate("Y+", measured_kv=0.5, commanded_kv=3.0,
                          i_measured_ma=19.9, i_limit_ma=20.0)
    assert not faults
    print("[OK] a ramp in progress never fires, however bad it looks")

    # Post-ramp blank window: even right after the ramp ends, still no fire.
    ramp.ramping.discard("Y+")
    monitor.evaluate("Y+", measured_kv=0.5, commanded_kv=3.0,
                      i_measured_ma=19.9, i_limit_ma=20.0)
    monitor.evaluate("Y+", measured_kv=0.5, commanded_kv=3.0,
                      i_measured_ma=19.9, i_limit_ma=20.0)
    assert not faults
    print("[OK] post-ramp blank windows absorb the settling tail")

    # After the blank window budget is spent, a genuine fault still fires.
    monitor.evaluate("Y+", measured_kv=0.5, commanded_kv=3.0,
                      i_measured_ma=19.9, i_limit_ma=20.0)
    monitor.evaluate("Y+", measured_kv=0.5, commanded_kv=3.0,
                      i_measured_ma=19.9, i_limit_ma=20.0)
    assert faults and faults[0][0] == "Y+"
    print("[OK] a real fault after the blank window still fires")

    # reset() clears state for a fresh run.
    monitor.reset("Y+")
    assert "Y+" not in monitor._consec and "Y+" not in monitor._fired
    print("[OK] reset() clears debounce/fired state")

    print("\n[OK] regulation_monitor self-test passed")
