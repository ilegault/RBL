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
import csv
import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from rbl.gui.app import MainWindow
from rbl.gui.faraday_cup_tab import FaradayCupTab
from rbl.hardware.current_monitor import format_current
from rbl.services.cup_session_writer import CupSessionWriter
from rbl.state.beamline import Beamline
from tests.payloads import CupActuationFeed, CupFeed, window_payload


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


class TestFaradayCupTabAcquisitionRuns:
    """Tests for threshold-triggered runs and force start/stop controls on FaradayCupTab."""

    def test_run_status_initial_disconnected(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        tab.show()
        qapp.processEvents()

        assert "Disconnected" in tab.lbl_run_status.text()
        assert not tab.btn_force_start.isEnabled()
        assert not tab.btn_force_stop.isEnabled()

    def test_run_status_connected_idle(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        # Send reading below arm threshold (0.1 µA < 0.5 µA)
        feed.send_reading(0.1e-6, timestamp=0.0, t_host=0.0)
        qapp.processEvents()

        assert "Idle" in tab.lbl_run_status.text()
        assert tab.btn_force_start.isEnabled()
        assert not tab.btn_force_stop.isEnabled()

    def test_threshold_triggered_acquisition_and_release(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        # Insertion begins at t=1.0 with 1.0 µA
        feed.send_reading(1.0e-6, timestamp=1.0, t_host=1.0)
        qapp.processEvents()
        # Debounce pending
        assert "In Beam" in tab.lbl_run_status.text() or "Idle" in tab.lbl_run_status.text()

        # At t=2.0 (>= 1.0s debounce elapsed), run 1 opens
        feed.send_reading(1.0e-6, timestamp=2.0, t_host=2.0)
        qapp.processEvents()

        assert "ACQUIRING (Run #1)" in tab.lbl_run_status.text()
        assert not tab.btn_force_start.isEnabled()
        assert tab.btn_force_stop.isEnabled()
        assert beamline.cup_acquiring

        # Cup withdrawn at t=10.0 (0.0 A)
        feed.send_reading(0.0, timestamp=10.0, t_host=10.0)
        qapp.processEvents()
        # Still acquiring during release interval
        assert "ACQUIRING" in tab.lbl_run_status.text()

        # At t=13.0 (>= 3.0s release elapsed), run 1 closes
        feed.send_reading(0.0, timestamp=13.0, t_host=13.0)
        qapp.processEvents()

        assert "Idle" in tab.lbl_run_status.text()
        assert tab.btn_force_start.isEnabled()
        assert not tab.btn_force_stop.isEnabled()
        assert not beamline.cup_acquiring

    def test_force_start_and_force_stop_buttons(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_reading(0.0, timestamp=0.0, t_host=0.0)
        qapp.processEvents()
        assert tab.btn_force_start.isEnabled()

        # Click Force Start
        tab.btn_force_start.click()
        qapp.processEvents()

        assert "ACQUIRING (Run #1)" in tab.lbl_run_status.text()
        assert not tab.btn_force_start.isEnabled()
        assert tab.btn_force_stop.isEnabled()
        assert beamline.cup_acquiring

        # Click Force Stop
        tab.btn_force_stop.click()
        qapp.processEvents()

        assert "Idle" in tab.lbl_run_status.text()
        assert tab.btn_force_start.isEnabled()
        assert not tab.btn_force_stop.isEnabled()
        assert not beamline.cup_acquiring

    def test_disconnection_mid_run_cleans_up_tab(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_reading(1.0e-6, timestamp=0.0, t_host=0.0)
        feed.send_reading(1.0e-6, timestamp=1.0, t_host=1.0)
        qapp.processEvents()
        assert "ACQUIRING" in tab.lbl_run_status.text()

        # Disconnect mid-run
        beamline.disconnect_picoammeter()
        qapp.processEvents()

        assert "Disconnected" in tab.lbl_run_status.text()
        assert not tab.btn_force_start.isEnabled()
        assert not tab.btn_force_stop.isEnabled()
        assert not beamline.cup_acquiring


class TestFaradayCupTabRunMetricsAndAverage:
    """Tests for active run duration, sample counts, running average, and over-range exclusion."""

    def test_run_duration_and_samples_updated_during_run(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        # Reading at t_host=10.0 (debouncing starts)
        feed.send_reading(1.0e-6, timestamp=1.0, t_host=10.0)
        qapp.processEvents()

        # Reading at t_host=11.0 (>= 1.0 s debounce -> Run 1 opens at t_host=11.0)
        feed.send_reading(1.0e-6, timestamp=2.0, t_host=11.0)
        qapp.processEvents()
        assert "ACQUIRING" in tab.lbl_run_status.text()

        # Send subsequent samples at t_host=12.0, 13.0, 14.0
        feed.send_reading(1.0e-6, timestamp=3.0, t_host=12.0)
        feed.send_reading(1.0e-6, timestamp=4.0, t_host=13.0)
        feed.send_reading(1.0e-6, timestamp=5.0, t_host=14.0)
        qapp.processEvents()

        # Duration is 14.0 - 11.0 = 3.0 s, 4 samples total in run
        assert "3.0 s" in tab.lbl_duration.text()
        assert tab.lbl_samples.text() == "4"

    def test_running_average_agrees_with_session_file(self, qapp, tmp_path):
        """Running average matches the average computed from logged session file."""
        beamline = Beamline()
        session_writer = CupSessionWriter(output_dir=tmp_path)
        tab = FaradayCupTab(beamline=beamline, session_writer=session_writer)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_reading(0.0, timestamp=0.0, t_host=0.0)
        qapp.processEvents()

        # Force start run
        tab.btn_force_start.click()
        qapp.processEvents()

        # Send series: 1.0 µA, 2.0 µA, 3.0 µA
        feed.send_reading(1.0e-6, timestamp=1.0, t_host=1.0)
        feed.send_reading(2.0e-6, timestamp=2.0, t_host=2.0)
        feed.send_reading(3.0e-6, timestamp=3.0, t_host=3.0)
        qapp.processEvents()

        # Capture displayed running average while run is open
        displayed_average = tab.lbl_average.text()
        assert "2.00 µA" in displayed_average

        # Close run and file
        tab.btn_force_stop.click()
        tab.close()
        qapp.processEvents()

        # Read the logged session CSV file and compute average independently
        with open(session_writer.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader([line for line in f if not line.startswith("#")])
            rows = list(reader)

        sample_rows = [r for r in rows if r["record_type"] == "sample" and r["run_id"] == "1"]
        assert len(sample_rows) == 3
        valid_currents = [
            float(r["current_a"]) for r in sample_rows if r["over_range"] == "False"
        ]
        file_average = sum(valid_currents) / len(valid_currents)

        assert file_average == pytest.approx(2.0e-6)
        # Displayed text and computed file average must agree
        assert displayed_average == format_current(file_average)

    def test_over_range_samples_excluded_from_average_and_visibly_flagged(self, qapp):
        """Over-range samples are excluded from average calculation and flagged on UI."""
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_reading(0.0, timestamp=0.0, t_host=0.0)
        qapp.processEvents()

        tab.btn_force_start.click()
        qapp.processEvents()

        # 2 normal samples (4.0 µA, 6.0 µA) -> average = 5.0 µA
        feed.send_reading(4.0e-6, timestamp=1.0, t_host=1.0)
        feed.send_reading(6.0e-6, timestamp=2.0, t_host=2.0)
        qapp.processEvents()
        assert "5.00 µA" in tab.lbl_average.text()

        # 1 over-range sample (+9.91e37 with status word 0x40)
        feed.send_raw("+9.910000E+37,+1.000000,+00000064", t_host=3.0)
        qapp.processEvents()

        # Running average remains 5.00 µA (over-range excluded)
        assert "5.00 µA" in tab.lbl_average.text()

        # Total samples and over-range exclusion are explicitly visible
        assert "1 over-range excluded" in tab.lbl_samples.text()
        assert "1 over-range excluded" in tab.lbl_avg_detail.text()
        assert "2 valid samples" in tab.lbl_avg_detail.text()

    def test_disconnected_tab_metrics_are_clean_and_not_zeroed(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        tab.show()
        qapp.processEvents()

        assert "—" in tab.lbl_average.text()
        assert "—" in tab.lbl_duration.text()
        assert "—" in tab.lbl_samples.text()
        assert "Not connected" in tab.lbl_avg_detail.text()
        assert "0" not in tab.lbl_average.text()

    def test_mid_run_disconnection_resets_metrics_honestly(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_reading(1.0e-6, timestamp=0.0, t_host=0.0)
        feed.send_reading(1.0e-6, timestamp=1.0, t_host=1.0)
        qapp.processEvents()
        assert "ACQUIRING" in tab.lbl_run_status.text()
        assert "1.00 µA" in tab.lbl_average.text()

        # Disconnect mid-run
        beamline.disconnect_picoammeter()
        qapp.processEvents()

        assert "Disconnected" in tab.lbl_run_status.text()
        assert "—" in tab.lbl_average.text()
        assert "—" in tab.lbl_duration.text()
        assert "—" in tab.lbl_samples.text()
        assert "Not connected" in tab.lbl_avg_detail.text()


class TestFaradayCupTabLivePlot:
    """Tests for FaradayCupTab live scrolling plot, navigation, and zoom controls."""

    def test_live_plot_receives_data_and_navigation_controls(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_reading(1.5e-6, timestamp=1.0, t_host=1.0)
        feed.send_reading(2.5e-6, timestamp=2.0, t_host=2.0)
        qapp.processEvents()

        latest_t, latest_v = tab.buffer.latest()
        assert latest_t == 2.0
        assert latest_v == pytest.approx(2.5e-6)

        assert tab.plot.is_live
        assert "● LIVE" in tab.lbl_mode.text()
        assert not tab.btn_jump_live.isVisible()

        # Zoom out
        initial_window = tab.plot.window_seconds
        tab.plot.zoom_out()
        qapp.processEvents()
        assert tab.plot.window_seconds > initial_window
        assert "LIVE" in tab.lbl_mode.text()

        # Zoom in
        tab.plot.zoom_in()
        qapp.processEvents()
        assert tab.plot.window_seconds == initial_window

        # Drag slider into frozen mode
        tab.plot.slider.setValue(5000)
        qapp.processEvents()
        assert not tab.plot.is_live
        assert tab.btn_jump_live.isVisible()
        assert "Frozen" in tab.lbl_mode.text()

        # Click Jump to Live
        tab.btn_jump_live.click()
        qapp.processEvents()
        assert tab.plot.is_live
        assert not tab.btn_jump_live.isVisible()
        assert "● LIVE" in tab.lbl_mode.text()

    def test_show_hide_controls_redraw_timer(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_reading(1.0e-6, timestamp=1.0, t_host=1.0)
        qapp.processEvents()
        assert tab.plot.redraw_timer.isActive()

        # Hide tab
        tab.hide()
        qapp.processEvents()
        assert not tab.plot.redraw_timer.isActive()

        # Show tab again
        tab.show()
        qapp.processEvents()
        assert tab.plot.redraw_timer.isActive()

    def test_disconnection_clears_plot_trace(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_reading(3.0e-6, timestamp=1.0, t_host=1.0)
        qapp.processEvents()

        # Disconnect
        beamline.disconnect_picoammeter()
        qapp.processEvents()

        assert "—" in tab.lbl_current.text()
        assert not tab.plot.redraw_timer.isActive()


RAW_STATUS_IN = 1 << 3   # Bit 3 (OUT) open, Bit 2 (IN) closed (0), Bit 4 (AUTO) closed (0)
RAW_STATUS_OUT = 1 << 2  # Bit 2 (IN) open, Bit 3 (OUT) closed (0), Bit 4 (AUTO) closed (0)
RAW_STATUS_TRANSIT = (1 << 2) | (1 << 3)  # Both open (1), neither asserted
RAW_STATUS_INDETERMINATE = 0  # Both closed (0), both asserted (fault)
RAW_STATUS_NOT_AUTO = (1 << 3) | (1 << 4)  # IN closed (0), AUTO open (1)


class TestFaradayCupActuationUI:
    """Tests for Faraday cup actuation controls and indicators on FaradayCupTab (Ticket 05)."""

    def test_actuation_initial_disconnected_state(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        tab.show()
        qapp.processEvents()

        # Both indicators must be visible and distinct, showing disconnected / "—"
        assert tab.lbl_commanded.isVisible()
        assert tab.lbl_confirmed.isVisible()
        assert "—" in tab.lbl_commanded.text()
        assert "—" in tab.lbl_confirmed.text()

        # Insert and Retract buttons disabled when LabJack is not connected
        assert not tab.btn_insert.isEnabled()
        assert not tab.btn_retract.isEnabled()

    def test_actuation_buttons_enabled_when_labjack_connected(self, qapp):
        beamline = Beamline()
        beamline.lj.handle = 1
        tab = FaradayCupTab(beamline=beamline)
        feed = CupActuationFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_fio_state(RAW_STATUS_IN, t=1.0)
        qapp.processEvents()

        assert tab.btn_insert.isEnabled()
        assert tab.btn_retract.isEnabled()

    def test_actuation_confirming_move_updates_both_indicators(self, qapp):
        beamline = Beamline()
        beamline.lj.handle = 1
        tab = FaradayCupTab(beamline=beamline)
        feed = CupActuationFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        # Initially IN
        feed.send_fio_state(RAW_STATUS_IN, t=1.0)
        qapp.processEvents()
        assert "IN" in tab.lbl_commanded.text()
        assert "IN" in tab.lbl_confirmed.text()

        # Command OUT via tab Retract button
        tab.btn_retract.click()
        qapp.processEvents()

        # Commanded updates immediately to OUT; Confirmed remains IN
        assert "OUT" in tab.lbl_commanded.text()
        assert "IN" in tab.lbl_confirmed.text()

        # Feed sends in-transit status word
        feed.send_fio_state(RAW_STATUS_TRANSIT, t=1.1)
        qapp.processEvents()
        assert "OUT" in tab.lbl_commanded.text()
        confirmed_text = tab.lbl_confirmed.text().upper()
        assert "IN TRANSIT" in confirmed_text or "TRANSIT" in confirmed_text

        # Status contacts confirm OUT
        feed.send_fio_state(RAW_STATUS_OUT, t=1.2)
        qapp.processEvents()

        # Both indicators now show OUT
        assert "OUT" in tab.lbl_commanded.text()
        assert "OUT" in tab.lbl_confirmed.text()
        assert not tab.lbl_fault.isVisible()

    def test_actuation_non_confirming_move_raises_timeout_fault(self, qapp):
        beamline = Beamline()
        beamline.lj.handle = 1
        tab = FaradayCupTab(beamline=beamline)
        feed = CupActuationFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_fio_state(RAW_STATUS_IN, t=1.0)
        qapp.processEvents()

        # Command OUT at t=1.0
        tab.btn_retract.click()
        qapp.processEvents()
        assert "OUT" in tab.lbl_commanded.text()
        assert "IN" in tab.lbl_confirmed.text()

        # Advance window to t=1.5 (0.5 s elapsed < 2.0 s timeout)
        feed.send_fio_state(RAW_STATUS_IN, t=1.5)
        qapp.processEvents()
        assert not tab.lbl_fault.isVisible()
        assert "IN" in tab.lbl_confirmed.text()

        # Advance window to t=3.2 (2.2 s elapsed >= 2.0 s timeout)
        feed.send_fio_state(RAW_STATUS_IN, t=3.2)
        qapp.processEvents()

        # Move failed to confirm: confirmed unchanged, timeout fault raised naming move OUT
        assert "IN" in tab.lbl_confirmed.text()
        assert tab.lbl_fault.isVisible()
        fault_text = tab.lbl_fault.text()
        assert "OUT" in fault_text
        lower = fault_text.lower()
        assert "confirm" in lower or "timeout" in lower or "2.0" in lower

    def test_actuation_controller_not_in_auto_warning(self, qapp):
        beamline = Beamline()
        beamline.lj.handle = 1
        tab = FaradayCupTab(beamline=beamline)
        feed = CupActuationFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_fio_state(RAW_STATUS_NOT_AUTO, t=1.0)
        qapp.processEvents()

        # Not in AUTO warning should state plainly that commands will be ignored
        assert tab.lbl_auto_mode.isVisible()
        auto_text = tab.lbl_auto_mode.text().lower()
        assert "auto" in auto_text or "local" in auto_text
        assert "ignore" in auto_text

    def test_actuation_indeterminate_status_presents_as_fault(self, qapp):
        beamline = Beamline()
        beamline.lj.handle = 1
        tab = FaradayCupTab(beamline=beamline)
        feed = CupActuationFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_fio_state(RAW_STATUS_INDETERMINATE, t=1.0)
        qapp.processEvents()

        # Indeterminate reading presented as a fault, not as a normal position
        confirmed_text = tab.lbl_confirmed.text().lower()
        assert "fault" in confirmed_text or "indeterminate" in confirmed_text
        assert tab.lbl_fault.isVisible()

    def test_actuation_move_in_flight_prevents_second_command(self, qapp, monkeypatch):
        beamline = Beamline()
        beamline.lj.handle = 1
        tab = FaradayCupTab(beamline=beamline)
        feed = CupActuationFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_fio_state(RAW_STATUS_OUT, t=1.0)
        qapp.processEvents()

        commands_sent = []
        monkeypatch.setattr(beamline, "command_cup", lambda pos: commands_sent.append(pos))
        monkeypatch.setattr(beamline, "command_cup_in", lambda: commands_sent.append("IN"))
        monkeypatch.setattr(beamline, "command_cup_out", lambda: commands_sent.append("OUT"))

        # Click Insert -> move in flight to IN
        tab.btn_insert.click()
        qapp.processEvents()
        assert len(commands_sent) == 1

        # While move is in flight, click Insert again
        tab.btn_insert.click()
        qapp.processEvents()
        # No second command queued!
        assert len(commands_sent) == 1

    def test_actuation_stale_reading_distinct_from_disconnected(self, qapp):
        beamline = Beamline()
        beamline.lj.handle = 1
        tab = FaradayCupTab(beamline=beamline)
        feed = CupActuationFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        # Send non-FULL profile window (FIO_STATE is None)
        feed.send_fio_state(None, t=1.0, profile="WAVEFORM")
        qapp.processEvents()

        assert tab.lbl_stale.isVisible()
        stale_text = tab.lbl_stale.text().lower()
        assert "stale" in stale_text
        # Distinguishable from disconnected
        assert tab.btn_insert.isEnabled()

    def test_main_window_wiring_cup_actuation(self, win, qapp):
        # Verify that MainWindow wires beamline.cup_actuation_changed to faraday_cup_tab
        tab = win.faraday_cup_tab
        win.beamline.lj.handle = 1
        feed = CupActuationFeed(beamline=win.beamline)
        feed.send_fio_state(RAW_STATUS_OUT, t=1.0)
        feed.command_cup_out()
        qapp.processEvents()

        assert "OUT" in tab.lbl_commanded.text()
        assert "OUT" in tab.lbl_confirmed.text()
        assert tab.btn_insert.isEnabled()


class TestFaradayCupDoseParameters:
    """Tests for dose tracking, irradiated area, and displacement coefficient provenance."""

    def test_dose_fields_exist_and_use_inputs_widgets(self, qapp):
        from rbl.gui.widgets.inputs import QuietDoubleSpinBox, ScientificDoubleSpinBox
        tab = FaradayCupTab()
        tab.show()
        qapp.processEvents()

        # Check area and source indicators
        assert hasattr(tab, "lbl_area")
        assert hasattr(tab, "lbl_area_source")
        assert "Raster Planner" in tab.lbl_area_source.text()

        # Check k and depth fields use inputs widgets
        assert isinstance(tab.spn_k, ScientificDoubleSpinBox)
        assert isinstance(tab.spn_depth, QuietDoubleSpinBox)

        # Check provenance fields exist
        assert hasattr(tab, "le_srim_version")
        assert hasattr(tab, "le_entry_date")

    def test_on_patch_dimensions_changed_updates_area(self, qapp):
        tab = FaradayCupTab()
        tab.show()
        qapp.processEvents()

        # Update patch dimensions: 5.0 mm x 10.0 mm -> 0.5 cm²
        tab.on_patch_dimensions_changed(5.0, 10.0)
        qapp.processEvents()

        assert math.isclose(tab.area_cm2, 0.5, rel_tol=1e-9)
        assert "0.5000 cm²" in tab.lbl_area.text()
        assert "5.000 × 10.000 mm" in tab.lbl_area.text()
        assert tab.patch_dimensions_mm == (5.0, 10.0)

    def test_operator_enters_k_and_provenance(self, qapp):
        tab = FaradayCupTab()
        tab.show()
        qapp.processEvents()

        tab.spn_k.setValue(1.0e-15)
        tab.spn_depth.setValue(250.0)
        tab.le_srim_version.setText("SRIM-2013.00")
        tab.le_entry_date.setText("2026-09-16")
        qapp.processEvents()

        assert math.isclose(tab.displacement_coeff, 1.0e-15, rel_tol=1e-9)
        assert math.isclose(tab.depth_nm, 250.0, rel_tol=1e-9)
        assert tab.srim_version == "SRIM-2013.00"
        assert tab.entry_date == "2026-09-16"

    def test_main_window_signal_wiring_patch_dimensions(self, win, qapp):
        """Assert changing the planner's patch dimensions changes the area the cup tab
        reports, through the real signal path.
        """
        planner = win.raster_planner_tab
        cup_tab = win.faraday_cup_tab

        # Initial seed check
        qapp.processEvents()
        initial_w = planner.sb_patch_x_mm.value()
        initial_h = planner.sb_patch_y_mm.value()
        expected_initial_area = (initial_w * initial_h) / 100.0
        assert math.isclose(cup_tab.area_cm2, expected_initial_area, rel_tol=1e-9)

        # Change planner patch dimensions
        planner.sb_patch_x_mm.setValue(8.0)
        planner.sb_patch_y_mm.setValue(12.0)
        qapp.processEvents()

        # 8.0 mm x 12.0 mm = 96.0 mm² = 0.96 cm²
        assert math.isclose(cup_tab.area_cm2, 0.96, rel_tol=1e-9)
        assert "0.9600 cm²" in cup_tab.lbl_area.text()
        assert "8.000 × 12.000 mm" in cup_tab.lbl_area.text()
        assert "Raster Planner" in cup_tab.lbl_area_source.text()




