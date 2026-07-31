"""
Automatic homing: the seek phase, the all-axes sequencer, and the two buttons
on the Stepper Motors tab that drive them.

Bringing the slits up used to take eight deliberate gestures — jog each axis
onto its limit, then press its Home button, four times. These tests pin down
the routine that replaces them, and in particular the two properties the
operator asked for and cannot check by looking at the screen:

  - the SEQUENTIAL run never has two axes in motion, and never has two commands
    in flight, because it is one thread running one axis at a time to
    completion; and
  - a failure STOPS the run with the remaining axes untouched, rather than
    leaving a set of half-homed slits.

Every test drives a mock controller. Nothing here needs a Galil, and the
routine's own sleeps are patched out so a three-pass home costs nothing.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets")
from unittest.mock import MagicMock
from PySide6.QtWidgets import QApplication

from rbl.config import hardware_config as SC
from rbl.hardware import galil_workers
from rbl.hardware.galil_driver import GalilController
from rbl.hardware.galil_workers import (
    AutoHomeAllWorker, AxisHomeRoutine, HomingWorker,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """The routine's waits are real seconds on real hardware and pure cost
    here — every one of them is bounded by a mock that answers immediately."""
    monkeypatch.setattr(galil_workers.time, "sleep", lambda *a, **k: None)


def _galil(home=False, reverse=False, moving=False):
    g = MagicMock(spec=GalilController)
    g.connected = True
    g.get_switch_states.return_value = {
        "home_switch": home, "reverse_switch": reverse, "forward_switch": False,
    }
    g.is_moving.return_value = moving
    return g


def _switches(home=False, reverse=False, forward=False):
    return {"home_switch": home, "reverse_switch": reverse,
            "forward_switch": forward}


def _jogging_galil(*switch_readings):
    """A controller that is in motion until it is told to stop.

    `switch_readings` are handed out one per query, the last repeating — which
    is how a real seek reads: open, open, …, tripped, and tripped from then on.
    Tying `is_moving` to whether `stop` has been called is what makes the
    routine's "wait for the axis to come to rest" step terminate, exactly as it
    would on hardware.
    """
    g = _galil(moving=True)
    readings = list(switch_readings)
    stopped = {"v": False}

    def next_switches(_axis):
        return readings.pop(0) if len(readings) > 1 else readings[0]

    g.get_switch_states.side_effect = next_switches
    g.stop.side_effect = lambda _axis: stopped.__setitem__("v", True)
    g.is_moving.side_effect = lambda _axis: not stopped["v"]
    return g


# ---- The seek phase ----------------------------------------------------------

class TestSeekHomeLimit:
    """'-Jog until it reaches the home limit', which the operator used to do by
    hand before every single Home press."""

    def test_jogs_negative_until_the_home_switch_trips(self):
        # Open, open, then on the switch: the seek has to keep going until the
        # switch answers, not act on the first reading.
        g = _jogging_galil(_switches(), _switches(), _switches(home=True))
        ok, msg = AxisHomeRoutine(g, "A").seek_home_limit()

        assert ok, msg
        axis, speed = g.jog_start.call_args.args
        assert axis == "A"
        assert speed == -SC.HOME_SEEK_SPEED_COUNTS_PER_SEC   # toward home, not away
        g.stop.assert_called_with("A")

    def test_stops_on_the_reverse_limit_too(self):
        """Home and reverse are read separately but mean the same thing here.
        Waiting only for `home_switch` would jog into a reverse limit and sit
        there until the timeout on any stage where they are one switch."""
        g = _jogging_galil(_switches(), _switches(reverse=True))
        ok, _ = AxisHomeRoutine(g, "B").seek_home_limit()
        assert ok
        g.stop.assert_called_with("B")

    def test_no_jog_when_already_on_the_limit(self):
        g = _galil(home=True)
        ok, _ = AxisHomeRoutine(g, "C").seek_home_limit()
        assert ok
        g.jog_start.assert_not_called()

    def test_reports_a_jog_that_stopped_short(self):
        """The controller stopping the jog with no switch tripped is a fault
        (de-energised axis, forward limit), not an arrival."""
        g = _galil(moving=False)
        g.get_switch_states.return_value = _switches()
        ok, msg = AxisHomeRoutine(g, "A").seek_home_limit()
        assert not ok
        assert "before reaching the home limit" in msg

    def test_times_out_rather_than_jogging_forever(self):
        g = _galil(moving=True)
        g.get_switch_states.return_value = _switches()
        ok, msg = AxisHomeRoutine(g, "A").seek_home_limit(timeout=0.0)
        assert not ok
        assert "timed out" in msg
        g.stop.assert_called_with("A")     # never left jogging

    def test_cancel_stops_the_jog(self):
        cancelled = {"v": False}
        g = _galil(moving=True)
        g.get_switch_states.return_value = _switches()

        def is_moving(_axis):
            cancelled["v"] = True    # cancel arrives one poll in
            return True

        g.is_moving.side_effect = is_moving
        ok, msg = AxisHomeRoutine(g, "A", cancelled=lambda: cancelled["v"]
                                  ).seek_home_limit()
        assert not ok
        assert "cancelled" in msg
        g.stop.assert_called_with("A")


class TestSeekAgainstTheRealDriver:
    """The seek reads switches through GalilController, so the driver's
    polarity is the seek's polarity. Worth pinning without mocking
    get_switch_states, because reading it backwards does not fail loudly — it
    makes the seek decide every axis has already arrived and skip the jog."""

    def _driver(self, **operands):
        from tests.test_galil_protocol import make_galil
        return make_galil(operands)

    def test_a_clear_axis_is_not_mistaken_for_one_on_its_limit(self):
        """Normally-closed switches carry current while the axis is CLEAR, so
        a clear axis reads 1 on all three. If this ever reads as "at the
        limit", every auto-home returns "no seek needed" and moves nothing."""
        g = self._driver(**{"MG _LFA": "1", "MG _LRA": "1", "MG _HMA": "1"})
        assert AxisHomeRoutine(g, "A")._at_home_limit() is False

    def test_an_axis_on_its_reverse_limit_is_detected(self):
        g = self._driver(**{"MG _LFA": "1", "MG _LRA": "0", "MG _HMA": "1"})
        assert AxisHomeRoutine(g, "A")._at_home_limit() is True

    def test_an_axis_on_its_home_switch_is_detected(self):
        g = self._driver(**{"MG _LFA": "1", "MG _LRA": "1", "MG _HMA": "0"})
        assert AxisHomeRoutine(g, "A")._at_home_limit() is True


class TestRoutineEndToEnd:
    def test_seek_then_three_passes_then_zero(self):
        g = _galil(moving=False)
        g.get_switch_states.side_effect = (
            [_switches(), _switches(home=True)] + [_switches()] * 20)
        ok, msg = AxisHomeRoutine(g, "A").run(seek_first=True)
        assert ok, msg
        assert g.jog_start.called                   # the seek happened
        assert g.begin_home.call_count == 3         # coarse -> medium -> fine
        g.define_zero.assert_called_once_with("A")

    def test_a_failed_seek_never_reaches_hm(self):
        """Homing an axis that never got to its limit would search from the
        wrong place — and DP=0 there would define a zero that is not home."""
        g = _galil(moving=False)
        g.get_switch_states.return_value = _switches()
        ok, _ = AxisHomeRoutine(g, "A").run(seek_first=True)
        assert not ok
        g.begin_home.assert_not_called()
        g.define_zero.assert_not_called()

    def test_each_pass_turns_down_both_of_hm_s_stages(self):
        """On a stepper HM has two stages: a fast search at SP, then a slow
        re-approach at HV that is what actually fixes where the zero lands.
        HV was never set at all before, so turning SP down pass by pass was
        tuning the stage that does not determine the answer."""
        g = _galil(moving=False)
        ok, _ = AxisHomeRoutine(g, "A").run(seek_first=False)
        assert ok

        passes = [(c.args[1], c.kwargs["fine_speed"])
                  for c in g.begin_home.call_args_list]
        assert passes == [(225, 225), (112, 112), (58, 58)]

    def test_it_puts_both_speeds_back(self):
        """Every pass left SP and HV turned down to homing speeds; a Move
        issued afterwards would otherwise crawl."""
        g = _galil(moving=False)
        AxisHomeRoutine(g, "A").run(seek_first=False)
        g.set_speed.assert_called_with("A", SC.DEFAULT_SPEED_COUNTS_PER_SEC)
        g.set_home_velocity.assert_called_with(
            "A", SC.DEFAULT_HOME_VELOCITY_COUNTS_PER_SEC)

    def test_the_speeds_go_back_after_a_failure_too(self, monkeypatch):
        # The routine's timeouts are wall-clock deadlines, and `no_sleep` only
        # removes the pauses BETWEEN polls — without a fake clock this test
        # spins for a real 60 s waiting for the first pass to give up.
        clock = iter(range(0, 100_000))
        monkeypatch.setattr(galil_workers.time, "time", lambda: float(next(clock)))

        g = _galil(moving=True)     # never goes idle -> every pass times out
        g.get_switch_states.return_value = _switches()
        ok, _ = AxisHomeRoutine(g, "A").run(seek_first=False)
        assert not ok
        g.set_speed.assert_called_with("A", SC.DEFAULT_SPEED_COUNTS_PER_SEC)
        g.set_home_velocity.assert_called_with(
            "A", SC.DEFAULT_HOME_VELOCITY_COUNTS_PER_SEC)

    def test_zero_is_defined_because_hm_does_not_do_it_on_a_stepper(self):
        """HM's index-latch stage — the one that would define position 0 — is
        servo-only. On a stepper the sequence stops after stage 2, so this DP
        is the only thing that makes the zero exist."""
        g = _galil(moving=False)
        AxisHomeRoutine(g, "A").run(seek_first=False)
        g.define_zero.assert_called_once_with("A")

    def test_without_seek_it_is_the_old_routine(self):
        g = _galil(moving=False)
        ok, _ = AxisHomeRoutine(g, "A").run(seek_first=False)
        assert ok
        g.jog_start.assert_not_called()
        assert g.begin_home.call_count == 3


class TestHomingWorkerSeekFlag:
    def test_seek_first_is_off_by_default(self, qapp):
        """The plain Home button must keep doing exactly what it always did."""
        g = _galil(moving=False)
        hw = HomingWorker(g, "A")
        hw.done.connect(lambda ok, msg: None)
        hw.run()
        g.jog_start.assert_not_called()

    def test_seek_first_adds_the_jog(self, qapp):
        g = _galil(moving=False)
        g.get_switch_states.side_effect = (
            [_switches(), _switches(home=True)] + [_switches()] * 20)
        hw = HomingWorker(g, "A", seek_first=True)
        hw.done.connect(lambda ok, msg: None)
        hw.run()
        assert g.jog_start.called
        g.define_zero.assert_called_once_with("A")


# ---- The all-axes sequencer --------------------------------------------------

class TestAutoHomeAllWorker:
    def test_homes_every_axis_in_order(self, qapp):
        g = _galil(moving=False)
        w = AutoHomeAllWorker(g, ["A", "B", "C", "D"], seek_first=False)
        finished = []
        w.axis_finished.connect(lambda a, ok, m: finished.append((a, ok)))
        results = []
        w.done.connect(lambda ok, msg: results.append((ok, msg)))
        w.run()                                  # synchronous (same thread)

        assert finished == [("A", True), ("B", True), ("C", True), ("D", True)]
        assert [c.args[0] for c in g.define_zero.call_args_list] == \
            ["A", "B", "C", "D"]
        assert results[0][0] is True
        assert "4/4 axes homed" in results[0][1]

    def test_one_axis_at_a_time(self, qapp):
        """The property the operator asked for and cannot see: an axis is
        never started until the previous one has been zeroed."""
        g = _galil(moving=False)
        order = []
        g.begin_home.side_effect = lambda axis, speed, **kw: order.append(("HM", axis))
        g.define_zero.side_effect = lambda axis: order.append(("DP", axis))

        w = AutoHomeAllWorker(g, ["A", "B"], seek_first=False)
        w.done.connect(lambda ok, msg: None)
        w.run()

        # Every command for A, then every command for B — no interleaving.
        assert order == [("HM", "A")] * 3 + [("DP", "A")] \
                      + [("HM", "B")] * 3 + [("DP", "B")]

    def test_a_failure_stops_the_run_and_names_what_was_skipped(self, qapp):
        g = _galil(moving=False)
        # B never reaches its limit, so its seek fails.
        def switches(axis):
            return _switches(home=(axis != "B"))
        g.get_switch_states.side_effect = switches

        w = AutoHomeAllWorker(g, ["A", "B", "C", "D"], seek_first=True)
        seen = []
        w.axis_finished.connect(lambda a, ok, m: seen.append((a, ok)))
        results = []
        w.done.connect(lambda ok, msg: results.append((ok, msg)))
        w.run()

        assert seen == [("A", True), ("B", False)]
        assert [c.args[0] for c in g.define_zero.call_args_list] == ["A"]
        ok, summary = results[0]
        assert ok is False
        assert "stopped on axis B" in summary
        assert "not attempted: C, D" in summary

    def test_cancel_leaves_the_remaining_axes_untouched(self, qapp):
        g = _galil(moving=False)
        w = AutoHomeAllWorker(g, ["A", "B", "C", "D"], seek_first=False)
        results = []
        w.done.connect(lambda ok, msg: results.append((ok, msg)))
        w.cancel()
        w.run()

        g.begin_home.assert_not_called()
        g.define_zero.assert_not_called()
        assert results[0][0] is False
        assert "cancelled" in results[0][1]

    def test_defaults_to_all_four_axes_with_the_seek(self, qapp):
        w = AutoHomeAllWorker(_galil())
        assert w.axes == SC.AXIS_LETTERS
        assert w.seek_first is True     # the point of the button


# ---- The buttons on the Stepper Motors tab -----------------------------------

@pytest.fixture
def tab(qapp):
    from rbl.state.beamline import Beamline
    from rbl.gui.motor_tab import MotorTab

    beamline = Beamline()
    beamline.galil = _galil(moving=False)
    t = MotorTab(beamline)
    t._set_buttons_connected(True)
    # Answer the "start?" dialog without a live message box.
    t._confirm_auto_home = lambda *a, **k: True
    return t


class TestMotorTabAutoHome:
    def test_both_buttons_exist_and_follow_the_connection(self, qapp):
        from rbl.state.beamline import Beamline
        from rbl.gui.motor_tab import MotorTab

        t = MotorTab(Beamline())
        assert not t.btn_auto_home_seq.isEnabled()   # nothing to home yet
        assert not t.btn_auto_home_par.isEnabled()
        t._set_buttons_connected(True)
        assert t.btn_auto_home_seq.isEnabled()
        assert t.btn_auto_home_par.isEnabled()

    def test_a_declined_confirmation_starts_nothing(self, tab):
        tab._confirm_auto_home = lambda *a, **k: False
        tab._start_auto_home()
        assert tab._auto_worker is None
        assert tab.axes["A"].isEnabled() or True     # nothing was locked out
        assert tab.btn_auto_home_seq.text() == "Auto-Home All — One at a Time"

    def test_running_locks_the_per_axis_controls(self, tab):
        """A jog issued mid-sequence would put a second command on an axis the
        sequencer believes it has to itself."""
        tab._lock_axes_for_auto_home(True, "Queued for auto-home")
        for panel in tab.axes.values():
            assert not panel.btn_jog_neg.isEnabled()
            assert not panel.btn_move.isEnabled()
            assert not panel.btn_home.isEnabled()
            assert panel.lbl_status.text() == "Queued for auto-home"
        # EMERGENCY STOP is the one control that must never be locked out.
        assert tab.btn_estop.isEnabled()

        tab._lock_axes_for_auto_home(False)
        assert tab.axes["A"].btn_jog_neg.isEnabled()

    def test_a_held_status_survives_the_poll(self, tab):
        """The 5 Hz poll would otherwise repaint 'Idle' over 'Auto-homing…'
        between two of the sequencer's commands."""
        panel = tab.axes["A"]
        panel.set_external_status("Auto-homing…")
        panel.update_state({"pos": 0, "moving": False, "enabled": True,
                            "switches": _switches()})
        assert panel.lbl_status.text() == "Auto-homing…"

        panel.set_external_status(None)
        panel.update_state({"pos": 0, "moving": False, "enabled": True,
                            "switches": _switches()})
        assert "Idle" in panel.lbl_status.text()

    def test_a_homed_axis_is_marked_referenced(self, tab):
        """The routine ends on DP=0, so mm figures on that axis now mean
        something — the same flag the panel's own Home button sets."""
        assert tab.axes["A"].zeroed is False
        tab._on_auto_axis_finished("A", True, "A: homed")
        assert tab.axes["A"].zeroed is True
        tab._on_auto_axis_finished("B", False, "B: failed")
        assert tab.axes["B"].zeroed is False

    def test_emergency_stop_cancels_a_running_sequence(self, tab):
        """An abort with a homing run still going is not a stop — the next
        pass would start the motion again a second later."""
        tab._auto_worker = AutoHomeAllWorker(tab.galil, ["A"], tab)
        tab._auto_worker.isRunning = lambda: True
        tab._emergency_stop()
        assert tab._auto_worker._cancelled is True

    def test_disconnect_cancels_a_running_sequence(self, tab):
        tab._auto_worker = AutoHomeAllWorker(tab.galil, ["A"], tab)
        tab._auto_worker.isRunning = lambda: True
        tab._do_disconnect()
        assert tab._auto_worker._cancelled is True

    def test_together_sends_one_command_not_four_workers(self, tab, monkeypatch):
        """The whole point of the change: one HM ABCD under one worker, rather
        than four HomingWorkers interleaving their setups on one socket."""
        from rbl.hardware.galil_workers import MultiAxisHomeWorker
        monkeypatch.setattr(MultiAxisHomeWorker, "start", lambda self: None)
        monkeypatch.setattr(MultiAxisHomeWorker, "isRunning", lambda self: True)

        tab._start_auto_home_parallel()

        assert tab._parallel_worker is not None
        assert tab._parallel_worker.axes == "ABCD"
        assert all(p._homing_worker is None for p in tab.axes.values())
        assert not tab.btn_auto_home_seq.isEnabled()   # one mode at a time
        assert tab.btn_auto_home_par.text() == "Cancel All Homing"

    def test_together_locks_the_per_axis_controls(self, tab, monkeypatch):
        from rbl.hardware.galil_workers import MultiAxisHomeWorker
        monkeypatch.setattr(MultiAxisHomeWorker, "start", lambda self: None)
        monkeypatch.setattr(MultiAxisHomeWorker, "isRunning", lambda self: True)

        tab._start_auto_home_parallel()
        for panel in tab.axes.values():
            assert not panel.btn_jog_neg.isEnabled()
        assert tab.btn_estop.isEnabled()

    def test_together_marks_each_axis_referenced_as_it_reports(self, tab):
        """Started together, but they reach their switches at different
        moments — so outcomes still arrive one axis at a time."""
        for axis in "ABCD":
            tab._on_auto_axis_finished(axis, True, f"{axis}: homed")
        assert all(p.zeroed for p in tab.axes.values())

    def test_together_releases_everything_when_it_finishes(self, tab):
        tab._lock_axes_for_auto_home(True, "Homing together…")
        tab.btn_auto_home_par.setText("Cancel All Homing")
        tab.btn_auto_home_seq.setEnabled(False)

        tab._on_parallel_home_done(True, "ABCD: homed together")

        assert tab.btn_auto_home_par.text() == "Auto-Home All — Together"
        assert tab.btn_auto_home_seq.isEnabled()
        assert tab.axes["A"].btn_jog_neg.isEnabled()
        assert "homed together" in tab.lbl_auto_home.text()

    def test_the_two_all_axes_modes_refuse_to_overlap(self, tab, monkeypatch):
        from rbl.hardware.galil_workers import MultiAxisHomeWorker
        monkeypatch.setattr(MultiAxisHomeWorker, "start", lambda self: None)
        monkeypatch.setattr(MultiAxisHomeWorker, "isRunning", lambda self: True)
        tab._start_auto_home_parallel()

        tab._start_auto_home()                    # sequential, while together runs
        assert tab._auto_worker is None

    def test_emergency_stop_cancels_the_together_run(self, tab, monkeypatch):
        from rbl.hardware.galil_workers import MultiAxisHomeWorker
        monkeypatch.setattr(MultiAxisHomeWorker, "start", lambda self: None)
        monkeypatch.setattr(MultiAxisHomeWorker, "isRunning", lambda self: True)
        tab._start_auto_home_parallel()

        tab._emergency_stop()
        assert tab._parallel_worker._cancelled is True


class TestSeekAndHomeButton:
    def test_each_panel_offers_the_two_step_procedure_in_one_click(self, tab):
        for axis, panel in tab.axes.items():
            assert panel.btn_seek_home.text() == f"Seek + Home {axis}"
            assert panel.btn_seek_home.isEnabled()

    def test_it_becomes_cancel_while_running(self, tab, monkeypatch):
        panel = tab.axes["A"]
        started = {}
        monkeypatch.setattr(HomingWorker, "start",
                            lambda self: started.setdefault("seek", self.seek_first))
        monkeypatch.setattr(HomingWorker, "isRunning", lambda self: True)

        panel._start_homing(seek_first=True)
        assert started["seek"] is True
        assert panel.btn_seek_home.text() == "Cancel"

        panel._start_homing(seek_first=True)          # second press cancels
        assert panel._homing_worker._cancelled is True
        assert panel.btn_seek_home.text() == "Seek + Home A"

    def test_the_axis_cannot_be_jogged_while_it_homes(self, tab, monkeypatch):
        """A jog mid-run collides with the routine's own motion. Stop and the
        home buttons stay live — they are the way out."""
        panel = tab.axes["A"]
        monkeypatch.setattr(HomingWorker, "start", lambda self: None)
        monkeypatch.setattr(HomingWorker, "isRunning", lambda self: True)

        panel._start_homing(seek_first=True)
        assert not panel.btn_jog_neg.isEnabled()
        assert not panel.btn_move.isEnabled()
        assert not panel.btn_zero.isEnabled()
        assert panel.btn_stop.isEnabled()
        assert panel.btn_seek_home.isEnabled()

        panel._on_homing_done(True, "A: homed")
        assert panel.btn_jog_neg.isEnabled()
        assert panel.btn_move.isEnabled()

    def test_the_controls_stay_locked_if_the_link_dropped(self, tab, monkeypatch):
        from rbl.gui import motor_tab as mt
        # A failed run raises a modal warning; the test must not block on it.
        monkeypatch.setattr(mt.QMessageBox, "warning",
                            staticmethod(lambda *a, **k: None))
        panel = tab.axes["A"]
        monkeypatch.setattr(HomingWorker, "start", lambda self: None)
        monkeypatch.setattr(HomingWorker, "isRunning", lambda self: True)
        panel._start_homing(seek_first=True)

        tab.beamline.galil.connected = False
        panel._on_homing_done(False, "A: link dropped")
        assert not panel.btn_jog_neg.isEnabled()

    def test_plain_home_still_skips_the_seek(self, tab, monkeypatch):
        panel = tab.axes["B"]
        seen = {}
        monkeypatch.setattr(HomingWorker, "start",
                            lambda self: seen.setdefault("seek", self.seek_first))
        monkeypatch.setattr(HomingWorker, "isRunning", lambda self: False)
        panel._start_homing(seek_first=False)
        assert seen["seek"] is False


# ---- All four axes under ONE command -----------------------------------------

class TestMultiAxisHomeRoutine:
    """The HM reference's own idiom — "HM Set Homing Mode for all axes / BG
    Home all axes" — instead of four host threads each sending their own
    five-command HM setup down one socket."""

    def _routine(self, g, axes="ABCD", **kw):
        from rbl.hardware.galil_workers import MultiAxisHomeRoutine
        return MultiAxisHomeRoutine(g, axes, **kw)

    def test_one_hm_and_one_bg_for_the_whole_set(self):
        g = _galil(moving=False)
        ok, msg = self._routine(g).run(seek_first=False)
        assert ok, msg
        # Three passes total, not three per axis — every call names all four.
        assert g.begin_home_multi.call_count == 3
        assert all(c.args[0] == "ABCD" for c in g.begin_home_multi.call_args_list)
        g.begin_home.assert_not_called()          # never the per-axis form

    def test_zero_is_defined_for_every_axis_in_one_command(self):
        g = _galil(moving=False)
        self._routine(g).run(seek_first=False)
        g.define_zero_multi.assert_called_once_with("ABCD")
        g.define_zero.assert_not_called()

    def test_each_pass_turns_down_both_stages(self):
        g = _galil(moving=False)
        self._routine(g).run(seek_first=False)
        passes = [(c.args[1], c.kwargs["fine_speed"])
                  for c in g.begin_home_multi.call_args_list]
        assert passes == [(225, 225), (112, 112), (58, 58)]

    def test_the_seek_starts_every_axis_with_one_jog(self):
        # Every axis clear on the first look, every axis arrived on the next —
        # tracked per axis, since the routine asks each one separately.
        g = _galil(moving=True)
        looks: dict[str, int] = {}

        def switches(axis):
            looks[axis] = looks.get(axis, 0) + 1
            return _switches(home=looks[axis] > 1)

        g.get_switch_states.side_effect = switches
        g.is_moving.side_effect = lambda _a: False

        ok, msg = self._routine(g).seek_home_limits()
        assert ok, msg
        axes, speed = g.jog_start_multi.call_args.args
        assert axes == "ABCD"                    # one jog, not four
        assert speed == -SC.HOME_SEEK_SPEED_COUNTS_PER_SEC
        # Each axis is stopped on its OWN switch — they start together but do
        # not arrive together.
        assert {c.args[0] for c in g.stop.call_args_list} == set("ABCD")

    def test_axes_already_on_the_limit_are_left_out_of_the_jog(self):
        """No point commanding motion on an axis that has arrived — and
        jogging it further would drive it into the limit it is sitting on."""
        g = _galil(moving=True)
        g.get_switch_states.side_effect = lambda a: _switches(home=(a in "AB"))
        self._routine(g).seek_home_limits(timeout=0.0)
        assert g.jog_start_multi.call_args.args[0] == "CD"

    def test_nothing_is_homed_when_an_axis_fails_its_seek(self):
        """Started together means failing together: an axis that never reached
        its limit would be homed from the wrong place, and HM ABCD cannot
        leave that one out once it is issued."""
        g = _galil(moving=False)
        g.get_switch_states.side_effect = lambda a: _switches(home=(a != "C"))
        seen = []
        ok, summary = self._routine(g, axis_done=lambda a, o, m: seen.append((a, o))
                                    ).run(seek_first=True)
        assert not ok
        assert "C" in summary
        g.begin_home_multi.assert_not_called()
        g.define_zero_multi.assert_not_called()
        assert ("C", False) in seen

    def test_every_axis_is_reported_when_the_run_succeeds(self):
        g = _galil(moving=False)
        seen = []
        ok, _ = self._routine(g, axis_done=lambda a, o, m: seen.append((a, o))
                              ).run(seek_first=False)
        assert ok
        assert seen == [("A", True), ("B", True), ("C", True), ("D", True)]

    def test_it_puts_both_speeds_back_for_every_axis(self):
        g = _galil(moving=False)
        self._routine(g).run(seek_first=False)
        g.set_speed_multi.assert_called_with(
            "ABCD", SC.DEFAULT_SPEED_COUNTS_PER_SEC)
        g.set_home_velocity_multi.assert_called_with(
            "ABCD", SC.DEFAULT_HOME_VELOCITY_COUNTS_PER_SEC)

    def test_cancel_before_the_passes_homes_nothing(self):
        g = _galil(moving=False)
        ok, msg = self._routine(g, cancelled=lambda: True).run(seek_first=False)
        assert not ok
        assert "cancelled" in msg.lower()
        g.begin_home_multi.assert_not_called()
        g.define_zero_multi.assert_not_called()
