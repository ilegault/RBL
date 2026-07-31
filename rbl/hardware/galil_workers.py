"""
galil_workers.py
Background QThread workers for the Galil DMC-4103: state polling, the per-axis
homing routine, and the all-axes automatic homing sequencer.

Bare QThreads, not GUI widgets — moved out of motor_tab.py so they are
testable and constructible without a widget, matching labjack_stream_worker.py.

WHAT HOMING ACTUALLY TAKES, AND WHY THERE IS A SEEK PHASE
---------------------------------------------------------
HM is a *search*: the controller creeps the axis along looking for the home
switch, at the speed it was given. The three-pass routine below runs it at 225,
112 and 58 cps precisely because a slow final approach is what makes the zero
repeatable — but at 58 cps an axis parked at the far end of its travel would
take many minutes to arrive, and would run out the pass timeout long before it
did. So the working procedure has always been: jog the axis down onto the limit
by hand first, THEN press Home. Two gestures per axis, eight for a set of
slits, every time the program starts.

`AxisHomeRoutine` is those two gestures as one thing — SEEK (a fast jog until
the home limit trips) followed by HOME (the three-pass HM, then DP=0) — and
`AutoHomeAllWorker` is that routine run over all four axes on ONE thread, so at
most one axis is ever in motion and at most one command is ever on the wire.
"""
import time

from PySide6.QtCore import QThread, Signal

from rbl.hardware.galil_driver import GalilController
from rbl.config import hardware_config as SC


# ─── Background poll thread ───────────────────────────────────────────────────

class GalilPollWorker(QThread):
    state = Signal(dict)
    error = Signal(str)

    def __init__(self, galil: GalilController, period_s: float = 0.2):
        super().__init__()
        self.galil    = galil
        self.period   = period_s
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        while self._running and self.galil.connected:
            t0 = time.time()
            try:
                snapshot = {}
                for axis in SC.AXIS_LETTERS:
                    snapshot[axis] = {
                        "pos":      self.galil.get_position(axis),
                        "moving":   self.galil.is_moving(axis),
                        "switches": self.galil.get_switch_states(axis),
                        "enabled":  not self.galil.is_motor_off(axis),
                    }
                self.state.emit(snapshot)
            except Exception as e:
                self.error.emit(str(e))
                break
            elapsed   = time.time() - t0
            remaining = max(0.0, self.period - elapsed)
            self.msleep(int(remaining * 1000))


# ─── The per-axis homing routine ──────────────────────────────────────────────

class AxisHomeRoutine:
    """Everything one axis needs in order to end up homed and zeroed at DP=0.

    Deliberately NOT a QThread. The identical routine has to run on the
    single-axis worker's thread, four times back-to-back on the sequencer's
    one thread, and — for the "all together" button — on four threads at once.
    Something that has to be all three cannot be the thing that owns a thread,
    so the caller supplies a progress callback and a cancellation predicate and
    drives `run()` on whatever thread it likes.

    ONE COMMAND AT A TIME falls out of this being straight-line code: every
    phase waits for the motion it started to stop before it issues the next
    command, and GalilController.cmd holds a lock for the send/receive pair.
    Nothing here overlaps two commands on one axis. (Two INSTANCES driven from
    two threads will interleave commands on different axes — that is the whole
    difference between the sequential and simultaneous buttons, and it is the
    caller's choice, not this class's.)
    """

    # Speeds and matching back-off distances for each successive HM pass
    # (coarse -> fine). Slower each time: the last pass is what sets the zero,
    # and a slow final approach is the only thing that makes it repeatable.
    SPEEDS   = [225, 112, 58]
    BACKOFFS = [1000, 500, 250]   # counts to back off before each pass

    _SEEK_SETTLE_S = 0.5    # let BG actually start the jog before believing _BG
    _SEEK_POLL_S   = 0.1    # how often the seek asks the switches where it is
    _IDLE_POLL_S   = 0.2

    def __init__(self, galil: GalilController, axis: str,
                 progress=None, cancelled=None):
        self.galil      = galil
        self.axis       = axis
        self._progress  = progress or (lambda _msg: None)
        self._cancelled = cancelled or (lambda: False)

    # ---- The whole routine ---------------------------------------------------

    def run(self, seek_first: bool = False) -> tuple[bool, str]:
        """Home this axis. Returns (success, message) — never raises.

        With `seek_first`, the axis is jogged down onto the home limit before
        HM runs, which is the manual "-Jog then Home" pair collapsed into one
        call. Without it this is exactly the routine the per-axis Home button
        has always run.
        """
        axis = self.axis
        try:
            if seek_first:
                ok, msg = self.seek_home_limit()
                if not ok:
                    return False, msg
            return self.home_passes()
        except Exception as e:
            return False, f"{axis}: homing error — {e}"

    # ---- Phase 1: seek ------------------------------------------------------

    def seek_home_limit(self, timeout: float = None) -> tuple[bool, str]:
        """Jog toward the home limit until a switch says we have arrived.

        Negative direction, matching `begin_home` — on these stages the home
        switch sits at the low-count end of the travel, so both the manual jog
        and HM's own search go the same way.

        Stops on EITHER the home switch or the reverse limit. They are read
        separately but mean the same thing here: the axis has reached the end
        HM searches from. Waiting only for `home_switch` would jog straight
        into a reverse limit and sit there until the timeout on any stage
        where the two are the same physical switch.
        """
        g, axis = self.galil, self.axis
        timeout = SC.HOME_SEEK_TIMEOUT_S if timeout is None else timeout

        if self._cancelled():
            return False, f"{axis}: cancelled before seeking the home limit"

        if self._at_home_limit():
            self._progress(f"{axis}: already on the home limit — no seek needed")
            return True, ""

        speed = SC.HOME_SEEK_SPEED_COUNTS_PER_SEC
        self._progress(
            f"{axis}: seeking home limit — JG -{speed} cps "
            f"({SC.cps_to_mm_per_sec(axis, speed):.2f} mm/s)…"
        )
        g.jog_start(axis, -speed)
        time.sleep(self._SEEK_SETTLE_S)   # BG has to take effect before _BG means anything

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._cancelled():
                g.stop(axis)
                return False, f"{axis}: cancelled while seeking the home limit"

            if self._at_home_limit():
                g.stop(axis)
                self._progress(f"{axis}: home limit reached")
                if not self.wait_idle(timeout=15.0):
                    return False, f"{axis}: timeout coming to rest on the home limit"
                return True, ""

            if not g.is_moving(axis):
                # The controller stopped the jog by itself. Either the limit
                # latched between two polls (CN's arg1 latches limits), or
                # something faulted — ask the switches which, rather than
                # reporting a success the axis did not achieve.
                if self._at_home_limit():
                    self._progress(f"{axis}: home limit reached")
                    return True, ""
                return False, (
                    f"{axis}: jog stopped before reaching the home limit — "
                    f"check the axis is energised (SH {axis}) and not sitting "
                    f"on the forward limit"
                )

            time.sleep(self._SEEK_POLL_S)

        g.stop(axis)
        return False, f"{axis}: timed out after {timeout:.0f} s seeking the home limit"

    def _at_home_limit(self) -> bool:
        sw = self.galil.get_switch_states(self.axis)
        return bool(sw["home_switch"] or sw["reverse_switch"])

    # ---- Phase 2: the three HM passes ---------------------------------------

    def home_passes(self) -> tuple[bool, str]:
        """Coarse -> medium -> fine HM, then DP=0 on the final position."""
        g    = self.galil
        axis = self.axis
        n    = len(self.SPEEDS)

        # If already on home switch, back off using the coarse distance first
        sw = g.get_switch_states(axis)
        if sw["home_switch"]:
            self._progress(f"{axis}: on home switch — backing off {self.BACKOFFS[0]} counts…")
            g.move_relative(axis, self.BACKOFFS[0])
            if not self.wait_idle(timeout=15.0):
                return False, "Timeout while backing off home switch"

        for pass_num, (speed, backoff) in enumerate(zip(self.SPEEDS, self.BACKOFFS)):
            if self._cancelled():
                return False, "Homing cancelled by user"

            # Back off before every pass using this pass's distance
            if pass_num > 0:
                self._progress(
                    f"{axis}: pass {pass_num+1}/{n} — backing off {backoff} counts…"
                )
                g.move_relative(axis, backoff)
                if not self.wait_idle(timeout=15.0):
                    return False, f"Timeout on back-off before pass {pass_num+1}"

            self._progress(
                f"{axis}: pass {pass_num+1}/{n} — "
                f"HM at {speed} cps "
                f"({SC.cps_to_mm_per_sec(axis, speed):.2f} mm/s)…"
            )
            g.begin_home(axis, speed)
            time.sleep(0.5)   # let motion start

            if not self.wait_idle(timeout=60.0):
                self._progress(f"{axis}: HM timeout on pass {pass_num+1}")
                g.stop(axis)
                time.sleep(0.3)
                # Restore speed and report failure — don't continue further passes
                try:
                    g.set_speed(axis, SC.DEFAULT_SPEED_COUNTS_PER_SEC)
                except Exception:
                    pass
                return False, f"{axis}: homing timed out on pass {pass_num+1}/{n}"

            self._progress(f"{axis}: pass {pass_num+1}/{n} complete")

        # All passes done — define zero on the final fine-speed position
        g.define_zero(axis)
        g.set_speed(axis, SC.DEFAULT_SPEED_COUNTS_PER_SEC)
        return True, f"{axis}: homed ({n} passes), DP=0, SP restored"

    # ---- Shared wait ---------------------------------------------------------

    def wait_idle(self, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._cancelled():
                return False
            try:
                if not self.galil.is_moving(self.axis):
                    return True
            except Exception:
                return False
            time.sleep(self._IDLE_POLL_S)
        return False


# ─── Auto-homing worker (one axis) ────────────────────────────────────────────

class HomingWorker(QThread):
    """`AxisHomeRoutine` for one axis, on its own thread.

    `seek_first` is what the per-axis "Seek + Home" button sets and the plain
    "Home" button does not: same routine, one extra phase in front of it.
    """
    progress = Signal(str)
    done     = Signal(bool, str)   # success, message

    # Kept as class attributes because callers (and tests) read them to say
    # what the routine is about to do before it starts.
    _SPEEDS   = AxisHomeRoutine.SPEEDS
    _BACKOFFS = AxisHomeRoutine.BACKOFFS

    def __init__(self, galil: GalilController, axis: str, parent=None,
                 seek_first: bool = False):
        super().__init__(parent)
        self.galil      = galil
        self.axis       = axis
        self.seek_first = seek_first
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        routine = AxisHomeRoutine(
            self.galil, self.axis,
            progress=self.progress.emit,
            cancelled=lambda: self._cancelled,
        )
        ok, msg = routine.run(seek_first=self.seek_first)
        self.done.emit(ok, msg)

    def _wait_idle(self, timeout: float = 30.0) -> bool:
        """Retained for callers that drove the wait themselves."""
        return AxisHomeRoutine(
            self.galil, self.axis, cancelled=lambda: self._cancelled
        ).wait_idle(timeout)


# ─── Auto-homing worker (all axes, one at a time) ─────────────────────────────

class AutoHomeAllWorker(QThread):
    """Seek-and-home every axis in turn, on ONE thread.

    This is the answer to "eight clicks before I can start calibrating
    anything": one press homes the whole set while the operator works on
    another tab.

    Strictly sequential, and that is the point rather than an implementation
    detail. Axis N+1 is not touched until axis N has reported DP=0, so there
    is never more than one axis moving, never more than one command in flight,
    and a failure stops the sequence with the remaining axes untouched instead
    of leaving four half-homed slits. The simultaneous variant on the Stepper
    Motors tab does the opposite deliberately — it runs four `HomingWorker`s
    at once — and exists so the two can be compared on real hardware.
    """
    progress      = Signal(str)          # console line
    axis_started  = Signal(str)          # axis letter
    axis_finished = Signal(str, bool, str)   # axis letter, success, message
    done          = Signal(bool, str)    # all succeeded, summary

    def __init__(self, galil: GalilController, axes=None, parent=None,
                 seek_first: bool = True, stop_on_failure: bool = True):
        super().__init__(parent)
        self.galil           = galil
        self.axes            = list(axes if axes is not None else SC.AXIS_LETTERS)
        self.seek_first      = seek_first
        self.stop_on_failure = stop_on_failure
        self._cancelled      = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        homed, failed, skipped = [], [], []

        for axis in self.axes:
            if self._cancelled:
                skipped = self.axes[self.axes.index(axis):]
                break

            slit = SC.AXIS_NAMES.get(axis, axis)
            self.axis_started.emit(axis)
            self.progress.emit(
                f"# Auto-home {len(homed) + len(failed) + 1}/{len(self.axes)}: "
                f"axis {axis} [{slit}] …")

            routine = AxisHomeRoutine(
                self.galil, axis,
                progress=self.progress.emit,
                cancelled=lambda: self._cancelled,
            )
            ok, msg = routine.run(seek_first=self.seek_first)

            self.progress.emit(f"{'✓' if ok else '✗'} {msg}")
            self.axis_finished.emit(axis, ok, msg)
            (homed if ok else failed).append(axis)

            if not ok and self.stop_on_failure:
                skipped = self.axes[self.axes.index(axis) + 1:]
                break

        self.done.emit(not failed and not skipped,
                       self._summary(homed, failed, skipped))

    def _summary(self, homed, failed, skipped) -> str:
        if self._cancelled:
            head = "Auto-home cancelled"
        elif failed:
            head = f"Auto-home stopped on axis {failed[0]}"
        elif skipped:
            head = "Auto-home incomplete"
        else:
            head = "Auto-home complete"

        parts = [f"{len(homed)}/{len(self.axes)} axes homed"]
        if homed:
            parts.append("homed: " + ", ".join(homed))
        if failed:
            parts.append("failed: " + ", ".join(failed))
        if skipped:
            parts.append("not attempted: " + ", ".join(skipped))
        return f"{head} — " + "; ".join(parts)
