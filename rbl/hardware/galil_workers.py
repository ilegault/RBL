"""
galil_workers.py
Background QThread workers for the Galil DMC-4103: state polling and
automated multi-pass homing.

Bare QThreads, not GUI widgets — moved out of motor_tab.py so they are
testable and constructible without a widget, matching labjack_stream_worker.py.
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


# ─── Auto-homing worker ───────────────────────────────────────────────────────

class HomingWorker(QThread):
    """Multi-pass homing for accuracy: coarse → medium → fine speed, always all passes.

    Each pass backs off a small amount then re-homes at a slower speed.
    define_zero is only called after the final (slowest) pass.
    """
    progress = Signal(str)
    done     = Signal(bool, str)   # success, message

    # Speeds and matching back-off distances for each successive pass (coarse → fine)
    _SPEEDS   = [225, 112, 58]
    _BACKOFFS = [1000, 500, 250]   # counts to back off before each pass

    def __init__(self, galil: GalilController, axis: str, parent=None):
        super().__init__(parent)
        self.galil = galil
        self.axis  = axis
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        g    = self.galil
        axis = self.axis
        n    = len(self._SPEEDS)
        try:
            # If already on home switch, back off using the coarse distance first
            sw = g.get_switch_states(axis)
            if sw["home_switch"]:
                self.progress.emit(f"{axis}: on home switch — backing off {self._BACKOFFS[0]} counts…")
                g.move_relative(axis, self._BACKOFFS[0])
                if not self._wait_idle(timeout=15.0):
                    self.done.emit(False, "Timeout while backing off home switch")
                    return

            for pass_num, (speed, backoff) in enumerate(zip(self._SPEEDS, self._BACKOFFS)):
                if self._cancelled:
                    self.done.emit(False, "Homing cancelled by user")
                    return

                # Back off before every pass using this pass's distance
                if pass_num > 0:
                    self.progress.emit(
                        f"{axis}: pass {pass_num+1}/{n} — backing off {backoff} counts…"
                    )
                    g.move_relative(axis, backoff)
                    if not self._wait_idle(timeout=15.0):
                        self.done.emit(False, f"Timeout on back-off before pass {pass_num+1}")
                        return

                self.progress.emit(
                    f"{axis}: pass {pass_num+1}/{n} — "
                    f"HM at {speed} cps "
                    f"({SC.cps_to_mm_per_sec(axis, speed):.2f} mm/s)…"
                )
                g.begin_home(axis, speed)
                time.sleep(0.5)   # let motion start

                if not self._wait_idle(timeout=60.0):
                    self.progress.emit(f"{axis}: HM timeout on pass {pass_num+1}")
                    g.stop(axis)
                    time.sleep(0.3)
                    # Restore speed and report failure — don't continue further passes
                    try:
                        g.set_speed(axis, SC.DEFAULT_SPEED_COUNTS_PER_SEC)
                    except Exception:
                        pass
                    self.done.emit(False, f"{axis}: homing timed out on pass {pass_num+1}/{n}")
                    return

                self.progress.emit(f"{axis}: pass {pass_num+1}/{n} complete")

            # All passes done — define zero on the final fine-speed position
            g.define_zero(axis)
            g.set_speed(axis, SC.DEFAULT_SPEED_COUNTS_PER_SEC)
            self.done.emit(True, f"{axis}: homed ({n} passes), DP=0, SP restored")

        except Exception as e:
            self.done.emit(False, f"{axis}: homing error — {e}")

    def _wait_idle(self, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._cancelled:
                return False
            try:
                if not self.galil.is_moving(self.axis):
                    return True
            except Exception:
                return False
            time.sleep(0.2)
        return False
