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
from rbl.services.cup_log import CupLog, CupView
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


def test_charge_text_matches_the_tab_when_charge_is_nonzero(rig, qapp):
    """Real charge reaches the panel through the same view the tab renders from."""
    tab, panel, _, beamline, _ = rig
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
