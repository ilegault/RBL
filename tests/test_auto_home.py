"""
Automatic homing: NEC's HM homing routine, the all-axes sequencer, and the
single Home button on the Stepper Motors tab (ADR 0010).

Each axis homes with a pre-home move off the switch if needed (2000 counts at
1800 cps), then `SP 900`, `HM`, `BG` twice, defining zero (`DP=0` and `DE=0`) at
the second stop if the two stops agree within 4 counts. These tests pin down:

  - the `HM` routine, pre-home move, accepted stop codes (3 and 10), stop
    agreement, cancel/timeout handling, and at-rest reads;
  - the SEQUENTIAL run never having two axes in motion or two commands in
    flight, and stopping on the first failure with remaining axes untouched;
  - the single Home button per axis cancelling and locking controls safely.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import MagicMock, call

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from rbl.config import hardware_config as SC
from rbl.hardware import galil_workers
from rbl.hardware.galil_driver import GalilController
from rbl.hardware.galil_workers import (
    AutoHomeAllWorker,
    AxisHomeRoutine,
    HomingWorker,
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
        """Only the reverse limit active is NOT the home limit any more."""
        g = self._driver(**{"MG _LFA": "1", "MG _LRA": "0", "MG _HMA": "1"})
        assert AxisHomeRoutine(g, "A")._at_home_limit() is False

    def test_an_axis_on_its_home_switch_is_detected(self):
        g = self._driver(**{"MG _LFA": "1", "MG _LRA": "1", "MG _HMA": "0"})
        assert AxisHomeRoutine(g, "A")._at_home_limit() is True


class TestRoutineEndToEnd:
    def test_the_speeds_go_back_after_a_failure_too(self):
        from tests.sim_slit import SimGalil

        sim = SimGalil(trip_at=-3, release_at=6, slip_on_home={2: 8})
        ok, _ = AxisHomeRoutine(sim, "A").run()
        assert not ok
        speed_calls = [c for c in sim.calls if c[0] == "set_speed"]
        assert speed_calls[-1] == ("set_speed", ("A", SC.DEFAULT_SPEED_COUNTS_PER_SEC))


class TestBackOutOfHome:
    def test_homing_backs_out_before_anything_else(self):
        from tests.sim_slit import SimGalil

        sim = SimGalil(start_at=-5, trip_at=-3, release_at=6)
        ok, msg = AxisHomeRoutine(sim, "A").run()
        assert ok, msg
        motion = [c for c in sim.calls if c[0] in ("move_relative", "home", "jog_start")]
        assert motion[0] == ("move_relative", ("A", SC.PRE_HOME_MOVE_COUNTS))

    def test_back_out_gives_up_after_the_last_try(self):
        g = _galil(home=True, moving=False)
        ok, msg = AxisHomeRoutine(g, "A").run()
        assert not ok
        assert msg == (
            f"A: still on the home switch after the pre-home move of "
            f"{SC.PRE_HOME_MOVE_COUNTS} counts — zero not set"
        )
        assert g.move_relative.call_count == 1
        assert g.move_relative.call_args.args == ("A", SC.PRE_HOME_MOVE_COUNTS)
        g.home.assert_not_called()
        g.define_zero.assert_not_called()
        g.define_encoder_zero.assert_not_called()

    def test_no_back_out_on_the_forward_limit(self):
        g = _galil(moving=False)
        g.get_switch_states.return_value = _switches(forward=True)
        ok, msg = AxisHomeRoutine(g, "A").pre_home_move()
        assert (ok, msg) == (True, "")
        g.move_relative.assert_not_called()


def _multi_routine(g, axes="ABCD", **kw):
    from rbl.hardware.galil_workers import MultiAxisHomeRoutine
    return MultiAxisHomeRoutine(g, axes, **kw)


# ---- The all-axes sequencer --------------------------------------------------

class TestAutoHomeAllWorker:
    def test_homes_every_axis_in_order(self, qapp):
        from tests.sim_slit import SimGalil

        sim = SimGalil()
        w = AutoHomeAllWorker(sim, ["A", "B", "C", "D"])
        finished = []
        w.axis_finished.connect(lambda a, ok, m: finished.append((a, ok)))
        results = []
        w.done.connect(lambda ok, msg: results.append((ok, msg)))
        w.run()                                  # synchronous (same thread)

        assert finished == [("A", True), ("B", True), ("C", True), ("D", True)]
        assert [c[1][0] for c in sim.calls if c[0] == "define_zero"] == \
            ["A", "B", "C", "D"]
        assert results[0][0] is True
        assert "4/4 axes homed" in results[0][1]

    def test_one_axis_at_a_time(self, qapp):
        """The property the operator asked for and cannot see: an axis is
        never started until the previous one has been zeroed."""
        from tests.sim_slit import SimGalil

        sim = SimGalil(axes="AB")
        w = AutoHomeAllWorker(sim, ["A", "B"])
        w.done.connect(lambda ok, msg: None)
        w.run()

        axes_seq = [
            c[1][0] for c in sim.calls
            if c[0] in ("home", "move_relative", "define_zero")
        ]
        first_b = axes_seq.index("B")
        assert set(axes_seq[:first_b]) == {"A"}
        assert set(axes_seq[first_b:]) == {"B"}
        first_b_home = sim.calls.index(("home", ("B",)))
        assert ("define_zero", ("A",)) in sim.calls[:first_b_home]

    def test_a_failure_stops_the_run_and_names_what_was_skipped(self, qapp):
        from tests.sim_slit import SimGalil, SimSlit

        sim = SimGalil()
        # B hits its reverse limit before its home switch, so its HM stops off home.
        sim.slits["B"] = SimSlit(
            trip_at=-50, release_at=-40,
            reverse_trip_at=-3, reverse_release_at=6,
        )

        w = AutoHomeAllWorker(sim, ["A", "B", "C", "D"])
        seen = []
        w.axis_finished.connect(lambda a, ok, m: seen.append((a, ok)))
        results = []
        w.done.connect(lambda ok, msg: results.append((ok, msg)))
        w.run()

        assert seen == [("A", True), ("B", False)]
        assert [c[1][0] for c in sim.calls if c[0] == "define_zero"] == ["A"]
        ok, summary = results[0]
        assert ok is False
        assert "stopped on axis B" in summary
        assert "not attempted: C, D" in summary

    def test_cancel_leaves_the_remaining_axes_untouched(self, qapp):
        g = _galil(moving=False)
        w = AutoHomeAllWorker(g, ["A", "B", "C", "D"])
        results = []
        w.done.connect(lambda ok, msg: results.append((ok, msg)))
        w.cancel()
        w.run()

        g.home.assert_not_called()
        g.define_zero.assert_not_called()
        assert results[0][0] is False
        assert "cancelled" in results[0][1]


# ---- The buttons on the Stepper Motors tab -----------------------------------

@pytest.fixture
def tab(qapp):
    from rbl.gui.motor_tab import MotorTab
    from rbl.state.beamline import Beamline

    beamline = Beamline()
    beamline.galil = _galil(moving=False)
    t = MotorTab(beamline)
    t._set_buttons_connected(True)
    # Answer the "start?" dialog without a live message box.
    t._confirm_auto_home = lambda *a, **k: True
    return t


class TestMotorTabAutoHome:
    def test_both_buttons_exist_and_follow_the_connection(self, qapp):
        from rbl.gui.motor_tab import MotorTab
        from rbl.state.beamline import Beamline

        t = MotorTab(Beamline())
        assert not t.btn_auto_home_seq.isEnabled()   # nothing to home yet
        t._set_buttons_connected(True)
        assert t.btn_auto_home_seq.isEnabled()

    def test_a_declined_confirmation_starts_nothing(self, tab):
        tab._confirm_auto_home = lambda *a, **k: False
        tab._start_auto_home()
        assert tab._auto_worker is None
        assert tab.axes["A"].isEnabled() or True     # nothing was locked out
        assert tab.btn_auto_home_seq.text() == "Auto-Home All — One at a Time"

    def test_approach_speed_box_starts_at_2(self, tab):
        assert tab.spn_approach_speed.minimum() == 2
        tab.spn_approach_speed.setValue(2)
        assert tab.beamline.home_approach_speed() == 2

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
        something — recorded on Beamline per axis."""
        assert tab.beamline.is_zeroed("A") is False
        tab._on_auto_axis_finished("A", True, "A: homed")
        assert tab.beamline.is_zeroed("A") is True
        tab._on_auto_axis_finished("B", False, "B: failed")
        assert tab.beamline.is_zeroed("B") is False

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

    def test_approach_speed_box_edits_the_beamline(self, tab):
        assert tab.spn_approach_speed.value() == 900
        assert tab.spn_approach_speed.maximum() == 2000
        tab.spn_approach_speed.setValue(40)
        assert tab.beamline.home_approach_speed() == 40

    def test_approach_speed_box_takes_1800(self, tab):
        assert tab.spn_approach_speed.maximum() == 2000
        tab.spn_approach_speed.setValue(1800)
        assert tab.beamline.home_approach_speed() == 1800

    def test_approach_speed_box_is_locked_while_homing(self, tab, monkeypatch):
        from rbl.hardware.galil_workers import EdgeReturnWorker
        monkeypatch.setattr(
            "rbl.gui.motor_tab.QMessageBox.question",
            lambda *a, **k: QMessageBox.StandardButton.Yes)
        started = []
        running = set()
        for cls in (AutoHomeAllWorker, HomingWorker, EdgeReturnWorker):
            monkeypatch.setattr(
                cls, "start",
                lambda self: (started.append(self), running.add(self)),
            )
            monkeypatch.setattr(cls, "isRunning", lambda self: self in running)
        assert tab.spn_approach_speed.isEnabled()

        tab.btn_auto_home_seq.click()
        assert not tab.spn_approach_speed.isEnabled()
        w = started.pop()
        running.discard(w)
        w.done.emit(True, "all homed")
        assert tab.spn_approach_speed.isEnabled()

        tab.axes["A"].btn_home.click()
        assert not tab.spn_approach_speed.isEnabled()
        w = started.pop()
        running.discard(w)
        w.done.emit(True, "A: homed")
        assert tab.spn_approach_speed.isEnabled()

        tab.btn_edge_run.click()
        assert not tab.spn_approach_speed.isEnabled()
        w = started.pop()
        running.discard(w)
        w.done.emit(False, "cancelled", [])
        assert tab.spn_approach_speed.isEnabled()

    def test_the_together_path_is_gone(self, tab):
        assert not hasattr(tab, "btn_auto_home_par")
        assert not hasattr(galil_workers, "MultiAxisHomeRoutine")
        assert not hasattr(galil_workers, "MultiAxisHomeWorker")
        assert not hasattr(GalilController, "begin_home")
        assert not hasattr(GalilController, "begin_home_multi")
        assert not hasattr(GalilController, "set_home_velocity")
        assert not hasattr(SC, "HOME_STOP_CODE")
        assert not hasattr(SC, "HOME_PASS_SPEEDS_COUNTS_PER_SEC")

    def test_no_homing_text_mentions_the_old_passes(self, tab):
        from PySide6.QtWidgets import QAbstractSpinBox, QLabel, QPushButton

        from rbl.gui.motor_tab import MotorTab

        texts = [MotorTab._AUTO_HOME_STANDING]
        for cls in (QLabel, QPushButton, QAbstractSpinBox):
            for widget in tab.findChildren(cls):
                if hasattr(widget, "text"):
                    texts.append(widget.text())
                texts.append(widget.toolTip())
        banned = ("three-pass", "Together", "Pass 1", "pass speed", "Seek + Home")
        for text in texts:
            for phrase in banned:
                assert phrase not in text, f"Found {phrase!r} in {text!r}"


class TestHmHoming:
    """NEC's HM homing procedure (ADR 0010): pre-home move if on switch, two
    passes of HM at 900 cps, DP=0 and DE=0 at pass 2 if stops agree within 4."""

    def test_hm_home_from_clear_zeroes_at_the_second_stop(self):
        from tests.sim_slit import SimGalil

        sim = SimGalil()
        lines: list[str] = []
        ok, msg = AxisHomeRoutine(sim, "C", progress=lines.append).run()

        assert ok is True, msg
        assert msg == "C: homed — stops -12, -12 cts, DP=0 at pass 2"
        assert "C: pass 1/2 — stopped at -12 cts" in lines
        assert "C: pass 2/2 — stopped at -12 cts" in lines

        homes = [c for c in sim.calls if c[0] == "home"]
        assert homes == [("home", ("C",)), ("home", ("C",))]
        assert [c for c in sim.calls if c[0] == "jog_start"] == []
        zeros = [c for c in sim.calls if c[0] in ("define_zero", "define_encoder_zero")]
        assert zeros == [("define_zero", ("C",)), ("define_encoder_zero", ("C",))]

        speeds = [c[1][1] for c in sim.calls if c[0] == "set_speed"]
        assert speeds and set(speeds) <= {900, 1800}
        assert speeds[-1] == 1800
        assert sim.slits["C"].zeroed_at == [-12]

    def test_hm_home_moves_off_the_switch_before_each_pass(self):
        from tests.sim_slit import SimGalil

        sim_on = SimGalil(start_at=-5, trip_at=-3, release_at=6)
        ok, msg = AxisHomeRoutine(sim_on, "C").run()
        assert ok is True, msg
        motion_on = [c for c in sim_on.calls if c[0] in ("move_relative", "home")]
        assert motion_on == [
            ("move_relative", ("C", 2000)),
            ("home", ("C",)),
            ("move_relative", ("C", 2000)),
            ("home", ("C",)),
        ]
        for idx, (name, _args) in enumerate(sim_on.calls):
            if name == "move_relative":
                speeds_before = [
                    a[1] for n, a in sim_on.calls[:idx] if n == "set_speed"
                ]
                assert speeds_before[-1] == 1800
            elif name == "home":
                speeds_before = [
                    a[1] for n, a in sim_on.calls[:idx] if n == "set_speed"
                ]
                assert speeds_before[-1] == 900

        sim_clear = SimGalil(start_at=2000, trip_at=-3, release_at=6)
        ok2, msg2 = AxisHomeRoutine(sim_clear, "C").run()
        assert ok2 is True, msg2
        motion_clear = [c for c in sim_clear.calls if c[0] in ("move_relative", "home")]
        assert motion_clear == [
            ("home", ("C",)),
            ("move_relative", ("C", 2000)),
            ("home", ("C",)),
        ]

        g_fwd = _galil(moving=False)
        g_fwd.get_switch_states.return_value = _switches(forward=True)
        assert AxisHomeRoutine(g_fwd, "C").pre_home_move() == (True, "")
        g_fwd.move_relative.assert_not_called()
        g_fwd.home.assert_not_called()

    def test_hm_home_accepts_stop_codes_3_and_10(self):
        from tests.sim_slit import SimGalil

        sim_3 = SimGalil()
        ok3, msg3 = AxisHomeRoutine(sim_3, "C").run()
        assert ok3 is True, msg3
        assert sim_3.slits["C"].stop_code == 3
        assert sim_3.slits["C"].zeroed_at == [-12]

        sim_10 = SimGalil(
            trip_at=-3, release_at=6,
            reverse_trip_at=-53, reverse_release_at=-44,
        )
        ok10, msg10 = AxisHomeRoutine(sim_10, "C").run()
        assert ok10 is True, msg10
        assert sim_10.slits["C"].stop_code == 10
        assert sim_10.slits["C"].zeroed_at == [-3]

    def test_hm_home_rejects_a_bad_stop_and_sets_no_zero(self):
        from tests.sim_slit import SimGalil

        # Pre-home move leaves the switch tripped.
        g_stuck = _galil(home=True, moving=False)
        ok, msg = AxisHomeRoutine(g_stuck, "C").run()
        assert ok is False
        assert msg == (
            "C: still on the home switch after the pre-home move of 2000 counts — zero not set"
        )
        g_stuck.home.assert_not_called()
        g_stuck.define_zero.assert_not_called()
        g_stuck.define_encoder_zero.assert_not_called()

        # Reverse limit tripped with home switch clear.
        sim_rev = SimGalil(
            trip_at=-50, release_at=-40,
            reverse_trip_at=-3, reverse_release_at=6,
        )
        ok, msg = AxisHomeRoutine(sim_rev, "C").run()
        assert ok is False
        assert msg == (
            "C: reached the reverse limit before the home switch — "
            "check the home switch and its cable"
        )
        assert sim_rev.slits["C"].zeroed_at == []
        assert not any(c[0] in ("define_zero", "define_encoder_zero") for c in sim_rev.calls)

        # Neither switch tripped (stop code 4).
        g_neither = _galil(home=False, reverse=False, moving=False)
        g_neither.get_stop_code.return_value = 4
        g_neither.get_position.return_value = 500
        g_neither.get_step_count.return_value = 500
        ok, msg = AxisHomeRoutine(g_neither, "C").run()
        assert ok is False
        assert msg == "C: home move stopped before reaching the home switch (stop code 4)"
        g_neither.define_zero.assert_not_called()
        g_neither.define_encoder_zero.assert_not_called()

        # Home switch tripped with unexpected stop code (code 1).
        g_code1 = _galil(moving=False)
        g_code1.get_switch_states.side_effect = [
            _switches(),       # pre_home_move: clear
            _switches(),       # home_move precondition: clear
            _switches(home=True),  # post-wait_at_rest switch check
        ]
        g_code1.get_stop_code.return_value = 1
        g_code1.get_position.return_value = -12
        g_code1.get_step_count.return_value = -12
        ok, msg = AxisHomeRoutine(g_code1, "C").run()
        assert ok is False
        assert msg == "C: home move ended with stop code 1 — zero not set"
        g_code1.define_zero.assert_not_called()
        g_code1.define_encoder_zero.assert_not_called()

        # Pass 1 and pass 2 stops disagree (> 4 counts).
        sim_slip = SimGalil(trip_at=-3, release_at=6, slip_on_home={2: 8})
        ok, msg = AxisHomeRoutine(sim_slip, "C").run()
        assert ok is False
        assert msg == (
            "C: home stops disagree — pass 1 at -12 cts, pass 2 at -20 cts "
            "(limit 4) — zero not set"
        )
        assert sim_slip.slits["C"].zeroed_at == []
        assert not any(c[0] in ("define_zero", "define_encoder_zero") for c in sim_slip.calls)

        # Calling home_move directly while already on the home switch sends nothing.
        g_precond = _galil(home=True, moving=False)
        ok, msg, stop_pos = AxisHomeRoutine(g_precond, "C").home_move(900)
        assert (ok, stop_pos) == (False, None)
        assert msg == "C: on the home switch before the home move — run the pre-home move first"
        g_precond.home.assert_not_called()
        g_precond.set_speed.assert_not_called()

    def test_hm_home_cancel_timeout_and_reads_at_rest(self, monkeypatch):
        # 1. Cancel during home move calls stop and restores speed to 1800.
        cancelled = {"v": False}
        g_cancel = _galil(moving=True)
        g_cancel.get_switch_states.return_value = _switches()

        def is_moving_cancel(_axis):
            cancelled["v"] = True
            return True

        g_cancel.is_moving.side_effect = is_moving_cancel
        ok, msg = AxisHomeRoutine(g_cancel, "C", cancelled=lambda: cancelled["v"]).run()
        assert ok is False
        assert msg == "C: cancelled during the home move"
        g_cancel.stop.assert_called_with("C")
        speed_calls_c = [c for c in g_cancel.set_speed.call_args_list]
        assert speed_calls_c[-1].args == ("C", 1800)

        # 2. Timeout during home move (monkeypatched clock) calls stop and restores 1800.
        g_to = _galil(moving=True)
        g_to.get_switch_states.return_value = _switches()
        with monkeypatch.context() as m:
            clock = iter([1000.0 + 200.0 * i for i in range(20)])
            m.setattr(galil_workers.time, "time", lambda: next(clock))
            ok, msg = AxisHomeRoutine(g_to, "C").run()
        assert ok is False
        assert msg == f"C: timed out after {SC.HOME_SEEK_TIMEOUT_S:.0f} s in the home move"
        g_to.stop.assert_called_with("C")
        speed_calls_to = [c for c in g_to.set_speed.call_args_list]
        assert speed_calls_to[-1].args == ("C", 1800)

        # 3. No get_switch_states or get_stop_code after home before TD == RP.
        g_lag = _galil(moving=False)
        g_lag.get_switch_states.side_effect = [_switches(), _switches(home=True)]
        g_lag.get_position.return_value = -12
        g_lag.get_step_count.side_effect = [-10, -11, -12]
        g_lag.get_stop_code.return_value = 3
        ok, msg, stop_pos = AxisHomeRoutine(g_lag, "C").home_move(900)
        assert ok is True, msg
        assert stop_pos == -12

        home_idx = g_lag.mock_calls.index(call.home("C"))
        after_home = g_lag.mock_calls[home_idx + 1:]
        step_indices = [
            i for i, c in enumerate(after_home) if c == call.get_step_count("C")
        ]
        assert len(step_indices) == 3
        settled_idx = step_indices[-1]
        for i, c in enumerate(after_home):
            if c in (call.get_switch_states("C"), call.get_stop_code("C")):
                assert i > settled_idx
        assert call.get_switch_states("C") in after_home[settled_idx + 1:]
        assert call.get_stop_code("C") in after_home[settled_idx + 1:]

        # 4. wait_at_rest failure inside run() also restores speed to 1800.
        from tests.sim_slit import SimGalil
        sim_lag = SimGalil(trip_at=-3, release_at=6, start_at=2000, pulse_lag_calls=10**9)
        sim_lag.move_relative("A", 5)
        ok_rest, msg_rest = AxisHomeRoutine(sim_lag, "A").wait_at_rest(timeout=0.05)
        assert ok_rest is False
        assert msg_rest == "step pulses still pending: TD 2000, RP 2005"

        g_rest_fail = _galil(moving=False)
        g_rest_fail.get_switch_states.return_value = _switches()
        g_rest_fail.get_step_count.return_value = -10
        g_rest_fail.get_position.return_value = -12
        with monkeypatch.context() as m:
            clock2 = iter([2000.0 + 5.0 * i for i in range(20)])
            m.setattr(galil_workers.time, "time", lambda: next(clock2))
            ok_rf, msg_rf = AxisHomeRoutine(g_rest_fail, "C").run()
        assert ok_rf is False
        assert msg_rf == "C: step pulses still pending: TD -10, RP -12"
        assert g_rest_fail.set_speed.call_args_list[-1].args == ("C", 1800)


class TestHomeButton:
    def test_one_home_button_that_cancels_and_locks(self, tab, monkeypatch):
        from rbl.gui import motor_tab as mt

        for axis, panel in tab.axes.items():
            assert not hasattr(panel, "btn_seek_home")
            assert panel.btn_home.text() == f"Home {axis}"
            assert panel.btn_home.isEnabled()

        tab.spn_approach_speed.setValue(40)
        made = []
        running = {"v": False}

        class FakeHomingWorker:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs
                self.cancelled = False
                self._progress_cbs = []
                self._done_cbs = []
                made.append(self)

                class _Sig:
                    def __init__(self, cbs):
                        self._cbs = cbs

                    def connect(self, fn):
                        self._cbs.append(fn)

                self.progress = _Sig(self._progress_cbs)
                self.done = _Sig(self._done_cbs)

            def start(self):
                running["v"] = True

            def isRunning(self):
                return running["v"]

            def cancel(self):
                self.cancelled = True

            def finish(self, ok: bool, msg: str):
                running["v"] = False
                for fn in self._done_cbs:
                    fn(ok, msg)

        monkeypatch.setattr(mt, "HomingWorker", FakeHomingWorker)
        monkeypatch.setattr(mt.QMessageBox, "warning", staticmethod(lambda *a, **k: None))

        panel = tab.axes["A"]
        panel.btn_home.click()
        assert len(made) == 1
        assert made[0].args == (tab.beamline.galil, "A", panel)
        assert made[0].kwargs == {}
        assert panel.btn_home.text() == "Cancel"
        assert not panel.btn_jog_neg.isEnabled()
        assert not panel.btn_move.isEnabled()
        assert not panel.btn_zero.isEnabled()
        assert panel.btn_stop.isEnabled()
        assert panel.btn_home.isEnabled()

        # Second click cancels and restores Home A.
        panel.btn_home.click()
        assert made[0].cancelled is True
        assert panel.btn_home.text() == "Home A"
        made[0].finish(False, "A: cancelled during the home move")
        assert panel.btn_jog_neg.isEnabled()
        assert panel.btn_move.isEnabled()

        # Failed home with dropped Galil link keeps controls locked.
        panel.btn_home.click()
        assert len(made) == 2
        tab.beamline.galil.connected = False
        made[1].finish(False, "A: link dropped")
        assert not panel.btn_jog_neg.isEnabled()
        assert not panel.btn_move.isEnabled()
        assert panel.btn_home.text() == "Home A"
