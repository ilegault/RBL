"""
galil_workers.py
Background QThread workers for the Galil DMC-4103: state polling, the per-axis
homing routine, and the all-axes automatic homing sequencer.

Bare QThreads, not GUI widgets — moved out of motor_tab.py so they are
testable and constructible without a widget, matching labjack_stream_worker.py.

HOMING ON THE RELEASE EDGE (ADR 0009)
------------------------------------
Each slit's home switch and reverse limit share the same physical sensor and
trip at the same point, so the controller's `HM` command always ends on the
reverse limit (stop code 3) before its second stage can run. Homing therefore
does not use `HM` or stop code 10 (see `docs/adr/0009-slits-homed-on-the-release-edge.md`).

Instead, each home pass has two parts:

  1. Approach: drive in (negative counts) at `approach_speed` (default
     `HOME_APPROACH_SPEED_COUNTS_PER_SEC`, 58 cps) until the axis stops with
     the home switch tripped. Stopping on the reverse limit too is expected.
  2. Creep out: step `+HOME_CREEP_STEP_COUNTS` (1 count) at a time, waiting
     for the axis to come to rest and reading the home switch after every step,
     until the switch reads clear. That release position is the pass's edge.
     Because the edge is always read at rest from the inside moving out,
     approach speed does not shift where zero lands.

Homing runs two passes (pass 2 backs out `HOME_PASS2_BACKOFF_COUNTS` = 300
counts first) and defines zero (`DP=0`) on pass 2's edge only if the two
edges agree within `HOME_EDGE_AGREEMENT_COUNTS` (4 counts). `AxisHomeRoutine`
is that sequence for one axis, and `AutoHomeAllWorker` runs it over all four
axes on ONE thread so at most one axis is ever in motion and at most one
command is ever on the wire.

BACKING OUT FIRST
-----------------
An axis that was turned off (MO) while sitting on its home switch (or reverse
limit) gives homing a confusing start. Every homing entry point therefore begins
by backing out (`HOME_START_BACKOFF_COUNTS` away from home, up to
`HOME_START_BACKOFF_TRIES` times) until neither the home switch nor the reverse
limit is active, unless the axis is already on its forward limit.

EDGE-RETURN TEST
----------------
`EdgeReturnRoutine` measures homing repeatability on one axis at one pass
speed: it backs out of the switch, then repeats N cycles of moving out
`out_counts` (+counts) and running a single HM pass back onto the home switch
at `pass_speed_cps`, recording `_RP` after each stop-code-10 arrival. Every
cycle approaches the edge from the same side (out, then one home pass back) so
backlash and switch hysteresis stay constant across readings. It never calls
`define_zero` or `define_zero_multi` because the measurement must not alter
where the slits think home is.
"""
import time

from PySide6.QtCore import QThread, Signal

from rbl.config import hardware_config as SC
from rbl.hardware.galil_driver import GalilController

# ─── Background poll thread ───────────────────────────────────────────────────

class GalilPollWorker(QThread):
    """5 Hz state poll that tolerates isolated failures.

    WHY THIS EXISTS: the poll used to give up on its first exception. During a
    homing run the controller is busy and one slow reply is routine; that single
    exception disconnected the Galil and cancelled the run. Now an exception
    below SC.GALIL_POLL_MAX_CONSECUTIVE_ERRORS emits `warning` and carries on;
    only that many failures in a row emit `error` and stop. A good poll resets
    the count, so two scattered hiccups never add up to a disconnect.
    """
    state = Signal(dict)
    warning = Signal(str)
    error = Signal(str)

    def __init__(self, galil: GalilController, period_s: float = 0.2):
        super().__init__()
        self.galil    = galil
        self.period   = period_s
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        failures = 0
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
                failures = 0
                self.state.emit(snapshot)
            except Exception as e:
                failures += 1
                if failures >= SC.GALIL_POLL_MAX_CONSECUTIVE_ERRORS:
                    self.error.emit(str(e))
                    break
                self.warning.emit(
                    f"Poll error {failures}/{SC.GALIL_POLL_MAX_CONSECUTIVE_ERRORS}: {e}")
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

    _SEEK_SETTLE_S = 0.5    # let BG actually start the jog before believing _BG
    _SEEK_POLL_S   = 0.1    # how often the seek asks the switches where it is
    _IDLE_POLL_S   = 0.2

    def __init__(self, galil: GalilController, axis: str,
                 progress=None, cancelled=None,
                 approach_speed: int | None = None):
        self.galil          = galil
        self.axis           = axis
        self._progress      = progress or (lambda _msg: None)
        self._cancelled     = cancelled or (lambda: False)
        self.approach_speed = (
            SC.HOME_APPROACH_SPEED_COUNTS_PER_SEC
            if approach_speed is None else approach_speed
        )

    # ---- The whole routine ---------------------------------------------------

    def run(self, seek_first: bool = False) -> tuple[bool, str]:
        """Home this axis. Returns (success, message) — never raises.

        With `seek_first`, the axis is jogged down onto the home limit before
        the release-edge passes run. Without it, pass 1 approaches at
        `approach_speed` directly.
        """
        axis = self.axis
        try:
            ok, msg = self.back_out_of_home()
            if not ok:
                return False, msg
            if seek_first:
                ok, msg = self.seek_home_limit()
                if not ok:
                    return False, msg
            return self.home_passes()
        except Exception as e:
            return False, f"{axis}: homing error — {e}"
        finally:
            self._restore_speeds()

    # ---- Phase 0: back out of home ------------------------------------------

    def back_out_of_home(self) -> tuple[bool, str]:
        """Back off the home switch / reverse limit before seeking or homing."""
        g, axis = self.galil, self.axis
        sw = g.get_switch_states(axis)
        if sw["forward_switch"]:
            self._progress(f"{axis}: on the forward limit — no back-out needed")
            return True, ""
        if not sw["home_switch"] and not sw["reverse_switch"]:
            return True, ""

        g.set_speed(axis, SC.HOME_SEEK_SPEED_COUNTS_PER_SEC)
        tries = SC.HOME_START_BACKOFF_TRIES
        counts = SC.HOME_START_BACKOFF_COUNTS
        for _ in range(tries):
            if self._cancelled():
                return False, f"{axis}: cancelled while backing out of home"
            self._progress(f"{axis}: backing out of home ({counts} counts)…")
            g.move_relative(axis, counts)
            if not self.wait_idle(timeout=15.0):
                return False, f"{axis}: timeout backing out of home"
            sw = g.get_switch_states(axis)
            if not sw["home_switch"] and not sw["reverse_switch"]:
                return True, ""

        return (
            False,
            f"{axis}: still on the home switch after backing out "
            f"{tries} × {counts} counts — zero not set",
        )

    # ---- Phase 1: seek ------------------------------------------------------

    def seek_home_limit(self, timeout: float = None) -> tuple[bool, str]:
        """Jog toward the home limit until a switch says we have arrived.

        Negative direction, matching `begin_home` — on these stages the home
        switch sits at the low-count end of the travel, so both the manual jog
        and HM's own search go the same way.

        Succeeds on the HOME SWITCH only. The reverse limit is a failure: the
        axis ran past where the home switch should have been, and a zero
        defined from there would be wrong without saying so.
        """
        g, axis = self.galil, self.axis
        timeout = SC.HOME_SEEK_TIMEOUT_S if timeout is None else timeout

        if self._cancelled():
            return False, f"{axis}: cancelled before seeking the home limit"

        arrival = self._arrival()
        if arrival == "home":
            self._progress(f"{axis}: already on the home limit — no seek needed")
            return True, ""
        if arrival == "reverse":
            return False, self._reverse_msg(axis)

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

            arrival = self._arrival()
            if arrival == "reverse":
                g.stop(axis)
                return False, self._reverse_msg(axis)
            if arrival == "home":
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
                arrival = self._arrival()
                if arrival == "home":
                    self._progress(f"{axis}: home limit reached")
                    return True, ""
                if arrival == "reverse":
                    return False, self._reverse_msg(axis)
                return False, (
                    f"{axis}: jog stopped before reaching the home limit — "
                    f"check the axis is energised (SH {axis}) and not sitting "
                    f"on the forward limit"
                )

            time.sleep(self._SEEK_POLL_S)

        g.stop(axis)
        return False, f"{axis}: timed out after {timeout:.0f} s seeking the home limit"

    def _arrival(self) -> str:
        """One switch read: "home", "reverse" (reverse limit WITHOUT the home
        switch), or "" (neither)."""
        sw = self.galil.get_switch_states(self.axis)
        if sw["home_switch"]:
            return "home"
        if sw["reverse_switch"]:
            return "reverse"
        return ""

    def _at_home_limit(self) -> bool:
        return self._arrival() == "home"

    @staticmethod
    def _reverse_msg(axis: str) -> str:
        return (f"{axis}: reached the reverse limit before the home switch — "
                f"check the home switch and its cable")

    # ---- Phase 2: release-edge home passes (ADR 0009) ------------------------

    def approach(self, speed: int) -> tuple[bool, str]:
        """Jog negative at `speed` until the axis stops on the home switch.

        Stopping on the reverse limit too is expected on these slits and is not
        checked by stop code.
        """
        g, axis = self.galil, self.axis
        if self._cancelled():
            return False, f"{axis}: cancelled while approaching the home switch"

        arrival = self._arrival()
        if arrival == "home":
            return True, ""
        if arrival == "reverse":
            return False, self._reverse_msg(axis)

        self._progress(
            f"{axis}: approaching home switch — JG -{speed} cps "
            f"({SC.cps_to_mm_per_sec(axis, speed):.2f} mm/s)…"
        )
        g.jog_start(axis, -speed)
        time.sleep(self._SEEK_SETTLE_S)

        deadline = time.time() + SC.HOME_SEEK_TIMEOUT_S
        while g.is_moving(axis):
            if self._cancelled():
                g.stop(axis)
                return False, f"{axis}: cancelled while approaching the home switch"
            if time.time() >= deadline:
                g.stop(axis)
                return False, (
                    f"{axis}: timed out after {SC.HOME_SEEK_TIMEOUT_S:.0f} s "
                    f"approaching the home switch"
                )
            time.sleep(self._SEEK_POLL_S)

        arrival = self._arrival()
        if arrival == "home":
            return True, ""
        if arrival == "reverse":
            return False, self._reverse_msg(axis)
        return False, f"{axis}: approach stopped before reaching the home switch"

    def creep_to_release(self) -> tuple[bool, str, int | None]:
        """Step outward one count at a time until the home switch releases."""
        g, axis = self.galil, self.axis
        g.set_speed(axis, self.approach_speed)
        steps = SC.HOME_CREEP_MAX_COUNTS // SC.HOME_CREEP_STEP_COUNTS
        for _ in range(steps):
            if self._cancelled():
                return (
                    False,
                    f"{axis}: cancelled while creeping off the home switch",
                    None,
                )
            g.move_relative(axis, SC.HOME_CREEP_STEP_COUNTS)
            if not self.wait_idle(timeout=15.0):
                if self._cancelled():
                    return (
                        False,
                        f"{axis}: cancelled while creeping off the home switch",
                        None,
                    )
                return (
                    False,
                    f"{axis}: timeout while creeping off the home switch",
                    None,
                )
            if not g.get_switch_states(axis)["home_switch"]:
                return True, "", g.get_position(axis)

        return (
            False,
            f"{axis}: home switch did not release within "
            f"{SC.HOME_CREEP_MAX_COUNTS} counts — check the sensor",
            None,
        )

    def home_passes(self) -> tuple[bool, str]:
        """Two release-edge passes; DP=0 on pass 2's edge if they agree."""
        g, axis = self.galil, self.axis
        try:
            ok, msg = self.approach(self.approach_speed)
            if not ok:
                return False, msg
            ok, msg, edge1 = self.creep_to_release()
            if not ok or edge1 is None:
                return False, msg
            self._progress(f"{axis}: pass 1/2 — edge at {edge1} cts")

            if self._cancelled():
                return False, f"{axis}: homing cancelled by user"

            g.set_speed(axis, SC.HOME_SEEK_SPEED_COUNTS_PER_SEC)
            g.move_relative(axis, SC.HOME_PASS2_BACKOFF_COUNTS)
            if not self.wait_idle(timeout=15.0):
                return False, f"{axis}: timeout backing out before pass 2"
            if g.get_switch_states(axis)["home_switch"]:
                return (
                    False,
                    f"{axis}: still on the home switch after backing out "
                    f"{SC.HOME_PASS2_BACKOFF_COUNTS} counts — zero not set",
                )

            ok, msg = self.approach(self.approach_speed)
            if not ok:
                return False, msg
            ok, msg, edge2 = self.creep_to_release()
            if not ok or edge2 is None:
                return False, msg
            self._progress(f"{axis}: pass 2/2 — edge at {edge2} cts")

            if abs(edge2 - edge1) > SC.HOME_EDGE_AGREEMENT_COUNTS:
                return (
                    False,
                    f"{axis}: home edges disagree — pass 1 at {edge1} cts, "
                    f"pass 2 at {edge2} cts (limit {SC.HOME_EDGE_AGREEMENT_COUNTS}) — zero not set",
                )

            g.define_zero(axis)
            return True, f"{axis}: homed — edges {edge1}, {edge2} cts, DP=0 at pass 2"
        finally:
            self._restore_speeds()

    def _restore_speeds(self):
        """Put SP back where the rest of the app expects it.

        Best-effort on purpose — this runs on the failure path too, where the
        link may be exactly what went wrong, and a raise here would replace a
        useful message with a connection error.
        """
        try:
            self.galil.set_speed(self.axis, SC.DEFAULT_SPEED_COUNTS_PER_SEC)
        except Exception:
            pass

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

    def __init__(self, galil: GalilController, axis: str, parent=None,
                 seek_first: bool = False,
                 approach_speed: int | None = None):
        super().__init__(parent)
        self.galil          = galil
        self.axis           = axis
        self.seek_first     = seek_first
        self.approach_speed = approach_speed
        self._cancelled     = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        routine = AxisHomeRoutine(
            self.galil, self.axis,
            progress=self.progress.emit,
            cancelled=lambda: self._cancelled,
            approach_speed=self.approach_speed,
        )
        ok, msg = routine.run(seek_first=self.seek_first)
        self.done.emit(ok, msg)

    def _wait_idle(self, timeout: float = 30.0) -> bool:
        """Retained for callers that drove the wait themselves."""
        return AxisHomeRoutine(
            self.galil, self.axis, cancelled=lambda: self._cancelled
        ).wait_idle(timeout)


# ─── The all-axes-at-once routine ─────────────────────────────────────────────

class MultiAxisHomeRoutine:
    """The same home, driven as ONE motion across several axes.

    Where AxisHomeRoutine issues `HM A` / `BG A`, this issues `HM ABCD` /
    `BG ABCD` — the HM reference's own idiom ("HM Set Homing Mode for all axes
    / BG Home all axes"). Every argument becomes a positional vector, so four
    axes are configured and released together and then sequenced by the
    CONTROLLER.

    That is the whole reason this class exists rather than four
    AxisHomeRoutines on four threads. Four threads interleave five-command HM
    setups on one socket, and while GalilController's lock keeps any single
    command intact, nothing keeps one axis's `SP`/`HV`/`JG`/`HM`/`BG` together
    — another thread's `SP` can land in the middle of it. Sending one vector
    of each removes the interleaving instead of hoping it is benign.

    Per-axis outcomes are reported through `axis_done` as they land, because
    "all four together" still finishes one axis at a time — they reach their
    switches at different moments.
    """

    SPEEDS   = SC.HOME_PASS_SPEEDS_COUNTS_PER_SEC
    BACKOFFS = [1000, 500, 250]

    _SEEK_SETTLE_S = AxisHomeRoutine._SEEK_SETTLE_S
    _SEEK_POLL_S   = AxisHomeRoutine._SEEK_POLL_S
    _IDLE_POLL_S   = AxisHomeRoutine._IDLE_POLL_S

    def __init__(self, galil: GalilController, axes=None,
                 progress=None, cancelled=None, axis_done=None, speeds=None):
        self.galil      = galil
        self.axes       = "".join(axes if axes is not None else SC.AXIS_LETTERS)
        self._progress  = progress or (lambda _msg: None)
        self._cancelled = cancelled or (lambda: False)
        self._axis_done = axis_done or (lambda _axis, _ok, _msg: None)
        self.speeds     = tuple(speeds) if speeds is not None else self.SPEEDS

    # ---- The whole routine ---------------------------------------------------

    def run(self, seek_first: bool = True) -> tuple[bool, str]:
        """Home every axis together. Returns (success, message), never raises."""
        try:
            ok, msg = self.back_out_of_homes()
            if not ok:
                return False, msg
            if seek_first:
                ok, msg = self.seek_home_limits()
                if not ok:
                    return False, msg
            return self.home_passes()
        except Exception as e:
            return False, f"{self.axes}: homing error — {e}"
        finally:
            self._restore_speeds()

    # ---- Phase 0: back out of home, all axes at once ------------------------

    def back_out_of_homes(self) -> tuple[bool, str]:
        """Back off the home switch / reverse limit on any axis that needs it."""
        g = self.galil
        pending = []
        for a in self.axes:
            sw = g.get_switch_states(a)
            if sw["forward_switch"]:
                self._progress(f"{a}: on the forward limit — no back-out needed")
            elif sw["home_switch"] or sw["reverse_switch"]:
                pending.append(a)

        if not pending:
            return True, ""

        tries = SC.HOME_START_BACKOFF_TRIES
        counts = SC.HOME_START_BACKOFF_COUNTS
        g.set_speed_multi("".join(pending), SC.HOME_SEEK_SPEED_COUNTS_PER_SEC)
        for _ in range(tries):
            if self._cancelled():
                return False, "Cancelled while backing out of home"
            moving = "".join(pending)
            self._progress(f"{moving}: backing out of home ({counts} counts)…")
            g.move_relative_multi(moving, counts)
            if not self.wait_idle(moving, timeout=15.0):
                return False, "Timeout while backing out of home"
            still = []
            for a in pending:
                sw = g.get_switch_states(a)
                if sw["home_switch"] or sw["reverse_switch"]:
                    still.append(a)
            pending = still
            if not pending:
                return True, ""

        msg = ("Still on the home switch after backing out: "
               + ", ".join(pending) + " — no zero set")
        for a in pending:
            self._axis_done(a, False, msg)
        return False, msg

    # ---- Phase 1: seek, all axes at once -------------------------------------

    def seek_home_limits(self, timeout: float = None) -> tuple[bool, str]:
        """Jog every axis toward its home limit under one JG/BG.

        Each axis is stopped INDIVIDUALLY as its own switch trips (`ST A`),
        because they will not arrive together — starting together is what is
        shared here, not finishing.
        """
        g = self.galil
        timeout = SC.HOME_SEEK_TIMEOUT_S if timeout is None else timeout

        if self._cancelled():
            return False, "Cancelled before seeking the home limits"

        failed: dict[str, str] = {}
        pending = []
        already = []
        for a in self.axes:
            arrival = self._arrival(a)
            if arrival == "home":
                already.append(a)
            elif arrival == "reverse":
                failed[a] = AxisHomeRoutine._reverse_msg(a)
            else:
                pending.append(a)
        if already:
            self._progress(f"{','.join(already)}: already on the home limit")
        if failed:
            for axis, msg in failed.items():
                self._axis_done(axis, False, msg)
            return False, ("Seek failed on " + ", ".join(sorted(failed))
                           + " — no axis was homed")
        if not pending:
            return True, ""

        speed = SC.HOME_SEEK_SPEED_COUNTS_PER_SEC
        moving = "".join(pending)
        self._progress(
            f"{moving}: seeking home limits together — JG -{speed} cps, "
            f"BG {moving}…"
        )
        g.jog_start_multi(moving, -speed)
        time.sleep(self._SEEK_SETTLE_S)

        deadline = time.time() + timeout
        while pending and time.time() < deadline:
            if self._cancelled():
                g.stop("".join(pending))
                return False, "Cancelled while seeking the home limits"

            for axis in list(pending):
                arrival = self._arrival(axis)
                if arrival == "reverse":
                    g.stop(axis)
                    pending.remove(axis)
                    failed[axis] = AxisHomeRoutine._reverse_msg(axis)
                    self._progress(failed[axis])
                elif arrival == "home":
                    g.stop(axis)
                    pending.remove(axis)
                    self._progress(f"{axis}: home limit reached")
                elif not g.is_moving(axis):
                    arrival = self._arrival(axis)
                    if arrival == "home":
                        pending.remove(axis)
                        self._progress(f"{axis}: home limit reached")
                        continue
                    pending.remove(axis)
                    if arrival == "reverse":
                        failed[axis] = AxisHomeRoutine._reverse_msg(axis)
                        self._progress(failed[axis])
                        continue
                    failed[axis] = (
                        f"{axis}: jog stopped before reaching the home limit — "
                        f"check the axis is energised (SH {axis})")
                    self._progress(failed[axis])

            time.sleep(self._SEEK_POLL_S)

        if pending:
            g.stop("".join(pending))
            for axis in pending:
                failed[axis] = f"{axis}: timed out seeking the home limit"
        if failed:
            for axis, msg in failed.items():
                self._axis_done(axis, False, msg)
            return False, ("Seek failed on " + ", ".join(sorted(failed))
                           + " — no axis was homed")

        if not self.wait_idle(self.axes, timeout=15.0):
            return False, "Timeout coming to rest on the home limits"
        return True, ""

    def _arrival(self, axis: str) -> str:
        """"home", "reverse" (reverse limit without the home switch) or ""."""
        sw = self.galil.get_switch_states(axis)
        if sw["home_switch"]:
            return "home"
        if sw["reverse_switch"]:
            return "reverse"
        return ""

    def _at_home_limit(self, axis: str) -> bool:
        return self._arrival(axis) == "home"

    # ---- Phase 2: the three HM passes, all axes at once ----------------------

    def home_passes(self) -> tuple[bool, str]:
        g = self.galil
        axes = self.axes
        n = len(self.speeds)

        on_switch = "".join(a for a in axes
                            if g.get_switch_states(a)["home_switch"])
        if on_switch:
            self._progress(f"{on_switch}: on home switch — backing off "
                           f"{self.BACKOFFS[0]} counts…")
            g.move_relative_multi(on_switch, self.BACKOFFS[0])
            if not self.wait_idle(on_switch, timeout=15.0):
                return False, "Timeout while backing off the home switches"

        for pass_num, (speed, backoff) in enumerate(zip(self.speeds, self.BACKOFFS)):
            if self._cancelled():
                return False, "Homing cancelled by user"

            if pass_num > 0:
                self._progress(f"{axes}: pass {pass_num+1}/{n} — backing off "
                               f"{backoff} counts…")
                g.move_relative_multi(axes, backoff)
                if not self.wait_idle(axes, timeout=15.0):
                    return False, f"Timeout on back-off before pass {pass_num+1}"

            self._progress(
                f"{axes}: pass {pass_num+1}/{n} — HM {axes} ; BG {axes} at "
                f"SP/HV {speed} cps…")
            g.begin_home_multi(axes, speed, fine_speed=speed)
            time.sleep(0.5)   # let motion start

            if not self.wait_idle(axes, timeout=60.0):
                self._progress(f"{axes}: HM timeout on pass {pass_num+1}")
                g.stop(axes)
                time.sleep(0.3)
                return False, f"Homing timed out on pass {pass_num+1}/{n}"

            bad = []
            for axis in axes:
                code = g.get_stop_code(axis)
                if code != SC.HOME_STOP_CODE:
                    bad.append(f"{axis} (stop code {code})")
            if bad:
                return False, ("Homing stopped off the home switch on "
                               + ", ".join(bad) + " — no zero set")

            self._progress(f"{axes}: pass {pass_num+1}/{n} complete")

        # HM leaves a stepper's position untouched, so this DP is what makes
        # the zero exist — one command for every axis.
        g.define_zero_multi(axes)
        for axis in axes:
            self._axis_done(axis, True, f"{axis}: homed ({n} passes), DP=0")
        return True, (f"{axes}: homed together ({n} passes), DP=0, "
                      f"SP/HV restored")

    # ---- Shared -------------------------------------------------------------

    def wait_idle(self, axes: str, timeout: float = 30.0) -> bool:
        """Wait until EVERY axis in `axes` has stopped."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._cancelled():
                return False
            try:
                if not any(self.galil.is_moving(a) for a in axes):
                    return True
            except Exception:
                return False
            time.sleep(self._IDLE_POLL_S)
        return False

    def _restore_speeds(self):
        """Put SP and HV back for every axis. Best-effort, as in the
        single-axis routine — this also runs when the link is what failed."""
        for call in (
            lambda: self.galil.set_speed_multi(
                self.axes, SC.DEFAULT_SPEED_COUNTS_PER_SEC),
            lambda: self.galil.set_home_velocity_multi(
                self.axes, SC.DEFAULT_HOME_VELOCITY_COUNTS_PER_SEC),
        ):
            try:
                call()
            except Exception:
                pass


class MultiAxisHomeWorker(QThread):
    """`MultiAxisHomeRoutine` on its own thread — ONE thread, not four."""
    progress      = Signal(str)
    axis_finished = Signal(str, bool, str)
    done          = Signal(bool, str)

    def __init__(self, galil: GalilController, axes=None, parent=None,
                 seek_first: bool = True, speeds=None):
        super().__init__(parent)
        self.galil      = galil
        self.axes       = "".join(axes if axes is not None else SC.AXIS_LETTERS)
        self.seek_first = seek_first
        self.speeds     = speeds
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        routine = MultiAxisHomeRoutine(
            self.galil, self.axes,
            progress=self.progress.emit,
            cancelled=lambda: self._cancelled,
            axis_done=self.axis_finished.emit,
            speeds=self.speeds,
        )
        ok, msg = routine.run(seek_first=self.seek_first)
        self.done.emit(ok, msg)


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
                 seek_first: bool = True, stop_on_failure: bool = True,
                 approach_speed: int | None = None):
        super().__init__(parent)
        self.galil           = galil
        self.axes            = list(axes if axes is not None else SC.AXIS_LETTERS)
        self.seek_first      = seek_first
        self.stop_on_failure = stop_on_failure
        self.approach_speed  = approach_speed
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
                approach_speed=self.approach_speed,
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


# ─── Edge-return repeatability test (one axis, one pass speed) ────────────────

class EdgeReturnRoutine:
    """Collect edge-return readings on one axis at one pass speed.

    Not a QThread for the same reasons as `AxisHomeRoutine`: callers can run
    it on a worker thread (`EdgeReturnWorker`), sequentially across speeds, or
    directly in tests. Never calls `define_zero` or `define_zero_multi`.
    """

    def __init__(
        self,
        galil: GalilController,
        axis: str,
        pass_speed_cps: int,
        cycles: int,
        out_counts: int,
        progress=None,
        cancelled=None,
    ):
        self.galil = galil
        self.axis = axis
        self.pass_speed_cps = pass_speed_cps
        self.cycles = cycles
        self.out_counts = out_counts
        self._progress = progress or (lambda _msg: None)
        self._cancelled = cancelled or (lambda: False)

    def run(self) -> tuple[bool, str, list[int]]:
        """Run all cycles and return (success, message, readings). Never raises."""
        g, axis = self.galil, self.axis
        readings: list[int] = []
        home = AxisHomeRoutine(g, axis, self._progress, self._cancelled)
        try:
            ok, msg = home.back_out_of_home()
            if not ok:
                return False, msg, readings

            for k in range(1, self.cycles + 1):
                if self._cancelled():
                    return (
                        False,
                        f"{axis}: edge-return cancelled after {k - 1} cycles",
                        readings,
                    )

                g.set_speed(axis, SC.HOME_SEEK_SPEED_COUNTS_PER_SEC)
                g.move_relative(axis, self.out_counts)
                if not home.wait_idle(timeout=60.0):
                    return False, f"{axis}: timeout on cycle {k}/{self.cycles}", readings

                g.begin_home(axis, self.pass_speed_cps, fine_speed=self.pass_speed_cps)
                if not home.wait_idle(timeout=120.0):
                    return False, f"{axis}: timeout on cycle {k}/{self.cycles}", readings

                code = g.get_stop_code(axis)
                if code != SC.HOME_STOP_CODE:
                    return (
                        False,
                        f"{axis}: cycle {k}/{self.cycles} did not stop on the "
                        f"home switch (stop code {code})",
                        readings,
                    )

                pos = g.get_position(axis)
                readings.append(pos)
                self._progress(f"{axis}: cycle {k}/{self.cycles} — edge at {pos} cts")

            return (
                True,
                f"{axis}: {self.cycles} edge returns at {self.pass_speed_cps} cps",
                readings,
            )
        except Exception as e:
            return False, f"{axis}: edge-return error — {e}", readings
        finally:
            home._restore_speeds()


class EdgeReturnWorker(QThread):
    """`EdgeReturnRoutine` for one axis at one pass speed, on its own thread."""

    progress = Signal(str)
    done = Signal(bool, str, list)

    def __init__(
        self,
        galil: GalilController,
        axis: str,
        pass_speed_cps: int,
        cycles: int = SC.EDGE_RETURN_DEFAULT_CYCLES,
        out_counts: int = SC.EDGE_RETURN_DEFAULT_OUT_COUNTS,
        parent=None,
    ):
        super().__init__(parent)
        self.galil = galil
        self.axis = axis
        self.pass_speed_cps = pass_speed_cps
        self.cycles = cycles
        self.out_counts = out_counts
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        routine = EdgeReturnRoutine(
            self.galil,
            self.axis,
            self.pass_speed_cps,
            self.cycles,
            self.out_counts,
            progress=self.progress.emit,
            cancelled=lambda: self._cancelled,
        )
        ok, msg, readings = routine.run()
        self.done.emit(ok, msg, readings)
