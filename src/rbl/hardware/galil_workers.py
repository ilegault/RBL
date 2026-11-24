"""
galil_workers.py
Background QThread workers for the Galil DMC-4103: state polling, the per-axis
homing routine, and the all-axes automatic homing sequencer.

Bare QThreads, not GUI widgets — moved out of motor_tab.py so they are
testable and constructible without a widget, matching labjack_stream_worker.py.

HOMING WITH HM AT 900 CPS (ADR 0010)
------------------------------------
Slit homing uses NEC's procedure (`docs/adr/0010-slits-homed-with-hm-as-nec-specifies.md`):

  1. Pre-home move (`pre_home_move`): if the axis is sitting on its home switch
     or reverse limit (and not on the forward limit), move out
     `PRE_HOME_MOVE_COUNTS` (2000 counts) at `DEFAULT_SPEED_COUNTS_PER_SEC`
     (1800 cps) and verify both switches read clear.
  2. Home move (`home_move`): set speed to `HOMING_SPEED_COUNTS_PER_SEC`
     (900 cps), run `HM <axis>` + `BG <axis>`, wait until idle and at rest
     (`_TD == _RP`), then read switches and `_SC`. Accept only home switch
     tripped with stop code in `HOME_ACCEPTED_STOP_CODES` (3 or 10).

Homing runs two passes; if their stop positions agree within
`HOME_STOP_AGREEMENT_COUNTS` (4 counts), zero is defined at pass 2's stop with
`DP=0` and `DE=0`, and speed is restored to `DEFAULT_SPEED_COUNTS_PER_SEC`
(1800 cps) in a `finally` block. `AxisHomeRoutine` is that sequence for one
axis, and `AutoHomeAllWorker` runs it over all four axes on ONE thread so at
most one axis is ever in motion and at most one command is ever on the wire.

EDGE-RETURN TEST
----------------
`EdgeReturnRoutine` measures repeatability on one axis at `approach_speed`:
it runs `pre_home_move` once, then repeats N cycles of moving out `out_counts`
(+counts) at `approach_speed`, running `HM` (`home_move`) at `approach_speed`
to record the stop position, and creeping out one count at a time to the
release edge (`creep_to_release`). It never calls `define_zero` or
`define_encoder_zero`.
"""
import time

from PySide6.QtCore import QThread, Signal

from rbl.config import hardware_config as SC
from rbl.hardware.edge_return import EdgeReturnReading
from rbl.hardware.galil_driver import GalilController
from rbl.hardware.switch_map import (
    SwitchMapRow,
    find_switch_edges,
    switch_change_line,
    switch_map_summary,
)

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
    """Everything one axis needs in order to end up homed and zeroed at DP=0, DE=0.

    Deliberately NOT a QThread. The identical routine has to run on the
    single-axis worker's thread and four times back-to-back on the sequencer's
    one thread. Something that has to be both cannot be the thing that owns a
    thread, so the caller supplies a progress callback and a cancellation
    predicate and drives `run()` on whatever thread it likes.

    ONE COMMAND AT A TIME falls out of this being straight-line code: every
    phase waits for the motion it started to stop (and for step pulses to
    finish via `wait_at_rest`) before it issues the next command, and
    GalilController.cmd holds a lock for the send/receive pair.
    """

    _SEEK_SETTLE_S = 0.5    # let BG actually start the move before believing _BG
    _SEEK_POLL_S   = 0.1    # how often the home move polls _BG
    _IDLE_POLL_S   = 0.2

    def __init__(self, galil: GalilController, axis: str,
                 progress=None, cancelled=None):
        self.galil          = galil
        self.axis           = axis
        self._progress      = progress or (lambda _msg: None)
        self._cancelled     = cancelled or (lambda: False)

    # ---- The whole routine ---------------------------------------------------

    def run(self) -> tuple[bool, str]:
        """Home this axis with two HM passes at 900 cps.

        Returns `(success, message)` — never raises. Runs `pre_home_move()`
        then `home_move(900)` twice; if the two stop positions agree within
        `HOME_STOP_AGREEMENT_COUNTS`, defines `DP=0` then `DE=0`. Always
        restores the slit speed to `DEFAULT_SPEED_COUNTS_PER_SEC` in `finally`.
        """
        axis = self.axis
        try:
            ok, msg = self.pre_home_move()
            if not ok:
                return False, msg
            ok, msg, stop1 = self.home_move(SC.HOMING_SPEED_COUNTS_PER_SEC)
            if not ok or stop1 is None:
                return False, msg
            self._progress(f"{axis}: pass 1/2 — stopped at {stop1} cts")

            if self._cancelled():
                return False, f"{axis}: homing cancelled by user"

            ok, msg = self.pre_home_move()
            if not ok:
                return False, msg
            ok, msg, stop2 = self.home_move(SC.HOMING_SPEED_COUNTS_PER_SEC)
            if not ok or stop2 is None:
                return False, msg
            self._progress(f"{axis}: pass 2/2 — stopped at {stop2} cts")

            if abs(stop2 - stop1) > SC.HOME_STOP_AGREEMENT_COUNTS:
                return (
                    False,
                    f"{axis}: home stops disagree — pass 1 at {stop1} cts, "
                    f"pass 2 at {stop2} cts (limit {SC.HOME_STOP_AGREEMENT_COUNTS}) — zero not set",
                )

            self.galil.define_zero(axis)
            self.galil.define_encoder_zero(axis)
            return True, f"{axis}: homed — stops {stop1}, {stop2} cts, DP=0 at pass 2"
        except Exception as e:
            return False, f"{axis}: homing error — {e}"
        finally:
            self._restore_speeds()

    # ---- Phase 0: pre-home move & back-out ----------------------------------

    def pre_home_move(self) -> tuple[bool, str]:
        """Move off the home switch / reverse limit before running HM (ADR 0010).

        On the forward limit, or when both home switch and reverse limit are
        clear, no move is made. Otherwise moves out `PRE_HOME_MOVE_COUNTS` at
        `DEFAULT_SPEED_COUNTS_PER_SEC` and verifies both switches read clear.
        """
        g, axis = self.galil, self.axis
        sw = g.get_switch_states(axis)
        if sw["forward_switch"]:
            self._progress(f"{axis}: on the forward limit — no pre-home move needed")
            return True, ""
        if not sw["home_switch"] and not sw["reverse_switch"]:
            return True, ""
        if self._cancelled():
            return False, f"{axis}: cancelled in the pre-home move"

        counts = SC.PRE_HOME_MOVE_COUNTS
        self._progress(
            f"{axis}: pre-home move ({counts} counts at "
            f"{SC.DEFAULT_SPEED_COUNTS_PER_SEC} cps)…"
        )
        g.set_speed(axis, SC.DEFAULT_SPEED_COUNTS_PER_SEC)
        g.move_relative(axis, counts)
        if not self.wait_idle(timeout=15.0):
            if self._cancelled():
                return False, f"{axis}: cancelled in the pre-home move"
            return False, f"{axis}: timeout in the pre-home move"

        sw = g.get_switch_states(axis)
        if sw["home_switch"] or sw["reverse_switch"]:
            return (
                False,
                f"{axis}: still on the home switch after the pre-home move "
                f"of {counts} counts — zero not set",
            )
        return True, ""

    def back_out_of_home(self) -> tuple[bool, str]:
        """Back off the home switch / reverse limit.

        Still called by `SwitchMapRoutine`.
        """
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

    # ---- Phase 1: HM home move (ADR 0010) -----------------------------------

    def home_move(self, speed: int) -> tuple[bool, str, int | None]:
        """Run `HM` + `BG` at `speed`, wait until at rest, then check stop state.

        Every switch and stop-code read happens strictly after `wait_at_rest`
        so trailing step pulses cannot produce a stale reading (ADR 0010).
        Accepts only home switch tripped with stop code in
        `HOME_ACCEPTED_STOP_CODES` (3 or 10).
        """
        g, axis = self.galil, self.axis
        if self._cancelled():
            return False, f"{axis}: cancelled during the home move", None

        if g.get_switch_states(axis)["home_switch"]:
            return (
                False,
                f"{axis}: on the home switch before the home move — run the pre-home move first",
                None,
            )

        self._progress(
            f"{axis}: homing (HM) at {speed} cps "
            f"({SC.cps_to_mm_per_sec(axis, speed):.2f} mm/s)…"
        )
        g.set_speed(axis, speed)
        g.home(axis)
        time.sleep(self._SEEK_SETTLE_S)   # BG has to take effect before _BG means anything

        deadline = time.time() + SC.HOME_SEEK_TIMEOUT_S
        while g.is_moving(axis):
            if self._cancelled():
                g.stop(axis)
                return False, f"{axis}: cancelled during the home move", None
            if time.time() >= deadline:
                g.stop(axis)
                return (
                    False,
                    f"{axis}: timed out after {SC.HOME_SEEK_TIMEOUT_S:.0f} s in the home move",
                    None,
                )
            time.sleep(self._SEEK_POLL_S)

        ok, rest_msg = self.wait_at_rest(timeout=15.0)
        if not ok:
            if rest_msg:
                return False, f"{axis}: {rest_msg}", None
            if self._cancelled():
                return False, f"{axis}: cancelled during the home move", None
            return False, f"{axis}: timeout waiting for the home move to come to rest", None

        sw = g.get_switch_states(axis)
        code = g.get_stop_code(axis)
        if sw["home_switch"]:
            if code in SC.HOME_ACCEPTED_STOP_CODES:
                return True, "", g.get_position(axis)
            return (
                False,
                f"{axis}: home move ended with stop code {code} — zero not set",
                None,
            )
        if sw["reverse_switch"]:
            return False, self._reverse_msg(axis), None
        return (
            False,
            f"{axis}: home move stopped before reaching the home switch (stop code {code})",
            None,
        )

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

    # ---- Release-edge helper (EdgeReturnRoutine only) -----------------------

    def creep_to_release(self, speed: int) -> tuple[bool, str, int | None]:
        """Step outward one count at a time at `speed`, reading the home switch
        at rest until it releases. Only the edge-return test calls it.
        """
        g, axis = self.galil, self.axis
        g.set_speed(axis, speed)
        steps = SC.HOME_CREEP_MAX_COUNTS // SC.HOME_CREEP_STEP_COUNTS
        for _ in range(steps):
            if self._cancelled():
                return (
                    False,
                    f"{axis}: cancelled while creeping off the home switch",
                    None,
                )
            g.move_relative(axis, SC.HOME_CREEP_STEP_COUNTS)
            ok, msg = self.wait_at_rest(timeout=15.0)
            if not ok:
                if msg:
                    return False, f"{axis}: {msg}", None
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

    def wait_at_rest(self, timeout: float = 15.0) -> tuple[bool, str]:
        """Wait until the axis is idle (_BG == 0) and every step pulse has
        been sent (_TD == _RP).

        Returns `(True, "")` at rest; `(False, "")` if cancelled, errored, or
        still moving at the deadline; and
        `(False, f"step pulses still pending: TD {td}, RP {rp}")` if the
        profiler stopped but `_TD` never caught up to `_RP`.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._cancelled():
                return False, ""
            try:
                if not self.galil.is_moving(self.axis):
                    break
            except Exception:
                return False, ""
            time.sleep(self._IDLE_POLL_S)
        else:
            return False, ""

        td = rp = None
        while time.time() < deadline:
            if self._cancelled():
                return False, ""
            try:
                td = self.galil.get_step_count(self.axis)
                rp = self.galil.get_position(self.axis)
            except Exception:
                return False, ""
            if td == rp:
                return True, ""
            time.sleep(self._IDLE_POLL_S)

        if td is None or rp is None:
            return False, ""
        return False, f"step pulses still pending: TD {td}, RP {rp}"


# ─── Auto-homing worker (one axis) ────────────────────────────────────────────

class HomingWorker(QThread):
    """`AxisHomeRoutine` for one axis (HM at 900 cps), on its own thread."""
    progress = Signal(str)
    done     = Signal(bool, str)   # success, message

    def __init__(self, galil: GalilController, axis: str, parent=None):
        super().__init__(parent)
        self.galil      = galil
        self.axis       = axis
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        routine = AxisHomeRoutine(
            self.galil, self.axis,
            progress=self.progress.emit,
            cancelled=lambda: self._cancelled,
        )
        ok, msg = routine.run()
        self.done.emit(ok, msg)

    def _wait_idle(self, timeout: float = 30.0) -> bool:
        """Retained for callers that drove the wait themselves."""
        return AxisHomeRoutine(
            self.galil, self.axis, cancelled=lambda: self._cancelled
        ).wait_idle(timeout)


# ─── Auto-homing worker (all axes, one at a time) ─────────────────────────────

class AutoHomeAllWorker(QThread):
    """Home every axis in turn with `HM` at 900 cps, on ONE thread.

    This is the answer to "eight clicks before I can start calibrating
    anything": one press homes the whole set while the operator works on
    another tab.

    Strictly sequential, and that is the point rather than an implementation
    detail. Axis N+1 is not touched until axis N has reported DP=0, so there
    is never more than one axis moving, never more than one command in flight,
    and a failure stops the sequence with the remaining axes untouched instead
    of leaving four half-homed slits.
    """
    progress      = Signal(str)          # console line
    axis_started  = Signal(str)          # axis letter
    axis_finished = Signal(str, bool, str)   # axis letter, success, message
    done          = Signal(bool, str)    # all succeeded, summary

    def __init__(self, galil: GalilController, axes=None, parent=None,
                 stop_on_failure: bool = True):
        super().__init__(parent)
        self.galil           = galil
        self.axes            = list(axes if axes is not None else SC.AXIS_LETTERS)
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
            ok, msg = routine.run()

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


# ─── Edge-return repeatability test (one axis, approach speed) ────────────────

class EdgeReturnRoutine:
    """Collect (stop, edge) readings on one axis at the approach speed.

    Not a QThread for the same reasons as `AxisHomeRoutine`: callers can run
    it on a worker thread (`EdgeReturnWorker`) or directly in tests. Never
    calls `define_zero`, `define_zero_multi`, or `define_encoder_zero`. The
    move out runs at the approach speed being measured, so the axis never leaves
    the switch slower than it arrived.
    """

    def __init__(
        self,
        galil: GalilController,
        axis: str,
        approach_speed: int,
        cycles: int,
        out_counts: int,
        progress=None,
        cancelled=None,
    ):
        self.galil = galil
        self.axis = axis
        self.approach_speed = approach_speed
        self.cycles = cycles
        self.out_counts = out_counts
        self._progress = progress or (lambda _msg: None)
        self._cancelled = cancelled or (lambda: False)

    def run(self) -> tuple[bool, str, list[EdgeReturnReading]]:
        """Run all cycles and return (success, message, readings). Never raises."""
        g, axis = self.galil, self.axis
        readings: list[EdgeReturnReading] = []
        home = AxisHomeRoutine(g, axis, self._progress, self._cancelled)
        try:
            ok, msg = home.pre_home_move()
            if not ok:
                return False, msg, readings

            for k in range(1, self.cycles + 1):
                if self._cancelled():
                    return (
                        False,
                        f"{axis}: edge-return cancelled after {k - 1} cycles",
                        readings,
                    )

                g.set_speed(axis, self.approach_speed)
                g.move_relative(axis, self.out_counts)
                if not home.wait_idle(timeout=60.0):
                    return False, f"{axis}: timeout on cycle {k}/{self.cycles}", readings

                ok, msg, stop = home.home_move(self.approach_speed)
                if not ok or stop is None:
                    return False, msg, readings

                ok, msg, edge = home.creep_to_release(self.approach_speed)
                if not ok or edge is None:
                    return False, msg, readings

                readings.append(EdgeReturnReading(stop, edge))
                self._progress(
                    f"{axis}: cycle {k}/{self.cycles} — stop at {stop} cts, edge at {edge} cts"
                )

            return (
                True,
                f"{axis}: {self.cycles} edge returns at {self.approach_speed} cps",
                readings,
            )
        except Exception as e:
            return False, f"{axis}: edge-return error — {e}", readings
        finally:
            home._restore_speeds()


class EdgeReturnWorker(QThread):
    """`EdgeReturnRoutine` for one axis at the approach speed, on its own thread."""

    progress = Signal(str)
    done = Signal(bool, str, list)

    def __init__(
        self,
        galil: GalilController,
        axis: str,
        approach_speed: int,
        cycles: int = SC.EDGE_RETURN_DEFAULT_CYCLES,
        out_counts: int = SC.EDGE_RETURN_DEFAULT_OUT_COUNTS,
        parent=None,
    ):
        super().__init__(parent)
        self.galil = galil
        self.axis = axis
        self.approach_speed = approach_speed
        self.cycles = cycles
        self.out_counts = out_counts
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        routine = EdgeReturnRoutine(
            self.galil,
            self.axis,
            self.approach_speed,
            self.cycles,
            self.out_counts,
            progress=self.progress.emit,
            cancelled=lambda: self._cancelled,
        )
        ok, msg, readings = routine.run()
        self.done.emit(ok, msg, readings)


# ─── Switch map measurement ──────────────────────────────────────────────────

class SwitchMapRoutine:
    """Step an axis slowly in through home and reverse switches, then back out.

    Records every switch state at rest (every step pulse sent) after each
    single-count step, without ever calling `define_zero`. Never raises;
    returns `(ok, message, rows)`.
    """

    def __init__(
        self,
        galil: GalilController,
        axis: str,
        progress=None,
        cancelled=None,
        on_row=None,
    ):
        self.galil = galil
        self.axis = axis
        self._progress = progress or (lambda _msg: None)
        self._cancelled = cancelled or (lambda: False)
        self._on_row = on_row or (lambda _row: None)

    def _record_row(
        self,
        rows: list[SwitchMapRow],
        step: int,
        direction: str,
        prev_row: SwitchMapRow | None,
    ) -> SwitchMapRow:
        pos = self.galil.get_position(self.axis)
        sw = self.galil.get_switch_states(self.axis)
        row = SwitchMapRow(
            step=step,
            direction=direction,
            position_counts=pos,
            home=bool(sw["home_switch"]),
            reverse=bool(sw["reverse_switch"]),
            forward=bool(sw["forward_switch"]),
        )
        rows.append(row)
        self._on_row(row)
        if prev_row is not None:
            for attr, name in (("home", "home"), ("reverse", "reverse"), ("forward", "forward")):
                cur = getattr(row, attr)
                if cur != getattr(prev_row, attr):
                    self._progress(switch_change_line(self.axis, direction, name, cur, pos))
        return row

    def _seek_any_switch(self, home: AxisHomeRoutine) -> tuple[bool, str]:
        """Jog inward at seek speed until home or reverse switch trips."""
        g, axis = self.galil, self.axis
        speed = SC.HOME_SEEK_SPEED_COUNTS_PER_SEC
        g.set_speed(axis, speed)
        g.jog_start(axis, -speed)
        time.sleep(AxisHomeRoutine._SEEK_SETTLE_S)

        deadline = time.time() + SC.HOME_SEEK_TIMEOUT_S
        while time.time() < deadline:
            if self._cancelled():
                g.stop(axis)
                return False, f"{axis}: switch map cancelled"
            sw = g.get_switch_states(axis)
            if sw["home_switch"] or sw["reverse_switch"]:
                g.stop(axis)
                if not home.wait_idle(timeout=15.0):
                    if self._cancelled():
                        return False, f"{axis}: switch map cancelled"
                    return False, f"{axis}: timeout coming to rest on switch"
                return True, ""
            if not g.is_moving(axis):
                sw = g.get_switch_states(axis)
                if sw["home_switch"] or sw["reverse_switch"]:
                    return True, ""
                return False, f"{axis}: jog stopped before reaching home or reverse switch"
            time.sleep(AxisHomeRoutine._SEEK_POLL_S)

        g.stop(axis)
        return False, f"{axis}: timed out after {SC.HOME_SEEK_TIMEOUT_S:.0f} s seeking switch"

    def run(self) -> tuple[bool, str, list[SwitchMapRow]]:
        """Run the switch map on one axis and return (ok, message, rows)."""
        g, axis = self.galil, self.axis
        rows: list[SwitchMapRow] = []
        home = AxisHomeRoutine(g, axis, self._progress, self._cancelled)
        try:
            ok, msg = home.back_out_of_home()
            if not ok:
                return False, msg, rows

            ok, msg = self._seek_any_switch(home)
            if not ok:
                return False, msg, rows

            g.set_speed(axis, SC.HOME_SEEK_SPEED_COUNTS_PER_SEC)
            g.move_relative(axis, SC.SWITCH_MAP_BACKOFF_COUNTS)
            if not home.wait_idle(timeout=30.0):
                if self._cancelled():
                    return False, f"{axis}: switch map cancelled", rows
                return False, f"{axis}: timeout backing off for switch map", rows

            g.set_speed(axis, SC.SWITCH_MAP_STEP_SPEED_COUNTS_PER_SEC)
            step = 0
            prev_row: SwitchMapRow | None = None
            first_trip_pos: int | None = None
            inward_stop = ""

            # Inward pass: step -1 until reverse limit trips or 100-count cap
            while True:
                if self._cancelled():
                    return False, f"{axis}: switch map cancelled", rows
                if g.get_switch_states(axis)["reverse_switch"]:
                    inward_stop = "reverse limit"
                    break
                g.move_relative(axis, -SC.SWITCH_MAP_STEP_COUNTS)
                ok, msg = home.wait_at_rest(timeout=15.0)
                if not ok:
                    if msg:
                        return False, f"{axis}: {msg}", rows
                    if self._cancelled():
                        return False, f"{axis}: switch map cancelled", rows
                    return False, f"{axis}: timeout stepping inward", rows
                step += 1
                prev_row = self._record_row(rows, step, "in", prev_row)
                if first_trip_pos is None and (prev_row.home or prev_row.reverse):
                    first_trip_pos = prev_row.position_counts
                if prev_row.reverse:
                    inward_stop = "reverse limit"
                    break
                if (
                    first_trip_pos is not None
                    and prev_row.position_counts
                    <= first_trip_pos - SC.SWITCH_MAP_MAX_PAST_FIRST_TRIP_COUNTS
                ):
                    inward_stop = f"{SC.SWITCH_MAP_MAX_PAST_FIRST_TRIP_COUNTS}-count cap"
                    break

            # Outward pass: step +1 until both switches clear, then margin steps
            out_steps = 0
            margin_remaining: int | None = None
            while True:
                if self._cancelled():
                    return False, f"{axis}: switch map cancelled", rows
                if margin_remaining is None:
                    if out_steps >= SC.SWITCH_MAP_MAX_OUT_COUNTS:
                        return (
                            False,
                            f"{axis}: switches did not clear within "
                            f"{SC.SWITCH_MAP_MAX_OUT_COUNTS} counts — check the sensor",
                            rows,
                        )
                elif margin_remaining <= 0:
                    break

                g.move_relative(axis, SC.SWITCH_MAP_STEP_COUNTS)
                ok, msg = home.wait_at_rest(timeout=15.0)
                if not ok:
                    if msg:
                        return False, f"{axis}: {msg}", rows
                    if self._cancelled():
                        return False, f"{axis}: switch map cancelled", rows
                    return False, f"{axis}: timeout stepping outward", rows
                step += 1
                out_steps += 1
                prev_row = self._record_row(rows, step, "out", prev_row)
                if margin_remaining is None:
                    if not prev_row.home and not prev_row.reverse:
                        margin_remaining = SC.SWITCH_MAP_CLEAR_MARGIN_COUNTS
                else:
                    margin_remaining -= 1

            summary = switch_map_summary(axis, find_switch_edges(rows), inward_stop)
            self._progress(summary)
            return True, summary, rows
        except Exception as e:
            return False, f"{axis}: switch map error — {e}", rows
        finally:
            home._restore_speeds()


class SwitchMapWorker(QThread):
    """`SwitchMapRoutine` for one axis on its own thread."""

    progress = Signal(str)
    row = Signal(object)
    done = Signal(bool, str, list)

    def __init__(self, galil: GalilController, axis: str, parent=None):
        super().__init__(parent)
        self.galil = galil
        self.axis = axis
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        routine = SwitchMapRoutine(
            self.galil,
            self.axis,
            progress=self.progress.emit,
            cancelled=lambda: self._cancelled,
            on_row=self.row.emit,
        )
        ok, msg, rows = routine.run()
        self.done.emit(ok, msg, rows)
