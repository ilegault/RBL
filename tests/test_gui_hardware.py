"""
Headless GUI tests (offscreen Qt) for the hardware tabs and the outer
tab navigation.

Covers, in particular:
  * tab switching — all four tabs (Motors, Current, Amplifiers, FuncGen)
    navigate correctly and the stacked widget lands on the right page;
  * tab state persistence — widget state (spinbox values, live/frozen mode)
    is preserved after navigating away and returning to a tab;
  * MotorTab unit conversions (cps <-> mm/s, counts <-> mm);
  * the command-history line edit;
  * CurrentTab live/frozen plot navigation and voltage->current readout;
  * both background poll workers (Galil + LabJack) running at the same time.
"""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from tests.payloads import LabJackFeed, window_payload

# 3.0 V on a 0-6 V log amp is the midpoint of its decade range -> 1 µA.
LOG_AMPS_AT_1UA = {"AIN0": 3.0, "AIN1": 3.0, "AIN2": 3.0, "AIN3": 3.0}


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


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


# ── MainWindow tab navigation ─────────────────────────────────────────────────

@pytest.fixture
def win(qapp):
    from rbl.gui.app import MainWindow
    w = MainWindow()
    w.resize(1440, 920)
    w.show()
    qapp.processEvents()
    yield w
    w.close()
    qapp.processEvents()


class TestOneWindowReachesBothTabs:
    """The wiring MainWindow makes, asserted end to end.

    One stream window into the app's own Beamline must land converted on both
    hardware tabs, each taking only its own half. This covers the connections
    themselves — nothing else does, and a tab silently wired to nothing shows
    an empty screen rather than failing.
    """

    def test_one_window_updates_the_current_and_amp_tabs(self, win, qapp):
        win.beamline.ingest_labjack_window(window_payload({
            "AIN0": 3.0, "AIN1": 3.0, "AIN2": 3.0, "AIN3": 3.0,   # 1 µA each
            "AIN12": 1.0, "AIN13": 3.0,                           # X+: 10 mA, 3 kV
        }, t=1.0))

        _, i = win.current_tab.buffers["AIN0"].latest()
        assert abs(i - 1e-6) < 1e-9
        assert "µA" in win.current_tab.lbl_i["AIN0"].text()

        _, kv = win.amp_tab.buffers["AIN13"].latest()
        assert abs(kv - 3.0) < 1e-9
        assert "3.000 kV" in win.amp_tab.lbl_meas["X+"].text()

    def test_neither_tab_sees_the_other_half(self, win, qapp):
        win.beamline.ingest_labjack_window(
            window_payload({"AIN0": 3.0, "AIN13": 3.0}, t=1.0))
        assert set(win.current_tab.buffers) & set(win.amp_tab.buffers) == set()


class TestTabNavigation:
    """Verify the outer tab stack switches correctly."""

    def test_starts_on_the_overview(self, win):
        # The app opens on Overview: it answers "what is the beamline doing"
        # without pressing anything, and it is where Connect All lives.
        idx = win.tab_index("Overview")
        assert win._outer_stack.currentIndex() == idx
        assert win._outer_tabbar.currentIndex() == idx
        assert win._outer_tabbar.tabText(idx) == "Overview"

    def test_tab_order_and_titles(self, win):
        expected_order = [
            "Overview",
            "Vacuum",
            "Stepper Motors",
            "Slit Currents",
            "Beam Profiler",
            "Camera",
            "Raster Planner",
            "Function Generators",
            "HV Amplifiers",
            "Dynamic Adjustment",
            "HV Calibration",
            "Load Characterization",
        ]
        actual_titles = [win._outer_tabbar.tabText(i) for i in range(win._outer_tabbar.count())]
        assert actual_titles == expected_order

    def test_tabs_derive_from_single_declaration(self, win):
        from rbl.gui.app import TAB_DECLARATIONS
        assert win._outer_tabbar.count() == len(TAB_DECLARATIONS)
        assert win._outer_stack.count() == len(TAB_DECLARATIONS)

        for i, decl in enumerate(TAB_DECLARATIONS):
            assert win._outer_tabbar.tabText(i) == decl.title
            scroll_area = win._outer_stack.widget(i)
            assert scroll_area.widget() is getattr(win, decl.attr_name)

    def test_stream_consuming_tabs_derived_from_declaration(self, win):
        from rbl.gui.app import TAB_DECLARATIONS
        expected = tuple(
            getattr(win, decl.attr_name)
            for decl in TAB_DECLARATIONS
            if decl.consumes_stream
        )
        assert win.stream_consuming_tabs == expected
        assert win._lj_tabs == expected

    def test_split_view_reordering_and_restoration(self, win, qapp):
        # Initial: Overview active
        overview_idx = win.tab_index("Overview")
        assert win._outer_stack.currentIndex() == overview_idx

        # Right click Stepper Motors to put into right split pane
        motor_idx = win.tab_index("Stepper Motors")
        win._on_tab_right_clicked(motor_idx)
        qapp.processEvents()
        assert win._split_index == motor_idx
        assert win._split_stack.isVisible()
        assert win._split_stack.currentWidget().widget() is win.motor_tab

        # In split mode, the outer stack has one fewer widget and indices shift
        assert win._outer_stack.count() == len(win.TAB_DECLARATIONS) - 1

        # Left click another tab (e.g. Slit Currents)
        slit_idx = win.tab_index("Slit Currents")
        win._on_outer_tab_clicked(slit_idx)
        qapp.processEvents()
        assert win._outer_stack.currentIndex() == win.stack_index(slit_idx)
        assert win._outer_stack.currentWidget().widget() is win.current_tab

        # Right-click same tab to exit split view
        win._on_tab_right_clicked(motor_idx)
        qapp.processEvents()
        assert win._split_index == -1
        assert not win._split_stack.isVisible()
        assert win._outer_stack.count() == len(win.TAB_DECLARATIONS)
        # Tab at position motor_idx in outer stack is motor_tab again
        assert win._outer_stack.widget(motor_idx).widget() is win.motor_tab


class TestTabStatePersistence:
    """Tab widgets must retain their internal state after navigating away and back."""

    def test_motor_spinbox_preserved_after_leaving(self, win, qapp):
        motor_idx = win.tab_index("Stepper Motors")
        slit_idx = win.tab_index("Slit Currents")
        win._on_outer_tab_clicked(motor_idx)
        panel = win.motor_tab.axes["A"]
        panel.spn_speed.setValue(500.0)
        win._on_outer_tab_clicked(slit_idx)        # leave
        qapp.processEvents()
        win._on_outer_tab_clicked(motor_idx)        # come back
        assert panel.spn_speed.value() == 500.0

    def test_current_tab_live_mode_preserved_after_leaving(self, win, qapp):
        motor_idx = win.tab_index("Stepper Motors")
        slit_idx = win.tab_index("Slit Currents")
        win._on_outer_tab_clicked(slit_idx)
        ct = win.current_tab
        assert ct.plot.is_live is True
        win._on_outer_tab_clicked(motor_idx)
        win._on_outer_tab_clicked(slit_idx)
        assert ct.plot.is_live is True

    def test_frozen_current_tab_stays_frozen_after_navigation(self, win, qapp):
        ct = win.current_tab
        motor_idx = win.tab_index("Stepper Motors")
        slit_idx = win.tab_index("Slit Currents")
        # Through the window's OWN Beamline, so this goes over the same
        # logamps_changed connection MainWindow makes at construction.
        for i in range(5):
            win.beamline.ingest_labjack_window(
                window_payload(LOG_AMPS_AT_1UA, t=float(i)))
        ct.plot._on_slider_changed(4000)    # enter frozen mode
        assert ct.plot.is_live is False
        win._on_outer_tab_clicked(motor_idx)        # leave current tab
        win._on_outer_tab_clicked(slit_idx)        # come back
        assert ct.plot.is_live is False      # still frozen


class TestRedrawGating:
    """The redraw timer is rendering, not data acquisition: it must run only
    while a tab is both connected AND the one on screen (Phase 5). Switching
    away must not stop data collection — only painting.
    """

    def test_current_tab_redraw_only_while_visible(self, win, qapp):
        ct = win.current_tab
        motor_idx = win.tab_index("Stepper Motors")
        slit_idx = win.tab_index("Slit Currents")
        win._on_outer_tab_clicked(motor_idx)         # start on Motors: current_tab hidden
        qapp.processEvents()
        ct.on_labjack_connected("TESTSERIAL")
        assert not ct.plot.redraw_timer.isActive()   # connected but hidden

        win._on_outer_tab_clicked(slit_idx)         # switch to Slit Currents
        qapp.processEvents()
        assert ct.plot.redraw_timer.isActive()       # now connected AND visible

        win._on_outer_tab_clicked(motor_idx)         # leave again
        qapp.processEvents()
        assert not ct.plot.redraw_timer.isActive()

    def test_amp_tab_redraw_only_while_visible(self, win, qapp):
        amp = win.amp_tab
        motor_idx = win.tab_index("Stepper Motors")
        amp_idx = win.tab_index("HV Amplifiers")
        win._on_outer_tab_clicked(motor_idx)
        qapp.processEvents()
        amp.on_labjack_connected("TESTSERIAL")
        assert not amp.plot.redraw_timer.isActive()

        win._on_outer_tab_clicked(amp_idx)         # switch to HV Amplifiers
        qapp.processEvents()
        assert amp.plot.redraw_timer.isActive()

        win._on_outer_tab_clicked(motor_idx)
        qapp.processEvents()
        assert not amp.plot.redraw_timer.isActive()

    def test_disconnect_stops_redraw_even_while_visible(self, win, qapp):
        ct = win.current_tab
        slit_idx = win.tab_index("Slit Currents")
        win._on_outer_tab_clicked(slit_idx)
        qapp.processEvents()
        ct.on_labjack_connected("TESTSERIAL")
        assert ct.plot.redraw_timer.isActive()

        ct.on_labjack_disconnected()
        assert not ct.plot.redraw_timer.isActive()

    def test_switching_tabs_does_not_stop_funcgen_poll_timer(self, win, qapp):
        # Poll timers are data acquisition, not rendering — Phase 5 must not
        # touch them. FuncGenTab's poll timer runs independent of visibility.
        fg = win.funcgen_tab
        fg._poll_timer.start()
        motor_idx = win.tab_index("Stepper Motors")
        funcgen_idx = win.tab_index("Function Generators")
        win._on_outer_tab_clicked(motor_idx)
        qapp.processEvents()
        assert fg._poll_timer.isActive()
        win._on_outer_tab_clicked(funcgen_idx)
        qapp.processEvents()
        assert fg._poll_timer.isActive()


# ── MotorTab unit conversions + history ──────────────────────────────────────

class TestMotorTabUnits:
    @pytest.fixture
    def motor(self, qapp):
        from rbl.gui.motor_tab import MotorTab
        from rbl.state.beamline import Beamline
        mt = MotorTab(Beamline())
        yield mt
        mt.abort_and_close()

    def test_default_speed_is_cps(self, motor):
        import rbl.config.hardware_config as SC
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
        import rbl.config.hardware_config as SC
        assert panel._target_in_counts() == SC.mm_to_counts("A", 10.0)

    def test_target_default_counts(self, motor):
        panel = motor.axes["B"]
        panel.cbo_target_unit.setCurrentText("counts")   # default unit is mm; switch to counts
        panel.spn_target.setValue(6300)
        assert panel._target_in_counts() == 6300

    def test_all_four_axes_present(self, motor):
        assert set(motor.axes.keys()) == {"A", "B", "C", "D"}

    def test_controls_disabled_until_connected(self, motor):
        panel = motor.axes["A"]
        assert not panel.btn_move.isEnabled()      # disabled when not connected


class TestHistoryLineEdit:
    def test_up_down_history(self, qapp):
        from rbl.gui.motor_tab import HistoryLineEdit
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
        from rbl.gui.motor_tab import HistoryLineEdit
        le = HistoryLineEdit()
        le.add_to_history("TH")
        le.add_to_history("TH")
        assert le._history == ["TH"]

    def test_up_on_empty_history_noop(self, qapp):
        from rbl.gui.motor_tab import HistoryLineEdit
        le = HistoryLineEdit()
        _press(le, Qt.Key.Key_Up)
        assert le.text() == ""


# ── CurrentTab readout + plot navigation ─────────────────────────────────────

class TestCurrentTab:
    @pytest.fixture
    def current(self, qapp):
        from rbl.gui.logamp_tab import CurrentTab
        ct = CurrentTab()
        yield ct
        ct.shutdown()

    @pytest.fixture
    def feed(self, current):
        return LabJackFeed(current)

    def test_window_updates_buffers_and_labels(self, current, feed):
        feed.send(LOG_AMPS_AT_1UA)
        # 0-6 V model: 3.0 V -> 1 µA
        _, v = current.buffers["AIN0"].latest()
        assert abs(v - 1e-6) < 1e-9
        assert "µA" in current.lbl_i["AIN0"].text()

    def test_raw_voltage_is_shown_beside_the_current(self, current, feed):
        """The tab shows the volts the current was derived from, and takes
        them from the snapshot rather than re-reading the payload."""
        feed.send(LOG_AMPS_AT_1UA)
        assert "3.000 V" in current.lbl_v["AIN0"].text()

    def test_unsampled_channel_shows_paused_not_stale(self, current, feed):
        """The WAVEFORM profile does not scan the log amps: their readouts
        must say so instead of holding the last good number."""
        feed.send(LOG_AMPS_AT_1UA)
        feed.send({})                      # nothing sampled this window
        assert "Waveform mode" in current.lbl_i["AIN0"].text()

    # With no Galil attached the indicator has no slit positions and falls back
    # to the raw current imbalance, which is what these assert on.
    def test_balanced_beam_reads_zero_imbalance(self, current, feed):
        feed.send(LOG_AMPS_AT_1UA)
        assert abs(current.beam_indicator.view.ratio_x) < 1e-9

    def test_imbalanced_beam_positive(self, current, feed):
        # X+ (AIN0) larger than X- (AIN1) -> positive imbalance
        feed.send({"AIN0": 4.0, "AIN1": 3.0, "AIN2": 3.0, "AIN3": 3.0})
        assert current.beam_indicator.view.ratio_x > 0

    def test_slit_positions_enable_mm_reconstruction(self, current, feed):
        """With slit geometry supplied, the indicator solves a real position."""
        current.set_slit_state({
            "connected": True, "zeroed": True,
            "positions": {"X+": 1.5, "X-": 1.5, "Y+": 5.0, "Y-": 5.0},
        })
        feed.send(LOG_AMPS_AT_1UA)
        est = current.beam_indicator.view.est
        assert est is not None and est.ok
        # Equal currents on symmetric slits -> beam on the axis.
        assert abs(est.x) < 1e-6 and abs(est.y) < 1e-6

    def test_starts_in_live_mode(self, current):
        assert current.plot.is_live is True
        assert current.plot.slider.value() == 10_000

    def test_drag_slider_left_enters_frozen(self, current, feed):
        for i in range(5):
            feed.send(LOG_AMPS_AT_1UA, t=float(i))
        current.plot.slider.setValue(4000)
        assert current.plot.is_live is False
        assert current.plot.frozen_right_edge is not None
        # isVisibleTo ignores whether the (un-shown) tab itself is on screen.
        assert current.btn_jump_live.isVisibleTo(current)

    def test_jump_to_live_returns_to_live(self, current, feed):
        for i in range(5):
            feed.send(LOG_AMPS_AT_1UA, t=float(i))
        current.plot.slider.setValue(4000)
        current.btn_jump_live.click()
        assert current.plot.is_live is True
        assert current.plot.slider.value() == 10_000

    def test_slider_far_right_is_live(self, current):
        current.plot.slider.setValue(9_900)
        assert current.plot.is_live is True

    def test_redraw_does_not_raise_when_empty(self, current):
        current._redraw_plot()   # no data yet -> must be a safe no-op


class _Collector(QObject):
    """A real QObject receiver so cross-thread signals use safe *queued*
    connections — exactly as MotorTab/CurrentTab do in the live app."""

    def __init__(self):
        super().__init__()
        self.galil_states = []
        self.errors = []

    def on_state(self, s):
        self.galil_states.append(s)

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
        from rbl.hardware import galil_workers
        from rbl.hardware.galil_workers import HomingWorker
        monkeypatch.setattr(galil_workers.time, "sleep", lambda *a, **k: None)
        g = self._mock_galil(home_switch=False)
        hw = HomingWorker(g, "A")
        results = []
        hw.done.connect(lambda ok, msg: results.append((ok, msg)))
        hw.run()                              # synchronous (same-thread)
        assert g.begin_home.call_count == 3   # coarse -> medium -> fine
        g.define_zero.assert_called_once_with("A")
        assert results and results[0][0] is True

    def test_backs_off_when_starting_on_home_switch(self, qapp, monkeypatch):
        from rbl.hardware import galil_workers
        from rbl.hardware.galil_workers import HomingWorker
        monkeypatch.setattr(galil_workers.time, "sleep", lambda *a, **k: None)
        g = self._mock_galil(home_switch=True)
        hw = HomingWorker(g, "B")
        hw.done.connect(lambda ok, msg: None)
        hw.run()
        # First action must be a positive (back-off) relative move.
        first_move = g.move_relative.call_args_list[0]
        assert first_move.args[0] == "B"
        assert first_move.args[1] > 0

    def test_cancel_before_pass_aborts(self, qapp, monkeypatch):
        from rbl.hardware import galil_workers
        from rbl.hardware.galil_workers import HomingWorker
        monkeypatch.setattr(galil_workers.time, "sleep", lambda *a, **k: None)
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
        import rbl.config.hardware_config as SC
        from rbl.hardware import galil_workers
        from rbl.hardware.galil_workers import HomingWorker
        monkeypatch.setattr(galil_workers.time, "sleep", lambda *a, **k: None)
        g = self._mock_galil(home_switch=False)
        hw = HomingWorker(g, "A")
        hw.done.connect(lambda ok, msg: None)
        hw.run()
        g.set_speed.assert_called_with("A", SC.DEFAULT_SPEED_COUNTS_PER_SEC)


class TestGalilPollWorkerErrorHandling:
    def test_poll_worker_emits_error_and_stops_on_exception(self, qapp):
        from unittest.mock import MagicMock

        from rbl.hardware.galil_driver import GalilController
        from rbl.hardware.galil_workers import GalilPollWorker

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
        gw.stop()
        gw.wait(2000)
        qapp.processEvents()
        assert col.errors, "poll worker should have reported the error"
        assert not gw.isRunning()


class TestConnectAll:
    """MainWindow presses each tab's own connect, in order, one per turn of
    the event loop.  Nothing here talks to hardware: every step is replaced
    with a recorder, which is the point - what is under test is the
    sequencing and the reporting, not the drivers."""

    def _fake_steps(self, win, results):
        calls = []

        def step(name, outcome):
            def run():
                calls.append(name)
                return outcome
            return run

        win._connect_all_steps = lambda: [
            (name, step(name, outcome)) for name, outcome in results]
        return calls

    def _drain(self, win, qapp):
        # The sequence advances on singleShot(0); pump until it settles.
        for _ in range(50):
            qapp.processEvents()
            if win.overview_tab.btn_connect_all.isEnabled():
                break

    def test_every_step_runs_in_order(self, win, qapp):
        calls = self._fake_steps(win, [
            ("Galil", ("connected", "ok")),
            ("LabJack T7", ("already", "already connected")),
            ("Scope", ("connected", "COM5")),
        ])
        win.overview_tab.btn_connect_all.click()
        self._drain(win, qapp)
        assert calls == ["Galil", "LabJack T7", "Scope"]

    def test_the_button_is_re_armed_when_the_sequence_finishes(self, win, qapp):
        self._fake_steps(win, [("Galil", ("connected", "ok"))])
        win.overview_tab.btn_connect_all.click()
        self._drain(win, qapp)
        assert win.overview_tab.btn_connect_all.isEnabled() is True

    def test_one_failure_does_not_stop_the_rest(self, win, qapp):
        calls = self._fake_steps(win, [
            ("Galil", ("failed", "no route to host")),
            ("Scope", ("connected", "COM5")),
        ])
        win.overview_tab.btn_connect_all.click()
        self._drain(win, qapp)
        assert calls == ["Galil", "Scope"]

    def test_a_failure_is_named_with_its_reason(self, win, qapp):
        self._fake_steps(win, [
            ("Galil", ("failed", "no route to host")),
            ("Scope", ("connected", "COM5")),
        ])
        win.overview_tab.btn_connect_all.click()
        self._drain(win, qapp)
        text = win.overview_tab.lbl_connect_all.text()
        assert "Galil" in text and "no route to host" in text
        assert "Scope" in text          # what DID come up is still reported

    def test_a_raising_step_is_caught_and_reported(self, win, qapp):
        def boom():
            raise RuntimeError("driver exploded")

        win._connect_all_steps = lambda: [("Galil", boom),
                                          ("Scope", lambda: ("connected", "COM5"))]
        win.overview_tab.btn_connect_all.click()
        self._drain(win, qapp)
        assert "driver exploded" in win.overview_tab.lbl_connect_all.text()
        assert win.overview_tab.btn_connect_all.isEnabled() is True

    def test_all_good_reports_everything_connected(self, win, qapp):
        self._fake_steps(win, [
            ("Galil", ("connected", "ok")),
            ("Scope", ("already", "already connected")),
        ])
        win.overview_tab.btn_connect_all.click()
        self._drain(win, qapp)
        text = win.overview_tab.lbl_connect_all.text()
        assert text.startswith("Connected:")
        assert "Galil" in text and "Scope" in text

    def test_the_real_step_list_covers_every_subsystem(self, win):
        names = [name for name, _fn in win._connect_all_steps()]
        assert names == ["Galil", "LabJack T7", "Function generators",
                         "Scope", "Vacuum gauges"]

    def test_connect_all_never_disconnects_anything(self, win):
        # Each step is a tab's connect_if_needed(); none of them is a
        # toggle.  A Connect All that could disconnect a live instrument
        # because it was already up would be a hazard, not a convenience.
        import inspect
        for _name, fn in win._connect_all_steps():
            src = inspect.getsource(fn)
            assert "disconnect" not in src.lower()
