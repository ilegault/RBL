"""
test_cup_panel.py
Headless tests for the Overview cup panel (logging-and-sessions ticket 29).

The panel renders ``CupView`` snapshots emitted by the Faraday Cup tab and lets the
operator open/close a cup test log through the one real ``CupLog``. These tests use
the real ``CupLog`` (rooted in ``tmp_path``), the real ``FaradayCupTab`` and the real
``CupPanel``; only the instrument payloads are faked.
"""
import os
from datetime import datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from rbl.gui import theme
from rbl.gui.faraday_cup_tab import FaradayCupTab
from rbl.gui.widgets.cup_panel import SESSION_OWNS_LOG_TOOLTIP, CupPanel
from rbl.hardware.cup_status import CupPosition
from rbl.services.cup_log import CupLog, CupView, RestartChoice
from rbl.snapshots import CupActuationState
from rbl.state.beamline import Beamline
from tests.payloads import CupFeed


def _actuation(t, position):
    """The controller's contacts as the stream publishes them, commanded == confirmed."""
    return CupActuationState(
        connected=True, commanded=position, confirmed=position, auto_mode=True,
        stale=False, last_transition_t=t, t=t,
    )


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def rig(qapp, tmp_path):
    """A real tab and panel sharing one real CupLog, wired the way MainWindow wires them."""
    beamline = Beamline()
    cup_log = CupLog(test_root=tmp_path / "cup")
    tab = FaradayCupTab(beamline=beamline, cup_log=cup_log)
    panel = CupPanel(cup_log)
    tab.cup_view_changed.connect(panel.on_cup_view)
    tab.show()
    panel.show()
    qapp.processEvents()
    yield tab, panel, cup_log, beamline, tmp_path
    cup_log.close()
    panel.close()
    tab.close()


def test_a_fresh_panel_says_not_logging_in_the_warn_colour(rig):
    _, panel, _, _, _ = rig
    assert panel.lbl_path.text() == "Not logging"
    assert theme.WARN in panel.lbl_path.styleSheet()
    assert panel.btn_logging.text() == "Start Logging"


def test_start_logging_creates_a_dated_file_and_hides_the_tabs_warning(rig, qapp):
    tab, panel, cup_log, _, tmp_path = rig
    assert not tab.lbl_not_logging.isHidden()

    panel.btn_logging.click()
    qapp.processEvents()

    assert cup_log.path is not None
    files = list((tmp_path / "cup").glob("*-*/*.csv"))
    assert len(files) == 1
    assert files[0].parent.name == datetime.now().astimezone().strftime("%Y-%m")
    assert panel.lbl_path.text() == str(files[0])
    assert theme.OK in panel.lbl_path.styleSheet()
    assert panel.btn_logging.text() == "Stop Logging"
    assert tab.lbl_not_logging.isHidden()


def test_stop_logging_closes_the_file_and_returns_to_not_logging(rig, qapp):
    tab, panel, cup_log, _, _ = rig
    panel.btn_logging.click()
    qapp.processEvents()
    assert cup_log.writer is not None

    panel.btn_logging.click()
    qapp.processEvents()

    assert cup_log.writer is None
    assert panel.lbl_path.text() == "Not logging"
    assert theme.WARN in panel.lbl_path.styleSheet()
    assert panel.btn_logging.text() == "Start Logging"
    assert not tab.lbl_not_logging.isHidden()


def test_a_session_owned_log_disables_the_button_with_a_reason(rig, qapp, tmp_path):
    _, panel, cup_log, _, _ = rig
    folder = tmp_path / "session"
    folder.mkdir()
    cup_log.open_for_session(folder)
    qapp.processEvents()

    assert not panel.btn_logging.isEnabled()
    assert panel.btn_logging.toolTip() == (
        "The session owns the cup log. Stop the session to end it."
    )
    assert panel.btn_logging.toolTip() == SESSION_OWNS_LOG_TOOLTIP
    assert panel.lbl_path.text() == str(folder / "cup.csv")

    cup_log.close()
    qapp.processEvents()
    assert panel.btn_logging.isEnabled()
    assert panel.btn_logging.toolTip() == ""


def test_charge_text_matches_the_faraday_tab_after_an_insertion_closes(rig, qapp):
    tab, panel, _, beamline, _ = rig
    feed = CupFeed(tab, beamline=beamline)
    panel.btn_logging.click()
    feed.send_reading(0.0, timestamp=0.0, t_host=0.0)
    qapp.processEvents()

    tab.btn_force_start.click()
    qapp.processEvents()
    assert panel.lbl_run.text() != "No run"
    tab.btn_force_stop.click()
    qapp.processEvents()

    assert panel.lbl_run.text() == "No run"
    assert panel.lbl_charge.text() == tab.lbl_running_q.text()


def test_charge_text_matches_the_tab_when_charge_is_nonzero(rig, qapp, monkeypatch):
    """Real charge reaches the panel through the same view the tab renders from."""
    tab, panel, _, beamline, _ = rig
    # The test stops and starts between insertions; beam held through each pause and a
    # fresh schedule is the accounting it was written against.
    monkeypatch.setattr(
        tab, "_ask_restart",
        lambda *a: RestartChoice(resume_schedule=False, beam_on_during_gap=True),
    )
    feed = CupFeed(tab, beamline=beamline)
    tab.spn_k.setValue(1e-15)
    tab.spn_charge_state.setValue(1)
    tab.on_patch_dimensions_changed(10.0, 10.0)
    panel.btn_logging.click()
    feed.send_reading(0.0, timestamp=0.0, t_host=0.0)
    qapp.processEvents()

    # Only automatic insertions are credited (ticket 30), and charge accrues over the
    # beam-on gap BEFORE an insertion, so it takes two scheduled insertions.
    tab.on_cup_actuation_state(_actuation(0.0, CupPosition.OUT))
    for t_in in (1000.0, 1500.0):
        tab.on_cup_actuation_state(_actuation(t_in - tab.spn_cycle_period.value(), CupPosition.OUT))
        tab.btn_cycle_arm.click()
        tab.on_cup_actuation_state(_actuation(t_in, CupPosition.OUT))  # CycleInsert
        tab.on_cup_actuation_state(_actuation(t_in + 0.2, CupPosition.IN))
        feed.send_reading(2e-6, timestamp=t_in + 0.3, t_host=t_in + 0.3)
        feed.send_reading(2e-6, timestamp=t_in + 1.5, t_host=t_in + 1.5)
        tab.on_cup_actuation_state(_actuation(t_in + 3.0, CupPosition.OUT))
        feed.send_reading(2e-6, timestamp=t_in + 3.1, t_host=t_in + 3.1)
        tab.btn_cycle_stop.click()
        qapp.processEvents()

    assert tab.lbl_running_q.text() != "0.000000e+00 C"
    assert panel.lbl_charge.text() == tab.lbl_running_q.text()
    assert panel.lbl_fluence.text() == tab.lbl_running_fluence.text()
    assert panel.lbl_dpa.text() == tab.lbl_running_dpa.text()


def test_fluence_and_dpa_show_a_dash_until_the_dose_inputs_exist(qapp):
    cup_log = CupLog()
    panel = CupPanel(cup_log)
    panel.on_cup_view(CupView(
        logging=True, log_path="x.csv", run_open=False, automatic_running=False,
        charge_c=1e-6, fluence=None, dpa=None,
    ))
    assert "—" in panel.lbl_fluence.text()
    assert "—" in panel.lbl_dpa.text()
    panel.on_cup_view(CupView(
        logging=True, log_path="x.csv", run_open=True, automatic_running=True,
        charge_c=1e-6, fluence=2.5e15, dpa=0.01,
    ))
    assert "2.5000e+15" in panel.lbl_fluence.text()
    assert "1.0000e-02" in panel.lbl_dpa.text()
    assert "Automatic" in panel.lbl_auto.text()
    panel.close()


def test_the_tab_emits_a_view_on_log_open_close_and_automatic_state(rig, qapp):
    tab, _, cup_log, _, _ = rig
    seen: list[CupView] = []
    tab.cup_view_changed.connect(seen.append)

    cup_log.open_test(datetime.now().astimezone())
    qapp.processEvents()
    assert seen[-1].logging is True
    assert seen[-1].log_path == cup_log.path

    cup_log.close()
    qapp.processEvents()
    assert seen[-1].logging is False
    assert seen[-1].log_path is None


def test_the_panel_shows_cup_current_and_confirmed_position(rig, qapp):
    from rbl.hardware.cup_status import CupPosition
    from rbl.snapshots import CupActuationState, CupState

    _, panel, _, _, _ = rig
    panel.on_cup_state(CupState(connected=True, current=2.5e-6, valid=True))
    panel.on_cup_actuation_state(
        CupActuationState(connected=True, commanded=CupPosition.OUT, confirmed=CupPosition.IN))
    assert "2.50" in panel.lbl_current.text() and "µA" in panel.lbl_current.text()
    assert panel.lbl_position.text() == "In"

    panel.on_cup_state(CupState(connected=True, over_range=True))
    assert panel.lbl_current.text() == "OVER-RANGE"
    panel.on_cup_state(CupState(connected=False))
    panel.on_cup_actuation_state(CupActuationState(connected=False))
    assert "—" in panel.lbl_current.text()
    assert "—" in panel.lbl_position.text()


def test_main_window_wires_the_overview_panel_to_the_faraday_tab(qapp):
    """Start Logging on the Overview opens the one CupLog the Faraday tab also watches."""
    from rbl.gui.app import MainWindow

    win = MainWindow()
    try:
        win.show()
        qapp.processEvents()
        panel = win.overview_tab.cup_panel
        assert panel is not None
        assert win.cup_log.writer is None or win.faraday_cup_tab.lbl_not_logging.isHidden()
        was_open = win.cup_log.writer is not None
        if was_open:
            win.cup_log.close()
            qapp.processEvents()
        assert not win.faraday_cup_tab.lbl_not_logging.isHidden()
        panel.btn_logging.click()
        qapp.processEvents()
        assert win.cup_log.writer is not None
        assert win.faraday_cup_tab.lbl_not_logging.isHidden()
        assert panel.lbl_path.text() == win.cup_log.path
        win.cup_log.close()
    finally:
        win.close()
        qapp.processEvents()


def test_uncertainty_bar_fills_to_drain_fraction_and_shows_target_mark_in_target_mode(qapp):
    panel = CupPanel(CupLog())
    panel.show()
    panel.on_cup_view(CupView(
        logging=False,
        log_path=None,
        run_open=True,
        automatic_running=True,
        charge_c=0.0,
        fluence=None,
        dpa=None,
        insertion_open=True,
        dwell_mode="target_uncertainty",
        drain_fraction=0.5,
        uncertainty=0.10,
        target=0.05,
        n=8,
        n_needed=16,
        time_left_s=1.2,
        converged=False,
        elapsed_s=2.0,
        dwell_limit_s=5.0,
    ))
    qapp.processEvents()
    try:
        bar = panel.uncertainty_bar
        assert bar.value() / bar.maximum() == pytest.approx(0.5)
        assert panel.uncertainty_target_mark.isVisible()
        assert "10.0 %" in panel.lbl_uncertainty_bar.text()
        assert "5 %" in panel.lbl_uncertainty_bar.text()
        assert theme.WARN in bar.styleSheet()
    finally:
        panel.close()


def test_uncertainty_bar_colours_reason_and_fixed_mode_target_mark(qapp):
    panel = CupPanel(CupLog())
    panel.show()
    try:
        panel.on_cup_view(CupView(
            logging=False, log_path=None, run_open=True, automatic_running=True,
            charge_c=0.0, fluence=None, dpa=None,
            insertion_open=True, dwell_mode="target_uncertainty",
            drain_fraction=0.2, uncertainty=0.04, target=0.05,
            n=12, n_needed=12, time_left_s=0.0, converged=True,
            elapsed_s=2.5, dwell_limit_s=5.0,
        ))
        qapp.processEvents()
        assert theme.OK in panel.uncertainty_bar.styleSheet()

        panel.on_cup_view(CupView(
            logging=False, log_path=None, run_open=True, automatic_running=True,
            charge_c=0.0, fluence=None, dpa=None,
            insertion_open=True, dwell_mode="target_uncertainty",
            drain_fraction=None, uncertainty=None, target=0.05,
            reason_text="no valid samples", n=0, converged=False,
            elapsed_s=1.5, dwell_limit_s=5.0,
        ))
        qapp.processEvents()
        assert panel.lbl_uncertainty_bar.text() == "no valid samples"
        assert theme.MUTED in panel.uncertainty_bar.styleSheet()

        panel.on_cup_view(CupView(
            logging=False, log_path=None, run_open=True, automatic_running=True,
            charge_c=0.0, fluence=None, dpa=None,
            insertion_open=True, dwell_mode="fixed",
            drain_fraction=0.2, uncertainty=0.04, target=0.05,
            n=12, converged=True,
            elapsed_s=1.5, dwell_limit_s=3.0,
        ))
        qapp.processEvents()
        assert not panel.uncertainty_target_mark.isVisible()
        assert theme.OK not in panel.uncertainty_bar.styleSheet()
        assert "→" not in panel.lbl_uncertainty_bar.text()
    finally:
        panel.close()


def test_insertion_bar_with_no_dwell_limit_shows_elapsed_only(qapp):
    panel = CupPanel(CupLog())
    panel.show()
    try:
        panel.on_cup_view(CupView(
            logging=False, log_path=None, run_open=True, automatic_running=False,
            charge_c=0.0, fluence=None, dpa=None,
            insertion_open=True, dwell_mode="target_uncertainty",
            elapsed_s=12.3, dwell_limit_s=None,
        ))
        qapp.processEvents()
        assert panel.lbl_insertion_bar.text() == "12.3 s"
        assert panel.insertion_bar.value() == 0
    finally:
        panel.close()


def test_closed_insertion_hides_bars_and_shows_last_insertion_text(qapp):
    panel = CupPanel(CupLog())
    panel.show()
    try:
        summary = "last: 4.2 % · converged · n = 23 · 1.8 s"
        panel.on_cup_view(CupView(
            logging=False, log_path=None, run_open=False, automatic_running=False,
            charge_c=0.0, fluence=None, dpa=None,
            insertion_open=False, last_insertion_text=summary,
        ))
        qapp.processEvents()
        assert panel.uncertainty_bar.isHidden()
        assert panel.insertion_bar.isHidden()
        assert not panel.lbl_last_insertion.isHidden()
        assert panel.lbl_last_insertion.text() == summary
    finally:
        panel.close()


def test_rig_converging_insertion_matches_tabs_uncertainty_percentage(rig, qapp):
    tab, panel, _, beamline, _ = rig
    feed = CupFeed(tab, beamline=beamline)
    tab.on_cup_actuation_state(_actuation(0.0, CupPosition.IN))
    feed.send_reading(0.0, timestamp=0.0, t_host=0.0)
    tab.btn_force_start.click()
    qapp.processEvents()

    # Feed >10 post-settle samples with small variation so uncertainty converges cleanly.
    for i in range(15):
        t = 1.5 + i * 0.1
        current = 2.0e-6 + (0.05e-6 if i % 2 == 0 else -0.05e-6)
        feed.send_reading(current, timestamp=t, t_host=t)
    qapp.processEvents()

    assert not panel.uncertainty_bar.isHidden()
    tab_pct = tab.lbl_uncertainty.text().split(" (")[0]
    assert tab_pct.endswith("%")
    assert tab_pct in panel.lbl_uncertainty_bar.text()


def test_panel_move_buttons_follow_the_t7_connection(qapp):
    panel = CupPanel(CupLog())
    try:
        assert not panel.btn_insert.isEnabled()
        assert not panel.btn_retract.isEnabled()

        panel.on_cup_actuation_state(_actuation(1.0, CupPosition.OUT))
        assert panel.btn_insert.isEnabled()
        assert panel.btn_retract.isEnabled()

        panel.on_cup_actuation_state(CupActuationState(connected=False))
        assert not panel.btn_insert.isEnabled()
        assert not panel.btn_retract.isEnabled()
    finally:
        panel.close()


def test_panel_move_buttons_emit_intent_only(qapp):
    panel = CupPanel(CupLog())
    inserts: list[None] = []
    retracts: list[None] = []
    panel.insert_requested.connect(lambda: inserts.append(None))
    panel.retract_requested.connect(lambda: retracts.append(None))
    try:
        panel.on_cup_actuation_state(_actuation(1.0, CupPosition.OUT))
        panel.btn_insert.click()
        qapp.processEvents()
        assert len(inserts) == 1
        assert len(retracts) == 0

        panel.btn_retract.click()
        qapp.processEvents()
        assert len(inserts) == 1
        assert len(retracts) == 1
    finally:
        panel.close()


def test_overview_insert_moves_the_cup_through_the_faraday_tab(qapp, monkeypatch):
    from rbl.gui.app import MainWindow

    win = MainWindow()
    try:
        win.show()
        qapp.processEvents()
        calls: list[None] = []
        monkeypatch.setattr(win.beamline, "command_cup_in", lambda: calls.append(None))
        win.beamline.cup_actuation_changed.emit(_actuation(1.0, CupPosition.OUT))
        qapp.processEvents()

        panel = win.overview_tab.cup_panel
        assert panel is not None
        panel.btn_insert.click()
        panel.btn_insert.click()
        qapp.processEvents()
        assert len(calls) == 1
    finally:
        win.close()
        qapp.processEvents()


def test_overview_retract_moves_the_cup_through_the_faraday_tab(qapp, monkeypatch):
    from rbl.gui.app import MainWindow

    win = MainWindow()
    try:
        win.show()
        qapp.processEvents()
        calls: list[None] = []
        monkeypatch.setattr(win.beamline, "command_cup_out", lambda: calls.append(None))
        win.beamline.cup_actuation_changed.emit(_actuation(1.0, CupPosition.IN))
        qapp.processEvents()

        panel = win.overview_tab.cup_panel
        assert panel is not None
        panel.btn_retract.click()
        panel.btn_retract.click()
        qapp.processEvents()
        assert len(calls) == 1
    finally:
        win.close()
        qapp.processEvents()
