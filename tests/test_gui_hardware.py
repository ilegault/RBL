"""
Headless GUI tests (offscreen Qt) for the hardware tabs and the outer
tab/split-screen state machine.

Covers, in particular:
  * the split-screen regression — Stepper Motors + Beam Current must divide the
    window exactly 50/50 and never overshoot the viewport;
  * every transition of the Analysis / Stepper / Current click state machine,
    including entering split, isolating a panel, and re-entering split;
  * MotorTab unit conversions (cps <-> mm/s, counts <-> mm);
  * the command-history line edit;
  * CurrentTab live/frozen plot navigation and voltage->current readout;
  * the Voltage Calculator;
  * both background poll workers (Galil + LabJack) running at the same time.

The whole module is skipped automatically if a Qt platform plugin cannot be
initialised (e.g. GUI libraries missing on a headless CI box).
"""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QScrollArea, QMessageBox
from PySide6.QtCore import Qt, QEvent, QObject
from PySide6.QtGui import QKeyEvent


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        try:
            app = QApplication([])
        except Exception as e:                       # pragma: no cover
            pytest.skip(f"Cannot start Qt: {e}")
    return app


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """Stop QMessageBox.* from blocking the test on a modal dialog."""
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


def _press(widget, key):
    widget.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
    )


def _visible_to_view(view, idx):
    return view._splitter.widget(idx).isVisibleTo(view)


# ── MainWindow + split-screen state machine ──────────────────────────────────

@pytest.fixture
def win(qapp, monkeypatch):
    # Neutralise the auto-run timer so no heavy compute thread starts mid-test.
    from rbl.gui.app import MainWindow
    monkeypatch.setattr(MainWindow, "run", lambda self: None)
    w = MainWindow()
    w.resize(1440, 920)
    w.show()
    qapp.processEvents()
    yield w
    w.close()
    qapp.processEvents()


class TestHardwareViewWrapping:
    def test_panels_are_scroll_wrapped(self, win):
        # Scroll-wrapping is what lets each panel shrink so the split fits.
        spl = win._hw_view._splitter
        assert isinstance(spl.widget(0), QScrollArea)
        assert isinstance(spl.widget(1), QScrollArea)

    def test_starts_on_analysis(self, win):
        assert win._outer_stack.currentIndex() == 0
        assert win._hw_split_active is False


class TestSplitStateMachine:
    """Drive _on_outer_tab_clicked through every meaningful transition."""

    def test_click_motors_shows_motors_only(self, win):
        win._on_outer_tab_clicked(1)
        assert win._outer_stack.currentIndex() == 1
        assert win._hw_split_active is False
        assert _visible_to_view(win._hw_view, 0) is True
        assert _visible_to_view(win._hw_view, 1) is False

    def test_motors_then_current_enters_split(self, win):
        win._on_outer_tab_clicked(1)        # motors
        win._on_outer_tab_clicked(2)        # cross to current -> SPLIT
        assert win._hw_split_active is True
        assert _visible_to_view(win._hw_view, 0) is True
        assert _visible_to_view(win._hw_view, 1) is True

    def test_current_then_motors_enters_split(self, win):
        win._on_outer_tab_clicked(2)        # current
        win._on_outer_tab_clicked(1)        # cross to motors -> SPLIT
        assert win._hw_split_active is True
        assert _visible_to_view(win._hw_view, 0) is True
        assert _visible_to_view(win._hw_view, 1) is True

    def test_click_in_split_isolates_motors(self, win):
        win._on_outer_tab_clicked(1)
        win._on_outer_tab_clicked(2)        # split
        win._on_outer_tab_clicked(1)        # click motors -> isolate motors
        assert win._hw_split_active is False
        assert _visible_to_view(win._hw_view, 0) is True
        assert _visible_to_view(win._hw_view, 1) is False

    def test_click_in_split_isolates_current(self, win):
        win._on_outer_tab_clicked(2)
        win._on_outer_tab_clicked(1)        # split
        win._on_outer_tab_clicked(2)        # click current -> isolate current
        assert win._hw_split_active is False
        assert _visible_to_view(win._hw_view, 0) is False
        assert _visible_to_view(win._hw_view, 1) is True

    def test_analysis_resets_split(self, win):
        win._on_outer_tab_clicked(1)
        win._on_outer_tab_clicked(2)        # split
        win._on_outer_tab_clicked(0)        # back to analysis
        assert win._outer_stack.currentIndex() == 0
        assert win._hw_split_active is False

    def test_can_re_enter_split_after_isolating(self, win):
        win._on_outer_tab_clicked(1)
        win._on_outer_tab_clicked(2)        # split
        win._on_outer_tab_clicked(1)        # isolate motors
        win._on_outer_tab_clicked(2)        # cross again -> split again
        assert win._hw_split_active is True
        assert _visible_to_view(win._hw_view, 0) is True
        assert _visible_to_view(win._hw_view, 1) is True

    def test_analysis_to_current_does_not_split(self, win):
        win._on_outer_tab_clicked(1)
        win._on_outer_tab_clicked(0)        # analysis (prev becomes 0)
        win._on_outer_tab_clicked(2)        # current straight from analysis
        assert win._hw_split_active is False
        assert _visible_to_view(win._hw_view, 0) is False
        assert _visible_to_view(win._hw_view, 1) is True

    def test_clicking_same_motors_tab_twice_stays_motors(self, win):
        win._on_outer_tab_clicked(1)
        win._on_outer_tab_clicked(1)        # same tab again
        assert win._hw_split_active is False
        assert _visible_to_view(win._hw_view, 0) is True
        assert _visible_to_view(win._hw_view, 1) is False


class TestSplitSizing:
    """The actual regression: split must be 50/50 and inside the viewport."""

    def test_split_is_even(self, qapp, win):
        win._on_outer_tab_clicked(1)
        win._on_outer_tab_clicked(2)        # split
        qapp.processEvents()
        spl = win._hw_view._splitter
        a, b = spl.sizes()
        assert abs(a - b) <= 2, f"panels not 50/50: {a} vs {b}"

    def test_split_does_not_overshoot_window(self, qapp, win):
        win._on_outer_tab_clicked(1)
        win._on_outer_tab_clicked(2)
        qapp.processEvents()
        spl = win._hw_view._splitter
        assert sum(spl.sizes()) <= spl.width() + 2, "split overflows the viewport"

    def test_split_stays_even_after_resize(self, qapp, win):
        win._on_outer_tab_clicked(1)
        win._on_outer_tab_clicked(2)
        qapp.processEvents()
        win.resize(1000, 800)
        qapp.processEvents()
        a, b = win._hw_view._splitter.sizes()
        assert abs(a - b) <= 2, f"not 50/50 after resize: {a} vs {b}"

    def test_isolating_gives_full_width_to_one_panel(self, qapp, win):
        win._on_outer_tab_clicked(1)
        win._on_outer_tab_clicked(2)        # split
        win._on_outer_tab_clicked(1)        # isolate motors
        qapp.processEvents()
        spl = win._hw_view._splitter
        # widget 1 hidden -> its size collapses to 0, motors takes the rest
        assert spl.sizes()[1] == 0


# ── MotorTab unit conversions + history ──────────────────────────────────────

class TestMotorTabUnits:
    @pytest.fixture
    def motor(self, qapp):
        from motor_tab import MotorTab
        mt = MotorTab()
        yield mt
        mt.abort_and_close()

    def test_default_speed_is_cps(self, motor):
        import rbl.hardware.slit_config as SC
        panel = motor.axes["A"]
        assert panel._speed_in_cps() == SC.DEFAULT_JOG_SPEED

    def test_speed_cps_to_mms_round_trip(self, motor):
        panel = motor.axes["A"]
        panel.spn_speed.setValue(630.0)        # ~1 mm/s
        panel.cbo_speed_unit.setCurrentText("mm/s")
        # ~1 mm/s back in cps must be ~630
        assert abs(panel._speed_in_cps() - 630) <= 2

    def test_target_counts_to_mm_and_back(self, motor):
        panel = motor.axes["A"]
        panel.cbo_target_unit.setCurrentText("mm")
        panel.spn_target.setValue(10.0)
        import rbl.hardware.slit_config as SC
        assert panel._target_in_counts() == SC.mm_to_counts("A", 10.0)

    def test_target_default_counts(self, motor):
        panel = motor.axes["B"]
        panel.spn_target.setValue(6300)
        assert panel._target_in_counts() == 6300

    def test_all_four_axes_present(self, motor):
        assert set(motor.axes.keys()) == {"A", "B", "C", "D"}

    def test_controls_disabled_until_connected(self, motor):
        panel = motor.axes["A"]
        assert not panel.btn_move.isEnabled()      # disabled when not connected


class TestHistoryLineEdit:
    def test_up_down_history(self, qapp):
        from motor_tab import HistoryLineEdit
        le = HistoryLineEdit()
        le.add_to_history("MO")
        le.add_to_history("SH A")

        _press(le, Qt.Key.Key_Up)
        assert le.text() == "SH A"
        _press(le, Qt.Key.Key_Up)
        assert le.text() == "MO"
        _press(le, Qt.Key.Key_Up)               # already oldest -> stays
        assert le.text() == "MO"
        _press(le, Qt.Key.Key_Down)
        assert le.text() == "SH A"
        _press(le, Qt.Key.Key_Down)             # back to (empty) draft
        assert le.text() == ""

    def test_duplicate_consecutive_not_stored_twice(self, qapp):
        from motor_tab import HistoryLineEdit
        le = HistoryLineEdit()
        le.add_to_history("TH")
        le.add_to_history("TH")
        assert le._history == ["TH"]

    def test_up_on_empty_history_noop(self, qapp):
        from motor_tab import HistoryLineEdit
        le = HistoryLineEdit()
        _press(le, Qt.Key.Key_Up)
        assert le.text() == ""


# ── CurrentTab readout + plot navigation ─────────────────────────────────────

class TestCurrentTab:
    @pytest.fixture
    def current(self, qapp):
        from current_tab import CurrentTab
        ct = CurrentTab()
        yield ct
        ct.shutdown()

    def test_reading_updates_buffers_and_labels(self, current):
        current._on_reading(1.0, {"AIN0": 3.0, "AIN1": 3.0, "AIN2": 3.0, "AIN3": 3.0})
        # 0-6 V model: 3.0 V -> 1 µA
        _, v = current.buffers["AIN0"].latest()
        assert abs(v - 1e-6) < 1e-9
        assert "µA" in current.lbl_i["AIN0"].text()

    def test_balanced_beam_reads_zero_imbalance(self, current):
        current._on_reading(1.0, {"AIN0": 3.0, "AIN1": 3.0, "AIN2": 3.0, "AIN3": 3.0})
        assert "+0.000" in current.lbl_xc.text()

    def test_imbalanced_beam_positive(self, current):
        # X+ (AIN0) larger than X- (AIN1) -> positive imbalance
        current._on_reading(1.0, {"AIN0": 4.0, "AIN1": 3.0, "AIN2": 3.0, "AIN3": 3.0})
        txt = current.lbl_xc.text()
        assert txt.startswith("X imbalance: +") and "+0.000" not in txt

    def test_starts_in_live_mode(self, current):
        assert current._is_live is True
        assert current.slider.value() == 10_000

    def test_drag_slider_left_enters_frozen(self, current):
        for i in range(5):
            current._on_reading(float(i), {"AIN0": 3.0, "AIN1": 3.0,
                                           "AIN2": 3.0, "AIN3": 3.0})
        current._on_slider_changed(4000)
        assert current._is_live is False
        assert current._frozen_right_edge is not None
        # isVisibleTo ignores whether the (un-shown) tab itself is on screen.
        assert current.btn_jump_live.isVisibleTo(current)

    def test_jump_to_live_returns_to_live(self, current):
        for i in range(5):
            current._on_reading(float(i), {"AIN0": 3.0, "AIN1": 3.0,
                                           "AIN2": 3.0, "AIN3": 3.0})
        current._on_slider_changed(4000)
        current._jump_to_live()
        assert current._is_live is True
        assert current.slider.value() == 10_000

    def test_slider_far_right_is_live(self, current):
        current._on_slider_changed(9_900)
        assert current._is_live is True

    def test_redraw_does_not_raise_when_empty(self, current):
        current._redraw_plot()   # no data yet -> must be a safe no-op


# ── Voltage Calculator ───────────────────────────────────────────────────────

class TestVoltageCalcTab:
    @pytest.fixture
    def volt(self, qapp):
        from rbl.gui.app import VoltageCalcTab
        return VoltageCalcTab()

    def test_initial_result_populated(self, volt):
        assert "kV" in volt.out_plate.text()

    def test_small_deflection_within_limits(self, volt):
        volt.energy.setValue(1.0)
        volt.charge.setValue(1)
        volt.deflection.setValue(1.0)
        assert "within" in volt.out_warn.text()

    def test_huge_deflection_exceeds_amplifier(self, volt):
        volt.energy.setValue(10.0)
        volt.deflection.setValue(90.0)
        volt.charge.setValue(1)
        assert "exceeds" in volt.out_warn.text().lower() or "⚠" in volt.out_warn.text()

    def test_species_change_updates_mass(self, volt):
        volt.species.setCurrentText("Gold (Au)")
        # mass spinbox displays 2 decimals -> 196.967 stored as 196.97
        assert abs(volt.mass.value() - 196.967) < 0.01

    def test_vpp_is_twice_peak(self, volt):
        volt.deflection.setValue(20.0)
        peak = float(volt.out_fg_peak.text().split()[1])
        vpp = float(volt.out_fg_vpp.text().split()[0])
        assert abs(vpp - 2.0 * peak) < 1e-2


# ── Both poll workers running concurrently (two-tab scenario) ────────────────

class _Collector(QObject):
    """A real QObject receiver so cross-thread signals use safe *queued*
    connections — exactly as MotorTab/CurrentTab do in the live app."""

    def __init__(self):
        super().__init__()
        self.galil_states = []
        self.lj_reads = []
        self.errors = []

    def on_state(self, s):
        self.galil_states.append(s)

    def on_reading(self, t, v):
        self.lj_reads.append((t, v))

    def on_error(self, m):
        self.errors.append(m)


class TestHomingWorker:
    """The multi-pass auto-homing routine is a core Galil interaction."""

    def _mock_galil(self, home_switch=False):
        from unittest.mock import MagicMock
        from rbl.hardware.galil_driver import GalilController
        g = MagicMock(spec=GalilController)
        g.get_switch_states.return_value = {
            "home_switch": home_switch,
            "forward_switch": False,
            "reverse_switch": False,
        }
        g.is_moving.return_value = False     # _wait_idle returns immediately
        return g

    def test_three_passes_then_define_zero(self, qapp, monkeypatch):
        import motor_tab
        from motor_tab import HomingWorker
        monkeypatch.setattr(motor_tab.time, "sleep", lambda *a, **k: None)
        g = self._mock_galil(home_switch=False)
        hw = HomingWorker(g, "A")
        results = []
        hw.done.connect(lambda ok, msg: results.append((ok, msg)))
        hw.run()                              # synchronous (same-thread)
        assert g.begin_home.call_count == 3   # coarse -> medium -> fine
        g.define_zero.assert_called_once_with("A")
        assert results and results[0][0] is True

    def test_backs_off_when_starting_on_home_switch(self, qapp, monkeypatch):
        import motor_tab
        from motor_tab import HomingWorker
        monkeypatch.setattr(motor_tab.time, "sleep", lambda *a, **k: None)
        g = self._mock_galil(home_switch=True)
        hw = HomingWorker(g, "B")
        hw.done.connect(lambda ok, msg: None)
        hw.run()
        # First action must be a positive (back-off) relative move.
        first_move = g.move_relative.call_args_list[0]
        assert first_move.args[0] == "B"
        assert first_move.args[1] > 0

    def test_cancel_before_pass_aborts(self, qapp, monkeypatch):
        import motor_tab
        from motor_tab import HomingWorker
        monkeypatch.setattr(motor_tab.time, "sleep", lambda *a, **k: None)
        g = self._mock_galil(home_switch=False)
        hw = HomingWorker(g, "A")
        results = []
        hw.done.connect(lambda ok, msg: results.append((ok, msg)))
        hw.cancel()
        hw.run()
        assert g.begin_home.call_count == 0
        g.define_zero.assert_not_called()
        assert results and results[0][0] is False

    def test_restores_default_speed_after_homing(self, qapp, monkeypatch):
        import motor_tab
        from motor_tab import HomingWorker
        import rbl.hardware.slit_config as SC
        monkeypatch.setattr(motor_tab.time, "sleep", lambda *a, **k: None)
        g = self._mock_galil(home_switch=False)
        hw = HomingWorker(g, "A")
        hw.done.connect(lambda ok, msg: None)
        hw.run()
        g.set_speed.assert_called_with("A", SC.DEFAULT_SPEED_COUNTS_PER_SEC)


class TestGalilPollWorkerErrorHandling:
    def test_poll_worker_emits_error_and_stops_on_exception(self, qapp):
        from unittest.mock import MagicMock
        from motor_tab import GalilPollWorker
        from rbl.hardware.galil_driver import GalilController

        g = MagicMock(spec=GalilController)
        g.connected = True
        g.get_position.side_effect = ConnectionError("link dropped")

        col = _Collector()
        gw = GalilPollWorker(g, period_s=0.01)
        gw.error.connect(col.on_error)
        gw.start()
        deadline = time.time() + 0.4
        while time.time() < deadline and gw.isRunning():
            qapp.processEvents()
            time.sleep(0.01)
        gw.stop(); gw.wait(2000)
        qapp.processEvents()
        assert col.errors, "poll worker should have reported the error"
        assert not gw.isRunning()


class TestConcurrentPollWorkers:
    def test_galil_and_labjack_workers_run_simultaneously(self, qapp):
        from unittest.mock import MagicMock
        from motor_tab import GalilPollWorker
        from current_tab import LabJackPollWorker
        from rbl.hardware.galil_driver import GalilController
        from rbl.hardware.labjack_driver import LabJackT7

        galil = MagicMock(spec=GalilController)
        galil.connected = True
        galil.get_position.return_value = 1000
        galil.is_moving.return_value = False
        galil.get_switch_states.return_value = {
            "forward_switch": False, "reverse_switch": False, "home_switch": False}
        galil.is_motor_off.return_value = False

        lj = MagicMock(spec=LabJackT7)
        lj.connected = True
        lj.read_channels.return_value = {"AIN0": 3.0, "AIN1": 3.0,
                                         "AIN2": 3.0, "AIN3": 3.0}

        col = _Collector()
        gw = GalilPollWorker(galil, period_s=0.02)
        lw = LabJackPollWorker(lj, period_s=0.02)
        gw.state.connect(col.on_state)
        gw.error.connect(col.on_error)
        lw.reading.connect(col.on_reading)
        lw.error.connect(col.on_error)

        gw.start()
        lw.start()
        try:
            deadline = time.time() + 0.4
            while time.time() < deadline:
                qapp.processEvents()
                time.sleep(0.01)
        finally:
            gw.stop(); lw.stop()
            gw.wait(2000); lw.wait(2000)
        qapp.processEvents()

        assert not col.errors, f"poll workers errored: {col.errors}"
        assert len(col.galil_states) >= 3, "Galil poll produced too few updates"
        assert len(col.lj_reads) >= 3, "LabJack poll produced too few reads"
        # The two workers use independent hardware objects -> no cross-talk.
        assert galil is not lj
