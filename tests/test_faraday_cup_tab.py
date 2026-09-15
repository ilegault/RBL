"""
test_faraday_cup_tab.py
Headless GUI and cross-tab agreement tests for the Faraday Cup tab.

Covers:
  * Initial disconnected state: honest "—", "● Disconnected", no stale or zeroed values.
  * Connect / disconnect UI lifecycle through Beamline.
  * Live reading rendering with auto-scaling across nA, µA, mA via CupFeed.
  * Over-range indication: rendered explicitly as "OVER-RANGE", never as a number.
  * Unavailable / zero-check sentinel handling: rendered as "—".
  * Disconnection cleanup: resets display to "—", never leaving stale values.
  * Cross-tab agreement: slit currents and cup current are rendered as distinct,
    differently-labelled quantities; cup reading is not derived from or conflated
    with the log-amp path.
  * Assertions check operator-visible text, not private widget attributes.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from rbl.gui.app import MainWindow
from rbl.gui.faraday_cup_tab import FaradayCupTab
from rbl.state.beamline import Beamline
from tests.payloads import CupFeed, window_payload


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """Stop QMessageBox.* from blocking the test on modal dialogs."""
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(
            QMessageBox,
            name,
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok),
        )


@pytest.fixture
def win(qapp):
    w = MainWindow()
    w.resize(1440, 920)
    w.show()
    qapp.processEvents()
    yield w
    w.close()
    qapp.processEvents()


class TestFaradayCupTabRendersCurrent:
    """Unit tests for FaradayCupTab rendering and lifecycle."""

    def test_initial_disconnected_state(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        tab.show()
        qapp.processEvents()

        assert "Disconnected" in tab.status_pill.text()
        assert "—" in tab.lbl_current.text()
        assert "Not connected" in tab.lbl_detail.text()
        assert tab.btn_connect.text() == "Connect"

    def test_connect_if_needed(self, qapp, monkeypatch):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        tab.show()
        qapp.processEvents()

        # Mock connect_picoammeter to avoid real VISA I/O in unit test
        called_resource = []
        monkeypatch.setattr(
            beamline,
            "connect_picoammeter",
            lambda res=None: called_resource.append(res),
        )

        status, detail = tab.connect_if_needed()
        assert status == "connected"
        assert len(called_resource) == 1

        # Simulate connected state
        monkeypatch.setattr(Beamline, "picoammeter_connected", property(lambda self: True))
        status2, detail2 = tab.connect_if_needed()
        assert status2 == "already"

    def test_renders_cup_current_autoscaling(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        # 1. Nanoamps: 12.34 nA
        feed.send_reading(12.34e-9, timestamp=1.0)
        qapp.processEvents()
        assert "12.34 nA" in tab.lbl_current.text()
        assert "Connected" in tab.status_pill.text()

        # 2. Microamps: 5.67 µA
        feed.send_reading(5.67e-6, timestamp=2.0)
        qapp.processEvents()
        assert "5.67 µA" in tab.lbl_current.text()

        # 3. Milliamps: 1.25 mA
        feed.send_reading(1.25e-3, timestamp=3.0)
        qapp.processEvents()
        assert "1.25 mA" in tab.lbl_current.text()

    def test_over_range_rendered_as_text_not_number(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        # Status word with over-range bit (bit 6 = 64) set
        feed.send_raw("+9.910000E+37,+1.000000,+00000064")
        qapp.processEvents()

        assert "OVER-RANGE" in tab.lbl_current.text()
        assert "9.9" not in tab.lbl_current.text()
        assert "Over-range" in tab.lbl_detail.text()

    def test_unavailable_reading_rendered_as_placeholder(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        # Unavailable sentinel (+9.900000E+37)
        feed.send_raw("+9.900000E+37,+1.000000,+00000000")
        qapp.processEvents()

        assert "—" in tab.lbl_current.text()
        assert "unavailable" in tab.lbl_detail.text().lower()

    def test_disconnection_resets_view_without_stale_numbers(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_reading(2.5e-6, timestamp=1.0)
        qapp.processEvents()
        assert "2.50 µA" in tab.lbl_current.text()

        # Disconnect picoammeter
        beamline.disconnect_picoammeter()
        qapp.processEvents()

        assert "—" in tab.lbl_current.text()
        assert "2.50" not in tab.lbl_current.text()
        assert "Disconnected" in tab.status_pill.text()
        assert "Not connected" in tab.lbl_detail.text()


class TestCrossTabAgreementSlitVsCupCurrent:
    """Cross-tab agreement invariant tests (CONTEXT.md / ADR 0001).

    Asserts that slit current (intercepted by slit jaws) and cup current
    (transmitted to Faraday cup) are rendered as distinct, differently-labelled
    quantities, and that neither path conflates with or derives from the other.
    """

    def test_distinct_labels_and_screens(self, win):
        # Slit currents tab exists and is titled "Slit Currents"
        slit_idx = win.tab_index("Slit Currents")
        cup_idx = win.tab_index("Faraday Cup")
        assert cup_idx == slit_idx + 1

        slit_tab = win.current_tab
        cup_tab = win.faraday_cup_tab

        # Slit tab and cup tab are distinct widgets on distinct screens
        assert slit_tab is not cup_tab

    def test_logamp_stream_does_not_update_cup_tab(self, win, qapp):
        # Push 1 µA to all log-amp channels via LabJack window
        win.beamline.ingest_labjack_window(
            window_payload({"AIN0": 3.0, "AIN1": 3.0, "AIN2": 3.0, "AIN3": 3.0}, t=1.0)
        )
        qapp.processEvents()

        # Slit currents tab received and converted the log-amp window
        assert "1.00 µA" in win.current_tab.lbl_i["AIN0"].text()

        # Faraday Cup tab was NOT touched and remains disconnected / placeholder
        assert "—" in win.faraday_cup_tab.lbl_current.text()
        assert "1.00" not in win.faraday_cup_tab.lbl_current.text()
        assert "Disconnected" in win.faraday_cup_tab.status_pill.text()

    def test_cup_reading_does_not_update_slit_currents_tab(self, win, qapp):
        # Push 4.5 µA to Faraday cup
        feed = CupFeed(win.faraday_cup_tab, beamline=win.beamline)
        feed.send_reading(4.5e-6, timestamp=1.0)
        qapp.processEvents()

        # Faraday Cup tab renders the cup reading
        assert "4.50 µA" in win.faraday_cup_tab.lbl_current.text()

        # Slit currents tab remains unaffected
        for ain in ("AIN0", "AIN1", "AIN2", "AIN3"):
            assert "4.50" not in win.current_tab.lbl_i[ain].text()
